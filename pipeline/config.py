"""Central configuration - everything overridable via environment variables (GitHub Secrets)."""
import os

# ---- AI (Groq) ----
GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
GROQ_ASR_MODEL = os.getenv("GROQ_ASR_MODEL", "whisper-large-v3")
GROQ_LLM_MODEL = os.getenv("GROQ_LLM_MODEL", "llama-3.3-70b-versatile")

# ---- YouTube API ----
YT_CLIENT_ID     = os.getenv("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN", "")
CHANNEL_NAME     = os.getenv("CHANNEL_NAME", "Brutal Reality")
CHANNEL_TAGLINE  = os.getenv("CHANNEL_TAGLINE", "Raw, unfiltered public-domain footage. New video every day.")
CATEGORY_ID      = os.getenv("YT_CATEGORY_ID", "25")   # 25 = News & Politics
PRIVACY_STATUS   = os.getenv("YT_PRIVACY_STATUS", "private")  # flip to public after API audit

# ---- Shorts ----
SHORT_STYLE   = os.getenv("SHORT_STYLE", "blurred")    # blurred | crop
SHORT_MIN_SEC = int(os.getenv("SHORT_MIN_SEC", "20"))
SHORT_MAX_SEC = int(os.getenv("SHORT_MAX_SEC", "45"))
SHORT_HARD_CAP = int(os.getenv("SHORT_HARD_CAP", "59"))  # never exceed (keeps auto-Shorts detection)

# ---- Pipeline ----
DRY_RUN       = os.getenv("DRY_RUN", "0") == "1"
OUTPUT_DIR    = os.getenv("OUTPUT_DIR", "output")
SHEET_PATH    = os.getenv("SHEET_PATH", "videos.xlsx")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")  # set -> use live Google Sheet instead of videos.xlsx
MAX_ATTEMPTS  = int(os.getenv("MAX_ATTEMPTS", "3"))
VERIFY_TIMEOUT_MIN = int(os.getenv("VERIFY_TIMEOUT_MIN", "25"))  # poll window for YT processing
DOWNLOAD_RETRIES   = int(os.getenv("DOWNLOAD_RETRIES", "3"))

# ---- License safety: only ever pull from Wikimedia upload hosts ----
ALLOWED_DOWNLOAD_HOSTS = ("upload.wikimedia.org",)
