__doc__ = '''This script reads the GCC streaming calendar and creates
corresponding events on YouTube to show live streams of them. The live
events are also placed in YouTube playlists for later retrieval.
'''

from datetime import datetime, timedelta
import pytz
import os.path
import time
import argparse
import json
from httplib2 import Response

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

trial_run_prefix = "TRIAL RUN - not executing:"

# If modifying these scopes, delete the streaming_token.json file in this
# directory.
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly",
          "https://www.googleapis.com/auth/youtube"]

dirname = os.path.dirname(__file__)
secret_file = os.path.join(dirname, 'youtube_session.json')


# These identify the stream keys in youtube, they're in order from sheet 1 - 5
with open(os.path.join(dirname,'stream_keys.json')) as f:
    stream_keys = json.load(f)

# really 5:30a
def time_until_next_5am():
  now = datetime.now()
  # Get the next 5am from the current time
  next_5am = now.replace(hour=5, minute=30, second=0, microsecond=0)

  # If it's already past 5am today, schedule for the next day
  if now >= next_5am:
    next_5am += timedelta(days=1)

  # add an extra 10 seconds so that it runs just after 5a
  return (next_5am - now).total_seconds() + 10


def create_youtube_live_stream(event, playlist, youtube_service, key, sheet, event_type, trial_run=False):
  global dirname
  starttime = event["start"].get("dateTime")
  endtime = event["end"].get("dateTime")
  start_datetime = datetime.fromisoformat(starttime)

  # this updates a title from "YYYY-YYYY title" to "title YYYY-YYYY"
  title_splits = event["summary"].split(" ", 1)
  video_title = ' '.join([title_splits[1], title_splits[0]])
  
  if event_type == "league":
    # if it's a league, do format of date - sheet - league
    title = f"{start_datetime.month}/{start_datetime.day} - Sheet {sheet} - {video_title}"
  else:
    # it's probably a bonspiel, so draw - time - sheet
    # bonspiels need to be in the format of "spielname - draw #"
    event_summary = event["summary"]
    spiel = event_summary[:event_summary.find("-")].strip()
    draw = event_summary[event_summary.find("-")+1:].strip()
    title = f"{draw} ({start_datetime.strftime("%H:%M")}) - Sheet {sheet} - {spiel}"
   
  #print ("descirption? " + event["description"])
  # set the required live_broadcast information  
  live_broadcast = {
      'snippet': {
          'title': title,
          'description': event["description"] if "description" in event.keys() else " ",
          'scheduledStartTime': starttime,
          'scheduledEndTime': endtime
      },
      'status': {
          'privacyStatus': 'public',  # TODO: Should be 'public' once we're live
          'selfDeclaredMadeForKids': False,
      },
      'contentDetails': {
        'monitorStream': {
          'enableMonitorStream': False
        }
      }
  }

  # create the live broadcast
  if trial_run:
    print(trial_run_prefix, "scheduling broadcast for:", title)
  else:
    print("scheduling broadcast for: " + title)
    request = youtube_service.liveBroadcasts().insert(
      part="snippet,status,contentDetails",
      body=live_broadcast
     )
    response = request.execute()

  time.sleep(5)

  # bind the broadcast to the correct stream key
  if trial_run:
    print(trial_run_prefix, "binding broadcast to OBS stream key")
  else:
    print("binding broadcast to OBS stream key")
    broadcast_stream_id = response['id']
    request = youtube_service.liveBroadcasts().bind(
       part="id,contentDetails",
       id=broadcast_stream_id,
       streamId=key
     )
    request.execute()

  time.sleep(5)
 
  
  # Add the live stream to the playlist
  if trial_run:
    print(trial_run_prefix, "adding event to playlist")
  else:
    print("adding event to playlist")
    request = youtube_service.playlistItems().insert(
      part="snippet",
      body={
          "snippet": {
              "playlistId": playlist['id'],
              "resourceId": {
                  "kind": "youtube#video",
                  "videoId": broadcast_stream_id
              }
          }
      })
    request.execute()
    
  time.sleep(5)

  # Set the thumbnail for the live stream
  thumbnail_file = os.path.join(
      dirname,
      'GCC_League.png' if event_type == "league" else 'GCC_Bonspiel.png')
  if os.path.exists(thumbnail_file):
    attempts, max_attempts = 0, 3
    if trial_run:
      print(trial_run_prefix, "attempting to add thumbnail to playlist")
    else:
      while attempts < max_attempts:
        try:
          request = youtube_service.thumbnails().set(
            videoId=broadcast_stream_id,
            media_body=thumbnail_file
          )
          attempts += 1

          print("attempting to add thumbnail to playlist, attempt",
                attempts, "of", max_attempts)
          request.execute()

          time.sleep(5)
          # if we got here, it succeeded so break the loop
          break
        except HttpError as error:
          print(f"An error occured while trying to set video thumbnail: {error}")
          print("trying again in 30 seconds")
          time.sleep(30)
    
      if attempts >= max_attempts:
         print ("couldn't set image, but moving on anyway. Video: " + title)
  return

def get_playlist(playlist_title, youtube_service):
    next_page_token = None
    
    # Search 
    while True:
      request = youtube_service.playlists().list(
          channelId="UC81pcWoRJbdnK77B3WS0zjg",
          part="id,snippet",
          maxResults=50,
          pageToken=next_page_token
      )
      response = request.execute()
    
      # Loop through the results for this page, if one matches reutrn it
      for playlist in response['items']:
        if playlist['snippet']['title'] == playlist_title:
          return playlist
      
      next_page_token = response.get('nextPageToken')
      if not next_page_token:
        # gets here if no playlist with the name found
        break
            

def create_playlist(playlist_title, playlist_description, youtube_service, trial_run=False):
  print (" play list title : " + playlist_title)
  print ("description : " + playlist_description)
  try:
    request_body = {
      'snippet': {
        'title': playlist_title,
        'description': playlist_description
      },
      'status': {
        'privacyStatus': 'public'
      }
    }
    if trial_run:
      print(trial_run_prefix, "Inserting playlist")
    else:
      request = youtube_service.playlists().insert(
        part="snippet, status",
        body=request_body
       )
      response = request.execute()
    
  except HttpError as error:
    print(f'An error has occurred while inserting: {error}')

  return response
    

def main(args):
  print("This is the calendar scheduling task for the youtube streams.")
  print("Please DO NOT CLOSE THIS WINDOW.")
  
  if not(args.now):
    # if now is not set, run scheduler at 5am
    time.sleep(60) # sleep for 60 seconds 
    # Calculate the time until the next 5:30am
    seconds_until_5am = time_until_next_5am()

    print ("sleeping for " + str(seconds_until_5am))
    # Sleep until the next 5am:30
    time.sleep(seconds_until_5am)
  
  creds = None
  # The file streaming_token.json in this directory
  # stores the user's access and refresh tokens, and is
  # created automatically when the authorization flow completes for the first
  # time.
  streaming_token_path = os.path.join(dirname, 'streaming_token.json')
  if os.path.exists(streaming_token_path):
    creds = Credentials.from_authorized_user_file(streaming_token_path, SCOPES)
  # If there are no (valid) credentials available, let the user log in.
  if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
      try:
        creds.refresh(Request())
      except Exception as e:
        print(f"Failed to refresh credentials: {e}")
        creds = None
    if not creds or not creds.valid:
      flow = InstalledAppFlow.from_client_secrets_file(
          secret_file, SCOPES
      )
      creds = flow.run_local_server(port=0)
    # Save the credentials for the next run
    print("Storing creds for next run")
    with open(streaming_token_path, "w") as token:
      token.write(creds.to_json())

  try:
    service = build("calendar", "v3", credentials=creds)
    youtube_service = build("youtube", "v3", credentials=creds)
  
    calendars_result = service.calendarList().list().execute()
    calendars = calendars_result.get('items', [])

    if not calendars:
        print('No calendars found.')
    for calendar in calendars:
        print(f"Calendar Name: {calendar['summary']}, Calendar ID: {calendar['id']}")
  
    # Call the Calendar API to get all events from now until to 5:29a tomorrow
    now = datetime.now(pytz.timezone('US/Pacific'))
    now = now.replace(second=0, microsecond=0) # do this so we add anything with 'this' timestamp
    nowstr = now.isoformat()
    eod = now + timedelta(days=1)
    eod = eod.replace(hour=5, minute=29, second=0, microsecond=0)
    eodstr = eod.isoformat()

    events_result = (
        service.events()
        .list(
            calendarId="tesnwash@gmail.com",
            timeMin=nowstr,
            timeMax=eodstr,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    events = events_result.get("items", [])
    #print ("got some events")

    if not events:
      print("No upcoming events found.")
      next
  
    for event in events:      
      # Extract the playlist name from the event
      # Leagues are different than bonspiels
      if event["summary"].lower().find("league") != -1:
        # if it's a league, do format of date - sheet - league
        event_type = "league"
        playlist_title = f"{event["summary"]}"
      else:
        # it's probably a bonspiel, so draw - time - sheet
        # bonspiels need to be in the format of "spielname - draw #"
        playlist_title = event["summary"][:event["summary"].find("-")].strip()
        event_type = "spiel"

      playlist = get_playlist(playlist_title, youtube_service)
      if playlist == None:
        playlist_description = event["description"] if "description" in event.keys() else " "
        # no playlist, so create one
        playlist = create_playlist(playlist_title, playlist_description, youtube_service, args.trial_run)

      # Now that we have the event and playlist, create a stream for each sheet
      for i, key in enumerate(stream_keys):
        create_youtube_live_stream(event, playlist, youtube_service, key, i+1, event_type, args.trial_run)

  except HttpError as error:
    print(f"An error occurred: {error}")

  if args.trial_run:
    print(trial_run_prefix, "wait for input after completing tasks")
  else:
    input("process ended...")


if __name__ == "__main__":

  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      '--now', default=False, action='store_true',
      help="If set, this will perform the operations now instead of waiting "
      "until 5:30 am")
  parser.add_argument(
      '-t', '--trial-run', default=False, action='store_true',
      help="Only read calendar, prepare, and print YouTube entries without "
      "actually registering them")
  parsed_args = parser.parse_args()
  
  main(parsed_args)
