"""LINE Messaging API 的小型非同步客戶端。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import uuid
from pathlib import Path
from typing import Any

import httpx

API_BASE = "https://api.line.me"
DATA_API_BASE = "https://api-data.line.me"
LINE_TEXT_LIMIT = 5000
LINE_MESSAGE_BATCH = 5
PUSH_MAX_ATTEMPTS = 3

logger = logging.getLogger(__name__)


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

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        retry_key: str | None = None,
    ) -> None:
        attempts = PUSH_MAX_ATTEMPTS if retry_key else 1
        last_error = "未知錯誤"
        headers = dict(self.headers)
        if retry_key:
            headers["X-Line-Retry-Key"] = retry_key

        for attempt in range(attempts):
            try:
                response = await self.client.post(
                    f"{API_BASE}{path}",
                    headers=headers,
                    json=payload,
                )
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                retryable = retry_key is not None
            else:
                # 相同 retry key 回傳 409，表示先前請求已被 LINE 接受。
                if response.is_success or (retry_key and response.status_code == 409):
                    return
                last_error = f"HTTP {response.status_code}：{response.text[:500]}"
                retryable = response.status_code == 429 or response.status_code >= 500

            if not retryable or attempt >= attempts - 1:
                break
            delay = 2**attempt
            logger.warning(
                "LINE API 暫時失敗，%s 秒後重試：path=%s attempt=%s/%s error=%s",
                delay,
                path,
                attempt + 1,
                attempts,
                last_error,
            )
            await asyncio.sleep(delay)

        raise LineAPIError(f"LINE API {path} 失敗：{last_error}")

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
                retry_key=str(uuid.uuid4()),
            )

    async def _wait_until_ready(self, message_id: str) -> None:
        url = f"{DATA_API_BASE}/v2/bot/message/{message_id}/content/transcoding"
        for delay in (1, 2, 3, 5, 8, 13):
            try:
                response = await self.client.get(url, headers=self.headers)
            except httpx.TransportError:
                logger.warning("查詢 LINE 影音轉碼狀態暫時失敗，將重試")
            else:
                if response.is_success:
                    status = response.json().get("status")
                    if status == "succeeded":
                        return
                    if status == "failed":
                        raise LineAPIError("LINE 無法準備這個影音檔。")
            await asyncio.sleep(delay)
        raise LineAPIError("等待 LINE 準備影音逾時，請稍後重傳。")

    async def download(
        self, message_id: str, destination: Path, max_bytes: int
    ) -> None:
        url = f"{DATA_API_BASE}/v2/bot/message/{message_id}/content"
        last_error = "未知錯誤"
        waited_for_transcoding = False
        for attempt in range(3):
            try:
                async with self.client.stream(
                    "GET", url, headers=self.headers
                ) as response:
                    if response.status_code == 202:
                        await response.aread()
                        if not waited_for_transcoding:
                            await self._wait_until_ready(message_id)
                            waited_for_transcoding = True
                        else:
                            last_error = "LINE 影音仍在準備中"
                        continue
                    if response.is_error:
                        detail = (await response.aread()).decode(errors="replace")
                        last_error = f"HTTP {response.status_code}：{detail[:300]}"
                        if response.status_code != 429 and response.status_code < 500:
                            raise LineAPIError(f"下載 LINE 影音失敗：{last_error}")
                    else:
                        declared = int(response.headers.get("content-length", 0))
                        if declared > max_bytes:
                            raise FileTooLargeError("影音檔超過服務允許的大小。")

                        total = 0
                        with destination.open("wb") as output:
                            async for data in response.aiter_bytes():
                                total += len(data)
                                if total > max_bytes:
                                    raise FileTooLargeError(
                                        "影音檔超過服務允許的大小。"
                                    )
                                output.write(data)
                        return
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt < 2:
                delay = 2**attempt
                logger.warning(
                    "下載 LINE 影音暫時失敗，%s 秒後重試：attempt=%s/3 error=%s",
                    delay,
                    attempt + 1,
                    last_error,
                )
                await asyncio.sleep(delay)

        raise LineAPIError(f"下載 LINE 影音重試 3 次仍失敗：{last_error}")
