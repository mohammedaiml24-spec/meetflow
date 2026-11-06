# MeetFlow — AI-Powered Meeting-to-Action Assistant

A deployable starter project that turns meeting transcripts and recordings into meeting summaries, decisions, action items, owners, due dates and clarification flags.

## Features

- Upload `.txt`, `.md`, `.vtt`, `.srt` transcript files or paste transcript text.
- Upload audio and transcribe with AssemblyAI speaker diarization.
- Live microphone recording in the browser; the resulting audio is transcribed with speaker labels.
- AI meeting summary, decisions, tasks, owners, deadlines and ambiguity flags (OpenAI API).
- SQLite meeting history and task storage.
- Task status tracking: Open / In Progress / Done.
- Email task assignments through SMTP.
- Calendar event download as `.ics` (import into Google Calendar, Outlook or Apple Calendar).
- Optional hourly reminder worker for tasks due within 24 hours.

## Important behaviour / limitations

- This is a working starter, not a fully managed enterprise product.
- Live listening records the microphone's mixed audio. It does not identify a speaker's real-world identity. AssemblyAI diarization labels voices as Speaker A/B and so on; a human must map those labels to names.
- Browser microphone access requires HTTPS (localhost is also allowed).
- The AI must not be treated as the final authority. Review owners, dates, decisions and clarification flags before emailing or scheduling.
- The calendar feature generates standard `.ics` files. Full two-way Google Calendar OAuth sync is not implemented in this starter.
- Reminder emails require SMTP configuration and `ENABLE_REMINDERS=true`. On free hosting, background workers may sleep; use a paid always-on worker for reliable reminders.
- SQLite on ephemeral hosting can reset on redeploy/restart. Use a persistent disk or replace SQLite with hosted PostgreSQL for production.

## Run locally (Windows)

1. Install Python 3.11 or newer from https://www.python.org/downloads/.
2. Extract this project ZIP.
3. Open the extracted folder in VS Code.
4. Open Terminal in VS Code and run:

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

5. Open `.env` and add:
   - `OPENAI_API_KEY` from https://platform.openai.com/api-keys
   - `ASSEMBLYAI_API_KEY` from https://www.assemblyai.com/dashboard/signup
   - SMTP values only if you want email assignments and reminders.
6. Start the app:

```powershell
python app.py
```

7. Open http://127.0.0.1:5000

API usage may incur charges. Keep keys private; never paste them into source files or commit `.env` to GitHub.

## Email setup (Gmail)

1. Enable 2-Step Verification on the sender Google account.
2. Create a Google App Password (Google Account → Security → App passwords).
3. Put the 16-character app password in `SMTP_PASSWORD` (not your normal Gmail password).
4. Add SMTP host `smtp.gmail.com`, port `587`, and sender email.
5. Restart the app. Email is sent only when you click **Email assignment**.

## Automatic reminders

Set `ENABLE_REMINDERS=true` and SMTP credentials. The background loop checks hourly and sends one reminder per task when the due date is today or tomorrow. This simple starter sends at most one reminder per task; production reminders should support configurable schedules, retries and time zones.

## Publish using GitHub + Render

### A. Create a GitHub repository

1. Sign in at https://github.com and click **New repository**.
2. Name it `meeting-to-action-assistant`.
3. Set it to **Public** (or Private if preferred); do not add a README if uploading this folder as-is.
4. In VS Code, open the project folder and open Terminal.
5. Run:

```bash
git init
git add .
git commit -m "Initial MeetFlow project"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/meeting-to-action-assistant.git
git push -u origin main
```

Replace `YOUR-USERNAME` with your GitHub username. If Git asks you to authenticate, complete the browser sign-in prompt.

### B. Deploy on Render (creates the public website link)

1. Open https://render.com and sign in using GitHub.
2. Choose **New + → Web Service**.
3. Connect the `meeting-to-action-assistant` repository.
4. Render should detect `render.yaml`. If asked, use:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
5. In Render → Environment, add `OPENAI_API_KEY` and `ASSEMBLYAI_API_KEY`. Add SMTP variables if you want email and set `ENABLE_REMINDERS` to `true` only after SMTP is configured.
6. Click **Deploy**. Wait for the deploy to finish.
7. Your public link will look like `https://meetflow-assistant.onrender.com` (the exact URL is assigned by Render). Open it and test the app.

GitHub stores your code; Render hosts the running Python app. A GitHub Pages link alone will not run this Python backend.

## Before using with a real team

- Obtain consent before recording meetings and follow applicable privacy/recording laws and company policy.
- Use a persistent database and backups.
- Add login, role-based permissions, CSRF protection, rate limiting and audit logs before handling confidential meetings.
- Restrict file types and scan uploads in production.
- Configure a persistent worker/queue for reliable reminders.
