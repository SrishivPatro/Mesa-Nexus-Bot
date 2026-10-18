# Mesa WhatsApp bot

Daily WhatsApp briefs, deadline alerts and a chat bot for your life at Mesa School of Business.
It reads **Nexus MCP** (assignments, notifications, announcements, schedule, grades,
attendance, courses, profile, plus the `reminder-automation` skill as the main deadline feed)
and **Google Calendar**. It sends messages through **CallMeBot**. It runs on **Railway** with
APScheduler and Flask.

| Message | When (IST) | What |
|---|---|---|
| Morning brief | daily 8:00 am | timetable · action items (assignments, forms, opt-ins, pre-reads, MRS, links, attachments) · attendance alerts · new grades · Google Calendar (today + 48h) |
| Deadline alert | hourly at :05 | anything due in 24h; a 48h heads-up for pre-work, one-pagers, case studies and opt-ins before their session; never repeats an item within 12h |
| Grade alert | daily 6:00 pm | new marks or feedback, each grade alerted once |
| Weekly summary | Sunday 9:00 pm | next 7 days due · next week's timetable · flagged announcements · attendance |

> This folder lives inside the `Beta-MVP` repo, but it is a separate project. On Railway, deploy it with
> **Root Directory = `/mesa-bot`** (see step 6).

---

## Setup, step by step

### 1. Get a CallMeBot API key (outbound WhatsApp)
1. Save **+34 644 63 21 91** in your phone's contacts (any name, e.g. "CallMeBot").
2. On WhatsApp, send that contact exactly: **`I allow callmebot to send me messages`**
3. Wait for the reply, *"API Activated for your phone number. Your APIKEY is 123456"*. Save the key.
   If no reply comes within 2 minutes, try again after 24 hours (see callmebot.com for the current activation number).
4. `CALLMEBOT_PHONE` = your number with country code, e.g. `+919876543210`.
   `CALLMEBOT_API_KEY` = the key from the reply.

### 2. Get your Nexus MCP token
1. Open Nexus and click **Nexus MCP** in the sidebar.
2. **Generate a token**. Tick every area: Schedule, Assignments, Grades, Attendance, Announcements,
   Notifications, Courses, Profile. Missing areas show up as errors in that section.
3. Pick an expiry date. When it expires, generate a new token and update the Railway variable.
4. Copy the **token** (starts with `mesa_pat_`) → `NEXUS_MCP_TOKEN`. It is shown **only once**, in the creation window.
   If you lost it, delete the old token and create a new one. The server URL defaults to
   `https://students.mesaschool.co.in/mcp`, and the bot calls `…/mcp/<token>` as the Nexus page describes.

### 3. Google Calendar API credentials (optional but recommended)
**Option A: service account (simplest for a server)**
1. Go to [Google Cloud Console](https://console.cloud.google.com/) → create or select a project.
2. **APIs & Services → Library** → search **Google Calendar API** → **Enable**.
3. **APIs & Services → Credentials → Create credentials → Service account**. Give it a name; it needs no roles.
4. Open the service account → **Keys → Add key → Create new key → JSON**. A `.json` file downloads.
5. Copy the service account's email (`…@….iam.gserviceaccount.com`).
6. In [Google Calendar](https://calendar.google.com) → ⚙️ Settings → your calendar → **Share with specific people** →
   add that email with **See all event details**.
7. `GOOGLE_CALENDAR_CREDENTIALS_JSON` = the **entire contents** of the JSON file (paste it as one value).
   `GOOGLE_CALENDAR_ID` = your calendar ID, usually your Gmail address. Comma-separate several calendars.

**Option B: your own Google account (OAuth)**
1. Do steps 1–2 above, then **OAuth consent screen** (External, add yourself as a test user).
2. **Credentials → Create credentials → OAuth client ID → Desktop app** → download `client_secret.json`.
3. Locally: `pip install -r requirements-dev.txt && python -m mesabot.cli google-auth client_secret.json`.
   A browser opens. Approve, and the command prints a JSON token with a `refresh_token`.
4. `GOOGLE_CALENDAR_CREDENTIALS_JSON` = that printed JSON. `GOOGLE_CALENDAR_ID` = `primary`.

If the variable is missing, messages say *"Google Calendar not connected"* and everything else still works.

### 4. Pick a webhook secret
Make up a long random string, e.g. `python -c "import secrets; print(secrets.token_urlsafe(24))"` → `WEBHOOK_SECRET`.
The inbound webhook refuses every request without it.

### 5. Try it locally (optional)
```bash
cd mesa-bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                                                    # 25 tests, offline
python -m mesabot.cli preview morning --fixtures tests/fixtures --now 2026-10-19T08:00:00+05:30
cp .env.example .env   # fill it in, then:
set -a; source .env; set +a
python -m mesabot.cli check                                   # verifies Nexus + Calendar
python -m mesabot.cli preview morning                         # real data, prints instead of sending
python -m mesabot.cli ask "am I at risk of low attendance"
python -m mesabot.cli send morning                            # really sends to WhatsApp
```

### 6. Deploy to Railway
1. Push this repo to GitHub (it already is: `SrishivPatro/Beta-MVP`).
2. [railway.com](https://railway.com) → **New Project → Deploy from GitHub repo** → pick the repo.
3. Open the service → **Settings**:
   - **Source → Root Directory** = `/mesa-bot`
   - **Config-as-code → Railway config file** = `/mesa-bot/railway.toml`
   - **Networking → Generate Domain** (you get `https://<name>.up.railway.app`)
   - Leave **Serverless / App Sleeping OFF**: the scheduler only runs while the process is awake.
4. **Add a Volume**: right-click the service (or ⌘K → "Volume") → **Add Volume** → mount path **`/data`**.
   Railway then sets `RAILWAY_VOLUME_MOUNT_PATH=/data`, and the log is stored at `/data/mesa_log.json`.
5. **Variables** tab → add (Raw Editor accepts `KEY=value` lines):

   | Variable | Required | Purpose |
   |---|---|---|
   | `NEXUS_MCP_TOKEN` | ✅ | Nexus MCP token (Bearer) |
   | `NEXUS_MCP_BASE_URL` | optional | Default `https://students.mesaschool.co.in/mcp` |
   | `CALLMEBOT_PHONE` | ✅ | Your WhatsApp number, with country code |
   | `CALLMEBOT_API_KEY` | ✅ | Key from the CallMeBot activation reply |
   | `GOOGLE_CALENDAR_CREDENTIALS_JSON` | optional | Service-account key JSON or authorized-user JSON (raw, base64, or a file path) |
   | `GOOGLE_CALENDAR_ID` | optional | Calendar id(s), comma-separated. Default `primary` |
   | `WEBHOOK_SECRET` | for the bot | Shared secret for `/webhook` and `/run/<job>` |
   | `DATA_DIR` | optional | Where `mesa_log.json` lives. Default: the Railway volume, else `./data` |
   | `DRY_RUN` | optional | `true` logs messages instead of sending them |
   | `RUN_SCHEDULER` | optional | `false` disables the scheduled jobs |
   | `ATTENDANCE_THRESHOLD` | optional | Default `75` |
   | `ATTENDANCE_AT_RISK_MARGIN` | optional | Warn when you can miss ≤ this many more classes. Default `1` |
   | `ATTENDANCE_UNTRACKED_COURSES` | optional | Comma list. Default `AI Workshops,Career Readiness,Workshops,Mesa Startup Lab` |
   | `ACTION_LOOKBACK_DAYS` | optional | Show undated action items posted in the last N days. Default `7` |
   | `WHATSAPP_CHUNK_SIZE` | optional | Long messages are split at this many characters. Default `1400` |
   | `LOG_LEVEL` | optional | Default `INFO` |
6. Deploy. Open `https://<name>.up.railway.app/health`. It lists any missing variables and the next run of each job.
7. Test a job right away:
   `curl -X POST "https://<name>.up.railway.app/run/morning?key=$WEBHOOK_SECRET"` (also `deadlines`, `grades`, `weekly`).

> Railway pricing: Railway no longer has an always-free tier. New accounts get trial credit, and then
> the Hobby plan applies. This bot is a single small process (about 100 MB RAM) and should stay at the low end of usage.

### 7. Inbound messages (talking to the bot)
**CallMeBot only sends messages; it cannot forward your replies to a server.** To chat with the bot, use one of these relays.
Each one posts your message to `/webhook`:

**A. Twilio WhatsApp Sandbox (real two-way WhatsApp, free trial)**
1. Create a Twilio account → **Messaging → Try it out → Send a WhatsApp message**, and join the sandbox from your phone.
2. Under **Sandbox settings → When a message comes in**, enter `https://<name>.up.railway.app/webhook?key=<WEBHOOK_SECRET>` (POST).
3. Message the sandbox number, e.g. `due`. The reply comes back in the same chat (as TwiML).
   Scheduled messages still arrive from CallMeBot. The sandbox may ask you to re-join after a period of inactivity.

**B. Shortcut / automation (iOS Shortcuts, Android Tasker/HTTP Shortcuts, n8n, Make, Zapier)**
POST `{"text": "attendance"}` to `https://<name>.up.railway.app/webhook` with header `X-Webhook-Secret: <secret>`.
The reply comes back as JSON *and* is sent to your WhatsApp via CallMeBot. Add `"reply_via": "none"` to skip the WhatsApp copy.

**C. Quick test from a browser:** `https://<name>.up.railway.app/webhook?key=<secret>&text=mrs`

Only your own number (`CALLMEBOT_PHONE`) gets answers. Messages from any other `From` are rejected.

---

## Bot commands

| Send | You get |
|---|---|
| `due` | items due today, due this week, and overdue |
| `today` | today's timetable and action items |
| `week` | the week ahead (due items, flagged announcements, timetable, attendance, calendar) |
| `attendance` | % for every course: 🔴 below 75% (classes needed to recover), 🟠 at risk (classes you can still miss), 🟢 ok, untracked courses labelled |
| `grades` | every released mark |
| `pending` | everything not submitted or not marked done, including items carried from last term |
| `mrs` | this week's MRS Practice Lab module and whether it's done |
| `schedule <day>` | e.g. `schedule monday`, `schedule tomorrow`, `schedule next fri`, `schedule 24 oct` |
| `done <keyword>` | marks a form, link, attachment, announcement action, assignment or carried-forward attendance warning as done, e.g. `done mrs`, `done ils form`, `done case.pdf` |
| `feedback <assignment>` | full feedback text for one grade |
| `help` | this list |
| anything else | routed to the right Nexus endpoint: *"when is my next Finance class"*, *"what did I score in Art of Selling"*, *"am I at risk of low attendance"*, *"what announcements came this week"*, *"who teaches SQL"* |

If a `done` keyword matches several items, the bot lists them and asks for a few more words.

## How it decides things
- **Term-aware:** the active term comes from `list_courses` dates (see CLAUDE.md). New terms are picked up automatically.
  Unsubmitted assignments and below-75% attendance from the previous term are carried forward until cleared or marked `done`.
- **Attendance:** courses with no attendance (AI Workshops, Career Readiness, Workshops, Mesa Startup Lab) show *"attendance not tracked"*
  and never alert. New-term courses with no sessions yet show *"no attendance taken yet"*.
- **Announcements:** regex extracts Google Form links, other links, deadlines written in plain English, attachment filenames,
  and tags (opt-in, pre-read, pre-work, one-pager, case study, RSVP, mandatory). Items whose deadline has passed drop off.
- **Graded = resolved:** an in-class assessment that has marks is not shown as pending, even though Nexus says "not submitted".
- **Grades:** on its first run the 6 pm check records every existing mark without alerting, so you are not flooded.
