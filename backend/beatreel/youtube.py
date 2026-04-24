"""YouTube audio extraction via yt-dlp."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from yt_dlp import YoutubeDL

_YT_HOSTS = re.compile(
    r"^https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/",
    re.IGNORECASE,
)


@dataclass
class YouTubeAudio:
    path: Path
    title: str
    uploader: str
    duration: float
    thumbnail: str
    webpage_url: str


class YouTubeError(RuntimeError):
    pass


def validate_url(url: str) -> str:
    """Only allow youtube.com / youtu.be URLs. Raises on anything else."""
    url = url.strip()
    if not _YT_HOSTS.match(url):
        raise YouTubeError("Only youtube.com and youtu.be URLs are allowed.")
    return url


def extract_audio(url: str, dest_dir: Path) -> YouTubeAudio:
    """Download and transcode the audio from a YouTube URL. Returns the MP3 path and metadata."""
    url = validate_url(url)
    dest_dir.mkdir(parents=True, exist_ok=True)

    outtmpl = str(dest_dir / "audio.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        raise YouTubeError(f"Failed to extract audio: {exc}")

    mp3_path = dest_dir / "audio.mp3"
    if not mp3_path.exists():
        raise YouTubeError("yt-dlp ran but produced no audio.mp3. Is ffmpeg on PATH?")

    return YouTubeAudio(
        path=mp3_path,
        title=str(info.get("title") or "Unknown"),
        uploader=str(info.get("uploader") or ""),
        duration=float(info.get("duration") or 0.0),
        thumbnail=str(info.get("thumbnail") or ""),
        webpage_url=str(info.get("webpage_url") or url),
    )


def probe(url: str) -> dict:
    """Metadata-only preview (no download). For showing the user what they pasted."""
    url = validate_url(url)
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise YouTubeError(f"Failed to read video info: {exc}")

    return {
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url", url),
    }
