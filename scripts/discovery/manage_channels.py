#!/usr/bin/env python3
"""
許可チャンネル（config/channels.yaml）と候補リスト（config/candidates.yaml）を管理するCLI。

使い方:
  ./.venv/bin/python scripts/discovery/manage_channels.py list
  ./.venv/bin/python scripts/discovery/manage_channels.py approve <channel_id_or_handle>
  ./.venv/bin/python scripts/discovery/manage_channels.py reject <channel_id_or_handle> --reason "..."
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys
import yaml

BASE_DIR = Path(__file__).resolve().parents[2]
CHANNELS_PATH = BASE_DIR / "config" / "channels.yaml"
CANDIDATES_PATH = BASE_DIR / "config" / "candidates.yaml"


def load_yaml(path: Path, key: str) -> dict:
    if not path.exists():
        return {"version": 1, key: []}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("version", 1)
    data.setdefault(key, [])
    return data


def save_yaml(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )


def find_item(items: list[dict], query: str) -> tuple[int, dict | None]:
    q = query.strip().lower()
    for idx, item in enumerate(items):
        for field in ("id", "handle", "name"):
            val = str(item.get(field) or "").lower()
            if val == q or (field == "handle" and val.lstrip("@") == q.lstrip("@")):
                return idx, item
    return -1, None


def cmd_list():
    ch = load_yaml(CHANNELS_PATH, "channels")["channels"]
    cand = load_yaml(CANDIDATES_PATH, "candidates")["candidates"]
    print(f"=== 承認済みチャンネル ({len(ch)}件) ===")
    for c in ch:
        print(f"  [{c.get('status')}] {c.get('name')} ({c.get('handle')}) | {c.get('id')}")
    pending = [c for c in cand if c.get("status") == "pending_review"]
    rejected = [c for c in cand if c.get("status") == "rejected"]
    print(f"\n=== 確認待ち候補 ({len(pending)}件) ===")
    for c in pending:
        print(
            f"  [pending] {c.get('name')} ({c.get('handle')}) | subs={c.get('subscribers')} | "
            f"ja_sub={c.get('has_ja_subs')} | chat={c.get('has_live_chat')}"
        )
    if rejected:
        print(f"\n=== 却下済み ({len(rejected)}件) ===")
        for c in rejected:
            print(f"  [rejected] {c.get('name')} ({c.get('handle')}) : {c.get('reject_reason', '')}")


def cmd_approve(target: str):
    ch_data = load_yaml(CHANNELS_PATH, "channels")
    cand_data = load_yaml(CANDIDATES_PATH, "candidates")

    idx, item = find_item(cand_data["candidates"], target)
    if item is None:
        print(f"[エラー] 候補が見つかりません: {target}", file=sys.stderr)
        sys.exit(1)

    _, existing = find_item(ch_data["channels"], item["id"])
    if existing is not None:
        print(f"既に承認済みです: {existing['name']}")
        return

    approved = {
        "id": item["id"],
        "name": item["name"],
        "handle": item.get("handle"),
        "channel_url": item.get("channel_url"),
        "status": "approved",
        "approved_at": date.today().isoformat(),
        "guideline_source": item.get("guideline_source"),
        "guideline_excerpt": item.get("guideline_excerpt", []),
        "evidence": item.get("evidence"),
        "clip_tag": item.get("clip_tag"),
        "rules": {
            "required_credit": "元配信URL・チャンネルURLを概要欄に明記",
            "exclude": ["メンバーシップ限定", "歌枠・楽曲権利あり", "切り抜き禁止指定枠"],
        },
        "credit_template": (
            f"▼元チャンネル：{item['name']}\n"
            f"{item.get('channel_url')}\n\n"
            "▼元配信\n{source_url}\n"
            + (f"\n{item['clip_tag']}\n" if item.get("clip_tag") else "")
        ),
    }
    ch_data["channels"].append(approved)
    cand_data["candidates"].pop(idx)

    save_yaml(CHANNELS_PATH, ch_data)
    save_yaml(CANDIDATES_PATH, cand_data)
    print(f"承認しました → channels.yaml に追加: {approved['name']} ({approved['handle']})")


def cmd_reject(target: str, reason: str):
    cand_data = load_yaml(CANDIDATES_PATH, "candidates")
    _, item = find_item(cand_data["candidates"], target)
    if item is None:
        print(f"[エラー] 候補が見つかりません: {target}", file=sys.stderr)
        sys.exit(1)
    item["status"] = "rejected"
    item["reject_reason"] = reason
    save_yaml(CANDIDATES_PATH, cand_data)
    print(f"却下しました: {item['name']} ({item.get('handle')})")


def main():
    parser = argparse.ArgumentParser(description="切り抜き対象チャンネル管理")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p_app = sub.add_parser("approve")
    p_app.add_argument("target", help="channel_id / handle / name")
    p_rej = sub.add_parser("reject")
    p_rej.add_argument("target", help="channel_id / handle / name")
    p_rej.add_argument("--reason", default="")
    args = parser.parse_args()

    if args.cmd == "list":
        cmd_list()
    elif args.cmd == "approve":
        cmd_approve(args.target)
    elif args.cmd == "reject":
        cmd_reject(args.target, args.reason)


if __name__ == "__main__":
    main()
