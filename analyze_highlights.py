#!/usr/bin/env python3
"""
承認済みチャンネルの最新配信アーカイブ（または指定動画）からライブチャットログと日本語字幕を取得し、
チャット急増＋リアクション語（草/w/笑/👏/かわいい等）から見どころ候補を自動抽出するスクリプト。

安全・鮮度フィルタ:
- デフォルトでチャンネルの最新配信アーカイブ（/streams）を対象とし、公開日からの経過日数（age_days）を記録
- 字幕内の日時依存・告知PRワード（開催/当日/会場/イベント/告知/○月○日 等）を自動検出
- 過去配信（デフォルト14日以上前）で告知PRワードに該当するシーンは警告を付与しスコアを減点
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import subprocess
import yaml

BASE_DIR = Path(__file__).resolve().parent
CHANNELS_PATH = BASE_DIR / "channels.yaml"
WORK_DIR = BASE_DIR / "clips"
JST = timezone(timedelta(hours=9))

REACTION_RE = re.compile(
    r"草|w{2,}|ｗ{2,}|笑|👏|かわいい|可愛|すご|神|すき|好き|尊|えらい|最高|！{2,}|!{2,}|888|８８８",
    re.I,
)
TIME_SENSITIVE_RE = re.compile(
    r"開催|当日|会場|イベント|告知|発売|チケット|予約開始|設置|決定いたしまし|実施|コラボカフェ|ポップアップ|グッズ販売|\d{1,2}月\d{1,2}日|\d{4}[年./-]\d{1,2}[月./-]\d{1,2}",
    re.I,
)


def to_sec(t: str) -> float:
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def calc_age_days(upload_date: str | None) -> int | None:
    if not upload_date or len(upload_date) != 8 or not upload_date.isdigit():
        return None
    try:
        dt = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=JST)
        return max(0, (datetime.now(JST) - dt).days)
    except ValueError:
        return None


def fetch_latest_archive_url(channel_url: str) -> str | None:
    """チャンネルの /streams（フォールバック: /videos）から最新の通常アーカイブURLを取得。"""
    base = channel_url.rstrip("/")
    for suffix in ("/streams", "/videos"):
        p = subprocess.run(
            [
                "yt-dlp",
                "--flat-playlist",
                "--playlist-end",
                "6",
                "--dump-single-json",
                "--no-warnings",
                f"{base}{suffix}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if p.returncode != 0 or not p.stdout.strip():
            continue
        try:
            data = json.loads(p.stdout)
        except json.JSONDecodeError:
            continue
        for entry in data.get("entries") or []:
            vid = entry.get("id")
            live_status = entry.get("live_status") or ""
            dur = entry.get("duration") or 0
            # 配信予定枠・配信中・極端に短い動画はスキップ
            if not vid or live_status in {"is_upcoming", "is_live"}:
                continue
            if dur and dur < 180:
                continue
            return f"https://www.youtube.com/watch?v={vid}"
    return None


def fetch_video_meta(url: str) -> dict:
    p = subprocess.run(
        ["yt-dlp", "--dump-single-json", "--skip-download", "--no-warnings", "--no-playlist", url],
        capture_output=True,
        text=True,
        timeout=35,
    )
    if p.returncode != 0 or not p.stdout.strip():
        return {}
    try:
        m = json.loads(p.stdout)
        return {
            "title": m.get("title"),
            "upload_date": m.get("upload_date"),
            "duration": m.get("duration"),
        }
    except json.JSONDecodeError:
        return {}


def parse_vtt(vtt_path: Path) -> list[tuple[float, float, str]]:
    if not vtt_path.exists():
        return []
    text = vtt_path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\n+", text)
    entries = []
    for b in blocks:
        m = re.search(r"(\d+:\d+:\d+\.\d+)\s*-->\s*(\d+:\d+:\d+\.\d+)", b)
        if not m:
            continue
        st, et = to_sec(m.group(1)), to_sec(m.group(2))
        lines = [re.sub(r"<[^>]+>", "", l).strip() for l in b.splitlines()[1:] if "-->" not in l]
        clean = " ".join(l for l in lines if l)
        if clean and (not entries or entries[-1][2] != clean):
            entries.append((st, et, clean))
    return entries


def extract_vtt_snippet(entries: list[tuple[float, float, str]], start_s: float, end_s: float) -> list[str]:
    out = []
    prev = ""
    for st, et, c in entries:
        if start_s <= st <= end_s:
            diff = c[len(prev) :].strip() if (prev and c.startswith(prev)) else c
            if diff:
                mm, ss = divmod(int(st), 60)
                out.append(f"[{mm:02d}:{ss:02d}] {diff}")
            prev = c
    return out


def parse_live_chat(chat_path: Path, bin_sec: int = 20, max_peaks: int = 8) -> list[dict]:
    if not chat_path.exists():
        return []
    bins = defaultdict(lambda: {"count": 0, "reaction": 0, "samples": []})
    with chat_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            replay = obj.get("replayChatItemAction") or {}
            msec = replay.get("videoOffsetTimeMsec")
            if msec is None:
                continue
            sec = int(msec) / 1000.0
            if sec < 30:  # skip pre-stream waiting room spam
                continue
            actions = replay.get("actions") or []
            for act in actions:
                item = (act.get("addChatItemAction") or {}).get("item") or {}
                renderer = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
                if not renderer:
                    continue
                runs = (renderer.get("message") or {}).get("runs") or []
                msg = "".join(r.get("text", "") for r in runs).strip()
                if not msg:
                    continue
                b = int(sec // bin_sec) * bin_sec
                bins[b]["count"] += 1
                if REACTION_RE.search(msg):
                    bins[b]["reaction"] += 1
                if len(bins[b]["samples"]) < 5:
                    bins[b]["samples"].append(msg[:40])

    ranked = []
    for b, info in bins.items():
        score = info["count"] + info["reaction"] * 1.5
        ranked.append({"start_sec": b, "end_sec": b + bin_sec, "score": score, **info})
    ranked.sort(key=lambda x: x["score"], reverse=True)

    # Pick top non-overlapping peaks (at least 60s apart)
    peaks = []
    for r in ranked:
        if all(abs(r["start_sec"] - p["start_sec"]) >= 60 for p in peaks):
            peaks.append(r)
        if len(peaks) >= max_peaks:
            break
    return peaks


def process_channel(
    ch: dict,
    use_guideline_source: bool = False,
    override_url: str | None = None,
    stale_days: int = 14,
) -> dict:
    handle = (ch.get("handle") or ch["id"]).lstrip("@")
    out_dir = WORK_DIR / handle
    out_dir.mkdir(parents=True, exist_ok=True)

    if override_url:
        url = override_url
    elif use_guideline_source:
        url = ch["guideline_source"]
    else:
        url = fetch_latest_archive_url(ch["channel_url"]) or ch["guideline_source"]

    vmeta = fetch_video_meta(url)
    upload_date = vmeta.get("upload_date")
    age_days = calc_age_days(upload_date)
    is_stale_archive = age_days is not None and age_days >= stale_days

    subprocess.run(
        [
            "yt-dlp",
            "--skip-download",
            "--write-auto-subs",
            "--write-subs",
            "--sub-lang",
            "ja,live_chat",
            "--sub-format",
            "vtt",
            "--no-warnings",
            "-o",
            str(out_dir / "source"),
            url,
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )

    vtt_path = out_dir / "source.ja.vtt"
    chat_path = out_dir / "source.live_chat.json"
    entries = parse_vtt(vtt_path)
    peaks = parse_live_chat(chat_path, bin_sec=25, max_peaks=8)

    # Fallback if chat is empty: pick 3 evenly spaced talk segments
    if not peaks and entries:
        total = entries[-1][0]
        for frac in (0.15, 0.45, 0.75):
            s = int(total * frac)
            peaks.append({"start_sec": s, "end_sec": s + 30, "score": 0, "count": 0, "reaction": 0, "samples": []})

    enriched = []
    for p in peaks:
        w_start = max(0, p["start_sec"] - 15)
        w_end = p["end_sec"] + 25
        p["window_start"] = w_start
        p["window_end"] = w_end
        p["transcript"] = extract_vtt_snippet(entries, w_start, w_end)

        joined_txt = " ".join(p["transcript"])
        hits = sorted(set(TIME_SENSITIVE_RE.findall(joined_txt)))
        p["time_sensitive_hits"] = hits
        p["time_sensitive_warning"] = bool(hits)
        p["stale_promo_risk"] = bool(hits and is_stale_archive)
        # Penalize time-sensitive / promotional scenes from older archives so timeless moments rank first
        p["effective_score"] = round(p["score"] * (0.2 if p["stale_promo_risk"] else 1.0), 1)
        enriched.append(p)

    enriched.sort(key=lambda x: x["effective_score"], reverse=True)
    enriched = enriched[:4]

    summary = {
        "handle": ch.get("handle"),
        "name": ch["name"],
        "video_url": url,
        "video_title": vmeta.get("title"),
        "upload_date": upload_date,
        "age_days": age_days,
        "is_stale_archive": is_stale_archive,
        "has_vtt": vtt_path.exists(),
        "has_chat": chat_path.exists(),
        "peaks": enriched,
    }
    (out_dir / "highlights.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description="承認済みチャンネルの見どころ候補を自動抽出")
    parser.add_argument("--channel", help="対象チャンネルの handle / channel_id / name（省略時は承認済み全件）")
    parser.add_argument("--video-url", help="特定の動画URLを直接解析（--channel と併用推奨）")
    parser.add_argument("--use-guideline-source", action="store_true", help="最新配信ではなく guideline_source の動画を解析")
    parser.add_argument("--stale-days", type=int, default=14, help="過去告知PR警告を有効にする経過日数（既定: 14）")
    args = parser.parse_args()

    data = yaml.safe_load(CHANNELS_PATH.read_text(encoding="utf-8"))
    approved = [c for c in data.get("channels", []) if c.get("status") == "approved"]

    if args.channel:
        q = args.channel.lower().lstrip("@")
        approved = [
            c for c in approved
            if q in {str(c.get("id") or "").lower(), str(c.get("handle") or "").lower().lstrip("@"), str(c.get("name") or "").lower()}
        ]

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(
            ex.map(
                lambda ch: process_channel(
                    ch,
                    use_guideline_source=args.use_guideline_source,
                    override_url=args.video_url if len(approved) == 1 else None,
                    stale_days=args.stale_days,
                ),
                approved,
            )
        )

    for r in results:
        age_str = f"{r['age_days']}日前 ({r['upload_date']})" if r["age_days"] is not None else "日付不明"
        print(f"\n================ {r['name']} ({r['handle']}) ================")
        print(f"URL: {r['video_url']} | 公開: {age_str} | VTT:{r['has_vtt']} | Chat:{r['has_chat']}")
        for idx, p in enumerate(r["peaks"][:3], 1):
            m1, s1 = divmod(int(p["window_start"]), 60)
            m2, s2 = divmod(int(p["window_end"]), 60)
            warn = ""
            if p["stale_promo_risk"]:
                warn = f" [WARN: 過去イベント/時限PRの可能性 hits={p['time_sensitive_hits']}]"
            elif p["time_sensitive_warning"]:
                warn = f" [NOTE: 日時依存語あり hits={p['time_sensitive_hits']}]"
            print(
                f"  Peak #{idx}: {m1:02d}:{s1:02d} - {m2:02d}:{s2:02d} "
                f"(chat={p['count']}, react={p['reaction']}, score={p['effective_score']}){warn}"
            )
            print(f"    Chat samples: {p['samples']}")
            print("    Transcript:")
            for line in p["transcript"][:14]:
                print(f"      {line}")


if __name__ == "__main__":
    main()
