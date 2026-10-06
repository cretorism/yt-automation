"""TikTok cross-posting via the official Content Posting API (optional).

Enabled automatically when TIKTOK_CLIENT_KEY + TIKTOK_CLIENT_SECRET +
TIKTOK_REFRESH_TOKEN are all set as secrets - otherwise every TikTok step
is skipped and the pipeline behaves exactly as before.

Audit parity with YouTube: while the TikTok developer app is unaudited, the
API only allows SELF_ONLY (private) direct posts on a small daily quota.
After the app passes TikTok's audit, set the repo variable
TIKTOK_PRIVACY_STATUS=PUBLIC_TO_EVERYONE to go public automatically.
"""
import os
import re
import time

import requests

from . import config

TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

CHUNK = 64 * 1024 * 1024          # API cap: one chunk <= 64 MB
CAPTION_CAP = 2200                # TikTok caption limit
VERIFY_TIMEOUT_MIN = 20

DONE_STATUSES = {"PUBLISH_COMPLETE", "SEND_TO_INBOX", "SELF_ONLY_VISIBLE"}

_token_cache: dict = {}


def enabled() -> bool:
    return bool(config.TIKTOK_CLIENT_KEY and config.TIKTOK_CLIENT_SECRET
                and config.TIKTOK_REFRESH_TOKEN)


def build_caption(title: str, tags: list) -> str:
    tags_out = "#" + " #".join(re.sub(r"\W+", "", t) for t in tags[:5] if re.sub(r"\W+", "", t))
    return f"{title.strip()}\n\n{tags_out}".strip()[:CAPTION_CAP]


def _access_token() -> str:
    """Refresh the user access token (valid 24h); cached for this run."""
    if _token_cache.get("exp", 0) > time.time():
        return _token_cache["tok"]
    r = requests.post(TOKEN_URL, data={
        "client_key": config.TIKTOK_CLIENT_KEY,
        "client_secret": config.TIKTOK_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": config.TIKTOK_REFRESH_TOKEN,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=60)
    r.raise_for_status()
    j = r.json()
    if "access_token" not in j:
        raise RuntimeError(f"TikTok token refresh failed: {j}")
    if j.get("refresh_token") and j["refresh_token"] != config.TIKTOK_REFRESH_TOKEN:
        print("[tiktok] WARNING: TikTok rotated your refresh token - update the "
              "TIKTOK_REFRESH_TOKEN GitHub secret with this value:\n"
              f"{j['refresh_token']}")
    _token_cache["tok"] = j["access_token"]
    _token_cache["exp"] = time.time() + int(j.get("expires_in", 86400)) - 600
    return _token_cache["tok"]


def upload_video(path: str, caption: str) -> str:
    """Direct-post a local video file (chunked upload). Returns the publish_id."""
    tok = _access_token()
    size = os.path.getsize(path)
    chunks = max(1, (size + CHUNK - 1) // CHUNK)
    print(f"[tiktok] direct post: {os.path.basename(path)} "
          f"({size / 1e6:.1f} MB, {chunks} chunk(s), privacy={config.TIKTOK_PRIVACY_STATUS})")

    init = requests.post(INIT_URL, headers={
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json; charset=UTF-8",
    }, json={
        "post_info": {
            "title": caption.strip()[:CAPTION_CAP],
            "privacy_level": config.TIKTOK_PRIVACY_STATUS,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": min(CHUNK, size),
            "total_chunk_count": chunks,
        },
    }, timeout=60)
    init.raise_for_status()
    ij = init.json()
    err = ij.get("error") or {}
    if err.get("code", "ok") != "ok":
        raise RuntimeError(f"TikTok init failed ({err.get('code')}): {err.get('message')}")
    publish_id = ij["data"]["publish_id"]
    upload_url = ij["data"]["upload_url"]

    mime = "video/mp4" if path.endswith(".mp4") else "video/webm"
    sent = 0
    with open(path, "rb") as f:
        for i in range(chunks):
            data = f.read(CHUNK)
            end = sent + len(data) - 1
            put = requests.put(upload_url, data=data, headers={
                "Content-Type": mime,
                "Content-Range": f"bytes {sent}-{end}/{size}",
            }, timeout=600)
            put.raise_for_status()
            sent += len(data)
            print(f"[tiktok] chunk {i + 1}/{chunks} done ({sent / 1e6:.0f}/{size / 1e6:.0f} MB)")

    _wait_processed(tok, publish_id)
    return publish_id


def _wait_processed(tok: str, publish_id: str) -> None:
    deadline = time.time() + VERIFY_TIMEOUT_MIN * 60
    while time.time() < deadline:
        r = requests.post(STATUS_URL, headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json; charset=UTF-8",
        }, json={"publish_id": publish_id}, timeout=60)
        r.raise_for_status()
        data = r.json().get("data") or {}
        st = data.get("status", "unknown")
        print(f"[tiktok] status: {st}")
        if st == "PUBLISH_FAILED":
            raise RuntimeError(f"TikTok refused video {publish_id}: "
                               f"{data.get('fail_message') or 'unknown reason'}")
        if st in DONE_STATUSES:
            return
        time.sleep(30)
    print(f"[tiktok] still processing after {VERIFY_TIMEOUT_MIN} min - continuing "
          "(it usually finishes in the app shortly); check TikTok")


def public_link(tok: str = None, publish_id: str = "") -> str | None:
    """Best-effort public URL. Returns None for private posts (no public data)."""
    try:
        tok = tok or _access_token()
        r = requests.post(STATUS_URL, headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json; charset=UTF-8",
        }, json={"publish_id": publish_id}, timeout=60)
        vid = ((r.json().get("data") or {}).get("public_data") or {}).get("video_id")
        return f"https://www.tiktok.com/video/{vid}" if vid else None
    except Exception:  # noqa: BLE001
        return None
