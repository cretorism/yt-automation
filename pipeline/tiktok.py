"""TikTok cross-posting via the official Content Posting API (optional).

Enabled automatically when TIKTOK_CLIENT_KEY + TIKTOK_CLIENT_SECRET +
TIKTOK_REFRESH_TOKEN are all set as secrets - otherwise every TikTok step
is skipped and the pipeline behaves exactly as before.

Routing (important): on an unaudited developer app the direct-post endpoint
rejects every upload unless the TikTok ACCOUNT itself is private
(unaudited_client_can_only_post_to_private_accounts) - even with
privacy_level=SELF_ONLY. Public-channel accounts therefore use DRAFT mode by
default: the video goes to the dedicated inbox endpoint and lands in the
authorized user's TikTok inbox for a one-tap manual post with any privacy
chosen in the app. Only after the app passes TikTok's audit AND the repo
variable TIKTOK_PRIVACY_STATUS=PUBLIC_TO_EVERYONE is set does upload_video
switch to fully automatic direct posting (requires video.publish in scope).
"""
import os
import re
import time

import requests

from . import config

TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
INBOX_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

CHUNK = 64 * 1024 * 1024          # API cap: one chunk <= 64 MB
CAPTION_CAP = 2200                # TikTok caption limit
VERIFY_TIMEOUT_MIN = 20

DONE_STATUSES = {"PUBLISH_COMPLETE", "SEND_TO_INBOX", "SELF_ONLY_VISIBLE"}

_token_cache: dict = {}
_scope_cache: dict = {"val": ""}   # granted scope from the last refresh


def enabled() -> bool:
    return bool(config.TIKTOK_CLIENT_KEY and config.TIKTOK_CLIENT_SECRET
                and config.TIKTOK_REFRESH_TOKEN)


def build_caption(title: str, tags: list) -> str:
    tags_out = "#" + " #".join(re.sub(r"\W+", "", t) for t in tags[:5] if re.sub(r"\W+", "", t))
    return f"{title.strip()}\n\n{tags_out}".strip()[:CAPTION_CAP]


def _api_error(resp) -> str:
    """TikTok's error body (code/message/log_id) - a bare HTTP code is useless for debugging."""
    try:
        e = (resp.json() or {}).get("error") or {}
        if e:
            return f"{e.get('code')}: {e.get('message')} (log_id={e.get('log_id')})"
    except Exception:  # noqa: BLE001
        pass
    return resp.text[:300]


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
    print(f"[tiktok] token OK (granted scope: {j.get('scope', 'unknown')})")
    _scope_cache["val"] = j.get("scope", "")
    if j.get("refresh_token") and j["refresh_token"] != config.TIKTOK_REFRESH_TOKEN:
        print("[tiktok] WARNING: TikTok rotated your refresh token - update the "
              "TIKTOK_REFRESH_TOKEN GitHub secret with this value:\n"
              f"{j['refresh_token']}")
    _token_cache["tok"] = j["access_token"]
    _token_cache["exp"] = time.time() + int(j.get("expires_in", 86400)) - 600
    return _token_cache["tok"]


def upload_video(path: str, caption: str) -> str:
    """Chunked upload of a local video file.

    Default -> DRAFT mode: the video goes to the dedicated inbox endpoint
    and lands in the authorized user's TikTok inbox for one-tap posting
    from the app (the caption cannot be set via API on inbox uploads);
    returns the sentinel 'DRAFT_INBOX'. This is the only mode that works
    for unaudited apps with a public account.
    TIKTOK_PRIVACY_STATUS=PUBLIC_TO_EVERYONE + video.publish in scope ->
    direct post instead (audited apps only; returns a publish_id).
    """
    tok = _access_token()
    scope = _scope_cache.get("val", "")
    if "video.publish" not in scope and "video.upload" not in scope:
        raise RuntimeError(f"TikTok token scope has no video permission ({scope or 'unknown'} - "
                           "re-run the token cell)")
    # Route: draft-inbox by default; direct post only when explicitly opted
    # in post-audit. (Scope-only auto-detect sent this token - which still
    # carries video.publish from earlier consent grants - to the direct-post
    # endpoint, where every unaudited + public-account attempt 403s.)
    want_public = config.TIKTOK_PRIVACY_STATUS == "PUBLIC_TO_EVERYONE"
    if want_public and "video.publish" not in scope:
        print("[tiktok] direct post requested but token lacks video.publish "
              "-> falling back to draft upload")
        want_public = False
    draft = not want_public
    size = os.path.getsize(path)
    chunks = max(1, (size + CHUNK - 1) // CHUNK)
    chunk = (size + chunks - 1) // chunks   # equal chunks, last takes the remainder
    print(f"[tiktok] {'draft to inbox' if draft else 'direct post'}: {os.path.basename(path)} "
          f"({size / 1e6:.1f} MB, {chunks} chunk(s) x {chunk / 1e6:.1f} MB"
          + ("" if draft else f", privacy={config.TIKTOK_PRIVACY_STATUS}") + ")")

    srcinfo = {
        "source": "FILE_UPLOAD",
        "video_size": size,
        "chunk_size": chunk,
        "total_chunk_count": chunks,
    }
    if draft:
        # Drafts have a DEDICATED endpoint that takes source_info ONLY (no
        # title, no privacy - the caption is added in the TikTok app). Sending
        # a draft to INIT_URL (direct post) is what 403'd for unaudited apps.
        init_url, init_body = INBOX_INIT_URL, {"source_info": srcinfo}
    else:
        init_url, init_body = INIT_URL, {
            "post_info": {"title": caption.strip()[:CAPTION_CAP],
                          "privacy_level": config.TIKTOK_PRIVACY_STATUS},
            "source_info": srcinfo,
        }
    init = requests.post(init_url, headers={
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json; charset=UTF-8",
    }, json=init_body, timeout=60)
    if init.status_code >= 400:
        raise RuntimeError(f"TikTok init HTTP {init.status_code}: {_api_error(init)}")
    ij = init.json()
    err = ij.get("error") or {}
    if err.get("code", "ok") != "ok":
        raise RuntimeError(f"TikTok init failed ({err.get('code')}): {err.get('message')}")
    publish_id = (ij.get("data") or {}).get("publish_id")
    upload_url = (ij.get("data") or {}).get("upload_url")
    if not upload_url:
        raise RuntimeError(f"TikTok init gave no upload_url: {ij}")

    mime = "video/mp4" if path.endswith(".mp4") else "video/webm"
    sent = 0
    with open(path, "rb") as f:
        for i in range(chunks):
            data = f.read(chunk)
            end = sent + len(data) - 1
            put = requests.put(upload_url, data=data, headers={
                "Content-Type": mime,
                "Content-Range": f"bytes {sent}-{end}/{size}",
            }, timeout=600)
            if put.status_code >= 400:
                raise RuntimeError(f"TikTok chunk {i + 1}/{chunks} HTTP {put.status_code}: "
                                   f"{_api_error(put)}")
            sent += len(data)
            print(f"[tiktok] chunk {i + 1}/{chunks} done ({sent / 1e6:.0f}/{size / 1e6:.0f} MB)")

    if draft or not publish_id:
        print("[tiktok] video uploaded to the TikTok INBOX as a draft - open the")
        print("        TikTok app (Inbox notification), add the caption + cover,")
        print("        then tap Post - inbox uploads cannot set a caption via API")
        return "DRAFT_INBOX"
    _wait_processed(tok, publish_id)
    return publish_id


def _wait_processed(tok: str, publish_id: str) -> None:
    deadline = time.time() + VERIFY_TIMEOUT_MIN * 60
    while time.time() < deadline:
        r = requests.post(STATUS_URL, headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json; charset=UTF-8",
        }, json={"publish_id": publish_id}, timeout=60)
        if r.status_code >= 400:
            print(f"[tiktok] status fetch HTTP {r.status_code}: {_api_error(r)}")
            time.sleep(30)
            continue
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