"""FastAPI server wrapping the beatreel pipeline."""
from __future__ import annotations

import os
import re
import shutil
import threading
import traceback
import uuid
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Literal, Optional

# Load backend/.env before importing anything that reads env vars.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from beatreel import medal as medal_api
from beatreel import youtube as yt
from beatreel.aspect import available as available_aspects
from beatreel.pipeline import PipelineConfig, run
from beatreel.gemini_pool import keys_from_env, parse_keys
from beatreel.render import ensure_ffmpeg
from beatreel.scenes import scene_detection_available

BASE_DIR = Path(__file__).parent
JOBS_ROOT = BASE_DIR / "jobs"
JOBS_ROOT.mkdir(exist_ok=True)

Status = Literal["queued", "running", "done", "error"]


@dataclass
class Job:
    id: str
    status: Status = "queued"
    stage: str = "queued"
    progress: float = 0.0
    tempo: Optional[float] = None
    num_cuts: Optional[int] = None
    num_candidates: Optional[int] = None
    num_clips_scanned: Optional[int] = None
    final_duration: Optional[float] = None
    seed: Optional[int] = None
    aspect: str = "landscape"
    error: Optional[str] = None
    output_path: Optional[str] = None
    # inputs kept so we can re-roll without re-uploading
    clips_dir: Optional[str] = None
    music_path: Optional[str] = None
    target_duration: float = 60.0
    intensity: str = "balanced"
    game: str = "valorant"
    source_mode: str = "clips"
    moments_found: Optional[int] = None
    moments_selected: Optional[int] = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict:
        excluded = {"lock", "output_path", "clips_dir", "music_path"}
        with self.lock:
            return {f.name: getattr(self, f.name) for f in fields(self) if f.name not in excluded}


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


app = FastAPI(title="beatreel", version="0.1.0")


@app.get("/api/health")
def health() -> dict:
    try:
        ensure_ffmpeg()
        ffmpeg_ok, ffmpeg_err = True, None
    except Exception as exc:
        ffmpeg_ok, ffmpeg_err = False, str(exc)
    env_keys = keys_from_env()
    return {
        "ok": True,
        "ffmpeg": ffmpeg_ok,
        "ffmpeg_error": ffmpeg_err,
        "scene_detection": scene_detection_available(),
        "aspects": available_aspects(),
        # Backwards compat: true if at least one key is configured
        "gemini_configured": len(env_keys) > 0,
        # New: count so the UI can show "3 keys loaded" status
        "gemini_keys_configured": len(env_keys),
    }


# ── Medal ────────────────────────────────────────────────────────────────────


@app.get("/api/medal/clips")
def medal_list(
    user_id: Optional[str] = None,
    limit: int = 50,
    x_medal_key: str = Header(..., alias="X-Medal-Key"),
) -> dict:
    if not x_medal_key.strip():
        raise HTTPException(status_code=400, detail="Missing X-Medal-Key header")
    try:
        clips = medal_api.list_latest(x_medal_key, user_id=user_id, limit=limit)
    except medal_api.MedalError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"clips": [c.to_json() for c in clips]}


@app.post("/api/medal/resolve")
async def medal_resolve(request: Request) -> dict:
    body = await request.json()
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    try:
        clip = medal_api.resolve_share_url(url)
    except medal_api.MedalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return clip.to_json()


@app.get("/api/medal/user")
def medal_user(q: str, limit: int = 50) -> dict:
    """List a Medal user's public clips from their profile page. No API key required."""
    try:
        clips, username = medal_api.list_user_public_clips(q, limit=limit)
    except medal_api.MedalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"username": username, "clips": [c.to_json() for c in clips]}


# ── YouTube ──────────────────────────────────────────────────────────────────


@app.post("/api/youtube/probe")
async def youtube_probe(request: Request) -> dict:
    body = await request.json()
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    try:
        return yt.probe(url)
    except yt.YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── Jobs ─────────────────────────────────────────────────────────────────────


@app.post("/api/jobs")
async def create_job(
    music: Optional[UploadFile] = File(None),
    clips: Optional[list[UploadFile]] = File(None),
    source_video: Optional[UploadFile] = File(None),
    source_mode: Literal["clips", "auto_clip"] = Form("clips"),
    duration: float = Form(60.0),
    intensity: Literal["chill", "balanced", "hype", "auto"] = Form("balanced"),
    aspect: Literal["landscape", "portrait", "square"] = Form("landscape"),
    game: Literal["valorant_ai", "valorant", "generic"] = Form("valorant"),
    seed: Optional[int] = Form(None),
    medal_clip_ids: Optional[str] = Form(None),
    medal_user_id: Optional[str] = Form(None),
    medal_share_urls: Optional[str] = Form(None),
    medal_public_clips: Optional[str] = Form(None),
    youtube_url: Optional[str] = Form(None),
    x_medal_key: Optional[str] = Header(None, alias="X-Medal-Key"),
    x_gemini_key: Optional[str] = Header(None, alias="X-Gemini-Key"),
) -> dict:
    job_id = uuid.uuid4().hex[:12]
    job_dir = JOBS_ROOT / job_id
    clips_dir = job_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    has_clip_files = bool(clips and any(c and c.filename for c in clips))
    has_medal_library = bool(medal_clip_ids and x_medal_key)
    has_medal_urls = bool(medal_share_urls)
    has_medal_public = bool(medal_public_clips and medal_public_clips.strip() not in ("", "[]"))
    has_source_video = bool(source_video and source_video.filename)

    if source_mode == "auto_clip":
        if not has_source_video:
            raise HTTPException(
                status_code=400,
                detail="source_mode=auto_clip requires a source_video upload.",
            )
    else:
        if not (has_clip_files or has_medal_library or has_medal_urls or has_medal_public):
            raise HTTPException(
                status_code=400,
                detail="Provide clip files, Medal clip ids (with X-Medal-Key), a Medal profile, or Medal share URLs.",
            )

    has_music_file = bool(music and music.filename)
    has_youtube = bool(youtube_url)
    if not has_music_file and not has_youtube:
        raise HTTPException(
            status_code=400,
            detail="Provide either a music file or a youtube_url.",
        )

    # Save uploaded clips
    if has_clip_files:
        for c in clips or []:
            if not c or not c.filename:
                continue
            name = Path(c.filename).name
            dst = clips_dir / name
            counter = 1
            while dst.exists():
                dst = clips_dir / f"{dst.stem}_{counter}{dst.suffix}"
                counter += 1
            with dst.open("wb") as f:
                shutil.copyfileobj(c.file, f)

    # Save uploaded source video for auto_clip mode
    source_video_path: Optional[Path] = None
    if has_source_video and source_video:
        src_ext = Path(source_video.filename or "source.mp4").suffix.lower() or ".mp4"
        source_video_path = job_dir / f"source{src_ext}"
        with source_video_path.open("wb") as f:
            shutil.copyfileobj(source_video.file, f)

    # Save uploaded music
    music_path: Optional[Path] = None
    if has_music_file and music:
        music_ext = Path(music.filename or "music").suffix.lower() or ".mp3"
        music_path = job_dir / f"music{music_ext}"
        with music_path.open("wb") as f:
            shutil.copyfileobj(music.file, f)

    # Create the Job record
    job = Job(
        id=job_id,
        target_duration=float(duration),
        intensity=intensity,
        aspect=aspect,
        seed=seed,
        game=game,
        source_mode=source_mode,
    )
    with JOBS_LOCK:
        JOBS[job_id] = job

    # Combine env-configured keys with any sent on the request. Dedupe while
    # preserving order so the pool's round-robin stays stable across requests.
    env_keys = keys_from_env()
    header_keys = parse_keys(x_gemini_key) if x_gemini_key else []
    combined_keys: list[str] = []
    seen: set[str] = set()
    for k in header_keys + env_keys:
        if k and k not in seen:
            combined_keys.append(k)
            seen.add(k)

    # Parse public-profile clips (full objects, pre-signed MP4s) if any.
    import json as _json
    parsed_public_clips: list[dict] = []
    if medal_public_clips:
        try:
            parsed_public_clips = _json.loads(medal_public_clips)
            if not isinstance(parsed_public_clips, list):
                parsed_public_clips = []
        except Exception:
            parsed_public_clips = []

    # Launch worker with anything we still need to fetch (Medal, YT)
    worker_args = {
        "job": job,
        "job_dir": job_dir,
        "clips_dir": clips_dir,
        "music_path": music_path,
        "duration": float(duration),
        "intensity": intensity,
        "aspect": aspect,
        "game": game,
        "gemini_keys": combined_keys,
        "source_mode": source_mode,
        "source_video_path": source_video_path,
        "seed": seed,
        "medal_key": x_medal_key,
        "medal_user_id": medal_user_id,
        "medal_clip_ids": (
            [s.strip() for s in medal_clip_ids.split(",") if s.strip()]
            if medal_clip_ids else []
        ),
        "medal_share_urls": (
            [s.strip() for s in re.split(r"[\n,]+", medal_share_urls) if s.strip()]
            if medal_share_urls else []
        ),
        "medal_public_clips": parsed_public_clips,
        "youtube_url": youtube_url,
    }

    thread = threading.Thread(target=_run_job, kwargs=worker_args, daemon=True)
    thread.start()
    return {"job_id": job_id}


def _run_job(
    job: Job,
    job_dir: Path,
    clips_dir: Path,
    music_path: Optional[Path],
    duration: float,
    intensity: str,
    aspect: str,
    game: str,
    gemini_keys: list[str],
    source_mode: str,
    source_video_path: Optional[Path],
    seed: Optional[int],
    medal_key: Optional[str],
    medal_user_id: Optional[str],
    medal_clip_ids: list[str],
    medal_share_urls: list[str],
    medal_public_clips: list[dict],
    youtube_url: Optional[str],
) -> None:
    def progress(stage: str, frac: float) -> None:
        with job.lock:
            job.stage = stage
            job.progress = max(0.0, min(1.0, frac))

    try:
        with job.lock:
            job.status = "running"

        # Collect Medal clips from library ids (needs API key) and from share URLs (no key needed)
        medal_clips: list[medal_api.MedalClip] = []

        if medal_clip_ids and medal_key:
            progress("fetching medal library", 0.01)
            available = medal_api.list_latest(
                medal_key, user_id=medal_user_id, limit=100
            )
            wanted = {cid for cid in medal_clip_ids}
            chosen = [c for c in available if c.content_id in wanted]
            if len(chosen) != len(wanted):
                missing = wanted - {c.content_id for c in chosen}
                raise RuntimeError(
                    f"Medal returned {len(chosen)}/{len(wanted)} requested clips. "
                    f"Missing: {', '.join(sorted(missing))[:200]}"
                )
            medal_clips.extend(chosen)

        if medal_share_urls:
            progress("resolving medal share urls", 0.02)
            for i, url in enumerate(medal_share_urls):
                progress(f"resolving url {i + 1}/{len(medal_share_urls)}", 0.02 + 0.02 * (i / len(medal_share_urls)))
                medal_clips.append(medal_api.resolve_share_url(url))

        # Public-profile clips: already have pre-signed MP4 URLs, download directly.
        for obj in medal_public_clips:
            raw = obj.get("rawFileUrl")
            if not raw:
                continue
            medal_clips.append(medal_api.MedalClip(
                content_id=str(obj.get("contentId") or ""),
                title=str(obj.get("title") or "Medal clip"),
                duration=float(obj.get("duration") or 0.0),
                thumbnail=str(obj.get("thumbnail") or ""),
                direct_clip_url=str(obj.get("directClipUrl") or ""),
                raw_file_url=str(raw),
                embed_iframe_url=str(obj.get("embedIframeUrl") or ""),
                created_ms=int(obj.get("createdMs") or 0),
            ))

        for i, clip in enumerate(medal_clips):
            progress(f"downloading clip {i + 1}/{len(medal_clips)}", 0.04 + 0.04 * (i / max(len(medal_clips), 1)))
            medal_api.download_clip(clip, clips_dir)

        # Extract YouTube audio if requested
        if youtube_url:
            progress("extracting youtube audio", 0.08)
            result = yt.extract_audio(youtube_url, job_dir / "yt")
            music_path = result.path

        if music_path is None:
            raise RuntimeError("No music track available (internal error).")

        output_path = job_dir / "reel.mp4"
        config = PipelineConfig(
            clips_dir=clips_dir,
            music_path=music_path,
            output_path=output_path,
            target_duration=duration,
            intensity=intensity,  # type: ignore[arg-type]
            aspect=aspect,  # type: ignore[arg-type]
            seed=seed,
            game=game,  # type: ignore[arg-type]
            gemini_api_keys=list(gemini_keys or []),
            source_mode=source_mode,  # type: ignore[arg-type]
            source_video=source_video_path,
        )
        with job.lock:
            job.clips_dir = str(clips_dir)
            job.music_path = str(music_path)

        # Pipeline progress is scaled into 0.08 → 1.0 to leave room for fetch stages
        def pipeline_progress(stage: str, frac: float) -> None:
            progress(stage, 0.08 + frac * 0.92)

        result = run(config, on_progress=pipeline_progress)
        with job.lock:
            job.status = "done"
            job.stage = "done"
            job.progress = 1.0
            job.tempo = result.tempo
            job.num_cuts = result.num_cuts
            job.num_candidates = result.num_candidates
            job.num_clips_scanned = result.num_clips_scanned
            job.final_duration = result.final_duration
            job.output_path = str(result.output_path)
            job.moments_found = result.moments_found if result.source_mode == "auto_clip" else None
            job.moments_selected = result.moments_selected if result.source_mode == "auto_clip" else None
    except Exception as exc:
        traceback.print_exc()
        with job.lock:
            job.status = "error"
            job.error = str(exc)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job.snapshot()


@app.post("/api/jobs/{job_id}/reroll")
def reroll_job(job_id: str) -> dict:
    """Re-run the pipeline on the same inputs with a fresh random seed.

    Re-uses cached per-clip scores, so only planning + rendering run again.
    """
    import secrets

    with JOBS_LOCK:
        prev = JOBS.get(job_id)
    if not prev:
        raise HTTPException(status_code=404, detail="job not found")
    with prev.lock:
        clips_dir = prev.clips_dir
        music_path = prev.music_path
        duration = prev.target_duration
        intensity = prev.intensity
        aspect = prev.aspect
        game = prev.game
    if not clips_dir or not music_path:
        raise HTTPException(status_code=409, detail="original job inputs not available")
    if not Path(clips_dir).exists() or not Path(music_path).exists():
        raise HTTPException(status_code=410, detail="original job inputs have been cleaned up")

    new_id = uuid.uuid4().hex[:12]
    new_dir = JOBS_ROOT / new_id
    new_dir.mkdir(parents=True, exist_ok=True)
    new_seed = secrets.randbelow(2**31 - 1)

    new_job = Job(
        id=new_id,
        target_duration=duration,
        intensity=intensity,
        aspect=aspect,
        seed=new_seed,
        game=game,
    )
    with JOBS_LOCK:
        JOBS[new_id] = new_job

    reroll_gemini_keys = keys_from_env()
    thread = threading.Thread(
        target=_run_job,
        kwargs={
            "job": new_job,
            "job_dir": new_dir,
            "clips_dir": Path(clips_dir),
            "music_path": Path(music_path),
            "duration": duration,
            "intensity": intensity,
            "aspect": aspect,
            "game": game,
            "gemini_keys": reroll_gemini_keys,
            "source_mode": "clips",  # re-roll always uses clips mode (source video gone)
            "source_video_path": None,
            "seed": new_seed,
            "medal_key": None,
            "medal_user_id": None,
            "medal_clip_ids": [],
            "medal_share_urls": [],
            "medal_public_clips": [],
            "youtube_url": None,
        },
        daemon=True,
    )
    thread.start()
    return {"job_id": new_id, "seed": new_seed}


@app.get("/api/jobs/{job_id}/video")
def get_video(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    with job.lock:
        path = job.output_path
        status = job.status
    if status != "done" or not path or not Path(path).exists():
        raise HTTPException(status_code=409, detail=f"video not ready (status={status})")
    return FileResponse(path, media_type="video/mp4", filename="beatreel.mp4")


@app.exception_handler(Exception)
def handle_uncaught(_request, exc: Exception):
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": str(exc)})
