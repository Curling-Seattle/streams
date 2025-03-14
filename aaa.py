from datetime import datetime, timedelta
import pytz
import os.path
import time
import argparse
from httplib2 import Response



from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

def test():

  content = '{"error": {"message": "An error occurred"}}'  # example JSON content



  try:
    content = '{"error": {"message": "An error occurred"}}'.encode('utf-8')
    
    # Create a mock response object using httplib2.Response
    status_code = 500
    reason = 'Internal Server Error'
    
    # Mock response object
    resp = Response({'status': status_code, 'reason': reason})
    
    # Raising the HttpError with the correct response object and content
    raise HttpError(resp=resp, content=content)


    #raise HttpError(resp={}, content=content)
  except HttpError as error:
    print(f"An error aaa occurred: {error}")

def main():

  try:
    test()
 
  except HttpError as error:
    print(f"An error occurred: {error}")



if __name__ == "__main__":
  main()
  
