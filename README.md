# Mesa Telegram bot

Daily briefs, deadline alerts and a chat bot for your life at Mesa School of Business, on **Telegram**.
It reads **Nexus MCP** (assignments, notifications, announcements, schedule, grades, attendance,
courses, profile, plus the `reminder-automation` skill as the main deadline feed) and, optionally,
**Google Calendar**. It runs on **Railway** with APScheduler and Flask.

| Message | When (IST) | What |
|---|---|---|
| Morning brief | daily 8:00 am | timetable · action items (assignments, forms, opt-ins, pre-reads, MRS, links, attachments) · attendance alerts · new grades · Google Calendar (today + 48h) |
| Deadline alert | hourly at :05 | anything due in 24h; a 48h heads-up for pre-work, one-pagers, case studies and opt-ins before their session; never repeats an item within 12h |
| Grade alert | daily 6:00 pm | new marks or feedback, each grade alerted once |
| Weekly summary | Sunday 9:00 pm | next 7 days due · next week's timetable · flagged announcements · attendance |

You can also message the bot any time (`due`, `attendance`, `done mrs`, "when is my next SQL class"…).
Replies happen in the same Telegram chat; no webhook or public URL is needed.

---

## Setup: 3 steps, about 15 minutes

### 1. Create your Telegram bot (2 min)
1. In Telegram, open **@BotFather** (the one with the blue tick) and send `/newbot`.
2. Pick a name (e.g. *Mesa Assistant*) and a username ending in `bot` (e.g. `my_mesa_helper_bot`).
3. BotFather replies with a **token** like `7123456789:AAH…`. That is your `TELEGRAM_BOT_TOKEN`.

### 2. Get your Nexus MCP token (2 min)
1. In Nexus, open **Nexus MCP** in the sidebar → **New token**.
2. Tick every area: Profile, Schedule, Assignments, Grades, Attendance, Announcements, Notifications.
3. Copy the token (`mesa_pat_…`) **before closing the window**. It is shown only once.
   If you lose it, delete the old token and create a new one. That is your `NEXUS_MCP_TOKEN`.

The bot calls `https://students.mesaschool.co.in/mcp/<token>`, as the Nexus MCP page describes.

### 3. Deploy on Railway (10 min)
1. [railway.com](https://railway.com) → sign in with GitHub → **New Project → Deploy from GitHub repo** → `Mesa-Nexus-Bot`.
2. Open the service → **Settings** and leave **Serverless / App Sleeping OFF**: scheduled messages only fire while the bot is awake.
3. **Add a Volume** (⌘K → "Volume", or right-click the service) with mount path **`/data`**.
   It stores the log of done items, alerts sent and grades seen.
4. **Variables** tab → add:
   ```
   NEXUS_MCP_TOKEN=mesa_pat_...
   TELEGRAM_BOT_TOKEN=7123456789:AAH...
   ```
5. Deploy. When it's running, **open your bot in Telegram and press Start**. It replies *"✅ Connected"*.
   The first chat that messages the bot becomes its owner, and every other chat is ignored. To lock it in,
   copy the chat id from that reply into a `TELEGRAM_CHAT_ID` variable.
6. Send `today` to the bot. You should get today's timetable straight back.

> Railway pricing: Railway has no always-free tier. New accounts get trial credit, then the Hobby plan applies.
> This bot is one small process (about 100 MB RAM).

### Optional: Google Calendar
**Service account (simplest):**
1. [Google Cloud Console](https://console.cloud.google.com/) → create a project → **APIs & Services → Library** → enable **Google Calendar API**.
2. **Credentials → Create credentials → Service account** → open it → **Keys → Add key → JSON**. A file downloads.
3. In [Google Calendar](https://calendar.google.com) → Settings → your calendar → **Share with specific people** → add the
   service account's email (`…@….iam.gserviceaccount.com`) with *See all event details*.
4. Railway variables: `GOOGLE_CALENDAR_CREDENTIALS_JSON` = the whole JSON file contents, and `GOOGLE_CALENDAR_ID` = your Gmail address.

**Or your own account (OAuth):** create an OAuth *Desktop app* client and download `client_secret.json`. Then run
`python -m mesabot.cli google-auth client_secret.json` locally and paste the printed JSON into `GOOGLE_CALENDAR_CREDENTIALS_JSON`.

Without these, messages say *"Google Calendar not connected"* and everything else works.

---

## All environment variables
| Variable | Required | Purpose |
|---|---|---|
| `NEXUS_MCP_TOKEN` | ✅ | Nexus MCP token (`mesa_pat_…`) |
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | optional | Locks the bot to your chat. Without it, the first chat to message the bot is saved as owner |
| `GOOGLE_CALENDAR_CREDENTIALS_JSON` | optional | Service-account key JSON or authorized-user JSON (raw, base64 or a file path) |
| `GOOGLE_CALENDAR_ID` | optional | Calendar id(s), comma-separated. Default `primary` |
| `NEXUS_MCP_BASE_URL` | optional | Default `https://students.mesaschool.co.in/mcp` |
| `DATA_DIR` | optional | Where `mesa_log.json` lives. Default: the Railway volume, else `./data` |
| `DRY_RUN` | optional | `true` logs messages instead of sending them |
| `RUN_SCHEDULER` / `RUN_TELEGRAM_POLLING` | optional | `false` disables the scheduled jobs / the chat |
| `ATTENDANCE_THRESHOLD` | optional | Default `75` |
| `ATTENDANCE_AT_RISK_MARGIN` | optional | Warn when you can miss ≤ this many more classes. Default `1` |
| `ATTENDANCE_UNTRACKED_COURSES` | optional | Default `AI Workshops,Career Readiness,Workshops,Mesa Startup Lab` |
| `ACTION_LOOKBACK_DAYS` | optional | Show undated action items posted in the last N days. Default `7` |
| `CARRY_FORWARD` | optional | `true` also shows the previous term's unsubmitted work and low attendance. Default `false` |
| `WEBHOOK_SECRET` | optional | Enables `/webhook` (other relays) and `POST /run/<job>?key=…` (run a job now) |
| `CALLMEBOT_PHONE` / `CALLMEBOT_API_KEY` | legacy | WhatsApp via CallMeBot, used only when `TELEGRAM_BOT_TOKEN` is not set |
| `LOG_LEVEL` | optional | Default `INFO` |

`https://<your-app>.up.railway.app/health` (after **Networking → Generate Domain**) shows missing variables,
whether Telegram is connected, and the next run of each job.

---

## Bot commands
Type them plainly or with a slash (`/due`). Telegram shows the slash menu.

| Send | You get |
|---|---|
| `due` | due today, this week, and overdue |
| `today` | today's timetable and action items |
| `week` | the week ahead (due items, flagged announcements, timetable, attendance, calendar) |
| `attendance` | % per course: 🔴 below 75% (classes needed to recover), 🟠 at risk (classes you can still miss), 🟢 ok; untracked courses labelled |
| `grades` | every released mark |
| `pending` | everything not submitted or not marked done, including items carried from last term |
| `mrs` | this week's MRS Practice Lab module and whether it's done |
| `schedule <day>` | e.g. `schedule monday`, `schedule tomorrow`, `schedule next fri`, `schedule 24 oct` |
| `done <keyword>` | marks a form, link, attachment, announcement action, assignment or carried-forward attendance warning as done, e.g. `done mrs`, `done ils form`, `done case.pdf` |
| `feedback <assignment>` | full feedback text for one grade |
| `help` | this list |
| anything else | routed to the right Nexus endpoint: *"when is my next Finance class"*, *"what did I score in Art of Selling"*, *"am I at risk of low attendance"*, *"what announcements came this week"*, *"who teaches SQL"* |

If a `done` keyword matches several items, the bot lists them and asks for a few more words.

## Run it locally (optional)
```bash
git clone https://github.com/SrishivPatro/Mesa-Nexus-Bot && cd Mesa-Nexus-Bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                                   # offline tests
python -m mesabot.cli preview morning --fixtures tests/fixtures --now 2026-10-19T08:00:00+05:30
cp .env.example .env && set -a && source .env && set +a
python -m mesabot.cli check                 # verifies Nexus + Calendar
python -m mesabot.cli preview morning       # real data, printed instead of sent
python -m mesabot.cli ask "am I at risk of low attendance"
```

## How it decides things
- **Term-aware:** the active term comes from `list_courses` dates (see CLAUDE.md). New terms are picked up automatically.
  Only the current term is shown. Set `CARRY_FORWARD=true` to also show the previous term's unsubmitted work and low attendance.
- **Attendance:** courses with no attendance (AI Workshops, Career Readiness, Workshops, Mesa Startup Lab) show *"attendance not tracked"*
  and never alert. New-term courses with no sessions yet show *"no attendance taken yet"*.
- **Announcements:** regex extracts Google Form links, other links, deadlines written in plain English, attachment filenames,
  and tags (opt-in, pre-read, pre-work, one-pager, case study, RSVP, mandatory). Items whose dates have passed drop off.
- **Graded = resolved:** an in-class assessment with marks is not shown as pending, even though Nexus says "not submitted".
- **Grades:** on its first run the 6 pm check records every existing mark without alerting, so you are not flooded.
