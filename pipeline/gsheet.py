"""Google Sheets backend: edit the queue in a browser, zero git commits.

Set the GOOGLE_SHEET_ID secret (the long ID inside the sheet's URL) and the
pipeline uses that live web sheet instead of videos.xlsx:
- reads the queue at the start of every scheduled run (fresh from the cloud)
- writes status / links / attempts straight back into the sheet as it works
- green-highlights a row ONLY after YouTube reports BOTH videos processed
  (same double-check rule as the xlsx backend)

Requires the refresh token to include the Sheets scope - the updated
get_token.py / Colab notebook already request it. If the token predates that,
Google rejects the refresh with invalid_scope; re-run the token notebook once.
"""
from datetime import datetime, timezone

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from . import config
from .sheet import HEADERS, Sheet as XlsxSheet

LONG_RESUMABLE = XlsxSheet.LONG_RESUMABLE
SHORT_RESUMABLE = XlsxSheet.SHORT_RESUMABLE

TOKEN_URI = "https://oauth2.googleapis.com/token"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

GREEN = {"red": 0.776, "green": 0.937, "blue": 0.808}   # C6EFCE - same as xlsx
RED = {"red": 1.0, "green": 0.780, "blue": 0.808}       # FFC7CE
WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}


def _a1(col: int) -> str:
    """1-based column number -> A, B, ... AA."""
    letters = ""
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


class GSheet:
    """Drop-in replacement for pipeline.sheet.Sheet, backed by Google Sheets."""

    def __init__(self, sheet_id: str | None = None, service=None):
        self.sheet_id = (sheet_id or config.GOOGLE_SHEET_ID or "").strip()
        if not self.sheet_id:
            raise RuntimeError("GOOGLE_SHEET_ID is not set (add it as a repo secret / env var)")
        self.svc = service or self._service()

        meta = self.svc.spreadsheets().get(spreadsheetId=self.sheet_id).execute()
        first = meta["sheets"][0]["properties"]
        self.tab = first["title"]
        self.tab_id = first["sheetId"]

        raw = (self.svc.spreadsheets().values()
               .get(spreadsheetId=self.sheet_id, range=f"'{self.tab}'!A1:Z5000")
               .execute() or {}).get("values", [])
        width = max([len(HEADERS)] + [len(r) for r in raw])
        self._grid = [list(r) + [""] * (width - len(r)) for r in raw]
        self._validate_headers()
        print(f"[gsheet] connected to '{self.tab}' ({len(self._grid) - 1} rows)")

    # ---- auth ----
    def _service(self):
        if not (config.YT_CLIENT_ID and config.YT_CLIENT_SECRET and config.YT_REFRESH_TOKEN):
            raise RuntimeError("Missing YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN secrets")
        creds = Credentials(
            token=None,
            refresh_token=config.YT_REFRESH_TOKEN,
            client_id=config.YT_CLIENT_ID,
            client_secret=config.YT_CLIENT_SECRET,
            token_uri=TOKEN_URI,
            scopes=[SHEETS_SCOPE],
        )
        try:
            creds.refresh(GoogleRequest())
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"Could not refresh Google token ({e}). If the error mentions invalid_scope, "
                "your YT_REFRESH_TOKEN predates the Sheets scope: re-run the get_token Colab "
                "notebook (it now includes Sheets access) and update the YT_REFRESH_TOKEN secret."
            ) from e
        return build("sheets", "v4", credentials=creds)

    # ---- structure ----
    def _validate_headers(self):
        row1 = [str(c).strip() for c in (self._grid[0] if self._grid else [])]
        missing = [h for h in HEADERS if h not in row1]
        if missing:
            raise ValueError(
                f"Google Sheet is missing columns {missing}. "
                f"Row 1 must contain exactly: {HEADERS}"
            )

    def _c(self, header: str) -> int:
        return next(i + 1 for i, c in enumerate(self._grid[0]) if str(c).strip() == header)

    # ---- read ----
    def rows(self):
        """Yield (row_number, values_dict) for every row with a URL, same as xlsx backend."""
        for r in range(2, len(self._grid) + 1):
            url = str(self._grid[r - 1][self._c("URL") - 1] or "").strip()
            if url.startswith("http"):
                yield r, {
                    "url": url,
                    "title_override": self._get(r, "Title override (optional)"),
                    "short_ts": self._get(r, "Short timestamp (optional)"),
                    "status": self._get(r, "Status"),
                    "long_link": self._get(r, "Long video link"),
                    "short_link": self._get(r, "Short link"),
                    "attempts": self._get(r, "Attempts") or 0,
                }

    def _get(self, row: int, header: str):
        return self._grid[row - 1][self._c(header) - 1]

    def _status(self, row: int) -> str:
        return str(self._get(row, "Status") or "").strip().lower()

    # ---- write ----
    def set(self, row: int, header: str, value):
        """Write one cell immediately (live persistence = same crash-resume as xlsx)."""
        col = self._c(header)
        self._grid[row - 1][col - 1] = "" if value is None else value
        self.svc.spreadsheets().values().update(
            spreadsheetId=self.sheet_id,
            range=f"'{self.tab}'!{_a1(col)}{row}",
            valueInputOption="RAW",
            body={"values": [["" if value is None else value]]},
        ).execute()

    def save(self):
        """No-op: every set() is already live in the cloud sheet."""

    def _fill_row(self, row: int, rgb: dict | None):
        self.svc.spreadsheets().batchUpdate(spreadsheetId=self.sheet_id, body={
            "requests": [{
                "repeatCell": {
                    "range": {
                        "sheetId": self.tab_id,
                        "startRowIndex": row - 1, "endRowIndex": row,
                        "startColumnIndex": 0, "endColumnIndex": len(HEADERS),
                    },
                    "cell": {"userEnteredFormat": {"backgroundColor": rgb or WHITE}},
                    "fields": "userEnteredFormat.backgroundColor",
                }
            }]
        }).execute()

    # ---- status helpers (identical semantics to xlsx backend) ----
    def next_pending(self):
        for r, v in self.rows():
            if self._status(r) in {x.lower() for x in LONG_RESUMABLE}:
                return r, v
        return None, None

    def next_short_pending(self):
        for r, v in self.rows():
            if self._status(r) in {x.lower() for x in SHORT_RESUMABLE}:
                return r, v
        return None, None

    def bump_attempts(self, row: int) -> int:
        n = int(self._get(row, "Attempts") or 0) + 1
        self.set(row, "Attempts", n)
        return n

    def set_status(self, row: int, text: str):
        self.set(row, "Status", text)
        self._fill_row(row, None)  # clear old green/red fill

    def mark_verified(self, row: int, long_link: str, short_link: str):
        """100% confirmed (YouTube reports 'processed') -> green-highlight the whole row."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self.set(row, "Status", "✅ verified")
        self.set(row, "Long video link", long_link)
        if short_link:
            self.set(row, "Short link", short_link)
        self.set(row, "Verified at (UTC)", now)
        self.set(row, "Error / notes", "")
        self._fill_row(row, GREEN)

    def mark_failed(self, row: int, error: str):
        self.set(row, "Status", "❌ failed")
        self.set(row, "Error / notes", str(error)[:500])
        self._fill_row(row, RED)
