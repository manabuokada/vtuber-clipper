#!/usr/bin/env python3
"""
切り抜き許可（切り抜きOK／切り抜き歓迎／切り抜きタグ等）を明記している
新人・個人勢VTuber候補を自動探索し、config/candidates.yaml に追記するスクリプト。

※ここでは候補登録のみ行い、自動切り抜きは絶対に実行しません。
  承認は scripts/discovery/manage_channels.py approve <handle> で人間が行います。
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import json
from pathlib import Path
import re
import subprocess
import sys
import yaml

BASE_DIR = Path(__file__).resolve().parents[2]
CHANNELS_PATH = BASE_DIR / "config" / "channels.yaml"
CANDIDATES_PATH = BASE_DIR / "config" / "candidates.yaml"

DEFAULT_QUERIES = [
    "ytsearch15:個人勢VTuber 初配信 切り抜き",
    "ytsearch15:新人VTuber 個人勢 雑談 切り抜き",
    "ytsearch15:個人勢VTuber 切り抜き歓迎",
    "ytsearch15:新人VTuber 切り抜きOK 雑談",
]

NEWCOMER_QUERIES = [
    "ytsearch20:初配信 新人VTuber 個人勢 2026年11月",
    "ytsearch20:初配信 新人VTuber 個人勢 2026年10月",
    "ytsearch20:初配信 デビュー 新人VTuber 個人勢 切り抜き",
    "ytsearch20:新人VTuber デビュー 雑談 切り抜きタグ",
]

MICRO_DEBUT_QUERIES = [
    "ytsearch20:初配信 新人vtuber 切り抜き大歓迎",
    "ytsearch20:初配信 個人勢vtuber 切り抜き歓迎",
    "ytsearch20:初配信 新人vtuber 切り抜きOK 2026",
    "ytsearch20:新人vtuber 雑談 切り抜きも大歓迎",
    "ytsearch20:個人勢vtuber デビュー 切り抜き大歓迎 2026",
]

X_URL_PATTERN = re.compile(r"https?://(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,30})")
STRONG_WELCOME_PATTERN = re.compile(
    r"切り抜き.*(大歓迎|歓迎|OK|ＯＫ|自由|可能|許可|嬉しい|ぜひ|是非)|"
    r"(大歓迎|歓迎|OK|ＯＫ|自由).*切り抜き",
    re.IGNORECASE,
)

EXCLUDE_PATTERN = re.compile(
    r"切り抜き(チャンネル|部屋|まとめ|専門|ch|Ch|CH)|切抜|【[^】]*切り抜き[^】]*】|解説アニメ|"
    r"ホロライブ|にじさんじ|ぶいすぽ|あおぎり高校|ななしいんく|のりプロ|VShojo|Vspo",
    re.IGNORECASE,
)

CLIP_LINE_PATTERN = re.compile(r"切り抜き|二次創作|ガイドライン")
POSITIVE_PERMISSION_PATTERN = re.compile(
    r"切り抜き.*(OK|ＯＫ|歓迎|自由|可能|大歓迎|タグ|どうぞ|嬉しい|募集中|許可)|"
    r"(OK|ＯＫ|歓迎|自由|大歓迎|募集中).*切り抜き|"
    r"切り抜きタグ|切り抜き(用)?ハッシュタグ",
    re.IGNORECASE,
)
NEGATIVE_PERMISSION_PATTERN = re.compile(r"切り抜き.*(禁止|NG|ＮＧ|不可|ご遠慮)")
HASHTAG_PATTERN = re.compile(r"[#＃][^\s　/／,、]+")


def load_yaml(path: Path, key: str) -> dict:
    if not path.exists():
        return {"version": 1, key: []}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("version", 1)
    data.setdefault(key, [])
    return data


def save_yaml(path: Path, data: dict):
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )


def flat_search(query: str) -> list[dict]:
    p = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings", query],
        capture_output=True,
        text=True,
    )
    items = []
    for line in p.stdout.splitlines():
        if not line.strip():
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            pass
    return items


def inspect_video(video_id: str) -> dict | None:
    url = f"https://www.youtube.com/watch?v={video_id}"
    p = subprocess.run(
        ["yt-dlp", "--dump-single-json", "--skip-download", "--no-warnings", "--no-playlist", url],
        capture_output=True,
        text=True,
        timeout=35,
    )
    if p.returncode != 0 or not p.stdout.strip():
        return None
    try:
        return json.loads(p.stdout)
    except Exception:
        return None


def extract_clip_tag(lines: list[str]) -> str | None:
    for line in lines:
        tags = HASHTAG_PATTERN.findall(line)
        for t in tags:
            return t.replace("＃", "#")
    return None


def extract_x_handles(desc: str) -> list[str]:
    handles = []
    for m in X_URL_PATTERN.finditer(desc):
        h = m.group(1)
        if h.lower() in {"intent", "share", "hashtag", "search", "home"}:
            continue
        url = f"https://x.com/{h}"
        if url not in handles:
            handles.append(url)
    return handles


def main():
    parser = argparse.ArgumentParser(description="切り抜き許可済み新人・個人勢VTuber候補を探索")
    parser.add_argument("--mode", choices=["default", "newcomer", "micro_debut"], default="default", help="探索モード")
    parser.add_argument("--max-inspect", type=int, default=24, help="詳細確認する最大動画数")
    parser.add_argument("--workers", type=int, default=6, help="並列数")
    parser.add_argument("--max-subs", type=int, default=50000, help="登録者数上限（新人・個人勢向け）")
    parser.add_argument("--min-date", default="", help="アップロード日下限 YYYYMMDD")
    parser.add_argument("--require-strong-welcome", action="store_true", help="『切り抜き大歓迎/OK』等の明示文言を必須にする")
    args = parser.parse_args()

    if args.mode == "newcomer":
        queries = NEWCOMER_QUERIES
    elif args.mode == "micro_debut":
        queries = MICRO_DEBUT_QUERIES
    else:
        queries = DEFAULT_QUERIES

    ch_data = load_yaml(CHANNELS_PATH, "channels")
    cand_data = load_yaml(CANDIDATES_PATH, "candidates")
    known_ids = {c["id"] for c in ch_data["channels"]} | {c["id"] for c in cand_data["candidates"]}

    print(f"1) YouTube 検索（mode={args.mode}, 軽量フラット一覧取得）...")
    raw_entries = []
    with ThreadPoolExecutor(max_workers=len(queries)) as ex:
        futs = [ex.submit(flat_search, q) for q in queries]
        for f in as_completed(futs):
            raw_entries.extend(f.result())

    # Deduplicate by channel_id (or uploader) and pre-filter
    to_inspect = []
    seen_ch = set(known_ids)
    for e in raw_entries:
        vid = e.get("id")
        cid = e.get("channel_id") or e.get("uploader_id") or vid
        title = e.get("title") or ""
        ch_name = e.get("channel") or e.get("uploader") or ""
        dur = e.get("duration") or 0
        if not vid or cid in seen_ch:
            continue
        if EXCLUDE_PATTERN.search(f"{ch_name} {title}"):
            continue
        # Prefer actual streams / talks (>= 3 min) rather than 30s promo shorts
        if dur and dur < 180:
            continue
        seen_ch.add(cid)
        to_inspect.append((vid, ch_name, title))
        if len(to_inspect) >= args.max_inspect:
            break

    print(f"2) 候補チャンネル {len(to_inspect)} 件の詳細・概要欄を並列チェック中...")
    new_candidates = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        fut_map = {ex.submit(inspect_video, vid): (vid, ch, ti) for vid, ch, ti in to_inspect}
        for f in as_completed(fut_map):
            try:
                d = f.result()
            except Exception:
                continue
            if not d:
                continue
            cid = d.get("channel_id")
            if not cid or cid in known_ids:
                continue
            ch_name = d.get("channel") or d.get("uploader") or ""
            title = d.get("title") or ""
            desc = d.get("description") or ""
            subs = d.get("channel_follower_count")

            if EXCLUDE_PATTERN.search(f"{ch_name} {title}"):
                continue
            if subs and subs > args.max_subs:
                continue
            if args.min_date and (d.get("upload_date") or "") < args.min_date:
                continue

            clip_lines = [l.strip() for l in desc.splitlines() if CLIP_LINE_PATTERN.search(l)]
            if not clip_lines:
                continue
            joined = "\n".join(clip_lines)
            if NEGATIVE_PERMISSION_PATTERN.search(joined):
                continue

            explicit_ok = bool(POSITIVE_PERMISSION_PATTERN.search(joined))
            strong_welcome = bool(STRONG_WELCOME_PATTERN.search(joined))
            if args.require_strong_welcome and not strong_welcome:
                continue
            has_ja_subs = "ja" in (d.get("subtitles") or {}) or "ja" in (d.get("automatic_captions") or {})
            has_live_chat = "live_chat" in (d.get("subtitles") or {})
            x_urls = extract_x_handles(desc)

            cand = {
                "id": cid,
                "name": ch_name,
                "handle": d.get("uploader_id"),
                "channel_url": d.get("channel_url"),
                "x_url": x_urls[0] if x_urls else None,
                "subscribers": subs,
                "status": "pending_review",
                "discovered_at": date.today().isoformat(),
                "explicit_clip_ok": explicit_ok,
                "strong_welcome": strong_welcome,
                "clip_tag": extract_clip_tag(clip_lines),
                "guideline_source": d.get("webpage_url"),
                "guideline_excerpt": clip_lines[:5],
                "sample_video": {
                    "id": d.get("id"),
                    "title": title,
                    "url": d.get("webpage_url"),
                    "upload_date": d.get("upload_date"),
                    "duration_sec": d.get("duration"),
                    "view_count": d.get("view_count"),
                },
                "has_ja_subs": has_ja_subs,
                "has_live_chat": has_live_chat,
            }
            new_candidates.append(cand)
            known_ids.add(cid)

    new_candidates.sort(
        key=lambda x: (
            x["explicit_clip_ok"],
            x["has_ja_subs"],
            x["has_live_chat"],
            x["sample_video"].get("upload_date") or "",
        ),
        reverse=True,
    )

    cand_data["candidates"].extend(new_candidates)
    save_yaml(CANDIDATES_PATH, cand_data)

    print(f"\n完了: 新規候補 {len(new_candidates)} 件を candidates.yaml に追加しました。")
    for i, c in enumerate(new_candidates, 1):
        ok_mark = "★明示OK" if c["explicit_clip_ok"] else "言及あり"
        print(
            f"{i}. [{ok_mark}] {c['name']} ({c['handle']}) | 登録者:{c['subscribers']} | "
            f"日本語字幕:{c['has_ja_subs']} | チャット:{c['has_live_chat']}"
        )
        print(f"   動画: {c['sample_video']['title'][:50]} ({c['sample_video']['upload_date']})")
        for line in c["guideline_excerpt"][:3]:
            print(f"   抜粋: {line[:95]}")


if __name__ == "__main__":
    main()
