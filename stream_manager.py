"""
GCC YouTube Stream Manager
===========================

Continuously syncs YouTube live broadcasts to Google Calendar events. Checks the calendar
every minute and creates/updates/starts/stops YouTube broadcasts to match.

DIRECTIVES
----------
Add these as lines anywhere in a calendar event's description (order doesn't matter; any
line that doesn't match one of these patterns is left alone as the event's visible
description text):

  title: <text>
      The stream's title for every sheet. REQUIRED before any streams are created for a new
      event. Use [sheet] anywhere in the text to insert "Sheet N" - without it every sheet
      gets the identical title. %H:%M and %m/%d are replaced with the event's actual start
      time/date.
        e.g.  title: Club Championship [sheet] - %m/%d %H:%M

  playlist: <text>
      Playlist to add every sheet's stream to (created if it doesn't already exist).
      REQUIRED before any streams are created for a new event.

  sheets: <comma-separated numbers>
      Restrict which sheets get a stream for this event, e.g. "sheets: 1, 3, 5". Omit this
      directive entirely to use every sheet (the default).

  thumbnail: <filename>
      Thumbnail image for every sheet, looked up in the shared images folder. Falls back to
      the default thumbnail if omitted or the named file isn't found.

  stream visibility: public | unlisted | private
      YouTube privacy setting for every sheet's stream. Defaults to public.

  playlist visibility: public | unlisted | private
      YouTube privacy setting used only when a NEW playlist is created - never changes an
      existing playlist's visibility. Defaults to public.

  sheet N title: <text>
      Full override for one specific sheet's title (N = 1, 2, 3, ...). Use [title] to insert
      the general title (the title: directive above, or the raw calendar event name if none
      was given) and [sheet] to insert "Sheet N". Nothing is added automatically - put
      [sheet] in the text yourself if you want it to show.
        e.g.  sheet 1 title: [title] - Skip Cam

  sheet N thumbnail: <filename>
      Thumbnail override for one specific sheet only.

EXAMPLE EVENT DESCRIPTION
--------------------------
  title: Winter League [sheet] - %m/%d
  playlist: 2026 Winter League
  sheets: 1, 2, 3
  sheet 2 title: [title] (Skip Cam)

  Live coverage of tonight's league draw.

CONFIGURATION
-------------
Deployment settings (calendar ID, channel ID, stream keys, file paths, etc.) live in
config.json next to this script - see config.example.json for the required format.
"""

import os
import json
import logging
import logging.handlers
import re
import ssl
import sys
import time
import traceback

if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
    print(__doc__)
    sys.exit(0)


def show_exception_and_exit(exc_type, exc_value, tb):
    try:
        logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, tb))
    except NameError:
        # this happened before logging was set up (e.g. during config loading) - fall back to
        # printing directly so it's still visible instead of silently vanishing
        traceback.print_exception(exc_type, exc_value, tb)
    input("Press enter to exit.")
    sys.exit(-1)


# installed as early as possible so nothing between here and full logging setup (config
# loading, timezone parsing, log file creation, etc.) can fail silently or close the window
# before anyone can read why
sys.excepthook = show_exception_and_exit

from datetime import datetime, timedelta, timezone

import pytz

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete your token file (see config.json's "token_file").
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly", "https://www.googleapis.com/auth/youtube"]

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
REQUIRED_CONFIG_KEYS = [
    "calendar_id", "channel_id", "stream_keys",
    "secret_file", "token_file", "state_file", "log_file", "shared_images_dir",
]


def load_config():
    if not os.path.exists(CONFIG_FILE):
        raise SystemExit(
            f"Missing {CONFIG_FILE}. Copy config.example.json to config.json and fill in "
            f"your own values before running this script."
        )
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(f"{CONFIG_FILE} is not valid JSON: {e}")
    except OSError as e:
        raise SystemExit(f"Could not read {CONFIG_FILE}: {e}")

    missing = [key for key in REQUIRED_CONFIG_KEYS if key not in config]
    if missing:
        raise SystemExit(f"{CONFIG_FILE} is missing required setting(s): {', '.join(missing)}")
    if not isinstance(config["stream_keys"], list) or not config["stream_keys"]:
        raise SystemExit(f"{CONFIG_FILE}: 'stream_keys' must be a non-empty list")

    return config


_config = load_config()

SECRET_FILE = _config["secret_file"]
TOKEN_FILE = _config["token_file"]
STATE_FILE = _config["state_file"]
LOG_FILE = _config["log_file"]

CALENDAR_ID = _config["calendar_id"]
CHANNEL_ID = _config["channel_id"]
SHARED_IMAGES_DIR = _config["shared_images_dir"]
DEFAULT_THUMBNAIL_FILE = _config.get("default_thumbnail_file", "GCC_Default.png")

VALID_VISIBILITIES = ("public", "unlisted", "private")

# per-sheet overrides: "sheet 1 title: ...", "sheet 2 thumbnail: ..."
SHEET_DIRECTIVE_PATTERN = re.compile(
    r"^\s*sheet\s+(?P<sheet>\d+)\s+(?P<key>title|thumbnail)\s*:\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)

# event-wide directives: "title: ...", "playlist: ...", "stream visibility: ...", "sheets: 1, 2, 3"
GLOBAL_DIRECTIVE_PATTERN = re.compile(
    r"^\s*(?P<key>title|playlist|thumbnail|stream\s+visibility|playlist\s+visibility|sheets)\s*:\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)

# placeholders usable inside title text: [title] stands in for the general title, [sheet]
# stands in for "Sheet N" - neither is inserted automatically unless you place it yourself
TITLE_PLACEHOLDER_PATTERN = re.compile(r"\[title\]", re.IGNORECASE)
SHEET_PLACEHOLDER_PATTERN = re.compile(r"\[sheet\]", re.IGNORECASE)

try:
    PACIFIC = pytz.timezone(_config.get("timezone", "US/Pacific"))
except pytz.exceptions.UnknownTimeZoneError:
    raise SystemExit(f"{CONFIG_FILE}: invalid timezone '{_config.get('timezone')}' - must be "
                      f"a valid IANA timezone name (e.g. 'US/Pacific')")

# events are tracked up through this cutoff each "stream day" (today's if not yet passed,
# otherwise tomorrow's)
CUTOFF_HOUR = _config.get("cutoff_hour", 4)
CUTOFF_MINUTE = _config.get("cutoff_minute", 59)
if not (0 <= CUTOFF_HOUR <= 23):
    raise SystemExit(f"{CONFIG_FILE}: 'cutoff_hour' must be 0-23, got {CUTOFF_HOUR}")
if not (0 <= CUTOFF_MINUTE <= 59):
    raise SystemExit(f"{CONFIG_FILE}: 'cutoff_minute' must be 0-59, got {CUTOFF_MINUTE}")

# optional daily time the script exits on its own - omit both to run indefinitely
QUIT_HOUR = _config.get("quit_hour")
QUIT_MINUTE = _config.get("quit_minute")
if (QUIT_HOUR is None) != (QUIT_MINUTE is None):
    raise SystemExit(f"{CONFIG_FILE}: 'quit_hour' and 'quit_minute' must be set together (or both omitted)")
if QUIT_HOUR is not None:
    if not (0 <= QUIT_HOUR <= 23):
        raise SystemExit(f"{CONFIG_FILE}: 'quit_hour' must be 0-23, got {QUIT_HOUR}")
    if not (0 <= QUIT_MINUTE <= 59):
        raise SystemExit(f"{CONFIG_FILE}: 'quit_minute' must be 0-59, got {QUIT_MINUTE}")

POLL_INTERVAL_SECONDS = _config.get("poll_interval_seconds", 60)

LOG_MAX_BYTES = _config.get("log_max_bytes", 5_000_000)
LOG_BACKUP_COUNT = _config.get("log_backup_count", 5)

# these identify the stream keys in youtube, in order from sheet 1 - N
stream_keys = _config["stream_keys"]


logger = logging.getLogger("stream_manager")
logger.setLevel(logging.INFO)
_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

_log_dir = os.path.dirname(LOG_FILE)
if _log_dir:
    os.makedirs(_log_dir, exist_ok=True)

_file_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
)
_file_handler.setFormatter(_formatter)
logger.addHandler(_file_handler)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_formatter)
logger.addHandler(_console_handler)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.warning(f"Failed to refresh credentials: {e}")
                creds = None

        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return creds


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def execute_with_retries(request, attempts=4, backoff_seconds=3):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return request.execute()
        except HttpError as e:
            last_error = e
            logger.warning(f"HTTP error (attempt {attempt}/{attempts}): {e}")
        except (OSError, ssl.SSLError) as e:
            last_error = e
            logger.warning(f"Network error (attempt {attempt}/{attempts}): {e}")
        time.sleep(backoff_seconds)
    raise last_error


def parse_youtube_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sleep_until_next_minute():
    now = datetime.now()
    sleep_seconds = 60 - now.second - now.microsecond / 1_000_000
    if sleep_seconds <= 0:
        sleep_seconds += 60
    time.sleep(sleep_seconds)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Calendar -> desired stream state
# ---------------------------------------------------------------------------

def get_calendar_events(calendar_service, time_min, time_max):
    events_result = execute_with_retries(
        calendar_service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
    )
    return events_result.get("items", [])


def log_tracked_events(events):
    if not events:
        logger.info("Startup: no calendar events currently in the tracked window.")
        return

    logger.info(f"Startup: tracking {len(events)} calendar event(s):")
    for event in events:
        start = event.get("start", {}).get("dateTime", "?")
        end = event.get("end", {}).get("dateTime", "?")
        summary = event.get("summary", "(no title)")
        logger.info(f"  - '{summary}' [{start} -> {end}] (event id={event['id']})")


def validate_visibility(value):
    normalized = value.strip().lower()
    if normalized in VALID_VISIBILITIES:
        return normalized
    logger.warning(f"ignoring invalid visibility '{value}' (expected public, unlisted, or private)")
    return None


def parse_sheet_list(value):
    sheets = set()
    for token in value.split(","):
        token = token.strip()
        if token.isdigit():
            sheets.add(int(token))
        elif token:
            logger.warning(f"ignoring invalid sheet number '{token}' in sheets directive")
    return sheets or None


def parse_description_directives(description):
    """Pull directive lines out of a calendar event description:
      title / playlist / thumbnail / stream visibility / playlist visibility / sheets  (event-wide)
      sheet N title / sheet N thumbnail                                                (per-sheet)
    Returns (directives, cleaned_description) where cleaned_description is what's left over for use
    as the actual, visible broadcast/playlist description."""
    directives = {
        "title": None,
        "playlist": None,
        "thumbnail": None,
        "stream_visibility": None,
        "playlist_visibility": None,
        "sheets": None,  # None means "all sheets"; otherwise a set of sheet numbers
        "sheet_overrides": {},
    }
    remaining_lines = []

    for line in description.splitlines():
        sheet_match = SHEET_DIRECTIVE_PATTERN.match(line)
        if sheet_match:
            sheet_number = int(sheet_match.group("sheet"))
            key = sheet_match.group("key").lower()
            directives["sheet_overrides"].setdefault(sheet_number, {})[key] = sheet_match.group("value")
            continue

        global_match = GLOBAL_DIRECTIVE_PATTERN.match(line)
        if global_match:
            key = re.sub(r"\s+", " ", global_match.group("key").strip().lower())
            value = global_match.group("value")
            if key == "stream visibility":
                directives["stream_visibility"] = validate_visibility(value)
            elif key == "playlist visibility":
                directives["playlist_visibility"] = validate_visibility(value)
            elif key == "sheets":
                directives["sheets"] = parse_sheet_list(value)
            else:
                directives[key] = value
            continue

        remaining_lines.append(line)

    return directives, "\n".join(remaining_lines).strip()


def substitute_placeholder(pattern, replacement, text):
    # a lambda replacement avoids re.sub treating backslashes in `replacement` specially
    return pattern.sub(lambda _: replacement, text)


def apply_date_placeholders(text, start_dt):
    # only these two exact literal placeholders are recognized - anything else containing a
    # '%' (e.g. "50% off") is left completely alone rather than risking a full strftime() pass
    text = text.replace("%H:%M", start_dt.strftime("%H:%M"))
    text = text.replace("%m/%d", start_dt.strftime("%m/%d"))
    return text


def compute_title(event, sheet_number, start_dt, directives):
    sheet_label = f"Sheet {sheet_number}"
    name = directives["title"] if directives["title"] else event["summary"]

    sheet_title = directives["sheet_overrides"].get(sheet_number, {}).get("title")
    if sheet_title:
        # fully verbatim aside from [title]/[sheet]/%-date placeholders
        resolved = substitute_placeholder(TITLE_PLACEHOLDER_PATTERN, name, sheet_title)
    else:
        resolved = name

    resolved = substitute_placeholder(SHEET_PLACEHOLDER_PATTERN, sheet_label, resolved)
    return apply_date_placeholders(resolved, start_dt)


# ---------------------------------------------------------------------------
# YouTube reads
# ---------------------------------------------------------------------------

def list_broadcasts_by_status(youtube, status):
    items = []
    page_token = None
    while True:
        response = execute_with_retries(
            youtube.liveBroadcasts().list(
                part="snippet,contentDetails,status",
                broadcastStatus=status,
                maxResults=50,
                pageToken=page_token,
            )
        )
        items += response.get("items", [])
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


def fetch_youtube_broadcasts(youtube):
    """Broadcasts currently active or upcoming, keyed by id."""
    broadcasts = {}
    for status in ("active", "upcoming"):
        for item in list_broadcasts_by_status(youtube, status):
            broadcasts[item["id"]] = item
    return broadcasts


def find_unclaimed_broadcast_by_title(broadcasts_by_id, claimed_ids, title):
    for broadcast_id, broadcast in broadcasts_by_id.items():
        if broadcast_id in claimed_ids:
            continue
        if broadcast["snippet"].get("title") == title:
            return broadcast
    return None


# ---------------------------------------------------------------------------
# YouTube writes
# ---------------------------------------------------------------------------

def get_playlist(youtube, playlist_title):
    page_token = None
    while True:
        response = execute_with_retries(
            youtube.playlists().list(
                channelId=CHANNEL_ID,
                part="id,snippet",
                maxResults=50,
                pageToken=page_token,
            )
        )
        for playlist in response.get("items", []):
            if playlist["snippet"]["title"] == playlist_title:
                return playlist
        page_token = response.get("nextPageToken")
        if not page_token:
            return None


def create_playlist(youtube, playlist_title, description, visibility):
    body = {
        "snippet": {"title": playlist_title, "description": description},
        "status": {"privacyStatus": visibility},
    }
    logger.info(f"No existing playlist named '{playlist_title}' - creating it (visibility={visibility})")
    playlist = execute_with_retries(youtube.playlists().insert(part="snippet,status", body=body))
    logger.info(f"Created playlist '{playlist_title}' (playlist id={playlist['id']})")
    return playlist


def get_or_create_playlist(youtube, playlist_title, description, visibility):
    # visibility only applies when a new playlist is created - an existing playlist is never
    # touched here, its visibility stays whatever it already was.
    playlist = get_playlist(youtube, playlist_title)
    if playlist is None:
        playlist = create_playlist(youtube, playlist_title, description, visibility)
    else:
        logger.info(f"Found existing playlist '{playlist_title}' (playlist id={playlist['id']})")
    return playlist


def resolve_thumbnail_file(override_filename):
    if override_filename:
        override_path = os.path.join(SHARED_IMAGES_DIR, os.path.basename(override_filename))
        if os.path.exists(override_path):
            return override_path
        logger.warning(f"thumbnail override '{override_filename}' not found in {SHARED_IMAGES_DIR}, "
                       f"falling back to default")

    return os.path.join(SHARED_IMAGES_DIR, DEFAULT_THUMBNAIL_FILE)


def set_thumbnail(youtube, broadcast_id, title, override_filename=None):
    thumbnail_file = resolve_thumbnail_file(override_filename)

    if not os.path.exists(thumbnail_file):
        return

    for attempt in range(1, 4):
        try:
            logger.debug(f"attempting to add thumbnail, attempt {attempt} of 3")
            youtube.thumbnails().set(videoId=broadcast_id, media_body=thumbnail_file).execute()
            return
        except HttpError as error:
            logger.warning(f"An error occurred while trying to set video thumbnail: {error}; "
                           f"trying again in 5 seconds")
            time.sleep(5)

    logger.warning("couldn't set thumbnail, but moving on anyway. Video: " + title)


def create_broadcast(youtube, playlist, stream_key, title, description, start_dt, end_dt,
                      thumbnail_override=None, visibility="public"):
    live_broadcast = {
        'snippet': {
            'title': title,
            'description': description,
            'scheduledStartTime': start_dt.isoformat(),
            'scheduledEndTime': end_dt.isoformat(),
        },
        'status': {
            'privacyStatus': visibility,
            'selfDeclaredMadeForKids': False,
        },
        'contentDetails': {
            'monitorStream': {'enableMonitorStream': False}
        }
    }

    logger.info(f"Creating YouTube stream: '{title}' [{start_dt.isoformat()} -> {end_dt.isoformat()}]")
    response = execute_with_retries(
        youtube.liveBroadcasts().insert(part="snippet,status,contentDetails", body=live_broadcast)
    )
    broadcast_id = response['id']

    logger.debug("binding broadcast to OBS stream key")
    execute_with_retries(
        youtube.liveBroadcasts().bind(part="id,contentDetails", id=broadcast_id, streamId=stream_key)
    )

    logger.debug("adding broadcast to playlist")
    execute_with_retries(
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist['id'],
                    "resourceId": {"kind": "youtube#video", "videoId": broadcast_id},
                }
            },
        )
    )

    set_thumbnail(youtube, broadcast_id, title, thumbnail_override)

    return broadcast_id


def describe_broadcast_changes(broadcast, title, description, start_dt, end_dt, visibility):
    """Compare a broadcast's current YouTube state against what the calendar now wants.
    Returns a list of human-readable change descriptions (empty if nothing changed)."""
    changes = []
    snippet = broadcast["snippet"]

    if snippet.get("title", "").strip() != title.strip():
        changes.append(f"title '{snippet.get('title')}' -> '{title}'")
    if snippet.get("description", "").strip() != description.strip():
        changes.append("description changed")
    if parse_youtube_time(snippet["scheduledStartTime"]) != start_dt:
        changes.append(f"start time {snippet.get('scheduledStartTime')} -> {start_dt.isoformat()}")
    if parse_youtube_time(snippet["scheduledEndTime"]) != end_dt:
        changes.append(f"end time {snippet.get('scheduledEndTime')} -> {end_dt.isoformat()}")
    if broadcast["status"].get("privacyStatus") != visibility:
        changes.append(f"visibility '{broadcast['status'].get('privacyStatus')}' -> '{visibility}'")

    return changes


def update_broadcast(youtube, broadcast, title, description, start_dt, end_dt, visibility):
    snippet = dict(broadcast["snippet"])
    snippet["title"] = title
    snippet["description"] = description
    snippet["scheduledStartTime"] = start_dt.isoformat()
    snippet["scheduledEndTime"] = end_dt.isoformat()

    status = dict(broadcast["status"])
    status["privacyStatus"] = visibility

    logger.debug("updating broadcast metadata for: " + title)
    execute_with_retries(
        youtube.liveBroadcasts().update(
            part="snippet,status", body={"id": broadcast["id"], "snippet": snippet, "status": status}
        )
    )
    broadcast["snippet"] = snippet
    broadcast["status"] = status


def transition_broadcast(youtube, broadcast_id, broadcast_status):
    logger.debug(f"transitioning broadcast {broadcast_id} to {broadcast_status}")
    execute_with_retries(
        youtube.liveBroadcasts().transition(broadcastStatus=broadcast_status, id=broadcast_id, part='status')
    )


def delete_broadcast(youtube, broadcast_id):
    logger.info(f"deleting broadcast {broadcast_id}")
    execute_with_retries(youtube.liveBroadcasts().delete(id=broadcast_id))


def remove_broadcast_for_deleted_event(youtube, broadcast, now, reason, context):
    """A broadcast needs to be pulled off youtube (event deleted, sheet no longer requested,
    orphan found at startup, etc). Judge stop-vs-delete by scheduled start/end time, not our
    own possibly-stale local status: if it was ever due to have started, just stop it (never
    destroy a stream that might have aired); otherwise it never had a chance to air, so it's
    safe to delete. Logs exactly which of those two actions was actually taken, so the log
    line always matches reality rather than guessing separately from the decision itself.

    reason: short phrase for why cleanup is happening (e.g. "Event removed"). Overridden with
    "Reached end of event" when the scheduled end time has already passed, regardless of what
    triggered the call, since that's always the more accurate explanation at that point.
    context: short phrase identifying what this is (event/sheet/broadcast), always included.
    """
    broadcast_id = broadcast["id"]
    scheduled_start = parse_youtube_time(broadcast["snippet"]["scheduledStartTime"])
    scheduled_end = parse_youtube_time(broadcast["snippet"]["scheduledEndTime"])

    if now >= scheduled_end:
        reason = "Reached end of event"

    if now >= scheduled_start:
        logger.info(f"{reason} - stopping stream: {context} (broadcast id={broadcast_id})")
        transition_broadcast(youtube, broadcast_id, "complete")
    else:
        logger.info(f"{reason} - removing stream: {context} (broadcast id={broadcast_id})")
        delete_broadcast(youtube, broadcast_id)


def cleanup_startup_orphans(youtube, known_broadcasts, state):
    """One-time pass right after the first reconciliation cycle: any broadcast within our
    tracking window that no calendar entry claimed is a leftover from an event that was
    deleted/modified while the script wasn't running."""
    now = datetime.now(timezone.utc)
    window_end = get_window_end(now.astimezone(PACIFIC))
    claimed_ids = {entry["broadcast_id"] for entry in state.values()}

    for broadcast_id, broadcast in list(known_broadcasts.items()):
        if broadcast_id in claimed_ids:
            continue
        scheduled_start = parse_youtube_time(broadcast["snippet"]["scheduledStartTime"])
        if scheduled_start > window_end:
            continue  # haven't looked at the calendar that far ahead yet - leave it alone
        remove_broadcast_for_deleted_event(
            youtube, broadcast, now,
            reason="No matching calendar entry found at startup",
            context=f"broadcast '{broadcast['snippet'].get('title')}'",
        )
        known_broadcasts.pop(broadcast_id, None)


# ---------------------------------------------------------------------------
# Main reconciliation cycle
# ---------------------------------------------------------------------------

def get_window_end(pacific_now):
    """The next upcoming CUTOFF_HOUR:CUTOFF_MINUTE - today's if it hasn't passed yet,
    otherwise tomorrow's."""
    end = pacific_now.replace(hour=CUTOFF_HOUR, minute=CUTOFF_MINUTE, second=59, microsecond=0)
    if pacific_now >= end:
        end += timedelta(days=1)
    return end


def get_quit_time(pacific_now):
    """The next QUIT_HOUR:QUIT_MINUTE strictly after pacific_now (so starting up during or just
    after the quit minute doesn't exit right away), or None if no quit time is configured."""
    if QUIT_HOUR is None:
        return None
    quit_at = pacific_now.replace(hour=QUIT_HOUR, minute=QUIT_MINUTE, second=0, microsecond=0)
    if quit_at <= pacific_now:
        quit_at += timedelta(days=1)
    return quit_at


def run_cycle(youtube, calendar_service, state, known_event_ids, known_broadcasts):
    now = datetime.now(timezone.utc)
    pacific_now = now.astimezone(PACIFIC)
    window_end = get_window_end(pacific_now)

    # known_broadcasts is only ever seeded once, at startup, from a real YouTube read - every
    # cycle after that relies purely on our own record of what we last set, updated in place
    # whenever we create/update/start/stop something ourselves. YouTube is never polled just
    # to check status.
    events = get_calendar_events(calendar_service, pacific_now, window_end)
    claimed_broadcast_ids = set()
    current_event_ids = set()
    # starts are deferred until every stop/removal this cycle has been issued, so a stream
    # stopping is never still "live" when the next one on the same sheet starts (e.g. back-to-back
    # draws where one event's end time is the next event's start time)
    pending_starts = []
    
    for event in events:
        if "dateTime" not in event.get("start", {}) or "dateTime" not in event.get("end", {}):
            # all-day or otherwise malformed event, nothing sensible to schedule
            logger.debug(f"found bad event for today, skipping") #ff
            continue
                
        event_id = event["id"]
        current_event_ids.add(event_id)
        # first time this run has seen this event (e.g. at startup) - used to log once, not every minute
        first_seen = event_id not in known_event_ids
        start_dt = datetime.fromisoformat(event["start"]["dateTime"])
        end_dt = datetime.fromisoformat(event["end"]["dateTime"])
        directives, description = parse_description_directives(event.get("description", " "))
        playlist_title = directives["playlist"] or event["summary"]
        stream_visibility = directives["stream_visibility"] or "public"
        playlist_visibility = directives["playlist_visibility"] or "public"
        allowed_sheets = directives["sheets"] if directives["sheets"] is not None \
            else set(range(1, len(stream_keys) + 1))

        missing_directives = [name for name, value in (("title", directives["title"]),
                                                         ("playlist", directives["playlist"])) if not value]
        if missing_directives:
            logger.error(f"Event '{event.get('summary')}' (id={event_id}) is missing required "
                         f"directive(s): {', '.join(missing_directives)} - no new streams will be "
                         f"created for it until this is set")

        playlist = None  # only fetched/created lazily, if a new broadcast is needed

        for sheet_number, stream_key in enumerate(stream_keys, start=1):
            state_key = f"{event_id}:{sheet_number}"

            if sheet_number not in allowed_sheets:
                # not one of the requested sheets (sheets: directive narrowed) - clean up any
                # broadcast we already made for it, same safe stop-vs-delete logic as a fully
                # removed event
                entry = state.pop(state_key, None)
                if entry is not None:
                    broadcast = known_broadcasts.get(entry["broadcast_id"])
                    if broadcast is not None:
                        remove_broadcast_for_deleted_event(
                            youtube, broadcast, now,
                            reason="Sheet no longer requested (sheets directive narrowed)",
                            context=f"event '{event.get('summary')}' sheet {sheet_number}",
                        )
                        known_broadcasts.pop(broadcast["id"], None)
                continue

            title = compute_title(event, sheet_number, start_dt, directives)
            thumbnail_override = directives["sheet_overrides"].get(sheet_number, {}).get("thumbnail") \
                or directives["thumbnail"]

            entry = state.get(state_key)
            broadcast = known_broadcasts.get(entry["broadcast_id"]) if entry else None

            if broadcast is None and entry is not None:
                # not active/upcoming anymore: already completed, or removed out from under us.
                # Leave it alone rather than guessing.
                if first_seen:
                    logger.info(f"Saved stream for event '{event.get('summary')}' sheet {sheet_number} "
                                f"(broadcast id={entry['broadcast_id']}) is no longer active/upcoming on "
                                f"YouTube - leaving it alone")
                continue

            if broadcast is not None and first_seen:
                logger.info(f"Found existing YouTube stream for event '{event.get('summary')}' "
                            f"sheet {sheet_number}: '{broadcast['snippet']['title']}' "
                            f"(broadcast id={broadcast['id']}) - using it")

            if broadcast is None:
                # no state entry yet - see if a matching broadcast already exists on YouTube
                # (e.g. state file was lost, or this is the first run against pre-existing streams)
                broadcast = find_unclaimed_broadcast_by_title(known_broadcasts, claimed_broadcast_ids, title)
                if broadcast is not None:
                    logger.info(f"Found existing YouTube stream for event '{event.get('summary')}' "
                                f"sheet {sheet_number}: '{title}' (broadcast id={broadcast['id']}) - using it")

            if broadcast is None:
                if missing_directives:
                    # error already logged once for the whole event above
                    continue
                if playlist is None:
                    playlist = get_or_create_playlist(youtube, playlist_title, description, playlist_visibility)
                broadcast_id = create_broadcast(
                    youtube, playlist, stream_key, title, description, start_dt, end_dt,
                    thumbnail_override, stream_visibility
                )
                logger.info(f"Created YouTube stream for event '{event.get('summary')}' sheet {sheet_number}: "
                            f"'{title}' (broadcast id={broadcast_id}, visibility={stream_visibility}, "
                            f"playlist='{playlist_title}')")
                state[state_key] = {"broadcast_id": broadcast_id}
                claimed_broadcast_ids.add(broadcast_id)
                # record what we just created locally - never re-read it back from youtube
                known_broadcasts[broadcast_id] = {
                    "id": broadcast_id,
                    "snippet": {
                        "title": title,
                        "description": description,
                        "scheduledStartTime": start_dt.isoformat(),
                        "scheduledEndTime": end_dt.isoformat(),
                    },
                    "status": {"lifeCycleStatus": "ready", "privacyStatus": stream_visibility},
                }
                if now >= start_dt and now < end_dt:
                    # it's already within its scheduled window (e.g. the calendar entry was just
                    # created or moved earlier) - start it now instead of waiting for next cycle
                    pending_starts.append((known_broadcasts[broadcast_id], event, sheet_number, title))
                continue

            claimed_broadcast_ids.add(broadcast["id"])
            state[state_key] = {"broadcast_id": broadcast["id"]}

            changes = describe_broadcast_changes(broadcast, title, description, start_dt, end_dt, stream_visibility)
            if changes:
                logger.info(f"Calendar change detected for event '{event.get('summary')}' sheet {sheet_number} "
                            f"(broadcast id={broadcast['id']}): {'; '.join(changes)} - updating YouTube")
                update_broadcast(youtube, broadcast, title, description, start_dt, end_dt, stream_visibility)

            status = broadcast["status"]["lifeCycleStatus"]
            if now >= start_dt and now < end_dt and status not in ("live", "testing"):
                pending_starts.append((broadcast, event, sheet_number, title))
            if now >= end_dt and status == "live":
                logger.info(f"Stopping stream: event '{event.get('summary')}' sheet {sheet_number} - '{title}' "
                            f"(broadcast id={broadcast['id']})")
                transition_broadcast(youtube, broadcast["id"], "complete")
                # stream has run its course - nothing left to track for it
                del state[state_key]
                known_broadcasts.pop(broadcast["id"], None)

    # calendar events that vanished since the last cycle (deleted/cancelled) - pull their
    # streams down off youtube too, and stop tracking them
    for event_id in known_event_ids - current_event_ids:
        for sheet_number in range(1, len(stream_keys) + 1):
            state_key = f"{event_id}:{sheet_number}"
            entry = state.pop(state_key, None)
            if entry is None:
                continue
            broadcast = known_broadcasts.get(entry["broadcast_id"])
            if broadcast is None:
                # already completed/gone on youtube's side - nothing to clean up there
                continue

            # a calendar event drops out of our query the instant its own end time passes,
            # same as an actual deletion would - remove_broadcast_for_deleted_event tells the
            # two apart (and stopping vs. removing) so the log always matches what happened
            remove_broadcast_for_deleted_event(
                youtube, broadcast, now,
                reason="Event removed",
                context=f"event id={event_id} sheet {sheet_number}",
            )
            known_broadcasts.pop(broadcast["id"], None)

    # every stop/removal above has now been issued - safe to start whatever's due
    for broadcast, event, sheet_number, title in pending_starts:
        logger.info(f"Starting stream: event '{event.get('summary')}' sheet {sheet_number} - '{title}' "
                    f"(broadcast id={broadcast['id']})")
        transition_broadcast(youtube, broadcast["id"], "live")
        broadcast["status"]["lifeCycleStatus"] = "live"

    known_event_ids.clear()
    known_event_ids.update(current_event_ids)


def main():
    logger.info("This is the calendar-driven stream manager for the youtube streams.")
    logger.info("   *****   Please DO NOT CLOSE THIS WINDOW   *****")
##fff    time.sleep(60)  # give OBS instances time to start up

    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)
    calendar_service = build("calendar", "v3", credentials=creds)
    state = load_state()
    known_event_ids = set()  # in-memory only: which calendar events we've seen so far this run

    startup_now = datetime.now(timezone.utc).astimezone(PACIFIC)
    log_tracked_events(get_calendar_events(calendar_service, startup_now, get_window_end(startup_now)))

    # the one and only time youtube gets read/polled - from here on it's only ever contacted to
    # create/update a broadcast, or to start/stop one, all driven off the calendar and our own
    # in-memory record of what we last set
    known_broadcasts = fetch_youtube_broadcasts(youtube)
    logger.info(f"Startup: found {len(known_broadcasts)} active/upcoming stream(s) on YouTube")

    quit_at = get_quit_time(startup_now)
    if quit_at is not None:
        logger.info(f"Scheduled quit: script will exit at {quit_at.strftime('%Y-%m-%d %H:%M %Z')}")

    first_cycle = True
    while True:
        try:
            run_cycle(youtube, calendar_service, state, known_event_ids, known_broadcasts)
            if first_cycle:
                cleanup_startup_orphans(youtube, known_broadcasts, state)
                first_cycle = False
        except HttpError as error:
            logger.error(f"An error occurred: {error}", exc_info=True)
        except Exception:
            logger.error("Unexpected error during cycle", exc_info=True)

        save_state(state)

        if quit_at is not None and datetime.now(timezone.utc) >= quit_at:
            logger.info(f"Configured script end time ({QUIT_HOUR:02d}:{QUIT_MINUTE:02d}) reached - "
                        f"exiting as scheduled. Live streams are left running.")
            return

        sleep_until_next_minute()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.critical("A process ending error occurred", exc_info=True)
    input("process ended...")
