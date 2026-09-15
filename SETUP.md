# SETUP — Brutal Reality automation (one-time, ~40 minutes)

This guide connects the pipeline you have to YouTube and GitHub. Do the steps
in order. After step 7 the system runs itself forever — you only paste URLs
into the control sheet (a live **Google Sheet** — recommended — or `videos.xlsx`).

---

## 0. What you need

- A GitHub account (free)
- A Google account that owns your YouTube channel (free)
- Your Groq API key (create at https://console.groq.com/keys — use a FRESH key)

---

## 1. Create the GitHub repo (5 min)

1. Go to github.com → **New repository** → name it e.g. `yt-automation`
2. Set it to **Public** (public = unlimited free Actions minutes)
3. Do NOT initialize with README
4. Upload all files from this folder (drag & drop works, or use GitHub Desktop).
   Keep the folder structure exactly as-is (`pipeline/`, `.github/`, `videos.xlsx`, ...).
   - Do NOT upload the `output/` folder (it's in .gitignore anyway)

## 2. Google Cloud + YouTube API (15 min)

1. Go to https://console.cloud.google.com/ → create a project (name: `brutal-reality-uploader`)
2. **APIs & Services → Library** → search **"YouTube Data API v3"** → **Enable**
3. **APIs & Services → OAuth consent screen**:
   - User type: **External** → Create
   - App name: `Brutal Reality uploader`, your email, done
   - **Audience → Publishing status → "In production"** ← IMPORTANT:
     leaving it in "Testing" makes your token expire every 7 days and
     breaks automation. Publishing to production fixes that (Google shows
     an "unverified app" warning during one consent — that's fine, it's your own app).
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Desktop app**
   - Copy the **Client ID** and **Client Secret** (you'll paste them in step 3)

## 3. Get the refresh token (10 min)

**Option A — locally (if you have Python installed):**
```
pip install google-auth-oauthlib
python get_token.py
```

**Option B — zero install (Google Colab):**
1. Open https://colab.research.google.com → New notebook
2. Paste the entire contents of `get_token.py` into one cell, but replace the
   two `input()` lines with your real values:
   ```python
   client_id = "PASTE_CLIENT_ID_HERE"
   client_secret = "PASTE_CLIENT_SECRET_HERE"
   ```
3. Run the cell → a link appears → open it → choose your channel's Google
   account → approve (click through the "unverified app" warning:
   Advanced → Go to app) → paste the redirect URL back into Colab when asked
4. It prints three values. Keep the page open.

The token covers **YouTube upload + Google Sheets** access (the Sheets part
powers the no-commit queue in step 4b). If you generated a token before this
scope was added, just re-run the notebook once and update the secret.

## 4. Put the secrets into GitHub (3 min)

Repo → **Settings → Secrets and variables → Actions → New repository secret**.
Add these four secrets (Names must match exactly):

| Name | Value |
|---|---|
| `GROQ_API_KEY` | your fresh Groq key |
| `YT_CLIENT_ID` | from step 2/3 |
| `YT_CLIENT_SECRET` | from step 2/3 |
| `YT_REFRESH_TOKEN` | the long token printed in step 3 |

## 4b. (Recommended) Control the queue from a Google Sheet — no commits

Skip git entirely: the bot reads a live web sheet at every run and writes
status + the green highlight straight back into it. You just paste URLs in
your browser.

1. Go to https://sheets.new (logged in as the **channel's** Google account)
   → name it e.g. `YT Queue`
2. Row 1 must contain these 9 headers exactly (easiest: **File → Import →
   Upload → `sheet_template.csv`** from this folder → *Replace current sheet*):
   ```
   URL | Title override (optional) | Short timestamp (optional) | Status | Long video link | Short link | Verified at (UTC) | Attempts | Error / notes
   ```
3. Copy the **sheet ID** from its URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_LONG_PART`**`/edit`
4. Add it as a new GitHub secret: `GOOGLE_SHEET_ID`
5. Paste your Commons URLs into column A, one per row. Done — no commits, ever.
   The bot fills Status/links and green-highlights verified rows in the web sheet.

Notes:
- The sheet must live in (or be shared as **Editor** with) the same Google
  account you generated the refresh token with.
- Without `GOOGLE_SHEET_ID` the bot falls back to `videos.xlsx` + git commits,
  so both modes stay available.

## 5. First test — dry run (5 min)

Repo → **Actions** tab → enable workflows if asked →
select **daily-long-upload** → **Run workflow** → tick **dry_run** → **Run**.

Watch the run turn green. Then run **daily-short-upload** the same way.

What dry run does: downloads your video, transcribes/picks the best moment,
renders the vertical Short — but skips the actual YouTube upload. The sheet
status column will show `🧪 dry-run ...`.

**Before the first real run**: open `videos.xlsx`, clear the Status cell of
the test row (or set it to `pending`), commit.

## 6. First real upload (5 min)

Run **daily-long-upload** without dry_run. The upload lands as **private**
(see step 7) — check your channel, confirm the video looks right.

## 7. The YouTube API audit (10 min, free — makes uploads public automatically)

While your app is un-audited, YouTube locks API uploads to **private**.
To lift that:

1. Go to https://support.google.com/youtube/contact/yt_api_form
   (YouTube API Services - Audit and Quota Extension Form)
2. Fill in: your Google Cloud project ID, that you use `youtube.upload` scope,
   link your GitHub repo as a demo, explain: *"Personal automation that
   redistributes public-domain / freely-licensed videos from Wikimedia Commons
   with full attribution, 2 uploads per day to my own channel."*
   Also request a quota extension to 15,000+ units/day if you plan to scale.
3. Approval usually takes days to a few weeks. Until then either flip videos
   public manually (one click) or wait it out.
4. After approval: in repo, set variable `YT_PRIVACY_STATUS` = `public`
   (Settings → Secrets and variables → Actions → **Variables** tab) — or edit
   `pipeline/config.py`. Done — fully hands-off.

## 8. Daily use (the only thing you ever do)

**Google Sheet mode (step 4b):** open the web sheet → type a Wikimedia Commons
file URL in the next empty row → walk away. Nothing to commit or sync.

**videos.xlsx mode:** open `videos.xlsx` → add a row → commit.

- **16:00 UTC** (12 PM New York / 21:45 Kathmandu) — downloads + uploads the long video
- **23:00 UTC** (7 PM New York prime time / 04:45 Kathmandu) — cuts + uploads the Short,
  verifies BOTH are 100% processed, then green-highlights the row ✅

Columns: `URL` (required) · `Title override` (optional) · `Short timestamp`
(optional, e.g. `5:00-5:38` — always wins over AI picking) · everything else
is filled by the bot.

## Status legend

| Status | Meaning |
|---|---|
| *(empty) / pending* | queued, will be picked up |
| ⏬ downloading | long slot working on it |
| 🟡 long uploaded - short queued | long is live, Short comes at 23:00 UTC |
| ✂️ building short | cutting/captioning the Short |
| 🔎 verifying uploads | YouTube is processing; polling until 100% |
| ✅ verified (green row) | YouTube confirmed BOTH videos fully processed |
| ❌ failed (red row) | failed 3 attempts — see Error column, fix, set back to pending |

## Quick checks

**Is my Groq key alive?**
```
curl -s https://api.groq.com/openai/v1/models -H "Authorization: Bearer YOUR_KEY"
```
A JSON model list = working. If this works on YOUR machine but the GitHub run
shows Groq errors, the key is fine and something else is wrong (check the log).

**Nothing ran today?** Actions tab → check the workflow wasn't auto-disabled
(GitHub disables schedules after 60 days of repo inactivity — any commit, even
adding a video row, keeps it alive).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Run red at metadata step | Commons briefly throttling; it retries automatically next run |
| `403 Forbidden` from Groq in Actions | key revoked/typo — make a fresh one, update the secret |
| Upload stuck private | expected pre-audit (step 7) |
| Row stuck on a transient status | rerun the workflow manually — stalled rows auto-resume |
| `invalid_scope` when refreshing token | token predates Sheets scope — re-run the get_token notebook, update `YT_REFRESH_TOKEN` |
| `PERMISSION_DENIED` / 403 on the sheet | sheet is in a different Google account — share it as **Editor** with the token's account |
| `Google Sheet is missing columns ...` | row 1 must have the exact 9 headers (import `sheet_template.csv`) |
| Short looks bad (subject off-center) | fill `Short timestamp` manually, or set repo variable `SHORT_STYLE`=`crop` for full-bleed center crop |

## Cost: $0/month

GitHub Actions (public repo, unlimited minutes) · YouTube Data API (free) ·
Groq free tier · Wikimedia hosting. When the channel earns, the same scripts
run unchanged on any $5 VPS.
