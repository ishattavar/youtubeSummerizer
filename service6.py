import os
import smtplib
import schedule
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pytube import YouTube
from moviepy.editor import VideoFileClip
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import whisper
import warnings
import openai
from dotenv import load_dotenv

warnings.simplefilter("ignore")

# Load environment variables from .env file
load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/youtube"]

# Load API key and email credentials from environment variables
openai.api_key = os.getenv("OPENAI_API_KEY")
email_sender = os.getenv("EMAIL_SENDER")
email_password = os.getenv("EMAIL_PASSWORD")

class Service:
    def __init__(self) -> None:
        self.scopes = SCOPES
        self.creds = None
        self.token_path = "token.json"
        self.youtube = build('youtube', 'v3', credentials=self.authenticate())
        self.latest_video_ids = {}

    def authenticate(self):
        if self.creds is not None:
            return self.creds
        
        if os.path.exists(self.token_path):
            self.creds = Credentials.from_authorized_user_file(self.token_path)
        else:
            flow = InstalledAppFlow.from_client_secrets_file('creds.json', self.scopes)
            self.creds = flow.run_local_server(port=0)

            with open(self.token_path, 'w') as token:
                token.write(self.creds.to_json())

        return self.creds

    def getChannelIDs(self, channel_names):
        channel_ids = {}
        for channel_name in channel_names:
            request = self.youtube.search().list(
                part="snippet",
                q=channel_name,
                type="channel",
                maxResults=5,
            )
            response = request.execute()

            for item in response["items"]:
                if item["snippet"]["title"].lower() == channel_name.lower():
                    channel_ids[channel_name] = item["snippet"]["channelId"]
                    break

        return channel_ids

    def subscribe_to_channel(self, channel_id, channel_name):
        try:
            subscription = self.youtube.subscriptions().insert(
                part='snippet',
                body={
                    'snippet': {
                        'resourceId': {
                            'kind': 'youtube#channel',
                            'channelId': channel_id
                        }
                    }
                }
            ).execute()

            print(f'Subscribed to channel {channel_name} successfully!')
        except Exception as e:
            print(f'Error subscribing to channel {channel_name}: {e}')

    def subscribe_to_multiple_channels(self, channel_names):
        channel_ids = self.getChannelIDs(channel_names)
        for channel_name, channel_id in channel_ids.items():
            self.subscribe_to_channel(channel_id, channel_name)

    def get_latest_video(self, channel_id):
        request = self.youtube.search().list(
            part="snippet",
            channelId=channel_id,
            order="date",
            type="video",
            maxResults=1,
        )

        response = request.execute()

        if response["items"]:
            latest_video = response["items"][0]
            video_id = latest_video["id"]["videoId"]
            video_title = latest_video["snippet"]["title"]
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            
            return video_id, video_title, video_url
        else:
            return None, None, None

    def download_video(self, video_url, filename='video.mp4'):
        try:
            yt = YouTube(video_url)
            stream = yt.streams.get_highest_resolution()
            stream.download(filename=filename)
            return filename
        except Exception as e:
            print(f"Error downloading video: {e}")
            return None

    def extract_audio(self, video_file, audio_file='audio.wav'):
        try:
            video = VideoFileClip(video_file)
            audio = video.audio
            audio.write_audiofile(audio_file)
            audio.close()
            video.close()
            return audio_file
        except Exception as e:
            print(f"Error extracting audio: {e}")
            return None

    def transcribe_audio_file(self, audio_file_path):
        model = whisper.load_model("base")
        path = audio_file_path

        prompt = "'If there is any repetition then move to next conversation.'"
        
        try:
            transcript = model.transcribe(path, initial_prompt=prompt, task = 'translate')
            return transcript
        except Exception as e:
            print(f"Error extracting audio: {e}")
            return None
    
    def summarize_transcript(self, transcript):
        try:
        
            response = openai.ChatCompletion.create(
                model="gpt-4-turbo",
                messages=[
                    {"role": "user", "content": f"Summarize the following transcript:\n\n{transcript}"}
                ],
                max_tokens=200,
                temperature=0.5
            )
            summary = response.choices[0].message['content'].strip()
            return summary
        except Exception as e:
            print(f"Error generating summary: {e}")
            return None
        
    def get_transcript_of_latest_video(self, video_url):
        video_file = self.download_video(video_url)
        if not video_file:
            return None, None
        
        audio_file = self.extract_audio(video_file)
        if not audio_file:
            return None, None
        
        transcript = self.transcribe_audio_file(audio_file)
        
        os.remove(video_file)
        os.remove(audio_file)
        
        if transcript:
            summary = self.summarize_transcript(transcript['text'])
            return transcript['text'], summary
        else:
            return None, None

    def send_email_notification(self, email_recipient, video_title, video_url, video_transcript=None, video_summary=None):
        msg = MIMEMultipart()
        msg['From'] = email_sender
        msg['To'] = email_recipient
        msg['Subject'] = "New Video Notification"

        body = f"A new video has been uploaded to the channel you subscribed to:\n\nTitle: {video_title}\nURL: {video_url}"
        
        if video_summary:
            body += f"\n\nSummary:\n{video_summary}"
        msg.attach(MIMEText(body, 'plain'))

        try:
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(email_sender, email_password)
            text = msg.as_string()
            server.sendmail(email_sender, email_recipient, text)
            server.quit()
            print(f'Email sent to {email_recipient} successfully!')
        except Exception as e:
            print(f'Error sending email: {e}')

    def check_for_new_videos(self, channel_ids, email_recipient):
        for channel_name, channel_id in channel_ids.items():
            video_id, video_title, video_url = self.get_latest_video(channel_id)
            if video_id:
                if channel_id in self.latest_video_ids:
                    if self.latest_video_ids[channel_id] != video_id:
                        self.latest_video_ids[channel_id] = video_id
                        video_transcript, video_summary = self.get_transcript_of_latest_video(video_url)
                        self.send_email_notification(email_recipient, video_title, video_url, video_transcript, video_summary)
                else:
                    self.latest_video_ids[channel_id] = video_id

    def get_Mail_On_Latest_Videos(self, channel_names, email_recipient):
        channel_ids = self.getChannelIDs(channel_names)
        for channel_name, channel_id in channel_ids.items():
            video_id, video_title, video_url = self.get_latest_video(channel_id)
            if video_id:
                self.latest_video_ids[channel_id] = video_id
                video_transcript, video_summary = self.get_transcript_of_latest_video(video_url)
                self.send_email_notification(email_recipient, video_title, video_url, video_transcript, video_summary)

        # Schedule the check for new videos every minute
        schedule.every(1).minute.do(self.check_for_new_videos, channel_ids, email_recipient)
        
        while True:
            schedule.run_pending()
            time.sleep(1)

# Example usage
if __name__ == "__main__":
    service = Service()
    
    # Prompt the user for YouTube channel names
    channel_names_input = input("Please enter YouTube channel names separated by commas: ")
    channel_names = [name.strip() for name in channel_names_input.split(',')]
    
    # Subscribe to multiple channels
    service.subscribe_to_multiple_channels(channel_names)
    
    # Ask the user if they want to receive notifications for the latest video updates
    receive_notifications = input("Do you want to receive notifications for the latest video updates? (yes/no): ").strip().lower()
    
    if receive_notifications == 'yes':
        email_recipient = input("Please enter the recipient email: ")

        # Get the latest videos and send email notifications
        service.get_Mail_On_Latest_Videos(channel_names, email_recipient)
    else:
        print("You have chosen not to receive notifications. Subscription complete.")
