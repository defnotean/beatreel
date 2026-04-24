"""Medal.tv API client.

Medal's public API exposes a user's clips but the `rawFileUrl` field requires
whitelisted access (returns `"not_authorized"` otherwise). We fall back to
scraping the CDN URL out of the clip's public embed/share page so that
non-whitelisted keys still work.

We also support resolving a Medal share URL directly (no API key required) by
scraping the same public page for title / thumbnail / duration / mp4.
"""
from __future__ import annotations

import hashlib
import html as _html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

MEDAL_BASE = "https://developers.medal.tv/v1"
USER_AGENT = "beatreel/0.1 (+https://github.com/)"


@dataclass
class MedalClip:
    content_id: str
    title: str
    duration: float
    thumbnail: str
    direct_clip_url: str
    raw_file_url: Optional[str]  # None if not authorized
    embed_iframe_url: str
    created_ms: int

    def to_json(self) -> dict:
        return {
            "contentId": self.content_id,
            "title": self.title,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
            "directClipUrl": self.direct_clip_url,
            "rawFileUrl": self.raw_file_url,
            "embedIframeUrl": self.embed_iframe_url,
            "createdMs": self.created_ms,
        }


class MedalError(RuntimeError):
    pass


def _parse_clip(obj: dict[str, Any]) -> MedalClip:
    raw_file = obj.get("rawFileUrl")
    if raw_file == "not_authorized" or raw_file == "":
        raw_file = None

    thumbnail = (
        obj.get("thumbnail")
        or obj.get("contentThumbnail")
        or obj.get("poster")
        or ""
    )
    return MedalClip(
        content_id=str(obj.get("contentId", "")),
        title=str(obj.get("contentTitle") or "Untitled clip"),
        duration=float(obj.get("videoLengthSeconds") or 0.0),
        thumbnail=thumbnail,
        direct_clip_url=str(obj.get("directClipUrl", "")),
        raw_file_url=raw_file,
        embed_iframe_url=str(obj.get("embedIframeUrl", "")),
        created_ms=int(obj.get("createdTimestamp") or 0),
    )


def list_latest(
    api_key: str,
    user_id: Optional[str] = None,
    category_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[MedalClip]:
    """GET /v1/latest — latest clips, optionally filtered to a userId."""
    params: dict[str, Any] = {"limit": min(limit, 100), "offset": offset}
    if user_id:
        params["userId"] = user_id
    if category_id:
        params["categoryId"] = category_id

    with httpx.Client(timeout=20.0, headers={"User-Agent": USER_AGENT}) as client:
        r = client.get(
            f"{MEDAL_BASE}/latest",
            params=params,
            headers={"Authorization": api_key, "Content-Type": "application/json"},
        )
    if r.status_code == 401 or r.status_code == 403:
        raise MedalError("Medal API rejected your API key (401/403). Is the key correct?")
    if r.status_code >= 400:
        raise MedalError(f"Medal API returned {r.status_code}: {r.text[:200]}")

    payload = r.json()
    # The API returns either a list or {"contentObjects": [...]} depending on endpoint.
    items = (
        payload.get("contentObjects")
        or payload.get("items")
        or (payload if isinstance(payload, list) else [])
    )
    return [_parse_clip(x) for x in items]


# Patterns that commonly appear in Medal clip / embed pages.
_CDN_PATTERNS = [
    re.compile(r'"contentUrl"\s*:\s*"(https?://[^"\s]+\.mp4[^"\s]*)"'),
    re.compile(r'<meta\s+property="og:video"\s+content="([^"]+\.mp4[^"]*)"'),
    re.compile(r'<meta\s+property="og:video:secure_url"\s+content="([^"]+\.mp4[^"]*)"'),
    re.compile(r'<source[^>]+src="([^"]+\.mp4[^"]*)"'),
    re.compile(r'"src"\s*:\s*"(https?://[^"\s]+\.mp4[^"\s]*)"'),
    re.compile(r'(https?://cdn\.medal\.tv/[^"\'\s]+\.mp4)'),
]


def scrape_cdn_url(clip_url: str) -> Optional[str]:
    """Fallback: fetch the public share page and pull the MP4 URL out of HTML."""
    if not clip_url:
        return None
    try:
        with httpx.Client(
            timeout=15.0,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            r = client.get(clip_url)
    except httpx.HTTPError:
        return None

    if r.status_code != 200:
        return None
    html = r.text
    for pat in _CDN_PATTERNS:
        m = pat.search(html)
        if m:
            url = m.group(1).replace("\\u002F", "/").replace("\\/", "/")
            return url
    return None


def resolve_clip_download_url(clip: MedalClip) -> Optional[str]:
    """Return a directly-downloadable mp4 URL or None if we can't find one."""
    if clip.raw_file_url:
        return clip.raw_file_url
    return scrape_cdn_url(clip.direct_clip_url)


_MEDAL_HOST_RE = re.compile(r"^https?://(?:www\.|m\.)?medal\.tv/", re.IGNORECASE)
_CLIP_ID_PATTERNS = [
    re.compile(r"[?&]contentId=([A-Za-z0-9]+)"),
    re.compile(r"/clips/([A-Za-z0-9]+)"),
    re.compile(r"/s/([A-Za-z0-9]+)"),
    re.compile(r"/games/[^/]+/[^/]+/([A-Za-z0-9]+)"),
]
_META_PATTERNS = {
    "title": [
        re.compile(r'<meta\s+property="og:title"\s+content="([^"]+)"', re.IGNORECASE),
        re.compile(r'<meta\s+name="twitter:title"\s+content="([^"]+)"', re.IGNORECASE),
        re.compile(r"<title>([^<]+)</title>", re.IGNORECASE),
    ],
    "thumbnail": [
        re.compile(r'<meta\s+property="og:image"\s+content="([^"]+)"', re.IGNORECASE),
        re.compile(r'<meta\s+name="twitter:image"\s+content="([^"]+)"', re.IGNORECASE),
    ],
    "duration": [
        re.compile(r'<meta\s+property="og:video:duration"\s+content="([0-9.]+)"', re.IGNORECASE),
        re.compile(r'"videoLengthSeconds"\s*:\s*([0-9.]+)'),
        re.compile(r'"duration"\s*:\s*"?PT([0-9]+)S"?', re.IGNORECASE),
    ],
}


def validate_share_url(url: str) -> str:
    url = (url or "").strip()
    if not _MEDAL_HOST_RE.match(url):
        raise MedalError("Only medal.tv URLs are allowed here.")
    # Strip fragments; normalize trailing slashes
    url = url.split("#", 1)[0]
    return url


def _extract_meta(html_text: str, key: str) -> Optional[str]:
    for pat in _META_PATTERNS[key]:
        m = pat.search(html_text)
        if m:
            return _html.unescape(m.group(1).strip())
    return None


def _extract_content_id(url: str, html_text: str) -> str:
    for pat in _CLIP_ID_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    # Try the page content (sometimes contentId is inlined in JSON)
    m = re.search(r'"contentId"\s*:\s*"([A-Za-z0-9]+)"', html_text)
    if m:
        return m.group(1)
    return "url_" + hashlib.md5(url.encode("utf-8")).hexdigest()[:10]


def resolve_share_url(url: str) -> MedalClip:
    """Fetch a Medal share URL and build a MedalClip from the page metadata."""
    url = validate_share_url(url)
    try:
        with httpx.Client(
            timeout=15.0,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            r = client.get(url)
    except httpx.HTTPError as exc:
        raise MedalError(f"Couldn't reach Medal: {exc}")

    if r.status_code != 200:
        raise MedalError(f"Medal returned {r.status_code} for {url}")

    html_text = r.text
    title = _extract_meta(html_text, "title") or "Medal clip"
    # Strip the "| Medal.tv" suffix that og:title often has
    title = re.sub(r"\s*[|\-]\s*Medal(\.tv)?\s*$", "", title).strip() or "Medal clip"

    thumbnail = _extract_meta(html_text, "thumbnail") or ""

    duration = 0.0
    dur_str = _extract_meta(html_text, "duration")
    if dur_str:
        try:
            duration = float(dur_str)
        except ValueError:
            duration = 0.0

    content_id = _extract_content_id(str(r.url), html_text)

    return MedalClip(
        content_id=content_id,
        title=title,
        duration=duration,
        thumbnail=thumbnail,
        direct_clip_url=str(r.url),
        raw_file_url=None,
        embed_iframe_url="",
        created_ms=0,
    )


def download_clip(clip: MedalClip, dest_dir: Path) -> Path:
    """Download a Medal clip to dest_dir. Raises MedalError on failure."""
    url = resolve_clip_download_url(clip)
    if not url:
        raise MedalError(
            f"Couldn't resolve a downloadable URL for clip {clip.content_id}. "
            "Your Medal API key likely isn't whitelisted for rawFileUrl access, "
            "and the fallback page-scrape didn't find a CDN link."
        )

    safe_title = re.sub(r"[^\w\-. ]+", "_", clip.title).strip() or clip.content_id
    dest = dest_dir / f"{safe_title}_{clip.content_id}.mp4"
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{safe_title}_{clip.content_id}_{counter}.mp4"
        counter += 1

    with httpx.stream(
        "GET", url, timeout=120.0, follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as r:
        if r.status_code >= 400:
            raise MedalError(f"Download failed ({r.status_code}) for {clip.content_id}")
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(chunk_size=1 << 16):
                f.write(chunk)
    return dest
