"""Wikimedia Commons: URL sanitizing, metadata (author/license) fetching, robust download."""
import os
import re
import time
import urllib.parse

from . import config, httpget

API = "https://commons.wikimedia.org/w/api.php"


def normalize_url(url: str) -> str:
    """Strip tracking params; enforce allowed host."""
    url = url.strip()
    clean = url.split("?")[0].split("#")[0]
    host = urllib.parse.urlparse(clean).netloc
    if not any(host == h or host.endswith("." + h) for h in config.ALLOWED_DOWNLOAD_HOSTS):
        raise ValueError(f"Refusing to download from non-Wikimedia host: {host}")
    return clean


def file_title_from_url(url: str) -> str:
    """https://upload.wikimedia.org/wikipedia/commons/c/ce/Foo.webm -> 'File:Foo.webm'"""
    name = urllib.parse.unquote(url.rstrip("/").split("/")[-1])
    return f"File:{name}"


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def _get_with_retry(url: str, params: dict = None, tries: int = 5):
    """GET with backoff (httpget tries requests then curl per attempt)."""
    return httpget.get(url, params=params, tries=tries)


def fetch_metadata(url: str) -> dict:
    """Title, author, license, dimensions, duration straight from the Commons API."""
    title = file_title_from_url(url)
    r = _get_with_retry(API, params={
        "action": "query", "format": "json",
        "titles": title, "prop": "imageinfo",
        "iiprop": "url|size|extmetadata|mime",
    })
    pages = r.json().get("query", {}).get("pages", {})
    if not pages:
        raise RuntimeError(f"Commons API returned no metadata for {title}")
    page = next(iter(pages.values()))
    if "imageinfo" not in page or not page["imageinfo"]:
        raise RuntimeError(f"Commons file not found or no imageinfo: {title}")
    ii = page["imageinfo"][0]
    em = ii.get("extmetadata", {})

    def emv(key):
        return _strip_html(em.get(key, {}).get("value", ""))

    return {
        "file_title": title,
        "url": ii.get("url", url),
        "mime": ii.get("mime", "video/webm"),
        "width": ii.get("width", 0),
        "height": ii.get("height", 0),
        "duration": float(ii.get("duration") or 0),
        "size": int(ii.get("size") or 0),
        "author": emv("Artist") or "Unknown author",
        "license": emv("LicenseShortName") or "See Commons",
        "commons_name": emv("ObjectName") or title.replace("File:", ""),
        "commons_description": emv("ImageDescription")[:300],
    }


def download(url: str, dest_dir: str, name_hint: str = "source") -> str:
    """Streamed download with retries; returns local path. Skips if already downloaded."""
    url = normalize_url(url)
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or ".webm"
    dest = os.path.join(dest_dir, f"{name_hint}{ext}")
    if os.path.exists(dest) and os.path.getsize(dest) > 1_000_000:
        print(f"[download] already exists, reusing {dest}")
        return dest
    httpget.download(url, dest, retries=config.DOWNLOAD_RETRIES)
    if os.path.getsize(dest) < 1_000_000:
        raise RuntimeError(f"Downloaded file suspiciously small ({os.path.getsize(dest)} bytes)")
    return dest


def build_long_metadata(meta: dict, title_override: str | None) -> dict:
    """Assemble YouTube title/description/tags with full CC attribution."""
    base_title = (title_override or "").strip() or meta["commons_name"]
    title = base_title[:100]

    desc = (
        f"{base_title}\n\n"
        f"📺 {config.CHANNEL_TAGLINE}\n\n"
        "── SOURCE & LICENSE ──────────────\n"
        f"• Footage: {meta['author']}\n"
        f"• License: {meta['license']} (via Wikimedia Commons)\n"
        f"• Original file: {meta['url']}\n"
        f"• File page: https://commons.wikimedia.org/wiki/{urllib.parse.quote(meta['file_title'].replace(' ', '_'))}\n"
    )
    if meta.get("commons_description"):
        desc += f"\nAbout this footage (from source): {meta['commons_description']}\n"
    desc += "\nThis channel redistributes public-domain / freely-licensed material with attribution."

    words = re.findall(r"[A-Za-z]{4,}", base_title)
    tags = list(dict.fromkeys([w.lower() for w in words]))[:12] + [
        "raw footage", "public domain", "documentary", config.CHANNEL_NAME.lower(),
    ]
    return {"title": title, "description": desc[:4900], "tags": tags}
