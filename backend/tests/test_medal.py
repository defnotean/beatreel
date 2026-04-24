from __future__ import annotations

import pytest

from beatreel.medal import (
    MedalError,
    _extract_content_id,
    _extract_meta,
    validate_share_url,
)


def test_validate_accepts_canonical_medal_hosts():
    for url in [
        "https://medal.tv/games/valorant/clips/abc123",
        "http://www.medal.tv/clips/xyz",
        "https://m.medal.tv/?contentId=abc",
    ]:
        assert validate_share_url(url).startswith("http")


def test_validate_rejects_non_medal():
    for url in [
        "https://youtube.com/watch?v=abc",
        "https://evil.com/medal.tv/",
        "https://medal-tv-fake.com/clip/abc",
        "not-a-url",
        "",
    ]:
        with pytest.raises(MedalError):
            validate_share_url(url)


def test_validate_strips_fragment():
    assert "#" not in validate_share_url("https://medal.tv/clips/abc#share-modal")


def test_extract_content_id_from_common_patterns():
    html = ""
    assert _extract_content_id("https://medal.tv/clips/abcDEF123", html) == "abcDEF123"
    assert _extract_content_id("https://medal.tv/?contentId=XYZ456", html) == "XYZ456"
    assert _extract_content_id("https://medal.tv/s/shortId", html) == "shortId"


def test_extract_content_id_falls_back_to_hash():
    cid = _extract_content_id("https://medal.tv/weird/path/format", "")
    assert cid.startswith("url_")
    assert len(cid) > 4


def test_extract_content_id_from_html_inline_json():
    html = '... some markup "contentId":"zZz999" other ...'
    assert _extract_content_id("https://medal.tv/weird", html) == "zZz999"


def test_extract_meta_og_title():
    html = '<meta property="og:title" content="Sick clip &amp; more" />'
    assert _extract_meta(html, "title") == "Sick clip & more"


def test_extract_meta_og_image():
    html = '<meta property="og:image" content="https://cdn.medal.tv/thumb.jpg">'
    assert _extract_meta(html, "thumbnail") == "https://cdn.medal.tv/thumb.jpg"


def test_extract_meta_falls_back_to_title_tag():
    html = "<html><head><title>Some Clip | Medal</title></head></html>"
    assert _extract_meta(html, "title") == "Some Clip | Medal"


def test_extract_meta_duration_from_og():
    html = '<meta property="og:video:duration" content="42.5">'
    assert _extract_meta(html, "duration") == "42.5"


def test_extract_meta_returns_none_when_missing():
    assert _extract_meta("<html></html>", "title") is None
