"""Orchestrator: python -m pipeline.main --slot long|short [--dry-run]

Slot 'long'  : pick first 'pending' row -> download -> upload long video -> mark short_pending
Slot 'short' : pick first 'short_pending' row -> re-download -> cut Short -> upload -> verify BOTH
               -> green-highlight the row only when YouTube reports everything processed.
Run by GitHub Actions twice a day (16:00 UTC long / 23:00 UTC short).
"""
import argparse
import os
import subprocess
import traceback

from . import commons, config, segment, sheet as sheetmod, upload_yt, make_short


def _git_commit_sheet():
    """Commit the updated videos.xlsx back to the repo when running inside Actions."""
    if os.getenv("GITHUB_ACTIONS") != "true":
        return
    subprocess.run(["git", "config", "user.email", "bot@users.noreply.github.com"], check=False)
    subprocess.run(["git", "config", "user.name", "yt-automation-bot"], check=False)
    subprocess.run(["git", "add", config.SHEET_PATH], check=False)
    subprocess.run(["git", "commit", "-m", "sheet status update [skip ci]"], check=False)
    subprocess.run(["git", "push"], check=False)


def _yt_link(vid: str) -> str:
    return f"https://youtube.com/watch?v={vid}"


def run_long_slot(sh: sheetmod.Sheet, dry: bool):
    row, v = sh.next_pending()
    if not row:
        print("[long] no pending rows - nothing to do")
        return
    print(f"[long] row {row}: {v['url']}")
    meta = commons.fetch_metadata(v["url"])
    sh.set_status(row, "⏬ downloading")
    sh.save()
    src = commons.download(v["url"], config.OUTPUT_DIR, name_hint=f"source_r{row}")

    if dry:
        sh.set_status(row, "🧪 dry-run: long skipped upload")
        sh.set(row, "Error / notes", f"downloaded OK: {os.path.basename(src)}; metadata OK ({meta['license']})")
        sh.save()
        return

    md = commons.build_long_metadata(meta, v["title_override"])
    vid = upload_yt.upload_video(src, md["title"], md["description"], md["tags"])
    sh.set(row, "Long video link", _yt_link(vid))
    sh.set_status(row, "🟡 long uploaded - short queued")
    sh.save()
    _git_commit_sheet()
    return src, meta, vid


def run_short_slot(sh: sheetmod.Sheet, dry: bool):
    row, v = sh.next_short_pending()
    if not row:
        print("[short] no short_pending rows - nothing to do")
        return
    print(f"[short] row {row}: {v['url']}")
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    meta = commons.fetch_metadata(v["url"])
    sh.set_status(row, "✂️ building short")
    sh.save()
    src = commons.download(v["url"], config.OUTPUT_DIR, name_hint=f"source_r{row}")

    # duration: trust Commons metadata, fall back to ffprobe
    duration = meta.get("duration") or 0
    if duration <= 0:
        import json as _json
        p = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", src],
                           capture_output=True, text=True)
        duration = float(_json.loads(p.stdout or "{}").get("format", {}).get("duration", 0))
    if duration < 8:
        raise RuntimeError(f"Video too short for a Short ({duration:.0f}s); skipping")

    plan = segment.plan_short(src, config.OUTPUT_DIR, duration, override=v["short_ts"])
    short_path = make_short.make_short(
        src, plan["start"], plan["end"], config.OUTPUT_DIR,
        captions_path=plan["captions_path"],
    )

    if dry:
        sh.set_status(row, "🧪 dry-run: short rendered, upload skipped")
        sh.set(row, "Error / notes",
               f"short OK {plan['start']:.0f}-{plan['end']:.0f}s via {plan['method']}")
        sh.save()
        return

    md = commons.build_long_metadata(meta, v["title_override"])
    s_title = (md["title"][:87] + " #Shorts").strip()
    s_desc = md["description"] + f"\n\nClip ({plan['reason']}) from the full video on this channel."
    s_vid = upload_yt.upload_video(short_path, s_title, s_desc, md["tags"])

    # --- 100% verification before the green light ---
    long_link = v["long_link"] or ""
    long_id = long_link.split("v=")[-1] if "v=" in long_link else None
    sh.set_status(row, "🔎 verifying uploads")
    sh.save()
    if long_id:
        upload_yt.wait_until_processed(long_id)
    upload_yt.wait_until_processed(s_vid)

    sh.mark_verified(row, _yt_link(long_id) if long_id else long_link, _yt_link(s_vid))
    sh.save()
    print(f"[short] row {row} ✅ VERIFIED (long + short processed by YouTube)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=["long", "short", "all"], default="all")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        config.DRY_RUN = True
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    sh = sheetmod.Sheet()
    if args.slot in ("long", "all"):
        row, _ = sh.next_pending()
        if row is not None:
            try:
                run_long_slot(sh, config.DRY_RUN)
            except Exception as e:  # noqa: BLE001
                _handle_failure(sh, row, e)
        else:
            print("[long] no pending rows")
    if args.slot in ("short", "all"):
        row, _ = sh.next_short_pending()
        if row is not None:
            try:
                run_short_slot(sh, config.DRY_RUN)
            except Exception as e:  # noqa: BLE001
                _handle_failure(sh, row, e)
        else:
            print("[short] no short_pending rows")

    sh.save()
    _git_commit_sheet()
    print("[main] sheet saved")


def _handle_failure(sh: sheetmod.Sheet, row, err: Exception):
    if not row:
        print(f"[error] {err}")
        traceback.print_exc()
        return
    attempts = sh.bump_attempts(row)
    if attempts >= config.MAX_ATTEMPTS:
        sh.mark_failed(row, f"{err}")
        print(f"[error] row {row} marked FAILED after {attempts} attempts: {err}")
    else:
        sh.set_status(row, "pending")  # retry next scheduled run
        sh.set(row, "Error / notes", f"attempt {attempts}/{config.MAX_ATTEMPTS}: {err}")
        print(f"[error] row {row} will retry (attempt {attempts}/{config.MAX_ATTEMPTS}): {err}")
    traceback.print_exc()


if __name__ == "__main__":
    main()
