"""HTTP helpers: requests first, curl subprocess as fallback.

Wikimedia's edge sometimes TLS-fingerprint-blocks python-requests from
datacenter IPs; curl has a different fingerprint and is preinstalled on
GitHub Actions runners, so this dual path works everywhere.
"""
import os
import subprocess
import urllib.parse
from types import SimpleNamespace

import requests

UA = "BrutalReality-Automation/1.0 (automated channel pipeline; contact via channel)"


class CurlResponse(SimpleNamespace):
    """Minimal duck-typed stand-in for requests.Response."""

    def json(self):
        import json
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code} via curl: {self.text[:150]}")


def _full_url(url: str, params: dict | None) -> str:
    if not params:
        return url
    return f"{url}?{urllib.parse.urlencode(params)}"


def get(url: str, params: dict | None = None, tries: int = 5) -> CurlResponse:
    """GET with retries; requests first, curl fallback. Returns .status_code/.text/.json()."""
    full = _full_url(url, params)
    last_err = None
    for attempt in range(1, tries + 1):
        # 1) requests
        try:
            r = requests.get(full, headers={"User-Agent": UA}, timeout=(30, 300))
            if r.status_code == 200:
                return CurlResponse(status_code=200, text=r.text)
            last_err = RuntimeError(f"requests: HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            last_err = e
        # 2) curl
        try:
            p = subprocess.run(
                ["curl", "-sS", "--fail", "--max-time", "120", "-A", UA, full],
                capture_output=True, text=True, timeout=150,
            )
            if p.returncode == 0 and p.stdout:
                return CurlResponse(status_code=200, text=p.stdout)
            last_err = RuntimeError(f"curl rc={p.returncode}: {p.stderr[:150]}")
        except Exception as e:  # noqa: BLE001
            last_err = e
        print(f"[httpget] attempt {attempt}/{tries} failed: {last_err}")
        import time
        time.sleep(4 * attempt)
    raise RuntimeError(f"GET failed after {tries} attempts: {last_err}")


def download(url: str, dest: str, retries: int = 3) -> str:
    """Streamed download to dest with retries; curl preferred, requests fallback."""
    tmp = dest + ".part"
    last_err = None
    for attempt in range(1, retries + 1):
        # 1) curl (handles resume + big files well)
        p = subprocess.run(
            ["curl", "-sS", "--fail", "--retry", "2", "--retry-delay", "5",
             "--connect-timeout", "30", "--max-time", "900", "-L", "-A", UA, "-o", tmp, url],
            capture_output=True, text=True, timeout=960,
        )
        if p.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 1_000_000:
            os.replace(tmp, dest)
            return dest
        last_err = f"curl rc={p.returncode}: {p.stderr[:200]}"
        # 2) requests streaming
        try:
            with requests.get(url, headers={"User-Agent": UA}, stream=True, timeout=(30, 600)) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
            if os.path.getsize(tmp) > 1_000_000:
                os.replace(tmp, dest)
                return dest
            last_err = f"requests: too small ({os.path.getsize(tmp)} B)"
        except Exception as e:  # noqa: BLE001
            last_err = f"requests: {e}"
        print(f"[httpget] download attempt {attempt}/{retries} failed: {last_err}")
        import time
        time.sleep(5 * attempt)
    raise RuntimeError(f"Download failed after {retries} attempts: {last_err}")
