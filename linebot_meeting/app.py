"""FastAPI Webhook 應用程式。"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from .config import Settings
from .line_api import LineClient, target_from_source, verify_signature
from .service import is_supported_message, is_video_message, process_media_event

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HELP_TEXT = (
    "🎙️ 會議記錄助理\n"
    "請直接傳送 LINE 語音或影片，或上傳常見的影音檔。\n"
    "影片會自動擷取並壓縮音軌，再進行語音辨識。\n"
    "我會回傳：重點摘要、決議、待辦事項、未解問題與完整逐字稿。"
)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()  # type: ignore[call-arg]
    line = LineClient(resolved.line_channel_access_token)
    media_slots = asyncio.Semaphore(resolved.max_concurrent_jobs)
    seen_events: set[str] = set()
    seen_order: deque[str] = deque()

    def remember_event(event_id: str) -> None:
        # 防止 webhook redelivery 重複處理，也避免集合隨服務時間無限成長。
        if len(seen_order) >= 10_000:
            seen_events.discard(seen_order.popleft())
        seen_events.add(event_id)
        seen_order.append(event_id)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        resolved.records_dir.mkdir(parents=True, exist_ok=True)
        yield
        await line.close()

    app = FastAPI(title="LINE Bot Meeting Notes", lifespan=lifespan)

    async def process_media_with_limit(event: dict[str, Any]) -> None:
        message_id = str(event.get("message", {}).get("id", ""))
        if media_slots.locked():
            logger.info("影音任務等待處理名額：message_id=%s", message_id)
        async with media_slots:
            logger.info("影音任務取得處理名額：message_id=%s", message_id)
            await process_media_event(event, resolved, line)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook")
    async def webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_line_signature: str = Header(default=""),
    ) -> dict[str, str]:
        body = await request.body()
        if not verify_signature(body, x_line_signature, resolved.line_channel_secret):
            raise HTTPException(status_code=400, detail="Invalid LINE signature")

        payload: dict[str, Any] = await request.json()
        for event in payload.get("events", []):
            if event.get("mode", "active") != "active":
                continue
            event_id = str(event.get("webhookEventId", ""))
            if event_id and event_id in seen_events:
                continue

            reply_token = event.get("replyToken")
            event_type = event.get("type")
            message = event.get("message", {})

            if event_type in {"follow", "join"} and reply_token:
                await line.reply(reply_token, [HELP_TEXT])
            elif event_type == "message" and message.get("type") == "text":
                if reply_token:
                    await line.reply(reply_token, [HELP_TEXT])
            elif event_type == "message" and is_supported_message(message):
                file_size = int(message.get("fileSize", 0) or 0)
                if file_size > resolved.max_source_bytes:
                    if reply_token:
                        await line.reply(
                            reply_token,
                            [
                                "檔案太大，請上傳 "
                                f"{resolved.max_source_mb}MB 以下的影音檔。"
                            ],
                        )
                    continue
                if not target_from_source(event.get("source", {})):
                    logger.warning("無法判斷背景推送目標，略過事件")
                    continue
                if reply_token:
                    video = is_video_message(message)
                    waiting = media_slots.locked()
                    await line.reply(
                        reply_token,
                        [
                            (
                                "目前正在處理另一份影音，這份已排入等待；"
                                if waiting
                                else ""
                            )
                            + (
                                "收到影片，將擷取音軌並轉成會議記錄；"
                                if video
                                else "收到錄音，將轉成會議記錄；"
                            )
                            + "完成後會再傳回這個聊天室。"
                        ],
                    )
                background_tasks.add_task(process_media_with_limit, event)
            elif event_type == "message" and reply_token:
                await line.reply(
                    reply_token, ["目前只支援常見的影音訊息或檔案。\n\n" + HELP_TEXT]
                )

            if event_id:
                remember_event(event_id)

        return {"status": "ok"}

    return app


app = create_app()
