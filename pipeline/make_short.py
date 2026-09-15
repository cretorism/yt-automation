"""Turns a (start,end) window of the source video into a vertical 1080x1920 Short."""
import json
import os
import subprocess

from . import config


def _probe(path: str) -> dict:
    p = subprocess.run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", path,
    ], capture_output=True, text=True)
    return json.loads(p.stdout or "{}")


def has_audio(path: str) -> bool:
    info = _probe(path)
    return any(s.get("codec_type") == "audio" for s in info.get("streams", []))


def _video_filter(style: str, captions: str | None) -> str:
    if style == "crop":
        chain = ("[0:v]crop=w='ih*9/16':h=ih:x='(iw-ow)/2':y=0,"
                 "scale=1080:1920[v]")
    else:  # blurred background (default) - keeps the full frame visible
        # downscale -> blur -> upscale: visually identical, ~16x faster than full-res blur
        chain = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=270:480:force_original_aspect_ratio=increase,"
            "crop=270:480,gblur=sigma=6,scale=1080:1920,eq=brightness=-0.06[bgb];"
            "[fg]scale=1080:-2[fgs];"
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2[v]"
        )
    if captions:
        chain = chain.replace("[v]", f"[vf];[vf]subtitles={captions}[vout]")
        return chain
    return chain.replace("[v]", "[vout]")


def make_short(src: str, start: float, end: float, out_dir: str,
               style: str | None = None, captions_path: str | None = None) -> str:
    """Renders output/short.mp4 and validates it. Raises on failure."""
    style = style or config.SHORT_STYLE
    dur = max(1.0, end - start)
    dur = min(dur, config.SHORT_HARD_CAP)
    out = os.path.join(out_dir, "short.mp4")
    cap_name = os.path.basename(captions_path) if captions_path else None

    cmd = ["ffmpeg", "-y", "-threads", "0", "-ss", f"{start:.2f}", "-i", src, "-t", f"{dur:.2f}"]
    if not has_audio(src):
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    cmd += [
        "-filter_complex", _video_filter(style, cap_name),
        "-map", "[vout]", "-map", "0:a" if has_audio(src) else "1:a",
        "-c:v", "libx264", "-crf", "21", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:a", "aac", "-b:a", "160k", "-shortest",
        "-movflags", "+faststart", out,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       cwd=os.path.dirname(captions_path) if captions_path else ".")
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg short render failed:\n{p.stderr[-1200:]}")

    info = _probe(out)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        raise RuntimeError("Rendered short has no video stream")
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    fdur = float(info.get("format", {}).get("duration", 0))
    if (w, h) != (1080, 1920):
        raise RuntimeError(f"Wrong dimensions {w}x{h}, expected 1080x1920")
    if fdur > config.SHORT_HARD_CAP + 1.5:
        raise RuntimeError(f"Short too long: {fdur:.1f}s")
    print(f"[make_short] OK: {out} ({w}x{h}, {fdur:.1f}s, style={style})")
    return out
