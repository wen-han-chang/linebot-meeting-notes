import pytest

from linebot_meeting.audio import AudioProcessingError, MediaTooLongError
from linebot_meeting.config import Settings
from linebot_meeting.line_api import FileTooLargeError
from linebot_meeting.service import (
    is_supported_message,
    is_video_message,
    user_error_message,
)
from linebot_meeting.transcription import TranscriptionError


def settings() -> Settings:
    return Settings(
        line_channel_secret="secret",
        line_channel_access_token="token",
        openai_api_key="key",
    )


def test_accepts_native_line_video() -> None:
    message = {"type": "video", "id": "123"}
    assert is_supported_message(message)
    assert is_video_message(message)


def test_accepts_common_video_file_extensions() -> None:
    for file_name in ("meeting.mp4", "meeting.mov", "meeting.mkv", "meeting.avi"):
        message = {"type": "file", "fileName": file_name}
        assert is_supported_message(message)
        assert is_video_message(message)


def test_keeps_audio_file_classified_as_audio() -> None:
    message = {"type": "file", "fileName": "meeting.flac"}
    assert is_supported_message(message)
    assert not is_video_message(message)


def test_rejects_unrelated_file() -> None:
    message = {"type": "file", "fileName": "meeting.pdf"}
    assert not is_supported_message(message)
    assert not is_video_message(message)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (FileTooLargeError(), "200MB"),
        (MediaTooLongError(), "180 分鐘"),
        (AudioProcessingError(), "包含聲音"),
        (TranscriptionError(), "轉錄服務"),
    ],
)
def test_user_error_messages_are_actionable(error: Exception, expected: str) -> None:
    assert expected in user_error_message(error, settings())
