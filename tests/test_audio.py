import subprocess
from pathlib import Path

import pytest

from linebot_meeting.audio import (
    SAFE_UPLOAD_BYTES,
    AudioProcessingError,
    MediaTooLongError,
    _run,
    plan_chunk_seconds,
    prepare,
)


def test_duration_limit_splits_small_long_file() -> None:
    assert (
        plan_chunk_seconds(
            size_bytes=1_000_000,
            duration=3_600,
            max_duration=600,
        )
        == 600
    )


def test_size_limit_keeps_chunk_safe() -> None:
    size = 48 * 1024 * 1024
    duration = 3_600
    seconds = plan_chunk_seconds(size, duration, max_duration=3_600)
    assert seconds * (size / duration) < SAFE_UPLOAD_BYTES


def test_prepare_rejects_overlong_media_before_compression(monkeypatch) -> None:
    monkeypatch.setattr("linebot_meeting.audio.ensure_ffmpeg", lambda: None)
    monkeypatch.setattr("linebot_meeting.audio.probe_duration", lambda _: 10_801.0)

    with pytest.raises(MediaTooLongError):
        prepare(
            Path("meeting.mp4"),
            Path("work"),
            max_chunk_seconds=600,
            max_media_seconds=10_800,
        )


def test_ffmpeg_timeout_becomes_audio_processing_error(monkeypatch) -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1)

    monkeypatch.setattr("linebot_meeting.audio.subprocess.run", timeout)
    with pytest.raises(AudioProcessingError, match="超過 1 秒"):
        _run(["ffmpeg", "-version"], timeout_seconds=1)
