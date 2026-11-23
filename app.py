import io
import json
import os
import re
import smtplib
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file
from google import genai
from google.genai import types

load_dotenv()
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
DB = os.getenv("DATABASE_PATH", "meeting_assistant.db")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS meetings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, meeting_date TEXT, transcript TEXT,
            summary TEXT, decisions TEXT, created_at TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id INTEGER, title TEXT, assignee TEXT, email TEXT,
            due_date TEXT, status TEXT DEFAULT 'Open', details TEXT,
            clarification TEXT, reminder_sent INTEGER DEFAULT 0,
            FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
        )""")


init_db()


def _response_text(response):
    text = getattr(response, "text", None)
    if text:
        return text.strip()
    # Fallback for SDK response variants.
    try:
        return response.candidates[0].content.parts[0].text.strip()
    except (AttributeError, IndexError, TypeError):
        raise RuntimeError("Gemini returned an empty response. Please try again.")


def _is_temporary_gemini_error(exc):
    message = str(exc).lower()
    return any(token in message for token in (
        "503", "unavailable", "high demand", "429", "resource_exhausted",
        "500", "internal", "temporarily", "deadline exceeded", "timeout"
    ))


def analyze_transcript(transcript):
    if not client:
        raise RuntimeError("Gemini is not configured. Add GEMINI_API_KEY to your .env file and restart MeetFlow.")

    prompt = """You are a careful meeting secretary. Analyze the meeting transcript.
Return ONLY valid JSON with this exact structure:
{
  "summary": "short, accurate summary",
  "decisions": ["explicit decisions only"],
  "tasks": [
    {
      "title": "clear action",
      "assignee": "person's name or empty string",
      "assignee_email": "email only if explicitly present, otherwise empty string",
      "due_date": "YYYY-MM-DD or empty string",
      "details": "useful context or empty string",
      "clarification": "what needs clarification, or empty string"
    }
  ]
}
Rules:
- Do not invent people, emails, dates, decisions, deadlines or commitments.
- Do not treat a discussion topic as a decision unless the transcript clearly says it was decided.
- Flag unclear owners, vague actions and relative deadlines (such as 'next Monday') in clarification.
- Convert a date to YYYY-MM-DD only when it is unambiguous from the transcript and meeting date.
- If there are no tasks or decisions, return empty arrays.

MEETING TRANSCRIPT:
"""
    prompt += transcript

    # Temporary capacity errors are retried with exponential backoff.
    # A persistent 503/429 is returned as a readable message instead of crashing.
    last_error = None
    for attempt in range(4):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            raw = _response_text(response)
            # Remove accidental Markdown fences if the model adds them.
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Gemini returned JSON in an unexpected format.")
            data.setdefault("summary", "")
            data.setdefault("decisions", [])
            data.setdefault("tasks", [])
            if not isinstance(data["decisions"], list):
                data["decisions"] = []
            if not isinstance(data["tasks"], list):
                data["tasks"] = []
            return data
        except Exception as exc:
            last_error = exc
            if attempt < 3 and _is_temporary_gemini_error(exc):
                time.sleep(2 ** attempt)
                continue
            break

    message = str(last_error or "Unknown Gemini error")
    if _is_temporary_gemini_error(last_error or Exception()):
        raise RuntimeError(
            f"Gemini is temporarily busy or rate-limited. MeetFlow retried automatically. "
            f"Please wait a minute and try again. Details: {message}"
        )
    raise RuntimeError(f"Gemini analysis failed: {message}")


def transcribe_audio(file_storage):
    key = os.getenv("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Add ASSEMBLYAI_API_KEY to .env to enable audio transcription.")
    headers = {"authorization": key}
    upload = requests.post(
        "https://api.assemblyai.com/v2/upload",
        headers=headers,
        data=file_storage.read(),
        timeout=180,
    )
    upload.raise_for_status()
    transcript_response = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers={**headers, "content-type": "application/json"},
        json={"audio_url": upload.json()["upload_url"], "speaker_labels": True},
        timeout=30,
    )
    transcript_response.raise_for_status()
    transcript_id = transcript_response.json()["id"]

    for _ in range(180):
        poll = requests.get(
            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
            headers=headers,
            timeout=30,
        )
        poll.raise_for_status()
        result = poll.json()
        if result["status"] == "completed":
            lines = [
                f"Speaker {item.get('speaker', '?')}: {item.get('text', '')}"
                for item in result.get("utterances", [])
            ]
            return "\n".join(lines) if lines else result.get("text", "")
        if result["status"] == "error":
            raise RuntimeError(result.get("error", "AssemblyAI transcription failed."))
        time.sleep(2)
    raise RuntimeError("Transcription timed out. Try a shorter recording.")


def send_task_email(task):
    required = ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"]
    if not all(os.getenv(name) for name in required):
        raise RuntimeError("Configure SMTP_HOST, SMTP_USER and SMTP_PASSWORD in .env first.")
    recipient = task.get("email")
    if not recipient:
        raise RuntimeError("This task has no assignee email address. Add an email address before sending.")
    msg = EmailMessage()
    msg["Subject"] = "Meeting action assigned: " + task["title"]
    msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER"))
    msg["To"] = recipient
    msg.set_content(f"""Hello {task.get('assignee') or 'there'},

A task was assigned from a meeting:

Task: {task['title']}
Due date: {task.get('due_date') or 'Not specified'}
Details: {task.get('details') or 'Please confirm the details with the meeting owner.'}

Please review the task and contact the meeting owner if anything is unclear.

This email was generated automatically by MeetFlow.""")
    with smtplib.SMTP(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587"))) as server:
        server.starttls()
        server.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
        server.send_message(msg)


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify(
        app="MeetFlow",
        gemini_configured=bool(GEMINI_API_KEY),
        gemini_model=GEMINI_MODEL,
        assemblyai_configured=bool(os.getenv("ASSEMBLYAI_API_KEY")),
    )


@app.post("/api/transcribe")
def api_transcribe():
    audio_file = request.files.get("audio")
    if not audio_file:
        return jsonify(error="Choose or record an audio file."), 400
    try:
        return jsonify(transcript=transcribe_audio(audio_file))
    except requests.HTTPError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        return jsonify(error=f"AssemblyAI request failed: {detail}"), 502
    except Exception as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/analyze")
def api_analyze():
    payload = request.get_json(silent=True) or {}
    transcript = (payload.get("transcript") or "").strip()
    if not transcript:
        return jsonify(error="Paste or transcribe a meeting transcript first."), 400
    if len(transcript) > 150000:
        return jsonify(error="This transcript is too long. Please split it into smaller sections."), 413
    try:
        data = analyze_transcript(transcript)
        title = (payload.get("title") or "Untitled meeting").strip() or "Untitled meeting"
        meeting_date = payload.get("meeting_date") or datetime.now().date().isoformat()
        with db() as c:
            cursor = c.execute(
                "INSERT INTO meetings(title,meeting_date,transcript,summary,decisions,created_at) VALUES(?,?,?,?,?,?)",
                (title, meeting_date, transcript, data["summary"], json.dumps(data["decisions"]), datetime.now().isoformat()),
            )
            meeting_id = cursor.lastrowid
            for task in data["tasks"]:
                if not isinstance(task, dict):
                    continue
                c.execute(
                    """INSERT INTO tasks(meeting_id,title,assignee,email,due_date,status,details,clarification)
                       VALUES(?,?,?,?,?,'Open',?,?)""",
                    (
                        meeting_id,
                        str(task.get("title") or "Unspecified task"),
                        str(task.get("assignee") or ""),
                        str(task.get("assignee_email") or ""),
                        str(task.get("due_date") or ""),
                        str(task.get("details") or ""),
                        str(task.get("clarification") or ""),
                    ),
                )
        return jsonify(id=meeting_id, **data)
    except Exception as exc:
        return jsonify(error=str(exc)), 502


@app.get("/api/history")
def history():
    with db() as c:
        rows = c.execute(
            "SELECT id,title,meeting_date,summary,created_at FROM meetings ORDER BY id DESC"
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.get("/api/meeting/<int:meeting_id>")
def meeting(meeting_id):
    with db() as c:
        meeting_row = c.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        task_rows = c.execute("SELECT * FROM tasks WHERE meeting_id=? ORDER BY id", (meeting_id,)).fetchall()
    if not meeting_row:
        return jsonify(error="Meeting not found."), 404
    result = dict(meeting_row)
    result["decisions"] = json.loads(result["decisions"] or "[]")
    result["tasks"] = [dict(row) for row in task_rows]
    return jsonify(result)


@app.get("/api/tasks")
def tasks():
    with db() as c:
        rows = c.execute(
            """SELECT tasks.*, meetings.title AS meeting_title FROM tasks
               LEFT JOIN meetings ON meetings.id=tasks.meeting_id
               ORDER BY CASE status WHEN 'Open' THEN 0 WHEN 'In Progress' THEN 1 ELSE 2 END, due_date"""
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.patch("/api/tasks/<int:task_id>")
def update_task(task_id):
    payload = request.get_json(silent=True) or {}
    status = payload.get("status")
    if status not in ["Open", "In Progress", "Done"]:
        return jsonify(error="Invalid task status."), 400
    with db() as c:
        cursor = c.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
    if cursor.rowcount == 0:
        return jsonify(error="Task not found."), 404
    return jsonify(ok=True)


@app.post("/api/tasks/<int:task_id>/email")
def email_task(task_id):
    with db() as c:
        row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        return jsonify(error="Task not found."), 404
    try:
        send_task_email(dict(row))
        return jsonify(ok=True)
    except Exception as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/calendar/<int:task_id>.ics")
def calendar_event(task_id):
    with db() as c:
        task = c.execute(
            "SELECT tasks.*, meetings.title AS meeting_title FROM tasks JOIN meetings ON meetings.id=tasks.meeting_id WHERE tasks.id=?",
            (task_id,),
        ).fetchone()
    if not task:
        return "Task not found", 404
    due_text = task["due_date"] or datetime.now().date().isoformat()
    try:
        due_date = datetime.strptime(due_text, "%Y-%m-%d").date()
    except ValueError:
        return "This task has an invalid due date. Update the task date before exporting.", 400
    from icalendar import Calendar, Event
    calendar = Calendar()
    event = Event()
    event.add("summary", "Task: " + task["title"])
    event.add("description", f"Meeting: {task['meeting_title']}\nAssignee: {task['assignee']}\n{task['details']}")
    event.add("dtstart", due_date)
    event.add("dtend", due_date + timedelta(days=1))
    calendar.add_component(event)
    return send_file(
        io.BytesIO(calendar.to_ical()),
        mimetype="text/calendar",
        as_attachment=True,
        download_name=f"task-{task_id}.ics",
    )


def reminder_loop():
    while True:
        try:
            with db() as c:
                due_tasks = c.execute(
                    """SELECT tasks.* FROM tasks
                       WHERE tasks.status!='Done' AND tasks.email!='' AND tasks.due_date!=''
                       AND tasks.reminder_sent=0 AND date(tasks.due_date)<=date('now','+1 day')"""
                ).fetchall()
            for row in due_tasks:
                try:
                    send_task_email({**dict(row), "title": "Reminder: " + row["title"]})
                    with db() as c:
                        c.execute("UPDATE tasks SET reminder_sent=1 WHERE id=?", (row["id"],))
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(3600)


if os.getenv("ENABLE_REMINDERS", "false").lower() == "true":
    threading.Thread(target=reminder_loop, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
