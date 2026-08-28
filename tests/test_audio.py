from pathlib import Path

import pytest

from linebot_meeting.audio import (
    SAFE_UPLOAD_BYTES,
    MediaTooLongError,
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
