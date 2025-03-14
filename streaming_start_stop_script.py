__doc__ = '''This script runs continuously and at every N-minute mark,
e.g. if N=10, :10, :20, :30, etc., it checks to see if any currently
running streams are past their end date, if so, it stops them.  It then
checks to see if any pending streams are past their start time, if so,
it starts them.
This script only interacts with the tesnwash@gmail.com youtube account.
'''

import os
import json
import time
import argparse
from datetime import datetime, timezone, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

trial_run_prefix = "TRIAL RUN - not executing:"

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly",
          "https://www.googleapis.com/auth/youtube"]

dirname = os.path.dirname(__file__)
secret_file = os.path.join(dirname, 'youtube_session.json')

def wait_until_next_interval(interval=10, trial_run=False, verbose=0):
    now = datetime.now()
        
    minutes_to_next = interval - (now.minute % interval)
    if minutes_to_next == 0:
        minutes_to_next = interval
      
    # Calculate the time to sleep
    next_time = (now + timedelta(minutes=minutes_to_next)).replace(second=0, microsecond=0)
    sleep_time = (next_time - now).total_seconds()
    if verbose > 0:
        print(f"Waiting {sleep_time} seconds...")
    time.sleep(sleep_time)
    

def list_running_broadcasts(youtube):
    try:
        request = youtube.liveBroadcasts().list(
            part='snippet,contentDetails,status',
            broadcastStatus='active',
            maxResults=10,
            broadcastType='all'
            
        )
        response = request.execute()
        return response.get('items', [])
    except HttpError as e:
        print(f'An error occurred: {e}')
        return []
        

# we usually have fewer than 20 upcoming broadcasts in a given day, but for spiels we can have a ton
# so have to paginate this response
def list_upcoming_broadcasts(youtube):
    upcoming_broadcasts = []
    page_token = None

    while True:
      try:
        request = youtube.liveBroadcasts().list(
          part='snippet,contentDetails,status',
          broadcastStatus='upcoming',
          pageToken=page_token,
          maxResults=50    
        )

        response = request.execute()
        # print ("got a list response")
        upcoming_broadcasts += (response.get('items', []))
        
        page_token = response.get("nextPageToken")
        
        if not page_token:
          break
          
      except HttpError as e:
        print(f'An error occurred: {e}')
        return []
    return upcoming_broadcasts

def transition_broadcast(youtube, broadcast_id, broadcast_status, trial_run=False):
    message = f"transitioning cast of {broadcast_id} to {broadcast_status}"
    if trial_run:
        print(trial_run_prefix, message)
    else:
        print(message)
        try:
            request = youtube.liveBroadcasts().transition(
                broadcastStatus=broadcast_status,
                id=broadcast_id,
                part='status'
            )
            response = request.execute()
            return response
        except HttpError as e:
            print(f'An error occurred while transitioning broadcast: {e}')
            return None


def stop_done_broadcasts(youtube, trial_run=False, verbose=0):
    now = datetime.now(timezone.utc)

    if verbose > 0:
        print ("getting running broadcasts")
    running_broadcasts = list_running_broadcasts(youtube)
    if verbose > 0:
        print("got {} running broadcast{}".format(
            len(running_broadcasts), '' if len(running_broadcasts) == 1 else 's'))
    for broadcast in running_broadcasts:
        broadcast_id = broadcast['id']
        title = broadcast['snippet']['title']
        if verbose > 1:
            print("Checking", title)
        
        if not "scheduledEndTime" in broadcast['snippet']:
            continue

        scheduled_end_time = datetime.fromisoformat(broadcast['snippet']['scheduledEndTime'])
        status = broadcast['status']['lifeCycleStatus']
        
        if now >= scheduled_end_time and status == 'live':
          print("Stopping broadcast: " + title)
          transition_broadcast(youtube, broadcast_id, 'complete', trial_run)
    
def start_due_broadcasts(youtube, trial_run=False, verbose=0):
    if verbose > 0:
        print ("getting upcoming broadcasts")
    upcoming_broadcasts = list_upcoming_broadcasts(youtube)
    if verbose > 0:
        print("got {} upcoming broadcast{}".format(
            len(upcoming_broadcasts), '' if len(upcoming_broadcasts) == 1 else 's'))
    now = datetime.now(timezone.utc)

    for broadcast in upcoming_broadcasts:
        broadcast_id = broadcast['id']
        title = broadcast['snippet']['title']
        if verbose > 1:
            print("Checking", title)
        if not "scheduledStartTime" in broadcast['snippet']:
            continue
        
        scheduled_start_time = datetime.fromisoformat(broadcast['snippet']['scheduledStartTime'])
        
        try:
            scheduled_end_time = datetime.fromisoformat(broadcast['snippet']['scheduledEndTime'])
        except KeyError as e:
            scheduled_end_time = None
  
        status = broadcast['status']['lifeCycleStatus']
          
        # start the stream if we're past the start time, but not past the end time
        # if we're past both, the stream never ran and we should just nuke it in youtube  
        if now >= scheduled_start_time and (now < scheduled_end_time or scheduled_end_time == None) and status != 'live':
            print("Starting broadcast: " + title)
            transition_broadcast(youtube, broadcast_id, 'live', trial_run)


def main(args):
    print("This is the start/stop script for the youtube streams")
    print("PLEASE DO NOT CLOSE THIS WINDOW")
    # sleep for 60 seconds on start to give the OBS instances time to start up
    time.sleep(60)

    creds = None
    # The file 'streaming_token.json' stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    streaming_token_path = os.path.join(dirname, 'streaming_token.json')
    if os.path.exists(streaming_token_path):
        creds = Credentials.from_authorized_user_file(streaming_token_path, SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(secret_file, SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        print("Storing creds for next run")
        with open(streaming_token_path, "w") as token:
            token.write(creds.to_json())
        
    youtube = build("youtube", "v3", credentials=creds)
    
    while True:
        now = datetime.now(timezone.utc)
        t = now.strftime("%m/%d/%Y, %H:%M:%S")
        # Wait 3 seconds when the script starts before checking the times to stop broadcasts
        if args.trial_run:
            print("Checking for streams at time: " + t)
        time.sleep(3)
        stop_done_broadcasts(youtube, args.trial_run, args.verbose)

        # wait another 2 seconds after stopping streams to start new ones
        time.sleep(2)
        start_due_broadcasts(youtube, args.trial_run, args.verbose)
        
        wait_until_next_interval(args.interval, args.trial_run, args.verbose)
    
    if args.trial_run:
        print(trial_run_prefix, "wait for input after completing tasks")
    else:
        input("process ended...")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '-i', '--interval', type=int, default=10,
        help="Check streaming status wnen minutes past the hour are multiples "
        "of this interval.")
    parser.add_argument(
        '-t', '--trial-run', default=False, action='store_true',
        help="Only check on streaming status and report without actually "
        "changing status")
    parser.add_argument(
        '-v', '--verbose', action='count', default=0,
        help='Add verbose comments')
    parsed_args = parser.parse_args()
  
    main(parsed_args)
