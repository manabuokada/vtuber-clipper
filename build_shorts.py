#!/usr/bin/env python3
"""
YAML設定（--spec）駆動のショート切り抜き自動生成スクリプト（1080×1920 縦動画）:
- 冒頭約2秒の「オチ先出しフック」（任意）→ トランジション切替で本編へ
- 自動ジェットカット（無音除去）＋ 1.06倍速の軽快テンポ
- 配信レイアウトごとに調整可能な上段・下段クロップ（upper_crop / face_crop / face_zoom_crop）
- グラデーション＆ネオン丸角フレームPNG（frame_overlay.png）
- オチ瞬間の 顔アップズーム ＋ 0.3秒カメラシェイク ＋ 漫画集中線PNG ＋ 赤フラッシュ枠
- シーン間の高速横スライドトランジション（xfade=slideleft, 0.14s）
- パンチワード強調＆バウンス登場テロップ ＋ リスナーコメント吹き出し
- 声が出ている間だけ自動で下がるサイドチェイン・ダッキングBGM ＋ 各種SE

使い方:
  ./.venv/bin/python build_shorts.py --spec shorts_spec.example.yaml
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"


def resolve_path(p: str | Path, base: Path) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (base / path)


def get_duration(path: Path) -> float:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    return float(p.stdout.strip())


def detect_speech_intervals(
    path: Path,
    noise_db: str = "-32dB",
    min_sil: float = 0.26,
    pad: float = 0.06,
    start_clip: float = 0.0,
    end_clip: float | None = None,
) -> list[tuple[float, float]]:
    dur = get_duration(path)
    hi = min(dur, end_clip) if end_clip is not None else dur
    p = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af", f"silencedetect=n={noise_db}:d={min_sil}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    silences = []
    cur_start = None
    for line in p.stderr.splitlines():
        m1 = re.search(r"silence_start:\s*([0-9.]+)", line)
        m2 = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m1:
            cur_start = float(m1.group(1))
        elif m2 and cur_start is not None:
            silences.append((cur_start, float(m2.group(1))))
            cur_start = None
    if cur_start is not None:
        silences.append((cur_start, dur))

    speech = []
    pos = start_clip
    for s0, s1 in silences:
        if s1 <= start_clip:
            continue
        if s0 >= hi:
            break
        if s0 > pos:
            speech.append((max(start_clip, pos - pad), min(hi, s0 + pad)))
        pos = max(pos, s1)
    if pos < hi:
        speech.append((max(start_clip, pos - pad), hi))

    merged = []
    for a, b in speech:
        if b - a < 0.16:
            continue
        if merged and a - merged[-1][1] < 0.12:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged or [(start_clip, hi)]


def map_time(orig_t: float, intervals: list[tuple[float, float]], speed: float) -> float:
    acc = 0.0
    for a, b in intervals:
        if orig_t <= a:
            return acc / speed
        if orig_t < b:
            return (acc + (orig_t - a)) / speed
        acc += (b - a)
    return acc / speed


def fmt_ass(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def render_segment(
    src: Path,
    dst: Path,
    intervals: list[tuple[float, float]],
    speed: float,
    punch_orig: float,
    layout_cfg: dict,
    always_zoom: bool = False,
) -> tuple[float, float]:
    parts_v, parts_a = [], []
    for i, (a, b) in enumerate(intervals):
        parts_v.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=(PTS-STARTPTS)/{speed:.4f}[v{i}]")
        parts_a.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,atempo={speed:.4f}[a{i}]")
    v_in = "".join(f"[v{i}]" for i in range(len(intervals)))
    a_in = "".join(f"[a{i}]" for i in range(len(intervals)))

    punch_t = 0.0 if always_zoom else map_time(punch_orig, intervals, speed)
    total_dur = sum(b - a for a, b in intervals) / speed

    upper_crop = layout_cfg.get("upper_crop", "iw:ih:0:0")
    face_crop = layout_cfg.get("face_crop", "iw*0.5:ih*0.7:iw*0.45:ih*0.08")
    face_zoom_crop = layout_cfg.get("face_zoom_crop", "iw*0.38:ih*0.53:iw*0.51:ih*0.12")

    # Camera shake expression active for 0.32s right at punch_t
    shake_x = f"if(between(t,{punch_t:.2f},{punch_t+0.32:.2f}),round(14*sin(t*75)),0)"
    shake_y = f"if(between(t,{punch_t:.2f},{punch_t+0.32:.2f}),round(868+10*cos(t*90)),868)"

    layout = (
        f"{v_in}concat=n={len(intervals)}:v=1:a=0,fps=30[vc];"
        f"{a_in}concat=n={len(intervals)}:v=0:a=1,aresample=48000[ac];"
        f"[vc]split=3[s_slide][s_face1][s_face2];"
        # Upper window (1044x584 placed inside rounded frame 18..1062, 266..850)
        f"[s_slide]crop={upper_crop},scale=1044:584:flags=lanczos[slide];"
        # Lower avatar close-up (1080x835 placed at y=868, clipped by frame_overlay.png)
        f"[s_face1]crop={face_crop},scale=1080:835:flags=lanczos[face_norm];"
        f"[s_face2]crop={face_zoom_crop},scale=1080:835:flags=lanczos[face_zoom];"
        f"[face_norm][face_zoom]overlay=0:0:enable='gte(t,{punch_t:.2f})'[face_base];"
        # Overlay subtle speedlines + red vignette briefly right on punchline
        f"[face_base][1:v]overlay=0:0:enable='between(t,{punch_t:.2f},{punch_t+0.75:.2f})'[face_sl];"
        f"[face_sl][2:v]overlay=0:0:enable='between(t,{punch_t:.2f},{punch_t+0.40:.2f})'[face_fx];"
        # Build 1080x1920 canvas
        f"color=c=#0E101C:s=1080x1920:r=30:d={total_dur + 0.15:.2f}[bg];"
        f"[bg][slide]overlay=18:266[c1];"
        f"[c1][face_fx]overlay=x='{shake_x}':y='{shake_y}'[c2];"
        # Apply gradient & glowing rounded frame PNG on top
        f"[c2][3:v]overlay=0:0,trim=duration={total_dur:.3f},setpts=PTS-STARTPTS[vout]"
    )

    fc = ";".join(parts_v + parts_a) + ";" + layout
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-i", str(ASSETS / "speedlines.png"),
        "-i", str(ASSETS / "red_vignette.png"),
        "-i", str(ASSETS / "frame_overlay.png"),
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "[ac]",
        "-r", "30", "-c:v", "libx264", "-preset", "fast", "-crf", "17", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
        str(dst), "-loglevel", "warning",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg segment failed on {src}: {r.stderr}")
    return get_duration(dst), punch_t


def normalize_ass_text(text: str) -> str:
    # YAML double-quoted "\N" decodes to U+0085 (NEL); normalize both real newlines and U+0085 to ASS \N
    return str(text).replace("\r\n", r"\N").replace("\n", r"\N").replace("\u0085", r"\N")


def pop_anim(text: str) -> str:
    return r"{\fscx76\fscy76\t(0,85,\fscx107\fscy107)\t(85,155,\fscx100\fscy100)}" + normalize_ass_text(text)


def build_from_spec(spec_path: Path) -> Path:
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    src_dir = resolve_path(spec["src_dir"], ROOT)
    out_dir = resolve_path(spec["out_dir"], ROOT)
    out_dir.mkdir(parents=True, exist_ok=True)

    for required in ("frame_overlay.png", "speedlines.png", "red_vignette.png", "sfx_pop.wav", "sfx_whoosh.wav", "sfx_impact.wav"):
        if not (ASSETS / required).exists():
            print(f"[エラー] 素材が見つかりません: {ASSETS / required}\n先に generate_assets.py を実行してください。", file=sys.stderr)
            sys.exit(1)

    speed = float(spec.get("speed", 1.06))
    trans_d = float(spec.get("transition_sec", 0.14))
    noise_db = str(spec.get("silence_noise_db", "-32dB"))
    min_sil = float(spec.get("min_silence_sec", 0.26))
    layout_cfg = spec.get("layout") or {}

    part_files: list[Path] = []
    part_durs: list[float] = []
    ass_events: list[str] = []
    sfx_events: list[tuple[float, str]] = []
    has_hook = False
    offset = 0.0

    # Optional Cold-Open Hook
    hook_cfg = spec.get("hook")
    if hook_cfg:
        has_hook = True
        hook_src = resolve_path(hook_cfg["src"], src_dir)
        hook_intervals = detect_speech_intervals(
            hook_src,
            noise_db=noise_db,
            min_sil=min_sil,
            start_clip=float(hook_cfg.get("start", 0.0)),
            end_clip=float(hook_cfg["end"]) if hook_cfg.get("end") is not None else None,
        )
        max_iv = int(hook_cfg.get("max_intervals", 2))
        hook_dst = out_dir / "part_0_hook.mp4"
        hook_dur, _ = render_segment(
            hook_src,
            hook_dst,
            hook_intervals[:max_iv],
            float(hook_cfg.get("speed", 1.10)),
            0.0,
            layout_cfg,
            always_zoom=True,
        )
        print(f"Hook: {hook_dur:.2f}s")
        part_files.append(hook_dst)
        part_durs.append(hook_dur)
        sfx_events.append((0.02, "impact"))

        if hook_cfg.get("banner"):
            ass_events.append(
                f"Dialogue: 3,0:00:00.00,{fmt_ass(hook_dur - 0.05)},HookBanner,hook,0,0,0,,"
                + pop_anim(str(hook_cfg["banner"]))
            )
        if hook_cfg.get("subtitle"):
            ass_events.append(
                f"Dialogue: 2,0:00:00.05,{fmt_ass(hook_dur - 0.05)},Impact,sub,0,0,0,,"
                + pop_anim(str(hook_cfg["subtitle"]))
            )
        offset = hook_dur - trans_d

    segments = spec.get("segments") or []
    if not segments:
        print("[エラー] spec に segments が定義されていません。", file=sys.stderr)
        sys.exit(1)

    for idx, seg in enumerate(segments, 1):
        seg_src = resolve_path(seg["src"], src_dir)
        intervals = detect_speech_intervals(
            seg_src,
            noise_db=noise_db,
            min_sil=min_sil,
            start_clip=float(seg.get("start", 0.0)),
            end_clip=float(seg["end"]) if seg.get("end") is not None else None,
        )
        dst = out_dir / f"part_{idx}.mp4"
        seg_dur, punch_rel = render_segment(
            seg_src,
            dst,
            intervals,
            speed,
            float(seg.get("punch_orig", seg.get("start", 0.0))),
            seg.get("layout") or layout_cfg,
        )
        print(f"Part {idx}: {seg_dur:.2f}s (punch @{punch_rel:.2f}s)")
        part_files.append(dst)
        part_durs.append(seg_dur)

        if has_hook or idx > 1:
            sfx_events.append((max(0.0, offset - 0.05), "whoosh"))
        sfx_events.append((offset + 0.08, "pop"))
        sfx_events.append((offset + punch_rel, "impact"))

        seg_end_abs = offset + seg_dur - (trans_d if idx < len(segments) else 0.0)

        if seg.get("badge"):
            ass_events.append(
                f"Dialogue: 3,{fmt_ass(offset + 0.05)},{fmt_ass(seg_end_abs)},Badge,badge,0,0,0,,"
                + pop_anim(f" {seg['badge']} ")
            )
        if seg.get("tsukkomi"):
            ass_events.append(
                f"Dialogue: 4,{fmt_ass(offset + punch_rel)},{fmt_ass(seg_end_abs)},Tsukkomi,tsukkomi,0,0,0,,"
                + pop_anim(str(seg["tsukkomi"]))
            )
        if seg.get("chat"):
            c_user, c_msg = seg["chat"]
            c_start = min(seg_end_abs - 0.8, offset + punch_rel + 0.45)
            if c_start < seg_end_abs:
                ass_events.append(
                    f"Dialogue: 3,{fmt_ass(c_start)},{fmt_ass(seg_end_abs)},ChatBubble,chat,0,0,0,,"
                    f"{{\\fad(120,120)}} ▶ {c_user}：{c_msg} "
                )

        for a0, b0, style, txt in seg.get("subs", []):
            ta = offset + map_time(float(a0), intervals, speed)
            tb = offset + map_time(float(b0), intervals, speed)
            if tb - ta < 0.25:
                tb = min(seg_end_abs, ta + 0.65)
            tb = min(tb, seg_end_abs)
            if ta >= tb:
                continue
            ass_events.append(f"Dialogue: 2,{fmt_ass(ta)},{fmt_ass(tb)},{style},sub,0,0,0,,{pop_anim(str(txt))}")

        offset += seg_dur - trans_d

    total_dur = sum(part_durs) - trans_d * (len(part_durs) - 1)
    print(f"Total Shorts duration: {total_dur:.2f}s")

    # 1) Join parts using xfade + acrossfade (or reuse single part directly)
    joined_mp4 = out_dir / "joined_xfade.mp4"
    if len(part_files) == 1:
        joined_mp4 = part_files[0]
    else:
        xf_inputs = []
        for p in part_files:
            xf_inputs += ["-i", str(p)]

        v_chain = []
        a_chain = []
        cur_v = "[0:v]"
        cur_a = "[0:a]"
        acc_d = part_durs[0]
        for i in range(1, len(part_files)):
            xf_off = acc_d - trans_d
            nv = f"[vx{i}]"
            na = f"[ax{i}]"
            tr_type = "fadewhite" if (has_hook and i == 1) else "slideleft"
            v_chain.append(f"{cur_v}[{i}:v]xfade=transition={tr_type}:duration={trans_d:.2f}:offset={xf_off:.3f}{nv}")
            a_chain.append(f"{cur_a}[{i}:a]acrossfade=d={trans_d:.2f}:c1=tri:c2=tri{na}")
            cur_v, cur_a = nv, na
            acc_d = xf_off + part_durs[i]

        r = subprocess.run(
            ["ffmpeg", "-y"] + xf_inputs + [
                "-filter_complex", ";".join(v_chain + a_chain),
                "-map", cur_v, "-map", cur_a,
                "-c:v", "libx264", "-preset", "fast", "-crf", "17", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
                str(joined_mp4), "-loglevel", "warning",
            ],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"xfade failed: {r.stderr}")

    # 2) Write ASS subtitles
    ass_path = out_dir / "shorts.ass"
    ass_text = f"""[Script Info]
Title: {spec.get('title', 'VTuber Shorts')}
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TopTitle1,Noto Sans CJK JP,58,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,2,0,1,6,3,8,30,30,42,1
Style: TopTitle2,Noto Sans CJK JP,74,&H0000EAFF,&H000000FF,&H001818D0,&H00000000,-1,0,0,0,100,100,2,0,1,9,4,8,30,30,122,1
Style: HookBanner,Noto Sans CJK JP,56,&H00FFFFFF,&H000000FF,&H001010D0,&HD8000000,-1,0,0,0,100,100,2,0,3,14,0,8,30,30,290,1
Style: Badge,Noto Sans CJK JP,48,&H00FFFFFF,&H000000FF,&H008018E0,&HD8000000,-1,0,0,0,100,100,2,0,3,12,0,7,38,38,286,1
Style: ChatBubble,Noto Sans CJK JP,38,&H00FFFFFF,&H000000FF,&H00302010,&HC8181828,-1,0,0,0,100,100,1,0,3,10,0,9,38,38,892,1
Style: Tsukkomi,Noto Sans CJK JP,68,&H0038FFFF,&H000000FF,&H001010D8,&H90000000,-1,0,0,0,100,100,2,0,1,9,4,8,40,40,955,1
Style: Main,Noto Sans CJK JP,62,&H00FFFFFF,&H000000FF,&H00202020,&H90000000,-1,0,0,0,100,100,1,0,1,7,4,2,50,50,250,1
Style: Impact,Noto Sans CJK JP,70,&H0000EEFF,&H000000FF,&H001515C0,&H90000000,-1,0,0,0,100,100,2,0,1,9,4,2,40,40,245,1
Style: Credit,Noto Sans CJK JP,34,&H00D0D8E8,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,1,0,1,3,0,2,40,40,68,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 5,0:00:00.00,{fmt_ass(total_dur)},TopTitle1,title,0,0,0,,{spec.get('top_title_1', '')}
Dialogue: 5,0:00:00.00,{fmt_ass(total_dur)},TopTitle2,title,0,0,0,,{spec.get('top_title_2', '')}
Dialogue: 5,0:00:00.00,{fmt_ass(total_dur)},Credit,credit,0,0,0,,{spec.get('credit', '')}
""" + "\n".join(ass_events) + "\n"
    ass_path.write_text(ass_text, encoding="utf-8")

    # 3) Final pass: Burn ASS + Sidechain-ducked BGM + SFX + -14 LUFS loudness norm
    bgm_path = ASSETS / "custom_bgm.mp3" if (ASSETS / "custom_bgm.mp3").exists() else ASSETS / "bgm_comedic.wav"
    sfx_map = {
        "pop": ASSETS / "sfx_pop.wav",
        "whoosh": ASSETS / "sfx_whoosh.wav",
        "impact": ASSETS / "sfx_impact.wav",
    }

    inputs = ["-i", str(joined_mp4), "-stream_loop", "-1", "-i", str(bgm_path)]
    sfx_filters = []
    sfx_labels = []
    for i, (t_sec, kind) in enumerate(sfx_events, 2):
        inputs += ["-i", str(sfx_map[kind])]
        ms = max(0, int(t_sec * 1000))
        sfx_filters.append(f"[{i}:a]adelay={ms}|{ms},volume=0.80[sfx{i}]")
        sfx_labels.append(f"[sfx{i}]")

    sfx_join = (";" + ";".join(sfx_filters)) if sfx_filters else ""
    audio_fc = (
        f"[0:a]asplit=2[voice_main][voice_sc];"
        f"[1:a]atrim=0:{total_dur:.3f},asetpts=PTS-STARTPTS,volume=0.22[bgm_lvl];"
        f"[bgm_lvl][voice_sc]sidechaincompress=threshold=0.03:ratio=5:attack=25:release=280[bgm_duck]"
        f"{sfx_join}"
        f";[voice_main][bgm_duck]{''.join(sfx_labels)}amix=inputs={2 + len(sfx_labels)}:duration=first:normalize=0,"
        f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,afade=t=out:st={max(1.0, total_dur - 0.35):.2f}:d=0.35[aout]"
    )

    out_mp4 = out_dir / str(spec.get("output_name", "edited_shorts.mp4"))
    r = subprocess.run(
        ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", f"[0:v]ass={ass_path.name}[vout];{audio_fc}",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
            "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-g", "60",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
            "-movflags", "+faststart",
            str(out_mp4), "-loglevel", "warning",
        ],
        cwd=str(out_dir),
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"final render failed: {r.stderr}")

    # Extract 3 verify frames (hook/start, mid, near end)
    verify_times = [
        ("verify_hook.jpg", min(0.8, total_dur * 0.1)),
        ("verify_mid.jpg", total_dur * 0.55),
        ("verify_end.jpg", max(1.0, total_dur * 0.85)),
    ]
    for fname, ts in verify_times:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", str(out_mp4), "-vframes", "1", str(out_dir / fname), "-loglevel", "error"]
        )

    print(f"\nDONE! -> {out_mp4} ({out_mp4.stat().st_size / (1024*1024):.1f} MB, {total_dur:.1f}s)")
    return out_mp4


def main():
    parser = argparse.ArgumentParser(description="YAML spec 駆動のショート切り抜き自動生成ツール")
    parser.add_argument("--spec", type=Path, required=True, help="シーン定義 YAML ファイルのパス")
    args = parser.parse_args()
    if not args.spec.exists():
        print(f"[エラー] spec ファイルが見つかりません: {args.spec}", file=sys.stderr)
        sys.exit(1)
    build_from_spec(args.spec)


if __name__ == "__main__":
    main()
