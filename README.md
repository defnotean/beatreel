# beatreel

Drop a folder of raw gameplay clips, drop a song, get a highlight reel synced to the beat. No editor required.

**Status:** v0. Optimized for FPS / reaction-heavy games (loud = interesting). Works on anything; best results on games with impact sounds.

## How it works

1. Detect beats in your music (librosa)
2. Score every clip by finding audio RMS peaks (gunshots, kill stings, reactions)
3. Pick the top-N highlights until we hit your target duration
4. Snap each cut point to the nearest beat
5. ffmpeg concat + audio mix → final MP4

## Requirements

- Python 3.11+
- Node.js 20+
- ffmpeg on PATH
  - Windows: `winget install ffmpeg` (or download from ffmpeg.org and add to PATH)
  - macOS: `brew install ffmpeg`
  - Linux: `apt install ffmpeg` (or your distro equivalent)

## Install & run

```bash
# Backend (Python)
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e .
uvicorn main:app --reload --port 8000

# Frontend (in another terminal)
cd frontend
npm install
npm run dev
```

Then open http://localhost:3000 and drop your clips + a song.

## CLI (optional)

```bash
beatreel --clips ./clips --music song.mp3 --out reel.mp4 --duration 60 --intensity hype
```

## Roadmap

- v0.1: Scene-change detection as a co-signal with audio spikes
- v0.2: Slower-game profiles (strategy, driving) with different detection weights
- v1: Desktop app (Tauri) wrapping backend + frontend
- v1.1: Optional game-specific detectors (killfeed OCR)

## Non-goals

- Per-cut human editing (that's an editor — this is not that)
- Cloud upload / SaaS (local-only by design)
- Built-in music library (licensing; you bring your own track)
