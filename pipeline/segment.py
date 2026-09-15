"""Finds the best 20-45s window for a Short.

Priority order:
  1. Manual override from the Excel column (always wins)
  2. Groq: Whisper transcript -> LLM picks the most gripping self-contained window
  3. Fallback (no Groq / API down): audio-energy + scene-cut heuristic
Also builds styled .ass captions from the transcript when available.
"""
import json
import os
import re
import subprocess

import requests

from . import config

GROQ_BASE = "https://api.groq.com/openai/v1"


# ---------------------------------------------------------------- audio prep
def extract_audio(src: str, out_dir: str) -> list[tuple[str, float]]:
    """Extract 16k mono mp3; chunk in 18-min pieces. Returns [(path, offset_seconds)]."""
    path = os.path.join(out_dir, "audio.mp3")
    subprocess.run([
        "ffmpeg", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
        "-b:a", "64k", path,
    ], check=True, capture_output=True)
    size = os.path.getsize(path)
    if size <= 24_000_000:
        return [(path, 0.0)]
    # split into 18-min chunks
    subprocess.run([
        "ffmpeg", "-y", "-i", path, "-f", "segment", "-segment_time", "1080",
        "-c", "copy", os.path.join(out_dir, "audio_%03d.mp3"),
    ], check=True, capture_output=True)
    os.remove(path)
    chunks = sorted(f for f in os.listdir(out_dir) if f.startswith("audio_") and f.endswith(".mp3"))
    return [(os.path.join(out_dir, c), i * 1080.0) for i, c in enumerate(chunks)]


# ---------------------------------------------------------------- Groq calls
def _groq_post(url: str, **kwargs) -> requests.Response:
    for attempt in range(1, 3):  # max 2 attempts - failures fall back to heuristics anyway
        try:
            r = requests.post(
                url, headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                timeout=120, **kwargs,
            )
            if r.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"Groq {r.status_code}: {r.text[:200]}")
            return r
        except requests.RequestException as e:
            if attempt == 2:
                raise
            print(f"[groq] attempt {attempt} failed ({e}), retrying...")
            import time; time.sleep(4 * attempt)


def groq_transcribe(src: str, out_dir: str) -> list[dict] | None:
    """Whisper via Groq. Returns [{'start':s,'end':s,'text':...}] or None on failure."""
    if not config.GROQ_API_KEY:
        print("[segment] no GROQ_API_KEY -> heuristic mode")
        return None
    segments: list[dict] = []
    try:
        for path, offset in extract_audio(src, out_dir):
            with open(path, "rb") as f:
                r = _groq_post(
                    f"{GROQ_BASE}/audio/transcriptions",
                    files={"file": (os.path.basename(path), f)},
                    data={"model": config.GROQ_ASR_MODEL, "response_format": "verbose_json"},
                )
            if r.status_code != 200:
                raise RuntimeError(f"Groq ASR {r.status_code}: {r.text[:200]}")
            for seg in r.json().get("segments", []):
                segments.append({
                    "start": seg["start"] + offset,
                    "end": seg["end"] + offset,
                    "text": seg["text"].strip(),
                })
        if not segments:
            return None
        print(f"[segment] transcript OK: {len(segments)} segments")
        return segments
    except Exception as e:  # noqa: BLE001
        print(f"[segment] Groq transcription unavailable ({e}) -> heuristic mode")
        return None


# ---------------------------------------------------------------- override
def parse_override(val) -> tuple[float, float] | None:
    if not val:
        return None
    s = str(val).strip().lower().replace("–", "-").replace("—", "-")

    def to_sec(t: str) -> float:
        parts = [float(p) for p in t.strip().split(":")]
        sec = 0.0
        for p in parts:
            sec = sec * 60 + p
        return sec

    m = re.match(r"^([\d:.\s]+)-([\d:.\s]+)$", s)
    if m:
        a, b = to_sec(m.group(1)), to_sec(m.group(2))
        if b > a:
            return a, min(b, a + config.SHORT_HARD_CAP)
    m = re.match(r"^([\d:.]+)$", s)
    if m:
        a = to_sec(m.group(1))
        return a, a + min(30, config.SHORT_HARD_CAP)
    return None


# ---------------------------------------------------------------- LLM pick
def _candidates(segments: list[dict]) -> list[dict]:
    """Sentence-aligned sliding windows inside the length band."""
    out, n = [], len(segments)
    step = max(1, n // 40)  # cap prompt size
    for i in range(0, n, step):
        j = i
        text_parts = []
        while j < n:
            text_parts.append(segments[j]["text"])
            dur = segments[j]["end"] - segments[i]["start"]
            if dur >= config.SHORT_MIN_SEC:
                break
            j += 1
        if i >= n or j >= n:
            break
        dur = segments[j]["end"] - segments[i]["start"]
        if dur > config.SHORT_MAX_SEC:
            continue
        text = " ".join(text_parts)
        out.append({
            "start": round(segments[i]["start"], 1),
            "end": round(segments[j]["end"], 1),
            "text": text[:500],
        })
    return out


def llm_pick(segments: list[dict], duration: float) -> tuple[float, float, str] | None:
    cands = _candidates(segments)
    if not cands:
        return None
    listing = "\n".join(
        f"[{c['start']}-{c['end']}s] {c['text']}" for c in cands
    )
    prompt = (
        f"You program YouTube Shorts for '{config.CHANNEL_NAME}', a channel posting raw, "
        "gripping, real public-domain footage. From this transcript of a long video, choose "
        f"the ONE window that works best as a vertical Short: most gripping or shocking real "
        f"moment, immediately self-contained, strong first sentence as a hook, clean ending, "
        f"no context needed, length between {config.SHORT_MIN_SEC} and {config.SHORT_MAX_SEC} seconds.\n\n"
        f"Candidate windows (start-end seconds: transcript):\n{listing}\n\n"
        f"Video duration: {duration:.0f}s.\n"
        'Respond with JSON only: {"start": <sec>, "end": <sec>, "reason": "<max 15 words>"}'
    )
    try:
        r = _groq_post(
            f"{GROQ_BASE}/chat/completions",
            json={
                "model": config.GROQ_LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,
                "max_tokens": 200,
            },
        )
        if r.status_code != 200:
            raise RuntimeError(f"Groq LLM {r.status_code}: {r.text[:200]}")
        data = json.loads(re.sub(r"^```json|```$", "", r.json()["choices"][0]["message"]["content"].strip()))
        start, end = float(data["start"]), float(data["end"])
        if 0 <= start < end <= duration + 1 and (end - start) >= config.SHORT_MIN_SEC * 0.7:
            end = min(end, start + config.SHORT_HARD_CAP, duration)
            print(f"[segment] LLM picked {start:.1f}-{end:.1f}s ({data.get('reason', '')})")
            return start, end, str(data.get("reason", ""))[:80]
        print(f"[segment] LLM window out of bounds: {start}-{end}")
    except Exception as e:  # noqa: BLE001
        print(f"[segment] LLM pick failed ({e}) -> heuristic mode")
    return None


# ---------------------------------------------------------------- heuristic
def _energy_per_second(src: str) -> dict[float, float]:
    p = subprocess.run(
        ["ffmpeg", "-i", src, "-vn", "-af",
         "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=300)
    # ffmpeg prints 'frame:N pts:T pts_time:S' and 'lavfi.astats.Overall.RMS_level=V'
    # on CONSECUTIVE lines - pair them up
    per_sec: dict[float, list[float]] = {}
    current_t = None
    for line in p.stdout.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            current_t = float(m.group(1))
            continue
        v = re.search(r"RMS_level=(-?[\d.]+|-inf)", line)
        if v and current_t is not None:
            val = v.group(1)
            per_sec.setdefault(int(current_t), []).append(-90.0 if val == "-inf" else float(val))
            current_t = None
    return {t: sum(vals) / len(vals) for t, vals in per_sec.items()}


def _scene_cuts(src: str, timeout_s: int = 150) -> set[float]:
    """Scene-cut timestamps. Decode can be slow on big VP9 files, so we cap it:
    if it can't finish in time we return what we have (empty set = energy-only pick)."""
    try:
        p = subprocess.run(
            ["ffmpeg", "-i", src, "-an", "-filter:v",
             "scale=320:-2,select='gt(scene,0.35)',showinfo", "-f", "null", "-"],
            capture_output=True, text=True, timeout=timeout_s)
        return {float(m.group(1)) for m in re.finditer(r"pts_time:([\d.]+)", p.stderr)}
    except subprocess.TimeoutExpired:
        print(f"[segment] scene detection exceeded {timeout_s}s -> skipping (energy-only)")
        return set()


def heuristic_pick(src: str, duration: float) -> tuple[float, float]:
    energy = _energy_per_second(src)
    if not energy:
        start = max(0.0, duration / 2 - 15)
        return start, min(duration, start + 30)
    cuts = _scene_cuts(src)
    win = min(30.0, max(config.SHORT_MIN_SEC, duration - 6))
    times = sorted(energy)
    best_s, best_score = None, -1e9
    for s in range(0, max(1, int(duration - win - 2))):
        window = [energy[t] for t in times if s <= t < s + win]
        if len(window) < win * 0.5:
            continue
        score = sum(window) / len(window)
        if any(abs(s - c) <= 1.5 for c in cuts):
            score += 3  # prefer starting on a clean cut
        if s < 5:
            score -= 2  # skip intro flashes
        if score > best_score:
            best_s, best_score = s, score
    start = float(best_s or 0)
    end = min(start + win, duration - 0.5)
    print(f"[segment] heuristic picked {start:.0f}-{end:.0f}s")
    return start, end


# ---------------------------------------------------------------- captions
def build_captions(segments: list[dict], start: float, end: float, out_path: str) -> str | None:
    inside = [s for s in segments if s["end"] > start + 0.2 and s["start"] < end - 0.2]
    if not inside:
        return None

    def esc(t: str) -> str:
        return t.replace("{", "(").replace("}", ")").replace("\n", " ").strip()

    lines = [
        "[Script Info]", "ScriptType: v4.00+",
        "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding",
        "Style: Cap,DejaVu Sans,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,4,2,2,60,60,460,1",
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for seg in inside:
        t0 = max(0.0, seg["start"] - start)
        t1 = min(end, seg["end"]) - start
        if t1 - t0 < 0.3:
            continue
        text = esc(seg["text"])
        if not text:
            continue

        def ts(t: float) -> str:
            h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
            return f"{h}:{m:02d}:{s:05.2f}"

        lines.append(f"Dialogue: 0,{ts(t0)},{ts(t1)},Cap,,0,0,0,,{text}")
    with open(out_path, "w") as f:
        f.write("\n".join(lines))
    return out_path


# ---------------------------------------------------------------- entry
def plan_short(src: str, out_dir: str, duration: float, override=None):
    """Returns dict(start, end, reason, captions_path, method)."""
    ov = parse_override(override)
    if ov:
        start, end = ov
        end = min(end, duration)
        print(f"[segment] using manual override {start}-{end}s")
        segments = groq_transcribe(src, out_dir) if config.GROQ_API_KEY else None
        caps = None
        if segments:
            caps = build_captions(segments, start, end, os.path.join(out_dir, "captions.ass"))
        return {"start": start, "end": end, "reason": "manual override",
                "captions_path": caps, "method": "override"}

    segments = groq_transcribe(src, out_dir)
    if segments:
        picked = llm_pick(segments, duration)
        if picked:
            start, end, reason = picked
            caps = build_captions(segments, start, end, os.path.join(out_dir, "captions.ass"))
            return {"start": start, "end": end, "reason": reason,
                    "captions_path": caps, "method": "ai"}
    start, end = heuristic_pick(src, duration)
    return {"start": start, "end": end, "reason": "audio-energy heuristic",
            "captions_path": None, "method": "heuristic"}
