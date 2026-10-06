"""One-time setup: generates a TikTok refresh token for cross-posting.

Run locally:  python get_tiktok_token.py
Or paste this whole file into a free Google Colab notebook cell (same loopback
trick as the YouTube token - nothing to install).

One-time setup in the TikTok developer app (https://developers.tiktok.com):
1. Create an app -> add the product "Content Posting API"
2. Under the app's "Products" settings, add redirect URI exactly:
   http://localhost:1
3. TikTok requires a Privacy Policy URL on the app - a free GitHub Pages page
   or public gist link works.
4. Copy the app's Client Key + Client Secret.

Note: while the app is unaudited, posts go out as PRIVATE (SELF_ONLY) with a
small daily quota. After TikTok's audit approves it, set the GitHub variable
TIKTOK_PRIVACY_STATUS=PUBLIC_TO_EVERYONE.
"""
import os
import secrets
import urllib.parse

import requests

TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REDIRECT = "http://localhost:1"
SCOPES = "user.info.basic,video.publish"

client_key = os.getenv("TIKTOK_CLIENT_KEY") or input("Client Key: ").strip()
client_secret = os.getenv("TIKTOK_CLIENT_SECRET") or input("Client Secret: ").strip()
state = secrets.token_hex(8)

auth = urllib.parse.urlencode({
    "client_key": client_key,
    "scope": SCOPES,
    "response_type": "code",
    "redirect_uri": REDIRECT,
    "state": state,
})
url = f"https://www.tiktok.com/v2/auth/authorize/?{auth}"

print("\n1. Open this link and log in with the TikTok account of your channel:\n")
print(url + "\n")
print("2. Approve the permissions.")
print("3. The browser lands on http://localhost:1/?code=... and shows an error -")
print("   EXPECTED. Copy the FULL URL from the address bar and paste it below.\n")

pasted = input("Paste the full redirect URL: ").strip()
qs = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
if "code" not in qs:
    raise SystemExit(f"No ?code= in that URL: {pasted}")
code = qs["code"][0]

r = requests.post(TOKEN_URL, data={
    "client_key": client_key,
    "client_secret": client_secret,
    "code": code,
    "grant_type": "authorization_code",
    "redirect_uri": REDIRECT,
}, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=60)
j = r.json()
if "refresh_token" not in j:
    raise SystemExit(f"Token exchange failed: {j}")

print("\n=============== COPY THESE INTO GITHUB SECRETS ===============")
print(f"TIKTOK_CLIENT_KEY     = {client_key}")
print(f"TIKTOK_CLIENT_SECRET  = {client_secret}")
print(f"TIKTOK_REFRESH_TOKEN  = {j['refresh_token']}")
print("===============================================================")
print(f"(open_id: {j.get('open_id')}  |  access token valid {j.get('expires_in', '?')}s)")
