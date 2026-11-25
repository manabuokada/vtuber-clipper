#!/usr/bin/env python3
"""
work/clips/ 配下の動画・中間ファイル・確認用画像を整理するクリーンアップ CLI。

安全設計:
- デフォルトは dry-run（削除候補と解放見込み容量の一覧表示のみ）。`--apply` 指定時のみ削除。
- 削除対象は明示ホワイトリスト（*.mp4 / verify_*.jpg / layout_probe.jpg）に限定。
- work/evidence/・config/・work/assets/ および設定・メタデータ・字幕ファイル
  （*.yaml / metadata.json / highlights.json / *.ass / *.vtt / *.live_chat.json 等）は厳格保護。

使い方:
  # 削除候補の確認（dry-run）
  ./.venv/bin/python scripts/maintenance/cleanup_work.py
  # 中間ファイルと確認用画像のみ削除
  ./.venv/bin/python scripts/maintenance/cleanup_work.py --intermediates-only --apply
  # 7日以上前の動画・確認用画像を削除
  ./.venv/bin/python scripts/maintenance/cleanup_work.py --older-than 7 --apply
  # work/clips/ の対象ファイルをすべて削除
  ./.venv/bin/python scripts/maintenance/cleanup_work.py --apply
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fnmatch
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGET_DIR = ROOT / "work" / "clips"

PROTECTED_DIRS = (
    (ROOT / "work" / "evidence").resolve(),
    (ROOT / "config").resolve(),
    (ROOT / "work" / "assets").resolve(),
)

INTERMEDIATE_PATTERNS = (
    "part_*.mp4",
    "joined_xfade.mp4",
    "verify_*.jpg",
    "layout_probe.jpg",
)

FULL_PATTERNS = INTERMEDIATE_PATTERNS + ("*.mp4",)


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    size_bytes: int
    age_days: float


def format_size(num_bytes: int) -> str:
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.2f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes} B"


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def is_protected_path(path: Path) -> bool:
    resolved = path.resolve()
    for pdir in PROTECTED_DIRS:
        if is_within(resolved, pdir):
            return True
    return False


def matches_cleanup_target(path: Path, intermediates_only: bool = False) -> bool:
    patterns = INTERMEDIATE_PATTERNS if intermediates_only else FULL_PATTERNS
    name = path.name
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)


def collect_candidates(
    target_dir: Path,
    *,
    older_than_days: float | None = None,
    intermediates_only: bool = False,
    now: float | None = None,
) -> list[CleanupCandidate]:
    target_resolved = target_dir.resolve()
    if is_protected_path(target_resolved):
        raise ValueError(f"保護対象ディレクトリはクリーンアップできません: {target_dir}")
    if not target_resolved.exists():
        return []

    ref_time = time.time() if now is None else now
    candidates: list[CleanupCandidate] = []

    for path in sorted(target_resolved.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if is_protected_path(path):
            continue
        if not matches_cleanup_target(path, intermediates_only=intermediates_only):
            continue

        stat = path.stat()
        age_days = max(0.0, (ref_time - stat.st_mtime) / 86400.0)
        if older_than_days is not None and age_days < older_than_days:
            continue

        candidates.append(
            CleanupCandidate(
                path=path,
                size_bytes=stat.st_size,
                age_days=age_days,
            )
        )

    return candidates


def delete_candidates(candidates: list[CleanupCandidate]) -> tuple[int, int, list[tuple[Path, str]]]:
    deleted_count = 0
    freed_bytes = 0
    errors: list[tuple[Path, str]] = []

    for item in candidates:
        try:
            item.path.unlink()
            deleted_count += 1
            freed_bytes += item.size_bytes
        except OSError as e:
            errors.append((item.path, str(e)))

    return deleted_count, freed_bytes, errors


def display_path(path: Path, base_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        try:
            return str(path.resolve().relative_to(base_dir.resolve()))
        except ValueError:
            return path.name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="work/clips/ 配下の動画・中間ファイル・確認用画像を整理するツール（デフォルト dry-run）"
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET_DIR,
        help="クリーンアップ対象ディレクトリ（デフォルト: work/clips）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="実際にファイルを削除する（未指定時は dry-run で一覧表示のみ）",
    )
    parser.add_argument(
        "--older-than",
        type=float,
        default=None,
        metavar="DAYS",
        help="最終更新から指定日数以上経過したファイルのみ対象にする",
    )
    parser.add_argument(
        "--intermediates-only",
        action="store_true",
        help="中間ファイル・確認用画像（part_*.mp4 / joined_xfade.mp4 / verify_*.jpg / layout_probe.jpg）のみ対象にする",
    )
    args = parser.parse_args()

    if args.older_than is not None and args.older_than < 0:
        print("[エラー] --older-than には 0 以上の数値を指定してください。", file=sys.stderr)
        sys.exit(1)

    target_dir = args.target if args.target.is_absolute() else (ROOT / args.target)
    try:
        candidates = collect_candidates(
            target_dir,
            older_than_days=args.older_than,
            intermediates_only=args.intermediates_only,
        )
    except ValueError as e:
        print(f"[エラー] {e}", file=sys.stderr)
        sys.exit(1)

    mode_label = "APPLY (削除実行)" if args.apply else "DRY RUN (削除しません)"
    scope_label = "中間ファイル・確認画像のみ" if args.intermediates_only else "全動画・中間ファイル・確認画像"
    age_label = f"{args.older_than:g}日以上前" if args.older_than is not None else "経過日数指定なし"
    print(f"=== cleanup_work [{mode_label}] ===")
    print(f"対象ディレクトリ: {display_path(target_dir, target_dir)}")
    print(f"対象範囲        : {scope_label} ({age_label})")

    if not candidates:
        print("\n対象ファイルはありませんでした。")
        return

    total_bytes = sum(c.size_bytes for c in candidates)
    print()
    for c in candidates:
        rel = display_path(c.path, target_dir)
        print(f"  - {rel} ({format_size(c.size_bytes)}, {c.age_days:.1f}日前)")

    print(f"\n対象合計: {len(candidates)} 件 / {format_size(total_bytes)}")

    if not args.apply:
        print("※ dry-run のため削除していません。実行するには --apply を付けてください。")
        return

    deleted_count, freed_bytes, errors = delete_candidates(candidates)
    print(f"削除完了: {deleted_count} 件 / 解放容量 {format_size(freed_bytes)}")
    if errors:
        for path, err in errors:
            print(f"[警告] 削除失敗: {display_path(path, target_dir)} ({err})", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
