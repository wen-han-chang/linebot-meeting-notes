"""LINE Messaging API 的小型非同步客戶端。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from pathlib import Path
from typing import Any

import httpx

API_BASE = "https://api.line.me"
DATA_API_BASE = "https://api-data.line.me"
LINE_TEXT_LIMIT = 5000
LINE_MESSAGE_BATCH = 5


class LineAPIError(RuntimeError):
    pass


class FileTooLargeError(LineAPIError):
    pass


def verify_signature(body: bytes, signature: str, channel_secret: str) -> bool:
    digest = hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


def target_from_source(source: dict[str, Any]) -> str | None:
    source_type = source.get("type")
    key = {"user": "userId", "group": "groupId", "room": "roomId"}.get(source_type)
    return str(source.get(key)) if key and source.get(key) else None


def split_text(text: str, limit: int = 4900) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.append(line[:limit].rstrip())
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current.rstrip())
            current = line
        else:
            current += line
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


class LineClient:
    def __init__(self, access_token: str, client: httpx.AsyncClient | None = None):
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=30)
        self.headers = {"Authorization": f"Bearer {access_token}"}

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _post(self, path: str, payload: dict[str, Any]) -> None:
        response = await self.client.post(
            f"{API_BASE}{path}",
            headers=self.headers,
            json=payload,
        )
        if response.is_error:
            raise LineAPIError(
                f"LINE API {path} 失敗（HTTP {response.status_code}）："
                f"{response.text[:500]}"
            )

    async def reply(self, reply_token: str, texts: list[str]) -> None:
        messages = [
            {"type": "text", "text": text[:LINE_TEXT_LIMIT]}
            for text in texts[:LINE_MESSAGE_BATCH]
        ]
        if messages:
            await self._post(
                "/v2/bot/message/reply",
                {"replyToken": reply_token, "messages": messages},
            )

    async def push(self, target: str, texts: list[str]) -> None:
        for index in range(0, len(texts), LINE_MESSAGE_BATCH):
            batch = texts[index : index + LINE_MESSAGE_BATCH]
            await self._post(
                "/v2/bot/message/push",
                {
                    "to": target,
                    "messages": [
                        {"type": "text", "text": text[:LINE_TEXT_LIMIT]}
                        for text in batch
                    ],
                },
            )

    async def _wait_until_ready(self, message_id: str) -> None:
        url = f"{DATA_API_BASE}/v2/bot/message/{message_id}/content/transcoding"
        for delay in (1, 2, 3, 5, 8, 13):
            response = await self.client.get(url, headers=self.headers)
            if response.is_success:
                status = response.json().get("status")
                if status == "succeeded":
                    return
                if status == "failed":
                    raise LineAPIError("LINE 無法準備這個音訊檔。")
            await asyncio.sleep(delay)
        raise LineAPIError("等待 LINE 準備音訊逾時，請稍後重傳。")

    async def download(
        self, message_id: str, destination: Path, max_bytes: int
    ) -> None:
        url = f"{DATA_API_BASE}/v2/bot/message/{message_id}/content"
        for attempt in range(2):
            async with self.client.stream("GET", url, headers=self.headers) as response:
                if response.status_code == 202 and attempt == 0:
                    await response.aread()
                    await self._wait_until_ready(message_id)
                    continue
                if response.is_error:
                    detail = (await response.aread()).decode(errors="replace")
                    raise LineAPIError(
                        f"下載 LINE 音訊失敗（HTTP {response.status_code}）："
                        f"{detail[:300]}"
                    )
                declared = int(response.headers.get("content-length", 0))
                if declared > max_bytes:
                    raise FileTooLargeError("音訊檔超過服務允許的大小。")

                total = 0
                with destination.open("wb") as output:
                    async for data in response.aiter_bytes():
                        total += len(data)
                        if total > max_bytes:
                            raise FileTooLargeError("音訊檔超過服務允許的大小。")
                        output.write(data)
                return
        raise LineAPIError("LINE 音訊尚未準備完成。")
