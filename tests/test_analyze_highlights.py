"""analyze_highlights.py の動画ID別キャッシュ分離（Issue #11）に関するユニットテスト。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "production"))

import analyze_highlights as ah  # noqa: E402


SAMPLE_VTT = """WEBVTT

00:01:00.000 --> 00:01:04.000
これは過去配信の字幕テキストです
"""

SAMPLE_CHAT_LINE = json.dumps(
    {
        "replayChatItemAction": {
            "videoOffsetTimeMsec": "65000",
            "actions": [
                {
                    "addChatItemAction": {
                        "item": {
                            "liveChatTextMessageRenderer": {
                                "message": {"runs": [{"text": "過去配信のチャット草"}]}
                            }
                        }
                    }
                }
            ],
        }
    },
    ensure_ascii=False,
)


class AnalyzeHighlightsCacheIsolationTest(unittest.TestCase):
    """字幕・チャットキャッシュが動画ID単位で正しく分離されることを検証する。"""

    def test_extract_video_id_patterns(self) -> None:
        """各YouTube URL形式およびフォールバックから動画IDを一意に抽出できること。"""
        # yt-dlp メタデータに id が含まれる場合は URL より優先して採用する
        self.assertEqual(
            ah.extract_video_id("https://www.youtube.com/watch?v=abc123XYZ01", {"id": "metaId00001"}),
            "metaId00001",
        )
        # 通常の視聴 URL（watch?v=...）から動画IDを抽出できる
        self.assertEqual(
            ah.extract_video_id("https://www.youtube.com/watch?v=abc123XYZ01&t=12s", {}),
            "abc123XYZ01",
        )
        # 短縮 URL（youtu.be/...）から動画IDを抽出できる
        self.assertEqual(
            ah.extract_video_id("https://youtu.be/shortId9999?si=foo", None),
            "shortId9999",
        )
        # ライブ配信 URL（/live/...）から動画IDを抽出できる
        self.assertEqual(
            ah.extract_video_id("https://www.youtube.com/live/liveId77777?feature=share", None),
            "liveId77777",
        )
        # Shorts URL（/shorts/...）から動画IDを抽出できる
        self.assertEqual(
            ah.extract_video_id("https://www.youtube.com/shorts/clipId55555", None),
            "clipId55555",
        )
        # YouTube 以外の未知 URL でも url_<8桁ハッシュ> へフォールバックする
        fallback = ah.extract_video_id("https://example.com/custom/stream", None)
        self.assertTrue(fallback.startswith("url_"))
        self.assertEqual(len(fallback), 12)

    def test_new_stream_without_subs_does_not_read_stale_caches(self) -> None:
        """字幕・チャット未生成の新着配信で過去配信のキャッシュを誤読込しないこと（Issue #11 回帰テスト）。"""
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            handle = "sample_vtuber"

            # 旧仕様のチャンネル固定ディレクトリに過去配信キャッシュが存在する状態を再現
            legacy_dir = work_dir / handle
            legacy_dir.mkdir(parents=True)
            (legacy_dir / "source.ja.vtt").write_text(SAMPLE_VTT, encoding="utf-8")
            (legacy_dir / "source.live_chat.json").write_text(SAMPLE_CHAT_LINE + "\n", encoding="utf-8")

            # 別動画 ID のキャッシュディレクトリも存在する状態を再現
            old_video_dir = work_dir / f"{handle}_OLDVIDEO001"
            old_video_dir.mkdir(parents=True)
            (old_video_dir / "source.ja.vtt").write_text(SAMPLE_VTT, encoding="utf-8")
            (old_video_dir / "source.live_chat.json").write_text(SAMPLE_CHAT_LINE + "\n", encoding="utf-8")

            ch = {
                "id": "UC_SAMPLE_00000000000000",
                "handle": f"@{handle}",
                "name": "Sample VTuber",
                "channel_url": f"https://www.youtube.com/@{handle}",
                "guideline_source": "https://www.youtube.com/watch?v=OLDVIDEO001",
            }
            new_url = "https://www.youtube.com/watch?v=NEWVIDEO002"
            captured_cmd: list[str] = []

            def fake_run(cmd, **kwargs):
                captured_cmd.extend(cmd)
                # 最新配信にまだ字幕・チャットが生成されていない状態を模倣（ファイル未出力）
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with (
                patch.object(ah, "WORK_DIR", work_dir),
                patch.object(ah, "fetch_latest_archive_url", return_value=new_url),
                patch.object(
                    ah,
                    "fetch_video_meta",
                    return_value={
                        "id": "NEWVIDEO002",
                        "title": "最新配信テスト",
                        "upload_date": "20261127",
                        "duration": 3600,
                    },
                ),
                patch.object(ah.subprocess, "run", side_effect=fake_run),
            ):
                summary = ah.process_channel(ch)

            # 過去キャッシュを拾わず「字幕・チャットなし・ピーク空」として返ること
            self.assertEqual(summary["video_id"], "NEWVIDEO002")
            self.assertEqual(summary["video_url"], new_url)
            self.assertFalse(summary["has_vtt"])
            self.assertFalse(summary["has_chat"])
            self.assertEqual(summary["peaks"], [])
            # 同名残骸ファイル対策として yt-dlp に --force-overwrites が渡されていること
            self.assertIn("--force-overwrites", captured_cmd)

            # 新着配信専用ディレクトリへ highlights.json が保存され、内容も矛盾しないこと
            new_dir = work_dir / f"{handle}_NEWVIDEO002"
            self.assertTrue((new_dir / "highlights.json").exists())
            saved = json.loads((new_dir / "highlights.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["video_id"], "NEWVIDEO002")
            self.assertFalse(saved["has_vtt"])
            self.assertFalse(saved["has_chat"])
            self.assertEqual(saved["peaks"], [])

    def test_two_videos_from_same_channel_are_isolated(self) -> None:
        """同一チャンネルの別配信を連続解析しても出力先・字幕・結果が混線しないこと。"""
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            handle = "sample_vtuber"
            ch = {
                "id": "UC_SAMPLE_00000000000000",
                "handle": f"@{handle}",
                "name": "Sample VTuber",
                "channel_url": f"https://www.youtube.com/@{handle}",
                "guideline_source": "https://www.youtube.com/watch?v=VIDEOAAA001",
            }

            def make_fake_run(vtt_text: str):
                # yt-dlp が -o で指定された出力先プレフィックス配下に字幕・チャットを保存する挙動を模倣
                def _fake_run(cmd, **kwargs):
                    out_idx = cmd.index("-o") + 1
                    out_prefix = Path(cmd[out_idx])
                    out_prefix.with_name(out_prefix.name + ".ja.vtt").write_text(vtt_text, encoding="utf-8")
                    out_prefix.with_name(out_prefix.name + ".live_chat.json").write_text(
                        SAMPLE_CHAT_LINE + "\n", encoding="utf-8"
                    )
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

                return _fake_run

            # 1本目（配信A）の解析
            with (
                patch.object(ah, "WORK_DIR", work_dir),
                patch.object(
                    ah,
                    "fetch_video_meta",
                    return_value={"id": "VIDEOAAA001", "title": "配信A", "upload_date": "20261125", "duration": 1800},
                ),
                patch.object(
                    ah.subprocess,
                    "run",
                    side_effect=make_fake_run("WEBVTT\n\n00:01:02.000 --> 00:01:06.000\n配信Aの発言\n"),
                ),
            ):
                res_a = ah.process_channel(ch, override_url="https://www.youtube.com/watch?v=VIDEOAAA001")

            # 2本目（配信B）の解析
            with (
                patch.object(ah, "WORK_DIR", work_dir),
                patch.object(
                    ah,
                    "fetch_video_meta",
                    return_value={"id": "VIDEOBBB002", "title": "配信B", "upload_date": "20261127", "duration": 1800},
                ),
                patch.object(
                    ah.subprocess,
                    "run",
                    side_effect=make_fake_run("WEBVTT\n\n00:01:02.000 --> 00:01:06.000\n配信Bの発言\n"),
                ),
            ):
                res_b = ah.process_channel(ch, override_url="https://www.youtube.com/watch?v=VIDEOBBB002")

            # 配信A・B それぞれ自身の字幕テキストが抽出され、互いに上書きされないこと
            self.assertTrue(res_a["has_vtt"] and res_a["has_chat"])
            self.assertTrue(res_b["has_vtt"] and res_b["has_chat"])
            self.assertIn("[01:02] 配信Aの発言", res_a["peaks"][0]["transcript"])
            self.assertIn("[01:02] 配信Bの発言", res_b["peaks"][0]["transcript"])
            # 動画IDごとの独立ディレクトリに highlights.json が個別に生成されていること
            self.assertTrue((work_dir / f"{handle}_VIDEOAAA001" / "highlights.json").exists())
            self.assertTrue((work_dir / f"{handle}_VIDEOBBB002" / "highlights.json").exists())


if __name__ == "__main__":
    unittest.main()
