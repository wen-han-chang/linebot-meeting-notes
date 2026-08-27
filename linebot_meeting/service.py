"""單筆 LINE 音訊事件的背景處理流程。"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .line_api import LineClient, split_text, target_from_source
from .minutes import create_minutes, render_minutes
from .transcription import transcribe_recording

logger = logging.getLogger(__name__)
ALLOWED_FILE_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".ogg",
    ".wav",
    ".webm",
}


def is_supported_message(message: dict[str, Any]) -> bool:
    if message.get("type") == "audio":
        return True
    if message.get("type") != "file":
        return False
    return (
        Path(str(message.get("fileName", ""))).suffix.lower() in ALLOWED_FILE_EXTENSIONS
    )


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

    file_name = str(message.get("fileName", "recording.m4a"))
    suffix = Path(file_name).suffix.lower() or ".m4a"
    try:
        with tempfile.TemporaryDirectory(prefix="line_download_") as temporary:
            source = Path(temporary) / f"source{suffix}"
            await line.download(message_id, source, settings.max_source_bytes)
            transcript = await asyncio.to_thread(transcribe_recording, source, settings)

        minutes_text: str | None = None
        try:
            minutes = await asyncio.to_thread(create_minutes, transcript.text, settings)
            minutes_text = render_minutes(minutes)
        except Exception:
            logger.exception("會議摘要失敗，仍回傳逐字稿")

        _save_record(settings, message_id, minutes_text, transcript.text)

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
    except Exception as exc:
        logger.exception("處理 LINE 音訊失敗")
        try:
            await line.push(
                target,
                [f"❌ 這份錄音處理失敗：{type(exc).__name__}。請確認格式或稍後重試。"],
            )
        except Exception:
            logger.exception("錯誤通知也無法送出")
