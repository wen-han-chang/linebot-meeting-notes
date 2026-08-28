"""用 ffmpeg 從影音擷取音軌、壓縮並切成適合語音轉錄的片段。"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SAFE_UPLOAD_BYTES = 24 * 1024 * 1024
TARGET_SAMPLE_RATE = 16_000
TARGET_BITRATE_KBPS = 48


class FFmpegMissingError(RuntimeError):
    pass


class AudioProcessingError(RuntimeError):
    pass


class MediaTooLongError(AudioProcessingError):
    pass


@dataclass(frozen=True)
class Chunk:
    path: Path
    offset: float


def ensure_ffmpeg() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise FFmpegMissingError(
            f"找不到 {'、'.join(missing)}，請先安裝 ffmpeg 並確認已加入 PATH。"
        )


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise AudioProcessingError(
            f"{command[0]} 執行失敗（exit {result.returncode}）："
            f"{result.stderr.strip()}"
        )
    return result


def probe_duration(path: Path) -> float:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AudioProcessingError(f"無法讀取 {path.name} 的錄音長度。") from exc
    if duration <= 0:
        raise AudioProcessingError("錄音長度為 0 秒。")
    return duration


def compress(source: Path, workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    destination = workdir / "compressed_16k.mp3"
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(TARGET_SAMPLE_RATE),
            "-b:a",
            f"{TARGET_BITRATE_KBPS}k",
            "-c:a",
            "libmp3lame",
            str(destination),
        ]
    )
    return destination


def plan_chunk_seconds(
    size_bytes: int,
    duration: float,
    *,
    max_duration: float,
    max_bytes: int = SAFE_UPLOAD_BYTES,
) -> float:
    if duration <= 0:
        raise AudioProcessingError("錄音長度為 0 秒，無法切段。")
    size_limited = duration
    if size_bytes > max_bytes:
        bytes_per_second = size_bytes / duration
        size_limited = (max_bytes / bytes_per_second) * 0.95
    return min(duration, max_duration, size_limited)


def split(path: Path, workdir: Path, chunk_seconds: float) -> list[Chunk]:
    pattern = workdir / "chunk_%03d.mp3"
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-f",
            "segment",
            "-segment_time",
            f"{chunk_seconds:.3f}",
            "-reset_timestamps",
            "1",
            "-c",
            "copy",
            str(pattern),
        ]
    )
    paths = sorted(workdir.glob("chunk_*.mp3"))
    if not paths:
        raise AudioProcessingError("切段後沒有產生音檔。")

    chunks: list[Chunk] = []
    offset = 0.0
    for chunk_path in paths:
        chunks.append(Chunk(chunk_path, offset))
        offset += probe_duration(chunk_path)
    return chunks


def prepare(
    source: Path,
    workdir: Path,
    *,
    max_chunk_seconds: float,
    max_media_seconds: float | None = None,
) -> list[Chunk]:
    ensure_ffmpeg()
    source_duration = probe_duration(source)
    if max_media_seconds is not None and source_duration > max_media_seconds:
        raise MediaTooLongError(
            f"影音長度 {source_duration / 60:.1f} 分鐘，"
            f"超過上限 {max_media_seconds / 60:.0f} 分鐘。"
        )
    compressed = compress(source, workdir)
    duration = probe_duration(compressed)
    seconds = plan_chunk_seconds(
        compressed.stat().st_size,
        duration,
        max_duration=max_chunk_seconds,
    )
    if seconds >= duration:
        return [Chunk(compressed, 0.0)]
    return split(compressed, workdir, seconds)
