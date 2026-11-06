import os, json, sqlite3, smtplib, threading, time, io
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from flask import Flask, render_template, request, jsonify, send_file
from dotenv import load_dotenv
import requests

load_dotenv()
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
DB = os.getenv("DATABASE_PATH", "meeting_assistant.db")

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS meetings(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, meeting_date TEXT,
            transcript TEXT, summary TEXT, decisions TEXT, created_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id INTEGER, title TEXT,
            assignee TEXT, email TEXT, due_date TEXT, status TEXT DEFAULT 'Open',
            details TEXT, clarification TEXT, reminder_sent INTEGER DEFAULT 0,
            FOREIGN KEY(meeting_id) REFERENCES meetings(id))""")
init_db()


def analyze_transcript(transcript):
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing.")

    prompt = """Analyze this meeting transcript. Return ONLY valid JSON with:
    summary (string), decisions (array of strings), tasks (array of objects with
    title, assignee, assignee_email, due_date (YYYY-MM-DD or empty), details,
    clarification (empty unless owner/deadline/action is ambiguous)).
    Do not invent names, emails, dates, decisions or commitments. Use null/empty
    when unknown. Flag vague assignments and relative deadlines for clarification.

    Transcript:
    """ + transcript

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-3.8-flash:generateContent"
    )

    response = requests.post(
        url,
        params={"key": api_key},
        json={
            "contents": [
                {"parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1
            }
        },
        timeout=90
    )

    response.raise_for_status()

    result = response.json()
    text = result["candidates"][0]["content"]["parts"][0]["text"]

    data = json.loads(text)
    data.setdefault("summary", "")
    data.setdefault("decisions", [])
    data.setdefault("tasks", [])

    return data


def transcribe_audio(file_storage):
    key = os.getenv("ASSEMBLYAI_API_KEY")
    if not key:
        raise RuntimeError("Add ASSEMBLYAI_API_KEY to enable audio transcription and speaker labels.")
    headers = {"authorization": key}
    upload = requests.post("https://api.assemblyai.com/v2/upload",
        headers=headers, data=file_storage.read(), timeout=180)
    upload.raise_for_status()
    transcript = requests.post("https://api.assemblyai.com/v2/transcript",
        headers={**headers,"content-type":"application/json"},
        json={"audio_url":upload.json()["upload_url"],"speaker_labels":True}, timeout=30)
    transcript.raise_for_status()
    tid = transcript.json()["id"]
    for _ in range(180):
        r = requests.get(f"https://api.assemblyai.com/v2/transcript/{tid}",headers=headers,timeout=30)
        r.raise_for_status()
        data = r.json()
        if data["status"] == "completed":
            # Keep speaker labels visible in the transcript.
            lines=[]
            for item in data.get("utterances",[]):
                lines.append(f"Speaker {item.get('speaker','?')}: {item.get('text','')}")
            return "\\n".join(lines) if lines else data.get("text","")
        if data["status"] == "error":
            raise RuntimeError(data.get("error","Transcription failed"))
        time.sleep(2)
    raise RuntimeError("Transcription timed out. Try a shorter recording.")

def send_task_email(task):
    required = ["SMTP_HOST","SMTP_USER","SMTP_PASSWORD"]
    if not all(os.getenv(x) for x in required):
        raise RuntimeError("Configure SMTP_HOST, SMTP_USER and SMTP_PASSWORD in .env first.")
    recipient = task.get("email")
    if not recipient:
        raise RuntimeError("This task has no assignee email address.")
    msg=EmailMessage()
    msg["Subject"]="Meeting action assigned: "+task["title"]
    msg["From"]=os.getenv("SMTP_FROM",os.getenv("SMTP_USER"))
    msg["To"]=recipient
    msg.set_content(f"""Hello {task.get('assignee') or 'there'},

A task was assigned from a meeting:

Task: {task['title']}
Due date: {task.get('due_date') or 'Not specified'}
Details: {task.get('details') or 'Please confirm the details with the meeting owner.'}

Please update the task status in the Meeting-to-Action Assistant.

This email was generated automatically.""")
    with smtplib.SMTP(os.getenv("SMTP_HOST"),int(os.getenv("SMTP_PORT","587"))) as s:
        s.starttls()
        s.login(os.getenv("SMTP_USER"),os.getenv("SMTP_PASSWORD"))
        s.send_message(msg)

@app.route("/")
def home():
    return render_template("index.html")

@app.post("/api/transcribe")
def api_transcribe():
    f=request.files.get("audio")
    if not f: return jsonify(error="Choose or record an audio file."),400
    try: return jsonify(transcript=transcribe_audio(f))
    except Exception as e: return jsonify(error=str(e)),400

@app.post("/api/analyze")
def api_analyze():
    payload=request.get_json(force=True)
    transcript=(payload.get("transcript") or "").strip()
    if not transcript: return jsonify(error="Paste or transcribe a meeting transcript first."),400
    try:
        data=analyze_transcript(transcript)
        title=(payload.get("title") or "Untitled meeting").strip()
        date=payload.get("meeting_date") or datetime.now().date().isoformat()
        with db() as c:
            cur=c.execute("INSERT INTO meetings(title,meeting_date,transcript,summary,decisions,created_at) VALUES(?,?,?,?,?,?)",
                (title,date,transcript,data["summary"],json.dumps(data["decisions"]),datetime.now().isoformat()))
            mid=cur.lastrowid
            for t in data["tasks"]:
                c.execute("""INSERT INTO tasks(meeting_id,title,assignee,email,due_date,status,details,clarification)
                    VALUES(?,?,?,?,?,'Open',?,?,?)""".replace("VALUES(?,?,?,?,?,'Open',?,?,?)","VALUES(?,?,?,?,?,'Open',?,?)"),
                    (mid,t.get("title","Unspecified task"),t.get("assignee") or "",t.get("assignee_email") or "",
                     t.get("due_date") or "",t.get("details") or "",t.get("clarification") or ""))
        return jsonify(id=mid, **data)
    except Exception as e: return jsonify(error=str(e)),400

@app.get("/api/history")
def history():
    with db() as c:
        rows=c.execute("SELECT id,title,meeting_date,summary,created_at FROM meetings ORDER BY id DESC").fetchall()
    return jsonify([dict(x) for x in rows])

@app.get("/api/meeting/<int:mid>")
def meeting(mid):
    with db() as c:
        m=c.execute("SELECT * FROM meetings WHERE id=?",(mid,)).fetchone()
        tasks=c.execute("SELECT * FROM tasks WHERE meeting_id=? ORDER BY id",(mid,)).fetchall()
    if not m: return jsonify(error="Meeting not found"),404
    d=dict(m); d["decisions"]=json.loads(d["decisions"]); d["tasks"]=[dict(x) for x in tasks]
    return jsonify(d)

@app.get("/api/tasks")
def tasks():
    with db() as c:
        rows=c.execute("""SELECT tasks.*, meetings.title AS meeting_title FROM tasks
            LEFT JOIN meetings ON meetings.id=tasks.meeting_id ORDER BY
            CASE status WHEN 'Open' THEN 0 WHEN 'In Progress' THEN 1 ELSE 2 END, due_date""").fetchall()
    return jsonify([dict(x) for x in rows])

@app.patch("/api/tasks/<int:tid>")
def update_task(tid):
    p=request.get_json(force=True)
    status=p.get("status")
    if status not in ["Open","In Progress","Done"]: return jsonify(error="Invalid status"),400
    with db() as c: c.execute("UPDATE tasks SET status=? WHERE id=?",(status,tid))
    return jsonify(ok=True)

@app.post("/api/tasks/<int:tid>/email")
def email_task(tid):
    with db() as c: row=c.execute("SELECT * FROM tasks WHERE id=?",(tid,)).fetchone()
    if not row: return jsonify(error="Task not found"),404
    try: send_task_email(dict(row)); return jsonify(ok=True)
    except Exception as e: return jsonify(error=str(e)),400

@app.get("/api/calendar/<int:tid>.ics")
def calendar_event(tid):
    with db() as c: t=c.execute("SELECT tasks.*,meetings.title AS mt FROM tasks JOIN meetings ON meetings.id=tasks.meeting_id WHERE tasks.id=?",(tid,)).fetchone()
    if not t: return "Task not found",404
    due=t["due_date"] or datetime.now().date().isoformat()
    start=datetime.fromisoformat(due).strftime("%Y%m%d")
    from icalendar import Calendar, Event
    cal=Calendar(); ev=Event()
    ev.add("summary","Task: "+t["title"])
    ev.add("description",f"Meeting: {t['mt']}\\nAssignee: {t['assignee']}\\n{t['details']}")
    ev.add("dtstart",datetime.strptime(start,"%Y%m%d").date())
    ev.add("dtend",datetime.strptime(start,"%Y%m%d").date()+timedelta(days=1))
    cal.add_component(ev)
    return send_file(io.BytesIO(cal.to_ical()),mimetype="text/calendar",as_attachment=True,download_name=f"task-{tid}.ics")

def reminder_loop():
    while True:
        try:
            with db() as c:
                due=c.execute("""SELECT tasks.*, meetings.title AS mt FROM tasks
                    JOIN meetings ON meetings.id=tasks.meeting_id
                    WHERE tasks.status!='Done' AND tasks.email!='' AND tasks.due_date!=''
                    AND tasks.reminder_sent=0 AND date(tasks.due_date)<=date('now','+1 day')""").fetchall()
            for row in due:
                try:
                    send_task_email({**dict(row),"title":"Reminder: "+row["title"]})
                    with db() as c: c.execute("UPDATE tasks SET reminder_sent=1 WHERE id=?",(row["id"],))
                except Exception: pass
        except Exception: pass
        time.sleep(3600)

if os.getenv("ENABLE_REMINDERS","false").lower()=="true":
    threading.Thread(target=reminder_loop,daemon=True).start()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","5000")),debug=False)
