from __future__ import annotations

import pytest

from beatreel.youtube import YouTubeError, validate_url


def test_accepts_standard_youtube_urls():
    for url in [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "https://m.youtube.com/watch?v=abc",
        "https://music.youtube.com/watch?v=abc",
    ]:
        assert validate_url(url).startswith("http")


def test_rejects_non_youtube_urls():
    for url in [
        "https://medal.tv/clips/abc",
        "https://vimeo.com/12345",
        "https://youtube.com.evil.com/watch?v=abc",
        "https://notyoutube.com/watch?v=abc",
        "ftp://youtube.com/watch?v=abc",
        "",
    ]:
        with pytest.raises(YouTubeError):
            validate_url(url)
