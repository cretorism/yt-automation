# Brutal Reality — YouTube automation pipeline

Fully automated YouTube channel ops driven by an Excel sheet:

```
videos.xlsx (you add Commons URLs)
   │
   ├─ 16:00 UTC — daily-long.yml
   │     download ← Wikimedia Commons (direct URL, license-checked)
   │     fetch author/license from Commons API → auto-attribution
   │     upload long video (YouTube Data API, resumable)
   │     → status: short queued
   │
   └─ 23:00 UTC — daily-short.yml
         re-download (cached), Whisper-transcribe via Groq
         LLM picks the most gripping 20–45 s window (fallback: audio-energy + scene cuts)
         ffmpeg → 1080×1920 vertical, blurred background, burned-in captions
         upload as #Shorts
         poll YouTube until BOTH videos report processed = 100% verified
         → row green-highlighted ✅ in videos.xlsx (committed back to the repo)
```

## Files

| Path | Purpose |
|---|---|
| `videos.xlsx` | **The only file you touch** — add URLs, bot fills the rest |
| `pipeline/main.py` | Orchestrator (`--slot long` / `--slot short`, `--dry-run`) |
| `pipeline/sheet.py` | Excel read/write, status colors, green-verified logic |
| `pipeline/commons.py` | Commons metadata + attribution + safe downloads |
| `pipeline/segment.py` | Groq Whisper + LLM moment-picking, heuristic fallback |
| `pipeline/make_short.py` | ffmpeg 9:16 render (blurred-bg or crop) |
| `pipeline/upload_yt.py` | Resumable upload + processing verification |
| `pipeline/httpget.py` | requests→curl dual transport (Wikimedia-proof) |
| `.github/workflows/daily-*.yml` | The two daily cron jobs |
| `get_token.py` | One-time YouTube refresh-token generator |
| `SETUP.md` | **Start here** — full setup walkthrough |

## Guarantees

- Downloads only from `upload.wikimedia.org` (hard-blocked elsewhere)
- Full CC attribution auto-injected into every description
- A row turns green **only** after YouTube itself reports both videos processed
- Failed rows retry up to 3 times, then fail loudly in the sheet
- No Groq? Heuristic picking keeps the pipeline alive (no captions, dumber pick)
- Dry-run mode does everything except upload

See **SETUP.md** for the one-time setup (≈40 min).
