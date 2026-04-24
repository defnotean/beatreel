from __future__ import annotations

import pytest

from beatreel.aspect import available, get_aspect


def test_all_presets_available():
    assert set(available()) == {"landscape", "portrait", "square"}


def test_aspect_dimensions():
    assert get_aspect("landscape").width == 1920
    assert get_aspect("landscape").height == 1080
    assert get_aspect("portrait").width == 1080
    assert get_aspect("portrait").height == 1920
    assert get_aspect("square").width == 1080
    assert get_aspect("square").height == 1080


def test_aspect_video_filter_is_nonempty_and_has_target_dims():
    for name in available():
        spec = get_aspect(name)
        assert spec.video_filter, f"{name} has empty filter"
        assert str(spec.width) in spec.video_filter
        assert str(spec.height) in spec.video_filter


def test_portrait_uses_crop_not_pad_for_social_aesthetic():
    # Social aspect should center-crop (not letterbox) — verifying intent via filter content
    assert "crop" in get_aspect("portrait").video_filter
    assert "crop" in get_aspect("square").video_filter
    # Landscape uses pad (preserves entire frame)
    assert "pad" in get_aspect("landscape").video_filter


def test_unknown_preset_raises():
    with pytest.raises(ValueError):
        get_aspect("weird")  # type: ignore[arg-type]
