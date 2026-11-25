#!/usr/bin/env python3
"""
YouTube Data API v3 を使って切り抜き動画をアップロードするスクリプト（方法A）。

事前準備:
  1. Google Cloud Console でプロジェクトを作成し「YouTube Data API v3」を有効化
  2. OAuth 同意画面を設定し、テストユーザーに自分の Google アカウントを追加
  3. 認証情報 →「OAuth クライアント ID」（アプリケーションの種類: デスクトップ アプリ）を作成
  4. JSON をダウンロードし、`config/client_secret.json` として配置

使い方:
  ./.venv/bin/python scripts/publish/upload_youtube.py --video work/clips/<name>/edited_shorts.mp4 --meta work/clips/<name>/metadata.json
  # テスト確認のみ（アップロードせず設定と認証チェック）:
  ./.venv/bin/python scripts/publish/upload_youtube.py --video work/clips/<name>/edited_shorts.mp4 --meta work/clips/<name>/metadata.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = BASE_DIR / "config"
CONFIG_PATH = CONFIG_DIR / "upload_config.json"


def check_channel_lock(youtube, lock_channel: bool = False) -> tuple[str, str]:
    resp = youtube.channels().list(part="snippet", mine=True).execute()
    items = resp.get("items") or []
    if not items:
        print(
            "[エラー] この Google アカウントには YouTube チャンネルがまだ作成されていません。\n"
            "YouTube を開いて右上アイコンから「チャンネルを作成」を行ってから再実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)
    ch_id = items[0]["id"]
    ch_title = items[0]["snippet"]["title"]

    if lock_channel:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
        cfg["allowed_channel_id"] = ch_id
        cfg["allowed_channel_title"] = ch_title
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"投稿先チャンネルをロックしました: {ch_title} ({ch_id})")
        return ch_id, ch_title

    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        allowed = cfg.get("allowed_channel_id")
        if allowed and allowed != ch_id:
            print(
                f"[誤爆防止停止] 認証チャンネル ({ch_title} / {ch_id}) が、"
                f"ロック済みチャンネル ({cfg.get('allowed_channel_title')} / {allowed}) と一致しません！",
                file=sys.stderr,
            )
            sys.exit(1)

    print(f"認証チャンネル確認OK: {ch_title} ({ch_id})")
    return ch_id, ch_title


def get_authenticated_service(client_secret_path: Path, token_path: Path):
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not client_secret_path.exists():
                print(
                    f"[エラー] OAuth クライアントシークレットが見つかりません: {client_secret_path}\n"
                    "Google Cloud Console からデスクトップアプリ用の OAuth JSON をダウンロードし、"
                    f"`{client_secret_path}` に配置してください。",
                    file=sys.stderr,
                )
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secret_path), SCOPES
            )
            creds = flow.run_local_server(port=0)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return build("youtube", "v3", credentials=creds)


def upload_video(youtube, video_path: Path, meta: dict, thumbnail_path: Path | None):
    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta.get("tags", []),
            "categoryId": str(meta.get("categoryId", "25")),  # 25 = News & Politics
            "defaultLanguage": meta.get("defaultLanguage", "ja"),
        },
        "status": {
            "privacyStatus": meta.get("privacyStatus", "private"),
            "selfDeclaredMadeForKids": meta.get("madeForKids", False),
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=8 * 1024 * 1024,
        resumable=True,
    )

    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    print(f"アップロード開始: {video_path.name}")
    print(f"タイトル: {body['snippet']['title']}")
    print(f"公開設定: {body['status']['privacyStatus']}")

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"  進捗: {int(status.progress() * 100)}%")

    video_id = response["id"]
    print(f"\nアップロード完了! Video ID: {video_id}")
    print(f"YouTube Studio: https://studio.youtube.com/video/{video_id}/edit")
    print(f"視聴URL: https://www.youtube.com/watch?v={video_id}")

    if thumbnail_path and thumbnail_path.exists():
        print(f"サムネイルを設定中: {thumbnail_path.name}")
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg"),
            ).execute()
            print("サムネイル設定完了!")
        except HttpError as e:
            print(f"[注意] サムネイル設定をスキップしました（Shorts仕様またはチャンネル未認証の可能性）: {e}")

    return video_id


def resolve_notify_url(cli_url: str | None) -> str | None:
    if cli_url:
        return cli_url.strip()
    env_url = os.environ.get("NOTIFY_WEBHOOK_URL", "").strip()
    if env_url:
        return env_url
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            url = str(cfg.get("notify_webhook_url") or "").strip()
            if url:
                return url
        except Exception:
            pass
    return None


def send_webhook_notification(
    webhook_url: str,
    channel_title: str,
    video_title: str,
    video_id: str,
    privacy: str,
) -> bool:
    studio_url = f"https://studio.youtube.com/video/{video_id}/edit"
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    host = (urlparse(webhook_url).hostname or "").lower()

    try:
        if "discord.com" in host or "discordapp.com" in host:
            payload = {
                "embeds": [
                    {
                        "title": "YouTube アップロード完了",
                        "description": f"**{video_title}**",
                        "color": 0x5865F2,
                        "fields": [
                            {"name": "チャンネル", "value": channel_title, "inline": True},
                            {"name": "公開設定", "value": privacy, "inline": True},
                            {"name": "YouTube Studio（確認・公開）", "value": studio_url, "inline": False},
                            {"name": "視聴URL", "value": watch_url, "inline": False},
                        ],
                    }
                ]
            }
            resp = requests.post(webhook_url, json=payload, timeout=15)
        elif "hooks.slack.com" in host:
            text = (
                f"*YouTube アップロード完了* ({channel_title})\n"
                f"• タイトル: {video_title}\n"
                f"• 公開設定: `{privacy}`\n"
                f"• Studio: {studio_url}\n"
                f"• 視聴URL: {watch_url}"
            )
            resp = requests.post(webhook_url, json={"text": text}, timeout=15)
        elif "ntfy.sh" in host or host.startswith("ntfy."):
            body = f"[{channel_title}] ({privacy})\n{video_title}\n{studio_url}"
            resp = requests.post(
                webhook_url,
                data=body.encode("utf-8"),
                headers={
                    "Title": "YouTube Upload Complete",
                    "Click": studio_url,
                    "Tags": "clapper",
                },
                timeout=15,
            )
        else:
            payload = {
                "event": "youtube_upload_complete",
                "channel": channel_title,
                "title": video_title,
                "video_id": video_id,
                "privacy": privacy,
                "studio_url": studio_url,
                "watch_url": watch_url,
            }
            resp = requests.post(webhook_url, json=payload, timeout=15)

        if 200 <= resp.status_code < 300:
            print(f"通知送信完了 ({host})")
            return True
        print(f"[注意] 通知送信が失敗しました (HTTP {resp.status_code}): {resp.text[:200]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[注意] 通知送信中にエラーが発生しました: {e}", file=sys.stderr)
        return False


def cleanup_uploaded_video(video_path: Path) -> bool:
    if not video_path.exists():
        return False
    try:
        size_mb = video_path.stat().st_size / (1024 * 1024)
        video_path.unlink()
        print(f"ローカル動画ファイルを削除しました: {video_path.name} ({size_mb:.2f} MB)")
        return True
    except OSError as e:
        print(f"[注意] ローカル動画ファイルの削除に失敗しました: {video_path} ({e})", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="YouTube Data API v3 動画アップローダー")
    parser.add_argument("--video", type=Path, default=BASE_DIR / "edited_clip.mp4")
    parser.add_argument("--meta", type=Path, default=BASE_DIR / "metadata.json")
    parser.add_argument("--thumbnail", type=Path, default=BASE_DIR / "thumbnail.jpg")
    parser.add_argument(
        "--client-secret", type=Path, default=CONFIG_DIR / "client_secret.json"
    )
    parser.add_argument("--token", type=Path, default=CONFIG_DIR / "token.json")
    parser.add_argument(
        "--privacy",
        choices=["private", "unlisted", "public"],
        default=None,
        help="metadata.json の privacyStatus を上書き",
    )
    parser.add_argument("--dry-run", action="store_true", help="アップロードせず内容確認のみ")
    parser.add_argument("--cleanup", action="store_true", help="アップロード成功後にローカル動画ファイル（--video）を削除")
    parser.add_argument("--auth-and-lock", action="store_true", help="初回認証して投稿先チャンネルIDをロックするだけ")
    parser.add_argument("--notify-url", default=None, help="完了通知先 Webhook URL（Discord / Slack / ntfy.sh 自動判別）")
    parser.add_argument("--no-notify", action="store_true", help="完了通知をスキップ")
    parser.add_argument("--test-notify", action="store_true", help="アップロードせず Webhook 通知テストのみ実行")
    args = parser.parse_args()

    if args.test_notify:
        url = resolve_notify_url(args.notify_url)
        if not url:
            print(
                "[エラー] 通知先 Webhook URL が未設定です。\n"
                "--notify-url / 環境変数 NOTIFY_WEBHOOK_URL / upload_config.json の notify_webhook_url のいずれかを設定してください。",
                file=sys.stderr,
            )
            sys.exit(1)
        ok = send_webhook_notification(
            url,
            channel_title="テストチャンネル",
            video_title="Webhook 通知テスト動画 #Shorts",
            video_id="dQw4w9WgXcQ",
            privacy="private",
        )
        sys.exit(0 if ok else 1)

    if args.auth_and_lock:
        youtube = get_authenticated_service(args.client_secret, args.token)
        os.chmod(args.token, 0o600)
        check_channel_lock(youtube, lock_channel=True)
        return

    if not args.video.exists():
        print(f"[エラー] 動画ファイルが見つかりません: {args.video}", file=sys.stderr)
        sys.exit(1)
    if not args.meta.exists():
        print(f"[エラー] メタデータファイルが見つかりません: {args.meta}", file=sys.stderr)
        sys.exit(1)

    meta = json.loads(args.meta.read_text(encoding="utf-8"))
    if args.privacy:
        meta["privacyStatus"] = args.privacy

    if args.dry_run:
        print("=== DRY RUN ===")
        print(f"Video     : {args.video} ({args.video.stat().st_size / (1024*1024):.2f} MB)")
        print(f"Thumbnail : {args.thumbnail if args.thumbnail.exists() else '(なし)'}")
        print(f"Secret    : {'OK' if args.client_secret.exists() else '未配置 (' + str(args.client_secret) + ')'}")
        print(f"Notify    : {'OFF (--no-notify)' if args.no_notify else ('設定あり' if resolve_notify_url(args.notify_url) else '未設定')}")
        print(f"Cleanup   : {'ON (--cleanup)' if args.cleanup else 'OFF'}")
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        return

    youtube = get_authenticated_service(args.client_secret, args.token)
    os.chmod(args.token, 0o600)
    _, ch_title = check_channel_lock(youtube, lock_channel=False)
    try:
        video_id = upload_video(youtube, args.video, meta, args.thumbnail if args.thumbnail.exists() else None)
    except HttpError as e:
        print(f"[YouTube API エラー] {e}", file=sys.stderr)
        sys.exit(1)

    if not args.no_notify:
        notify_url = resolve_notify_url(args.notify_url)
        if notify_url:
            send_webhook_notification(
                notify_url,
                channel_title=ch_title,
                video_title=meta["title"],
                video_id=video_id,
                privacy=meta.get("privacyStatus", "private"),
            )

    if args.cleanup:
        cleanup_uploaded_video(args.video)


if __name__ == "__main__":
    main()
