# Granite Curling Club of Seattle Streaming Operations

This document is meant to detail the live streaming settings for 
Granite Curling Club.

## OBS information
** todo **

## OBS Startup Scripts
** todo **

## YouTube Startup Scripts

There are two scripts that handle the scheduling and start/stop of the
youtube streams.  Both scripts are started at login by the Task
Scheduler. The user 'Stream User' is auto logged in when the computer
boots, so if that changes these scripts will need to get updated.

Both scripts use the same permissions, and require the user to
'approve' the scripts in a browser window when they are first
executed. These permissions should be permanent, but may need to be
re-run manually if the permissions are lost. This may happen if the
scripts aren't run for at least 6 months.

You can delete an event from the calendar any time before 5am the day
of the event and it won't create a stream. If changes need to be made
after 5am, you have to remove/edit the stream in YouTube Studio.  Do
not add new events in YouTube studio because it does not allow you to
set an end time. Any events manually created need to be
started/stopped manually.

## `daily_stream_scheduler.py`

IMPORTANT: Do not include links in calendar entries
	
This script is started at boot, and sleeps until the computer hits
5:30am. The computer reboots at 5:05am every day, so this runs after
boot. The reason this is set to run after boot is a console window
stays open while the script is waiting until 5:30am, so this lessens
the chance someone closes the window.

Once the script hits 5:30am it does the following:

 - Reads the calendar of tesnwash@gmail.com. 
 - Get any event scheduled to start between 5:30am today - 5:29am tomorrow.
 - For each event, if there is no playlist, create one (details below).
 - For each event, create 5 live streams, one for each sheet. 
 - The "description" from the calendar event is used to populate both the description for the playlists and the video events.
   - playlist descriptions cannot have links in them, so the script removes everything after (and including) the first "<"
   - video descriptions can have links, so league links are left in place.

The script differentiates leagues and bonspiels by the presence of the
word "League" within the event title.  For example,
`League-- 2024-2025 Tuesday Supper League`
This will use (or create if not found) a playlist of the same name,
"2024-2025 Tuesday Supper League" Videos within the playlist will be
created with the date and sheet, with the years moved to the end:
"9/22 - Sheet 1 - Tuesday Supper League 2024-2025" Any description
entered into the events will be used to populate the descriptions in
the playlist (only on create) and each video.

The league uses a file called GCC_League.png to set the
video thumbnail.  The file lives in the same directory as the scripts.

For bonspiels, the scheduler looks for a title of the form
`Bonspiels--` _Spielname_ `- Draw 1`.
An entry like this will create a playlist called "Spielname", still
using the first calendar event's description. Videos within the
playlist will be created with the date, time, and sheet, e.g.  "Draw 1
(9:00) - Sheet 1 - Spielname"

For bonspiel events, the script uses a file called GCC_Bonspiel.png to
set the video thumbnail. The same file is used for every bonspiel. To
customize it for a particular bonspiel, this file needs to get updated
at least 1 day before the bonspiel starts.  The file lives in the same
directory as the scripts.

The script makes use of stored credentials to access the YouTube and
Google API's. The credentials and identifiers are kept in .json files
and they are not stored in the repository. Ask an operator if you need
a copy.

## `streaming_start_stop_script.py`

This script is started at login and runs indefinitely. 
The script sleeps until the next 10 minute mark, e.g. :10, :20, :30, etc..
This script only interacts with the tesnwash@gmail.com youtube account.

At each ten minute interval it checks first to see if any currently
running streams are past their end date, if so, stop them.  It then
checks to see if any pending streams are past their start time, if so,
start them.

This script does open a console window that must remain open for the duration.

The script makes use of stored credentials to access the YouTube and
Google API's. These are the same files used by `daily_stream_scheduler.py`.
