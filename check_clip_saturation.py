#!/usr/bin/env python3
"""
候補VTuberごとに YouTube 上の既存切り抜き本数（第三者 vs 本人）を調査し、
競合度（saturation）を candidates.yaml に記録するスクリプト。

デフォルトでは第三者切り抜き 10 件以上を `saturated`（除外推奨）と判定します。
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote
import yaml

BASE_DIR = Path(__file__).resolve().parent
CANDIDATES_PATH = BASE_DIR / "candidates.yaml"
JST = timezone(timedelta(hours=9))


def clean_name(name: str) -> str:
    # Remove English slash suffix or decorative prefixes for search
    s = re.split(r"[/｜|【\[]", name)[0]
    s = re.sub(r"^(?:Conte\s*Ch\.|Meimu\s*ch\.|Chau\s*Ch\.)", "", s, flags=re.I)
    s = re.sub(r"-Hitode-", "", s, flags=re.I)
    return s.strip() or name.strip()


def flat_yt_search(query: str, limit: int = 25) -> list[dict]:
    p = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings", f"ytsearch{limit}:{query}"],
        capture_output=True,
        text=True,
        timeout=30,
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


GENERIC_TAGS = {"#切り抜き", "#切り抜き動画", "#vtuber", "#新人vtuber", "#個人勢vtuber"}


def check_one(cand: dict, threshold: int) -> dict:
    cid = cand["id"]
    short_name = clean_name(cand["name"])
    raw_tag = cand.get("clip_tag")
    clip_tag = None if (not raw_tag or raw_tag.lower() in GENERIC_TAGS) else raw_tag

    queries = []
    if clip_tag:
        queries.append(("tag", clip_tag))
    queries.append(("name_clip", f"{short_name} 切り抜き"))

    third_party_ids: dict[str, dict] = {}
    self_ids: dict[str, dict] = {}

    for qtype, q in queries:
        items = flat_yt_search(q, limit=25)
        for it in items:
            vid = it.get("id")
            if not vid:
                continue
            title = it.get("title") or ""
            ch = it.get("channel") or it.get("uploader") or ""
            ch_id = it.get("channel_id") or ""

            # Relevance check: must mention name or tag (for tag search, yt search can drift)
            tag_bare = (clip_tag or "").lstrip("#")
            relevant = (
                short_name.lower() in (title + " " + ch).lower()
                or (tag_bare and tag_bare.lower() in (title + " " + ch).lower())
            )
            if not relevant:
                continue

            is_clip_like = bool(
                re.search(r"切り抜き|切抜|clip|まとめ|名場面|shorts", title + " " + ch, re.I)
                or (tag_bare and tag_bare in title)
            )
            if not is_clip_like:
                continue

            entry = {
                "id": vid,
                "title": title,
                "channel": ch,
                "url": f"https://www.youtube.com/watch?v={vid}",
            }
            if ch_id == cid:
                self_ids[vid] = entry
            else:
                third_party_ids[vid] = entry

    tp_count = len(third_party_ids)
    self_count = len(self_ids)
    saturated = tp_count >= threshold

    yt_search_q = clip_tag if clip_tag else f"{short_name} 切り抜き"
    x_search_q = clip_tag if clip_tag else f"{short_name} 切り抜き"

    cand["clip_saturation"] = {
        "checked_at": datetime.now(JST).isoformat(),
        "threshold": threshold,
        "third_party_clip_count": tp_count,
        "self_clip_count": self_count,
        "status": "saturated_exclude" if saturated else "blue_ocean_ok",
        "yt_search_url": f"https://www.youtube.com/results?search_query={quote(yt_search_q)}",
        "x_search_url": f"https://x.com/search?q={quote(x_search_q)}&f=live",
        "sample_third_party_clips": list(third_party_ids.values())[:3],
    }
    return cand


def main():
    parser = argparse.ArgumentParser(description="切り抜き競合件数チェック")
    parser.add_argument("--threshold", type=int, default=10, help="除外ライン（第三者切り抜き件数）")
    parser.add_argument("--max-subs", type=int, default=2000, help="対象登録者数上限")
    args = parser.parse_args()

    data = yaml.safe_load(CANDIDATES_PATH.read_text(encoding="utf-8"))
    cands = data.get("candidates", [])
    targets = [c for c in cands if (c.get("subscribers") or 99999) <= args.max_subs]

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda c: check_one(c, args.threshold), targets))

    CANDIDATES_PATH.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )

    print(f"=== 切り抜き競合チェック結果（除外ライン: 第三者 {args.threshold}件以上） ===")
    for c in targets:
        s = c["clip_saturation"]
        mark = "除外推奨" if s["status"] == "saturated_exclude" else "OK(少)"
        print(
            f"[{mark}] {c['name']} ({c['handle']}) | 登録者:{c['subscribers']} | "
            f"タグ:{c.get('clip_tag')} | 第三者:{s['third_party_clip_count']}件 / 本人:{s['self_clip_count']}件"
        )
        print(f"   YouTube検索: {s['yt_search_url']}")
        print(f"   Xタグ検索  : {s['x_search_url']}")


if __name__ == "__main__":
    main()
