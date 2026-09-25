# GCC YouTube Stream Manager

A background script that keeps YouTube live broadcasts in sync with a Google Calendar. Volunteers schedule games/draws on a shared calendar as usual; the script polls that calendar once a minute and creates, updates, starts, and stops the corresponding YouTube broadcasts to match — no manual work in YouTube Studio.

## How it works

- Every minute, it reads the calendar for events in the current tracking window.
- For each event, it derives a broadcast title, playlist, thumbnail, and visibility from directives typed into that event's description (see [Calendar directives](#calendar-directives) below).
- It creates a YouTube broadcast per "sheet" (one stream key per sheet/camera), and starts/stops each one automatically based on the event's actual start/end time.
- If a calendar event is deleted, or a sheet is dropped from it, the corresponding broadcast is safely cleaned up — stopped if it might have already aired, deleted only if it's certain it never went live.
- YouTube is only contacted when something actually needs to change (a create, an update, a start, or a stop) plus once at startup to reconcile state — not on every poll. The calendar is polled every minute regardless, since Calendar's API quota is generous; YouTube's is not.

## Requirements

- Python 3.9+
- A Google Cloud project with the **Google Calendar API** and **YouTube Data API v3** enabled
- An OAuth 2.0 **Desktop app** client ID/secret downloaded from that project (Google Cloud Console → APIs & Services → Credentials)
- A YouTube channel with live streaming enabled, with one persistent stream key already created per sheet/camera

## Setup

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Google Cloud / OAuth**
   - Create a Google Cloud project (or use an existing one) and enable the Calendar API and YouTube Data API v3.
   - Create an OAuth 2.0 Client ID of type **Desktop app**, and download its JSON — this is your `secret_file`.
   - The first time the script runs, it'll open a browser for you to sign in and grant access, then save a `token_file` so it won't ask again until that token expires or is revoked.

3. **Configure**
   ```bash
   cp config.example.json config.json
   ```
   Then fill in `config.json` with your real values (this file is gitignored — it holds your stream keys and calendar ID, and should never be committed):

   | Key | Description |
   |---|---|
   | `calendar_id` | The calendar to read events from (its email-style ID) |
   | `channel_id` | Your YouTube channel ID |
   | `stream_keys` | List of persistent YouTube stream keys, one per sheet, in sheet order |
   | `secret_file` | Path to the OAuth client secret JSON from Google Cloud Console |
   | `token_file` | Path where the script stores your OAuth token after first sign-in |
   | `state_file` | Path where the script tracks which broadcast belongs to which calendar event |
   | `log_file` | Path to the script's log file |
   | `shared_images_dir` | Folder containing thumbnail images |
   | `default_thumbnail_file` | *(optional)* Filename (inside `shared_images_dir`) used when no `thumbnail:` directive is set — defaults to `GCC_Default.png` |
   | `timezone` | *(optional)* IANA timezone name for the daily tracking-window cutoff — defaults to `US/Pacific` |
   | `cutoff_hour` / `cutoff_minute` | *(optional)* Local time each day where the tracking window rolls over to the next day — defaults to `4:59` |
   | `quit_hour` / `quit_minute` | *(optional)* Local time each day when the script exits on its own (e.g. before a scheduled reboot) — omit both to run indefinitely. Live streams are left running and picked up by the next run |
   | `poll_interval_seconds` | *(optional)* How often to check the calendar — defaults to `60` |
   | `log_max_bytes` | *(optional)* Log file size that triggers rotation — defaults to `5000000` (~5 MB) |
   | `log_backup_count` | *(optional)* Number of rotated log files to keep — defaults to `5` |

   Note: paths on Windows need escaped backslashes in JSON (`"C:\\path\\to\\file"`) — or just use forward slashes (`"C:/path/to/file"`), which Windows accepts too.

4. **Run it**
   ```bash
   python stream_manager.py
   ```
   It runs continuously and is meant to be left running (e.g. in its own terminal window, or as a background service) — leave it up throughout your streaming schedule.

   Run `python stream_manager.py --help` at any time for the full built-in directive reference — the same text is also at the top of `stream_manager.py` itself, so it travels with the script even without the rest of this repo.

## Calendar directives

Directives are plain `key: value` lines typed anywhere into a calendar event's description; anything else in the description is left alone as the visible stream description. Full reference: `python stream_manager.py --help`.

```
title: <text>              # required before a new event's streams are created
playlist: <text>           # required before a new event's streams are created
sheets: 1, 3, 5             # optional - restrict to specific sheets (default: all)
thumbnail: <filename>       # optional - looked up in shared_images_dir
stream visibility: public | unlisted | private     # optional, default public
playlist visibility: public | unlisted | private   # optional, default public - only applies when a new playlist is created
sheet N title: <text>       # optional - full override for one sheet's title
sheet N thumbnail: <filename>  # optional - thumbnail override for one sheet
```

`title:` and `sheet N title:` support two placeholders — `[title]` (the general title) and `[sheet]` (resolves to "Sheet N") — plus `%H:%M` and `%m/%d`, replaced with the event's actual start time/date. Nothing is inserted automatically; if you want the sheet number or the time to show, put the placeholder where you want it.

Example event description:
```
title: Winter League [sheet] - %m/%d
playlist: 2026 Winter League
sheets: 1, 2, 3
sheet 2 title: [title] (Skip Cam)

Live coverage of tonight's league draw.
```

## Files

| File | Purpose |
|---|---|
| `stream_manager.py` | The script |
| `config.example.json` | Template for `config.json` — committed |
| `config.json` | Your real settings and secrets — gitignored, create it yourself |
| `stream_state.json` | Runtime state mapping calendar events to YouTube broadcasts — created automatically, don't hand-edit |
| `stream_manager.log` | Runtime log (also printed to the console) |
