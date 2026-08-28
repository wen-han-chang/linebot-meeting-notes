"""OpenAI 語音轉文字流程。"""

from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

from .audio import prepare
from .config import Settings

MAX_ATTEMPTS = 4
logger = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Transcript:
    text: str
    chunk_count: int


def _response_text(response: object) -> str:
    text = getattr(response, "text", None)
    return str(text if text is not None else response).strip()


def _transcribe_file(client: OpenAI, path: Path, settings: Settings) -> str:
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            with path.open("rb") as audio_file:
                response = client.audio.transcriptions.create(
                    model=settings.transcribe_model,
                    file=audio_file,
                    response_format="text",
                    prompt=settings.transcribe_prompt or None,
                )
            return _response_text(response)
        except (RateLimitError, APIConnectionError) as exc:
            last_error = exc
        except APIStatusError as exc:
            if exc.status_code < 500:
                raise TranscriptionError(
                    f"OpenAI 拒絕轉錄 {path.name}（HTTP {exc.status_code}）。"
                ) from exc
            last_error = exc

        if attempt < MAX_ATTEMPTS - 1:
            delay = 2 ** (attempt + 1)
            logger.warning(
                "OpenAI 轉錄暫時失敗，%s 秒後重試：file=%s attempt=%s/%s error=%s",
                delay,
                path.name,
                attempt + 1,
                MAX_ATTEMPTS,
                type(last_error).__name__,
            )
            time.sleep(delay)

    raise TranscriptionError(f"轉錄重試 {MAX_ATTEMPTS} 次仍失敗：{last_error}")


def transcribe_recording(source: Path, settings: Settings) -> Transcript:
    with tempfile.TemporaryDirectory(prefix="line_meeting_") as temporary:
        chunks = prepare(
            source,
            Path(temporary),
            max_chunk_seconds=settings.chunk_seconds,
            max_media_seconds=settings.max_media_seconds,
            ffmpeg_timeout_seconds=settings.ffmpeg_timeout_seconds,
        )
        # SDK 內建重試關閉，統一由 _transcribe_file 的有界重試控制。
        client = OpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.openai_timeout_seconds,
            max_retries=0,
        )
        parts = [_transcribe_file(client, chunk.path, settings) for chunk in chunks]

    text = "\n".join(part for part in parts if part).strip()
    if not text:
        raise TranscriptionError("錄音中沒有辨識到可用的語音內容。")
    return Transcript(text=text, chunk_count=len(chunks))
