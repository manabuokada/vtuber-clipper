#!/usr/bin/env python3
"""
work/clips/<handle>/raw_clip.mp4 と subtitles.ass から、YouTube向け編集済み動画
（edited_clip.mp4: 1080p H.264 + 48kHz AAC -14 LUFS + 冒頭フレーム即表示）
およびサムネイル（thumbnail.jpg）を並列レンダリングするスクリプト。
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess

BASE_DIR = Path(__file__).resolve().parents[2]
CLIPS_DIR = BASE_DIR / "work" / "clips"


def get_duration(path: Path) -> float:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        return float(p.stdout.strip())
    except Exception:
        return 45.0


def get_jp_font() -> str:
    p = subprocess.run(["fc-match", "-f", "%{file}", "Noto Sans CJK JP:style=Black"], capture_output=True, text=True)
    return p.stdout.strip()


def render_one(clip_dir: Path, font_path: str) -> tuple[str, bool, str]:
    handle = clip_dir.name
    raw = clip_dir / "raw_clip.mp4"
    ass = clip_dir / "subtitles.ass"
    meta_path = clip_dir / "metadata.json"
    out = clip_dir / "edited_clip.mp4"
    thumb = clip_dir / "thumbnail.jpg"

    if not raw.exists() or not ass.exists():
        return handle, False, "missing raw_clip.mp4 or subtitles.ass"

    dur = get_duration(raw)
    fade_out_start = max(1.0, dur - 0.5)

    # Ensure 1920x1080 canvas so ASS PlayResX/Y aligns perfectly regardless of source resolution
    vf = f"scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,ass={ass.name},fade=t=out:st={fade_out_start:.2f}:d=0.5"
    af = f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,afade=t=out:st={fade_out_start:.2f}:d=0.5"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(raw),
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.0",
        "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p", "-g", "60",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
        "-loglevel", "warning",
    ]
    r = subprocess.run(cmd, cwd=str(clip_dir), capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        return handle, False, r.stderr[-300:]

    # Generate clean 1280x720 thumbnail from middle of clip with bold headline
    headline = handle
    if meta_path.exists():
        m = json.loads(meta_path.read_text(encoding="utf-8"))
        t = m.get("title", "")
        # Shorten headline cleanly for thumbnail
        headline = t.replace("【新人VTuber切り抜き】", "").replace("【新人ご当地VTuber切り抜き】", "").split("【")[0].strip()[:26]

    mid_s = max(2.0, dur * 0.45)
    safe_headline = headline.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    thumb_vf = (
        f"scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
        f"drawbox=x=0:y=0:w=1280:h=110:color=black@0.72:t=fill,"
        f"drawtext=fontfile={font_path}:expansion=none:text='{safe_headline}':fontcolor=#FFE600:fontsize=40:"
        f"borderw=4:bordercolor=#101010:x=(w-text_w)/2:y=34"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{mid_s:.2f}", "-i", str(raw), "-vframes", "1", "-vf", thumb_vf, "-q:v", "2", str(thumb), "-loglevel", "error"],
        capture_output=True,
    )

    size_mb = out.stat().st_size / (1024 * 1024)
    return handle, True, f"{size_mb:.1f} MB ({dur:.1f}s)"


def main():
    parser = argparse.ArgumentParser(description="切り抜き動画一括レンダリング")
    parser.add_argument("handles", nargs="*", help="対象ハンドル（省略時は全フォルダ）")
    args = parser.parse_args()

    if args.handles:
        dirs = [CLIPS_DIR / h.lstrip("@") for h in args.handles]
    else:
        dirs = sorted(p for p in CLIPS_DIR.iterdir() if p.is_dir())

    font = get_jp_font()
    with ThreadPoolExecutor(max_workers=4) as ex:
        for handle, ok, info in ex.map(lambda d: render_one(d, font), dirs):
            mark = "OK" if ok else "ERR"
            print(f"[{mark}] {handle}: {info}")


if __name__ == "__main__":
    main()
