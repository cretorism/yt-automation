"""YouTube Data API v3: resumable upload + hard verification that processing finished."""
import os
import time

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from . import config

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _service():
    if not (config.YT_CLIENT_ID and config.YT_CLIENT_SECRET and config.YT_REFRESH_TOKEN):
        raise RuntimeError("Missing YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN secrets")
    creds = Credentials(
        token=None,
        refresh_token=config.YT_REFRESH_TOKEN,
        client_id=config.YT_CLIENT_ID,
        client_secret=config.YT_CLIENT_SECRET,
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    creds.refresh(GoogleRequest())
    return build("youtube", "v3", credentials=creds)


def upload_video(path: str, title: str, description: str, tags: list[str],
                 category_id: str | None = None) -> str:
    """Resumable upload. Returns the new video ID."""
    yt = _service()
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": tags[:30],
            "categoryId": category_id or config.CATEGORY_ID,
        },
        "status": {
            "privacyStatus": config.PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(path, chunksize=8 * 1024 * 1024, resumable=True,
                            mimetype="video/mp4" if path.endswith(".mp4") else "video/webm")
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    print(f"[upload] uploading {os.path.basename(path)} ({os.path.getsize(path)/1e6:.1f} MB)...")
    response = None
    retries = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"[upload] {os.path.basename(path)}: {int(status.progress() * 100)}%")
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and retries < 5:
                retries += 1
                print(f"[upload] transient {e.resp.status}, retry {retries}/5")
                time.sleep(10 * retries)
            else:
                raise
    vid = response["id"]
    print(f"[upload] done -> https://youtube.com/watch?v={vid}")
    return vid


def wait_until_processed(video_id: str, timeout_min: int | None = None) -> bool:
    """Poll YouTube until uploadStatus == 'processed' (100% verified, playable).
    Raises if YouTube reports rejection/failure or the window expires."""
    timeout_min = timeout_min or config.VERIFY_TIMEOUT_MIN
    yt = _service()
    deadline = time.time() + timeout_min * 60
    while time.time() < deadline:
        resp = yt.videos().list(part="status,processingDetails", id=video_id).execute()
        items = resp.get("items", [])
        if items:
            st = items[0].get("status", {})
            pr = items[0].get("processingDetails", {})
            upload_status = st.get("uploadStatus")
            proc = pr.get("processingStatus")
            print(f"[verify] {video_id}: uploadStatus={upload_status} processingStatus={proc}")
            if upload_status in ("rejected", "failed", "deleted"):
                reason = (pr.get("fileDetails") or {}).get("container", "")
                issues = items[0].get("status", {}).get("failureReason", "unknown")
                raise RuntimeError(f"YouTube refused video {video_id}: {upload_status} ({issues}) {reason}")
            if upload_status == "processed" and proc == "succeeded":
                return True
        else:
            print(f"[verify] {video_id} not visible yet...")
        time.sleep(30)
    raise RuntimeError(f"Verification timeout: {video_id} not processed after {timeout_min} min")
