#!/usr/bin/env python3
"""
切り抜き許諾のエビデンス（動画概要欄全文・外部ガイドラインページ・取得日時・SHA-256ハッシュ）を
evidence/<channel_id>/<YYYY-MM-DD>/ に保全し、candidates.yaml へ紐付けるスクリプト。

使い方:
  ./.venv/bin/python capture_evidence.py @handle1 @handle2
  ./.venv/bin/python capture_evidence.py --all-strong
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.request
import yaml

BASE_DIR = Path(__file__).resolve().parent
CANDIDATES_PATH = BASE_DIR / "candidates.yaml"
EVIDENCE_ROOT = BASE_DIR / "evidence"
JST = timezone(timedelta(hours=9))

URL_RE = re.compile(r"https?://[^\s\)\]>」』]+")
GUIDELINE_HINT_RE = re.compile(r"ガイドライン|規約|利用|二次創作|切り抜き|ルール|lit\.link|profcard|notion|note\.com", re.I)
CONDITION_RE = re.compile(r"切り抜き|二次創作|ガイドライン|規約|禁止|NG|ＮＧ|注意|悪意|歌枠|コラボ|収益|クレジット|URL|リンク|タグ", re.I)


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip = False
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self._skip = True
        elif tag in {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            t = data.strip()
            if t:
                self.parts.append(t)

    def get_text(self) -> str:
        raw = " ".join(self.parts)
        lines = [re.sub(r"\s+", " ", l).strip() for l in raw.splitlines()]
        return "\n".join(l for l in lines if l)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_url_text(url: str) -> tuple[str | None, str | None]:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"},
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            text = raw.decode("utf-8", errors="replace")
            if "html" in ctype.lower() or "<html" in text[:500].lower():
                p = TextExtractor()
                p.feed(text)
                return p.get_text(), sha256_bytes(raw)
            return text, sha256_bytes(raw)
    except Exception as e:
        return None, f"fetch_error: {e}"


def find_guideline_urls(desc: str) -> list[str]:
    lines = desc.splitlines()
    found = []
    for i, line in enumerate(lines):
        urls = URL_RE.findall(line)
        if not urls:
            continue
        context = " ".join(lines[max(0, i - 2) : min(len(lines), i + 2)])
        for u in urls:
            if any(skip in u for skip in ("youtube.com/watch", "youtu.be", "x.com/", "twitter.com/", "marshmallow-qa.com", "instagram.com", "tiktok.com")):
                continue
            if GUIDELINE_HINT_RE.search(context) or GUIDELINE_HINT_RE.search(u):
                if u not in found:
                    found.append(u)
    return found


def capture_one(cand: dict) -> dict:
    now = datetime.now(JST)
    day_str = now.strftime("%Y-%m-%d")
    cid = cand["id"]
    ev_dir = EVIDENCE_ROOT / cid / day_str
    ev_dir.mkdir(parents=True, exist_ok=True)

    video_url = cand["sample_video"]["url"]
    p = subprocess.run(
        ["yt-dlp", "--dump-single-json", "--skip-download", "--no-warnings", "--no-playlist", video_url],
        capture_output=True,
        text=True,
        timeout=35,
    )
    if p.returncode != 0 or not p.stdout.strip():
        return {"error": f"yt-dlp failed for {video_url}", "channel": cand["name"]}

    meta = json.loads(p.stdout)
    desc = meta.get("description") or ""
    desc_bytes = desc.encode("utf-8")
    desc_path = ev_dir / "video_description.txt"
    desc_path.write_bytes(desc_bytes)

    matched_lines = [l.strip() for l in desc.splitlines() if CONDITION_RE.search(l)]
    ext_urls = find_guideline_urls(desc)
    external_docs = []
    for idx, u in enumerate(ext_urls[:3], 1):
        txt, h = fetch_url_text(u)
        if txt:
            out_file = ev_dir / f"guideline_external_{idx}.txt"
            out_file.write_text(f"Source URL: {u}\nCaptured At: {now.isoformat()}\n\n{txt}\n", encoding="utf-8")
            ext_matched = [l.strip() for l in txt.splitlines() if CONDITION_RE.search(l)][:15]
            external_docs.append({
                "url": u,
                "saved_file": out_file.relative_to(BASE_DIR).as_posix(),
                "sha256": h,
                "matched_lines": ext_matched,
            })
        else:
            external_docs.append({"url": u, "error": h})

    record = {
        "captured_at": now.isoformat(),
        "channel_id": cid,
        "channel_name": cand["name"],
        "handle": cand.get("handle"),
        "channel_url": cand.get("channel_url"),
        "source_video_url": video_url,
        "source_video_title": meta.get("title"),
        "source_video_upload_date": meta.get("upload_date"),
        "description_file": desc_path.relative_to(BASE_DIR).as_posix(),
        "description_sha256": sha256_bytes(desc_bytes),
        "matched_lines": matched_lines,
        "external_guidelines": external_docs,
    }
    ev_json_path = ev_dir / "evidence.json"
    ev_json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    cand["evidence"] = {
        "captured_at": now.isoformat(),
        "evidence_json": ev_json_path.relative_to(BASE_DIR).as_posix(),
        "description_file": desc_path.relative_to(BASE_DIR).as_posix(),
        "description_sha256": record["description_sha256"],
        "external_guideline_urls": ext_urls,
    }
    return record


def main():
    parser = argparse.ArgumentParser(description="切り抜き許諾エビデンス保全ツール")
    parser.add_argument("targets", nargs="*", help="対象の handle / channel_id / name")
    parser.add_argument("--all-strong", action="store_true", help="strong_welcome または explicit_clip_ok な小規模候補を全取得")
    args = parser.parse_args()

    data = yaml.safe_load(CANDIDATES_PATH.read_text(encoding="utf-8"))
    cands = data.get("candidates", [])

    selected = []
    if args.all_strong:
        selected = [c for c in cands if c.get("explicit_clip_ok") and (c.get("subscribers") or 99999) <= 2000]
    else:
        qset = {t.lower().lstrip("@") for t in args.targets}
        for c in cands:
            keys = {
                str(c.get("id") or "").lower(),
                str(c.get("handle") or "").lower().lstrip("@"),
                str(c.get("name") or "").lower(),
            }
            if keys & qset:
                selected.append(c)

    if not selected:
        print("対象候補が見つかりませんでした。", file=sys.stderr)
        sys.exit(1)

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(capture_one, selected))

    CANDIDATES_PATH.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )

    for r in results:
        if "error" in r:
            print(f"[ERROR] {r['channel']}: {r['error']}")
            continue
        print(f"\n=== {r['channel_name']} ({r['handle']}) ===")
        print(f"Evidence JSON : {r['description_file'].replace('video_description.txt', 'evidence.json')}")
        print(f"Desc SHA-256  : {r['description_sha256'][:16]}...")
        print("概要欄の規約・条件抜粋:")
        for line in r["matched_lines"][:12]:
            print(f"  - {line}")
        if r["external_guidelines"]:
            print("外部ガイドライン取得結果:")
            for ext in r["external_guidelines"]:
                if "saved_file" in ext:
                    print(f"  [OK] {ext['url']} -> {ext['saved_file']}")
                    for el in ext["matched_lines"][:8]:
                        print(f"       * {el[:100]}")
                else:
                    print(f"  [WARN] {ext['url']} ({ext.get('error')})")


if __name__ == "__main__":
    main()
