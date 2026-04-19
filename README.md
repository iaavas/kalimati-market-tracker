# Kalimati price tracker

Fetches daily vegetable/fruit wholesale prices from [Kalimati market](https://kalimatimarket.gov.np/), stores them in **SQLite**, generates **PNG** posters, optional **notifications** (system / ntfy / webhook), and a local **analytics dashboard**.

---

## Quick setup (all platforms)

```bash
python3 -m venv .venv
```

**macOS / Linux**

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

**Windows (PowerShell or cmd)**

```text
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Configure `.env` (paths, `KALIMATI_NTFY_TOPIC`, `KALIMATI_WEBHOOK_URL`, `KALIMATI_SYSTEM_NOTIFY`, etc.). See `.env.example`.

---

## Main commands

| Command | Purpose |
|--------|---------|
| `python scripts/daily_job.py` | One manual run: scrape → SQLite → PNGs → drop alerts (if any). |
| `python scripts/kalimati_schedule.py sync` | Same as `daily_job.py` (for schedulers). |
| `python scripts/kalimati_schedule.py digest-am` | **7:30 AM-style** summary: always sends a **system notification** (if the OS supports it). |
| `python scripts/kalimati_schedule.py digest-pm` | **7:30 PM-style** evening summary notification. |
| `python -m kalimati dashboard` | Local web UI (default `http://127.0.0.1:8765`). |
| `python scripts/demo_notify.py` | Demo price-drop notification (ntfy/webhook/system). |

Typical **wall-clock** schedule (adjust for your timezone; see [Timezone](#timezone) below):

| Time | Command |
|------|---------|
| 07:00 | `kalimati_schedule.py sync` |
| 07:30 | `kalimati_schedule.py digest-am` |
| 19:30 | `kalimati_schedule.py digest-pm` |

---

## Running in the background

Background here means: **no terminal window required** (OS launches the job) or **detached** (`nohup` / `&`).

### macOS — scheduled jobs with **launchd** (recommended)

1. Edit **absolute paths** in:
   - `install/macos/com.user.kalimati.sync.plist` (07:00)
   - `install/macos/com.user.kalimati.digest.am.plist` (07:30)
   - `install/macos/com.user.kalimati.digest.pm.plist` (19:30)
2. From the repo root:

```bash
mkdir -p logs
cp install/macos/com.user.kalimati.sync.plist ~/Library/LaunchAgents/
cp install/macos/com.user.kalimati.digest.am.plist ~/Library/LaunchAgents/
cp install/macos/com.user.kalimati.digest.pm.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.kalimati.sync.plist
launchctl load ~/Library/LaunchAgents/com.user.kalimati.digest.am.plist
launchctl load ~/Library/LaunchAgents/com.user.kalimati.digest.pm.plist
```

**Check**

```bash
launchctl list | grep kalimati
```

**Unload (stop background schedule)**

```bash
launchctl unload ~/Library/LaunchAgents/com.user.kalimati.sync.plist
launchctl unload ~/Library/LaunchAgents/com.user.kalimati.digest.am.plist
launchctl unload ~/Library/LaunchAgents/com.user.kalimati.digest.pm.plist
```

**Dashboard in background** (until you stop it):

```bash
mkdir -p logs
nohup .venv/bin/python -m kalimati dashboard >> logs/dashboard.log 2>&1 &
```

Or use `scripts/start_dashboard_background.sh` if present.

**Notifications:** allow **Terminal** / **Python** (or the app shown in Notification Center) under **System Settings → Notifications**.

---

### Linux — **cron**

See `install/crontab.example`. Typical pattern:

```cron
CRON_TZ=Asia/Kathmandu
SHELL=/bin/bash
PATH=/usr/bin:/bin

0 7 * * * cd /path/to/kalimati && .venv/bin/python scripts/kalimati_schedule.py sync >>logs/cron-sync.log 2>&1
30 7 * * * cd /path/to/kalimati && .venv/bin/python scripts/kalimati_schedule.py digest-am >>logs/cron-digest.log 2>&1
30 19 * * * cd /path/to/kalimati && .venv/bin/python scripts/kalimati_schedule.py digest-pm >>logs/cron-digest.log 2>&1
```

Install **`notify-send`** (e.g. `libnotify-bin`) for desktop toasts. In `.env` set **`KALIMATI_SYSTEM_NOTIFY=1`** on Linux (macOS defaults on when unset).

**Dashboard in background:**

```bash
nohup .venv/bin/python -m kalimati dashboard >> logs/dashboard.log 2>&1 &
```

---

### Windows — **Task Scheduler**

1. Create the venv and install deps (see [Quick setup](#quick-setup-all-platforms)).
2. Open **PowerShell as Administrator**, `cd` to the repo root, then:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install\windows\register_tasks.ps1
```

This registers three daily tasks: **KalimatiSync** (07:00), **KalimatiDigestAM** (07:30), **KalimatiDigestPM** (19:30).

**Check**

```powershell
schtasks /Query /TN KalimatiSync /V
```

**Remove tasks**

```powershell
schtasks /Delete /TN KalimatiSync /F
schtasks /Delete /TN KalimatiDigestAM /F
schtasks /Delete /TN KalimatiDigestPM /F
```

System notifications use `install/windows/toast.ps1` (PowerShell tray balloon). Allow **Python** in **Settings → System → Notifications**.

**Dashboard in background** (example):

```powershell
cd C:\path\to\kalimati
Start-Process -WindowStyle Hidden .venv\Scripts\python.exe -ArgumentList "-m","kalimati","dashboard" -RedirectStandardOutput logs\dashboard.log -RedirectStandardError logs\dashboard.err
```

(Adjust paths; create a `logs` folder first.)

---

## Timezone

- **launchd** and **Task Scheduler** use the **machine’s local clock**.
- For **Nepal civil time** on a machine elsewhere, either set the OS timezone to **Asia/Kathmandu** or change the hour/minute in the plist / tasks / cron.
- On **Linux cron**, `CRON_TZ=Asia/Kathmandu` at the top of the crontab makes the schedule interpret times in that zone (when supported by your cron).

---

## Notifications

- **System:** macOS (`osascript`), Linux (`notify-send` + `KALIMATI_SYSTEM_NOTIFY=1`), Windows (`toast.ps1`). Price-drop alerts respect `KALIMATI_SYSTEM_NOTIFY`; **scheduled 7:30 digests** still attempt OS notification (`force` path).
- **ntfy:** set `KALIMATI_NTFY_TOPIC`, open `https://ntfy.sh/<topic>` to listen.
- **Webhook:** set `KALIMATI_WEBHOOK_URL` (JSON POST).

Demo:

```bash
.venv/bin/python scripts/demo_notify.py
```

---

## Optional: Facebook upload

Set in `.env`:

- `KALIMATI_FACEBOOK_PAGE_ID`
- `KALIMATI_FACEBOOK_ACCESS_TOKEN`

---

## More detail

Shorter install notes also live in **`INSTALL.txt`**.
