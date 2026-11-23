# MeetFlow — Agentic AI Meeting-to-Action Assistant

Project folder: **AGENTIC AI PROJECT DRAFT**

MeetFlow accepts meeting transcripts or audio recordings and creates a structured meeting brief with a summary, decisions, action items, owners, due dates and clarification flags.

## Features

- Paste a transcript or upload `.txt`, `.md`, `.vtt` or `.srt` files.
- Upload audio or record from the browser microphone.
- AssemblyAI transcription with speaker labels (Speaker A, Speaker B, etc.).
- Google Gemini analysis for summaries, decisions, tasks, assignees, deadlines and ambiguities.
- Automatic retry with exponential backoff for temporary Gemini capacity/rate-limit errors.
- SQLite meeting history and task tracker.
- Task statuses: Open, In Progress and Done.
- Optional SMTP task assignment email.
- Download task deadlines as `.ics` calendar files.

## Where to put your API keys

1. In VS Code, open the folder **AGENTIC AI PROJECT DRAFT**.
2. In the left Explorer panel, find `.env.example`.
3. Make a copy of it in the same folder and name the copy exactly `.env` (just `.env`, not `.env.txt`).
4. Open `.env` and fill these two lines:

```env
GEMINI_API_KEY=your_actual_Gemini_key_here
ASSEMBLYAI_API_KEY=your_actual_AssemblyAI_key_here
```

- Gemini key: https://aistudio.google.com/app/apikey
- AssemblyAI key: https://www.assemblyai.com/dashboard/signup
- Keep `GEMINI_MODEL=gemini-3.8-flash` unless Google AI Studio/API tells you to use another available model.
- Do not add quotation marks around keys. Do not put keys in `app.py`, HTML, JavaScript, GitHub, screenshots or chat messages.
- The old `OPENAI_API_KEY` is no longer used. You can remove it from your local `.env` after confirming MeetFlow works. It is not required in this project.
- AssemblyAI is needed only for audio transcription. Pasting/uploading a text transcript uses Gemini only.

## Run on Windows 11

Open the project folder in VS Code. From **Terminal → New Terminal**, run these commands one at a time:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

If PowerShell blocks activation for this terminal session, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

Then open http://127.0.0.1:5000 in your browser. Keep the terminal open while using MeetFlow. Press `Ctrl+C` in the terminal to stop it.

To check configuration, open http://127.0.0.1:5000/api/health. It reports whether the Gemini and AssemblyAI keys are present; it never displays the keys themselves.

## Gemini 503 / 429 handling

MeetFlow retries temporary Gemini service errors up to four attempts with increasing waits. If Google continues returning 503 (high demand) or 429 (rate limit/quota), wait and try later, or choose a currently available Flash model in Google AI Studio and update `GEMINI_MODEL` in `.env`. A free-tier API has rate and usage limits; it is not unlimited. The model name can change over time, so the value in `.env` is configurable.

## Audio transcription

Audio upload and browser microphone recording use AssemblyAI. The service has its own free-tier limits and billing rules. Speaker diarization labels voices (for example, Speaker A/B); it does not identify a person's real-world name. Review the transcript before analysis.

## Optional email setup

To send task assignment emails, configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` and `SMTP_FROM` in `.env`. For Gmail, use a Google App Password (not your regular account password). Leave these values blank if you do not need email.

## GitHub and Render deployment

- GitHub stores the source code; it does not run this Flask application by itself.
- Create a GitHub repository and upload/push the contents of this folder. Never upload `.env`; `.gitignore` excludes it.
- On Render, create a Web Service connected to the repository. Use build command `pip install -r requirements.txt` and start command `gunicorn app:app`.
- In Render's Environment settings, add `GEMINI_API_KEY`, `GEMINI_MODEL` and `ASSEMBLYAI_API_KEY` as environment variables. Do not commit secrets.
- A free hosting instance may sleep, and its local SQLite file may not be durable across restarts/redeploys. Use a persistent disk or managed database for reliable history.

## Important limitations and safety

- AI output can be wrong. Verify all tasks, owners, dates and decisions before relying on them or sending emails.
- Obtain consent before recording meetings and follow applicable privacy rules and organizational policies.
- Browser microphone access is supported on localhost and HTTPS.
- The calendar feature creates `.ics` files for importing into calendar apps; it is not a two-way Google Calendar integration.
- This is a project/demo starter. Before handling confidential or business-critical meetings, add authentication, access controls, CSRF protection, rate limiting, backups and a production database.
