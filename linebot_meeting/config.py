"""從環境變數載入服務設定。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    line_channel_secret: str = Field(min_length=1)
    line_channel_access_token: str = Field(min_length=1)
    openai_api_key: str = Field(min_length=1)

    transcribe_model: str = "gpt-4o-transcribe"
    summary_model: str = "gpt-5.6-luna"
    transcribe_prompt: str = "請保留中英夾雜、產品名稱、人名與專有名詞的原文。"
    chunk_minutes: int = Field(default=10, ge=1, le=30)
    max_source_mb: int = Field(default=200, ge=1, le=500)
    max_media_minutes: int = Field(default=180, ge=1, le=1440)
    max_concurrent_jobs: int = Field(default=1, ge=1, le=4)
    max_queued_jobs: int = Field(default=3, ge=0, le=20)
    openai_timeout_seconds: float = Field(default=180, ge=10, le=600)
    ffmpeg_timeout_seconds: int = Field(default=1800, ge=60, le=7200)
    max_transcript_messages: int = Field(default=15, ge=0, le=50)
    records_dir: Path = Path("records")

    @property
    def max_source_bytes(self) -> int:
        return self.max_source_mb * 1024 * 1024

    @property
    def chunk_seconds(self) -> float:
        return float(self.chunk_minutes * 60)

    @property
    def max_media_seconds(self) -> float:
        return float(self.max_media_minutes * 60)
