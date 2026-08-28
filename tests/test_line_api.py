import base64
import hashlib
import hmac
from pathlib import Path

import httpx
import pytest

from linebot_meeting.line_api import (
    LineAPIError,
    LineClient,
    split_text,
    target_from_source,
    verify_signature,
)


def test_signature_verification() -> None:
    body = b'{"events":[]}'
    secret = "channel-secret"
    signature = base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()
    assert verify_signature(body, signature, secret)
    assert not verify_signature(body + b" ", signature, secret)


def test_target_for_all_chat_types() -> None:
    assert target_from_source({"type": "user", "userId": "U1"}) == "U1"
    assert target_from_source({"type": "group", "groupId": "G1"}) == "G1"
    assert target_from_source({"type": "room", "roomId": "R1"}) == "R1"


def test_split_text_preserves_content() -> None:
    text = "第一行\n" + "長" * 30 + "\n最後"
    parts = split_text(text, limit=10)
    assert all(len(part) <= 10 for part in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")


async def test_push_retries_with_same_retry_key(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status = 500 if len(requests) == 1 else 200
        return httpx.Response(status, request=request)

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("linebot_meeting.line_api.asyncio.sleep", no_delay)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    line = LineClient("token", http)
    try:
        await line.push("U1", ["完成"])
    finally:
        await http.aclose()

    assert len(requests) == 2
    retry_keys = [request.headers["X-Line-Retry-Key"] for request in requests]
    assert retry_keys[0] == retry_keys[1]


async def test_reply_does_not_retry_without_supported_retry_key() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, request=request, text="temporary")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    line = LineClient("token", http)
    try:
        with pytest.raises(LineAPIError):
            await line.reply("reply-token", ["收到"])
    finally:
        await http.aclose()

    assert calls == 1


async def test_download_retries_transient_server_error(
    monkeypatch, tmp_path: Path
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, request=request, text="temporary")
        return httpx.Response(200, request=request, content=b"audio-data")

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("linebot_meeting.line_api.asyncio.sleep", no_delay)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    line = LineClient("token", http)
    destination = tmp_path / "audio.m4a"
    try:
        await line.download("M1", destination, max_bytes=1024)
    finally:
        await http.aclose()

    assert calls == 2
    assert destination.read_bytes() == b"audio-data"
