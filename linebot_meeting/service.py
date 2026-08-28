"""單筆 LINE 音訊事件的背景處理流程。"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .audio import AudioProcessingError, MediaTooLongError
from .config import Settings
from .line_api import (
    FileTooLargeError,
    LineAPIError,
    LineClient,
    split_text,
    target_from_source,
)
from .minutes import create_minutes, render_minutes
from .transcription import TranscriptionError, transcribe_recording

logger = logging.getLogger(__name__)
AUDIO_FILE_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".mpeg",
    ".ogg",
    ".wav",
    ".webm",
}
VIDEO_FILE_EXTENSIONS = {
    ".3g2",
    ".3gp",
    ".avi",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mts",
    ".ts",
    ".webm",
    ".wmv",
}
ALLOWED_FILE_EXTENSIONS = AUDIO_FILE_EXTENSIONS | VIDEO_FILE_EXTENSIONS


def is_supported_message(message: dict[str, Any]) -> bool:
    if message.get("type") in {"audio", "video"}:
        return True
    if message.get("type") != "file":
        return False
    return (
        Path(str(message.get("fileName", ""))).suffix.lower() in ALLOWED_FILE_EXTENSIONS
    )


def is_video_message(message: dict[str, Any]) -> bool:
    """判斷 LINE 原生影片或以檔案附件傳送的影片。"""
    if message.get("type") == "video":
        return True
    if message.get("type") != "file":
        return False
    suffix = Path(str(message.get("fileName", ""))).suffix.lower()
    return suffix in VIDEO_FILE_EXTENSIONS


def user_error_message(exc: Exception, settings: Settings) -> str:
    """將內部錯誤轉成不洩漏憑證或第三方回應內容的使用者訊息。"""
    if isinstance(exc, FileTooLargeError):
        return f"❌ 影音檔超過 {settings.max_source_mb}MB，請壓縮後再傳。"
    if isinstance(exc, MediaTooLongError):
        return f"❌ 影音長度超過 {settings.max_media_minutes} 分鐘的處理上限。"
    if isinstance(exc, AudioProcessingError):
        return "❌ 無法讀取影音音軌，請確認檔案未損壞且包含聲音。"
    if isinstance(exc, TranscriptionError):
        return "❌ 語音轉錄服務暫時失敗，請稍後再傳一次。"
    if isinstance(exc, LineAPIError):
        return "❌ 從 LINE 下載影音失敗，請稍後重新傳送。"
    return "❌ 影音處理發生未預期錯誤，請稍後重試。"


def _save_record(
    settings: Settings,
    message_id: str,
    minutes_text: str | None,
    transcript: str,
) -> Path:
    settings.records_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = settings.records_dir / f"{timestamp}_{message_id}.md"
    sections = []
    if minutes_text:
        sections.extend([minutes_text, ""])
    sections.extend(["# 完整逐字稿", "", transcript])
    destination.write_text("\n".join(sections), encoding="utf-8")
    return destination


async def process_media_event(
    event: dict[str, Any],
    settings: Settings,
    line: LineClient,
) -> None:
    target = target_from_source(event.get("source", {}))
    message = event.get("message", {})
    message_id = str(message.get("id", ""))
    if not target or not message_id:
        logger.warning("事件缺少回覆目標或 message ID")
        return

    video = is_video_message(message)
    default_name = "video.mp4" if video else "recording.m4a"
    file_name = str(message.get("fileName", default_name))
    suffix = Path(file_name).suffix.lower() or (".mp4" if video else ".m4a")
    try:
        logger.info(
            "開始處理 LINE %s：message_id=%s file_name=%s",
            "影片" if video else "錄音",
            message_id,
            file_name,
        )
        with tempfile.TemporaryDirectory(prefix="line_download_") as temporary:
            source = Path(temporary) / f"source{suffix}"
            await line.download(message_id, source, settings.max_source_bytes)
            logger.info(
                "LINE 影音下載完成：message_id=%s size_bytes=%s",
                message_id,
                source.stat().st_size,
            )
            logger.info(
                "%s音軌壓縮與轉錄開始：message_id=%s",
                "影片" if video else "錄音",
                message_id,
            )
            transcript = await asyncio.to_thread(transcribe_recording, source, settings)
            logger.info(
                "語音轉錄完成：message_id=%s characters=%s",
                message_id,
                len(transcript.text),
            )

        minutes_text: str | None = None
        try:
            logger.info("會議摘要開始：message_id=%s", message_id)
            minutes = await asyncio.to_thread(create_minutes, transcript.text, settings)
            minutes_text = render_minutes(minutes)
        except Exception:
            logger.exception("會議摘要失敗，仍回傳逐字稿")

        try:
            _save_record(settings, message_id, minutes_text, transcript.text)
        except OSError:
            # Render Free 的檔案系統是暫時性的；保存失敗不應擋住 LINE 回傳。
            logger.exception(
                "會議記錄寫入本機失敗，仍繼續推送：message_id=%s", message_id
            )

        messages: list[str] = []
        if minutes_text:
            messages.extend(split_text(minutes_text))
        else:
            messages.append("⚠️ 摘要產生失敗，但語音轉文字已完成。")

        transcript_parts = split_text(f"【完整逐字稿】\n{transcript.text}")
        limit = settings.max_transcript_messages
        if limit == 0:
            transcript_parts = []
        elif len(transcript_parts) > limit:
            transcript_parts = transcript_parts[:limit]
            transcript_parts.append(
                "⚠️ 逐字稿太長，LINE 僅顯示前段；完整版本已保存在伺服器 records 目錄。"
            )
        messages.extend(transcript_parts)
        await line.push(target, messages)
        logger.info("LINE 會議記錄推送完成：message_id=%s", message_id)
    except Exception as exc:
        logger.exception("處理 LINE 影音失敗：message_id=%s", message_id)
        try:
            await line.push(
                target,
                [user_error_message(exc, settings)],
            )
        except Exception:
            logger.exception("錯誤通知也無法送出")
