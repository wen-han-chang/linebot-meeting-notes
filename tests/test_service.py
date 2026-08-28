from linebot_meeting.service import is_supported_message, is_video_message


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
