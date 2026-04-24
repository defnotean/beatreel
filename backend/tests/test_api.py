"""Tests for the FastAPI endpoints using TestClient (no network, no ffmpeg)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_shape():
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    for key in ("ok", "ffmpeg", "ffmpeg_error", "scene_detection", "aspects"):
        assert key in data
    assert set(data["aspects"]) == {"landscape", "portrait", "square"}
    assert isinstance(data["scene_detection"], bool)


def test_youtube_probe_rejects_non_youtube():
    r = client.post("/api/youtube/probe", json={"url": "https://example.com/foo"})
    assert r.status_code == 400
    assert "youtube" in r.json()["detail"].lower()


def test_youtube_probe_requires_url():
    r = client.post("/api/youtube/probe", json={})
    assert r.status_code == 400


def test_medal_resolve_rejects_non_medal():
    r = client.post("/api/medal/resolve", json={"url": "https://example.com/clip"})
    assert r.status_code == 400
    assert "medal" in r.json()["detail"].lower()


def test_medal_resolve_requires_url():
    r = client.post("/api/medal/resolve", json={})
    assert r.status_code == 400


def test_medal_list_requires_key_header():
    r = client.get("/api/medal/clips")
    # FastAPI Header(..., alias="X-Medal-Key") without default → 422 missing header
    assert r.status_code == 422


def test_create_job_requires_some_clip_source():
    # No clips, no medal fields, no music — should fail with 400
    r = client.post(
        "/api/jobs",
        data={"duration": "60", "intensity": "balanced", "aspect": "landscape"},
    )
    assert r.status_code == 400


def test_get_missing_job_404():
    r = client.get("/api/jobs/does_not_exist")
    assert r.status_code == 404


def test_reroll_missing_job_404():
    r = client.post("/api/jobs/does_not_exist/reroll")
    assert r.status_code == 404


def test_video_missing_job_404():
    r = client.get("/api/jobs/does_not_exist/video")
    assert r.status_code == 404
