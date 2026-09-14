"""One-time setup: generates a YouTube API refresh token.

Run locally:  python get_token.py
Or paste this whole file into a free Google Colab notebook cell and run it
(nothing to install on your machine).

You need your OAuth Client ID + Client Secret from Google Cloud Console
(see SETUP.md step 3). A browser window will open - log into the channel's
Google account and approve. Then copy the printed values into GitHub Secrets.
"""
import os

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

client_id = os.getenv("") or input("Client ID: ").strip()
client_secret = os.getenv("") or input("Client Secret: ").strip()

from google_auth_oauthlib.flow import InstalledAppFlow

client_config = {
    "installed": {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    }
}

flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
creds = flow.run_local_server(port=0, prompt="consent")

print("\n================ COPY THESE INTO GITHUB SECRETS ================")
print(f"YT_CLIENT_ID     = {client_id}")
print(f"YT_CLIENT_SECRET = {client_secret}")
print(f"YT_REFRESH_TOKEN = {creds.refresh_token}")
print("================================================================")
