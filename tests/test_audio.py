from linebot_meeting.audio import SAFE_UPLOAD_BYTES, plan_chunk_seconds


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
