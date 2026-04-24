"""Valorant-specialized kill-sound detector.

Why template matching beats generic RMS/onset peak-picking:
- The Valorant kill-confirm sound is a short, consistent, tonal signature.
- Gunshots are broadband noise and fire *many* times per kill — a generic
  loudness/onset detector picks them over the (quieter) kill ding.
- Cross-correlating a mel-spectrogram template against clip audio locks onto
  the specific spectral shape of the kill sound, not just loud transients.

Bootstrap: we don't ship an official kill-sound sample (licensing). Instead we
*discover* the template from the user's own clips: the kill ding is the most
frequently-repeating tonal event across a highlight-reel batch. Gunshots recur
more often but are filtered out by spectral flatness (broadband vs tonal).
Ability sounds recur less than kills.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

import librosa
import numpy as np
from scipy.signal import find_peaks

from .highlights import Highlight

# ----- constants ------------------------------------------------------------
SR = 22050
HOP = 512
N_MELS = 64

# Mel-spec frequency range. The Valorant kill-ding lives in ~1-4 kHz; bass and
# low-mid (< 500Hz) is dominated by background music, voice, and map ambience
# which is NOT discriminative of kill moments. Starting the mel scale at 500Hz
# forces the detector to live in the band where the kill sound actually exists.
MEL_FMIN = 500
MEL_FMAX = 8000

# Template window: ~200ms captures the kill ding body without trailing noise
TEMPLATE_MS = 200
TEMPLATE_FRAMES = int(TEMPLATE_MS / 1000 * SR / HOP)

# Where the template starts relative to the detected onset (ms).
TEMPLATE_OFFSET_MS = -20

# Candidate peak spacing (s) — kills can come close together but not sub-second
CANDIDATE_MIN_SEP_S = 0.6

# Spectral-flatness threshold on a short post-onset window: below this is
# "tonal" enough to keep as a kill-ding candidate.
TONAL_FLATNESS_MAX = 0.35
FLATNESS_WINDOW_MS = 120

# Cosine-similarity threshold for two snippets to count as same-cluster
CLUSTER_SIM_MIN = 0.75

# Absolute minimum match correlation. Actual threshold is adaptive —
# max(MATCH_CORR_MIN, clip_median + MATCH_CORR_SIGMA * clip_std) — so a
# template that matches almost everything (which means it's bad) can't
# produce a flood of false peaks just by clearing a low absolute bar.
MATCH_CORR_MIN = 0.80
MATCH_CORR_SIGMA = 1.6
MATCH_PROMINENCE = 0.12

# Template validity check: after discovery, a good kill-sound template should
# have LOW median correlation against its source clips (most of the clip is
# not-a-kill). If the median exceeds this, the template is matching sustained
# content (music, voice) and is not a discriminative kill detector.
TEMPLATE_MAX_MEDIAN_CORR = 0.25

# Minimum candidates after filtering for discovery to be worthwhile
MIN_CANDIDATES_FOR_DISCOVERY = 6

# Minimum cluster size to accept a template
MIN_CLUSTER_SIZE = 3


# ----- core feature extraction ---------------------------------------------

def _load_mono(path: Path) -> np.ndarray:
    y, _sr = librosa.load(str(path), sr=SR, mono=True)
    return y


def _log_mel(y: np.ndarray) -> np.ndarray:
    """Log-mel spectrogram restricted to the kill-ding band, shape (n_mels, n_frames)."""
    S = librosa.feature.melspectrogram(
        y=y, sr=SR, hop_length=HOP, n_mels=N_MELS, fmin=MEL_FMIN, fmax=MEL_FMAX,
    )
    return librosa.power_to_db(S, ref=np.max)


def _harmonic_onset_peaks(y: np.ndarray) -> np.ndarray:
    """Onset peaks on the harmonic (tonal) component only.

    HPSS splits y into harmonic (sustained/pitched) + percussive (transient)
    components. Gunshots are almost entirely percussive; kill dings are
    almost entirely harmonic. Running onset detection on y_harmonic
    suppresses gunshot peaks and surfaces tonal events — exactly the
    candidate set we want for kill-sound discovery.
    """
    y_h, _y_p = librosa.effects.hpss(y, margin=3.0)
    onset_env = librosa.onset.onset_strength(y=y_h, sr=SR, hop_length=HOP, aggregate=np.median)
    if len(onset_env) == 0:
        return np.array([], dtype=int)
    distance = max(1, int(CANDIDATE_MIN_SEP_S * SR / HOP))
    baseline = float(np.median(onset_env))
    spread = float(onset_env.std())
    prominence = max(spread * 0.4, baseline * 0.25, 1e-4)
    peaks, _ = find_peaks(onset_env, prominence=prominence, distance=distance)
    return peaks


def _extract_snippet(log_mel: np.ndarray, onset_frame: int) -> Optional[np.ndarray]:
    """Fixed-size mel snippet starting slightly before the onset."""
    offset_frames = int(TEMPLATE_OFFSET_MS / 1000 * SR / HOP)
    start = onset_frame + offset_frames
    end = start + TEMPLATE_FRAMES
    if start < 0 or end > log_mel.shape[1]:
        return None
    return log_mel[:, start:end].copy()


def _spectral_flatness_at(y: np.ndarray, onset_frame: int) -> float:
    """Spectral flatness on a short post-onset window (0 = tonal, 1 = noise).

    Measured *after* HPSS-harmonic isolation would ideally happen, but we
    run it on the original signal here as a secondary filter: even with
    HPSS-cleaned candidate detection, some percussive events can sneak
    through (e.g., a gunshot that happens to have a brief tonal ring).
    """
    window_frames = int(FLATNESS_WINDOW_MS / 1000 * SR / HOP)
    start_sample = max(0, onset_frame * HOP)
    end_sample = min(len(y), (onset_frame + window_frames) * HOP)
    if end_sample <= start_sample:
        return 1.0
    segment = y[start_sample:end_sample]
    flat = librosa.feature.spectral_flatness(y=segment, hop_length=HOP)[0]
    return float(flat.mean()) if flat.size else 1.0


def _normalize_snippet(snip: np.ndarray) -> np.ndarray:
    """Per-band-demean then L2-normalize. Kills sustained/DC content so the
    template matches the *time-varying signature* (attack envelope, decay
    shape) instead of "is there loud audio in this band," which is what
    made the v1 template match 90% of every clip."""
    if snip.ndim == 2:
        snip = snip - snip.mean(axis=1, keepdims=True)
    v = snip.ravel().astype(np.float64)
    v = v - v.mean()
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


# ----- template discovery ---------------------------------------------------

def _collect_candidates(clip_paths: list[Path]) -> list[tuple[np.ndarray, float]]:
    """Across all clips, return (normalized_snippet, flatness) for tonal onsets."""
    out: list[tuple[np.ndarray, float]] = []
    for p in clip_paths:
        try:
            y = _load_mono(p)
        except Exception:
            continue
        if len(y) == 0:
            continue
        log_mel = _log_mel(y)
        peaks = _harmonic_onset_peaks(y)
        for peak_frame in peaks:
            snip = _extract_snippet(log_mel, int(peak_frame))
            if snip is None:
                continue
            flatness = _spectral_flatness_at(y, int(peak_frame))
            if flatness > TONAL_FLATNESS_MAX:
                continue  # broadband → gunshot-like, skip
            out.append((_normalize_snippet(snip), flatness))
    return out


def _dominant_cluster_template(snippets: list[np.ndarray]) -> Optional[tuple[np.ndarray, int]]:
    """Find the densest cluster of similar snippets; return (mean_template, size)."""
    n = len(snippets)
    if n < MIN_CLUSTER_SIZE:
        return None
    M = np.stack(snippets)  # (n, d) — already L2-normalized
    sim = M @ M.T  # cosine similarity (pairwise)
    # Count neighbors above threshold for each candidate (self excluded)
    neighbor_mask = sim >= CLUSTER_SIM_MIN
    np.fill_diagonal(neighbor_mask, False)
    counts = neighbor_mask.sum(axis=1)
    best = int(counts.argmax())
    if counts[best] + 1 < MIN_CLUSTER_SIZE:
        return None
    members = np.flatnonzero(neighbor_mask[best])
    members = np.concatenate([[best], members])
    # Average the cluster members' spectrograms for a denoised template
    cluster_mean = M[members].mean(axis=0)
    # Re-normalize the mean
    cluster_mean = cluster_mean - cluster_mean.mean()
    norm = np.linalg.norm(cluster_mean)
    if norm > 0:
        cluster_mean = cluster_mean / norm
    return cluster_mean, int(len(members))


def _template_is_discriminative(
    template: np.ndarray, clip_paths: list[Path], sample: int = 3,
) -> bool:
    """A real kill-ding template should be quiet for most of a clip and spike only
    at kill moments. If the median correlation against training clips is high,
    the template is matching sustained/pervasive content (background music,
    voice, ambience) and is not a discriminator — reject it.
    """
    for p in clip_paths[:sample]:
        try:
            y = _load_mono(p)
        except Exception:
            continue
        corr, _ = _match_clip(y, template)
        if corr.size and float(np.median(corr)) > TEMPLATE_MAX_MEDIAN_CORR:
            return False
    return True


def discover_template(clip_paths: list[Path]) -> Optional[np.ndarray]:
    """Return a flattened, per-band-demeaned, L2-normalized template vector,
    or None if discovery failed or produced a non-discriminative template.
    Shape (N_MELS * TEMPLATE_FRAMES,).
    """
    cands = _collect_candidates(clip_paths)
    if len(cands) < MIN_CANDIDATES_FOR_DISCOVERY:
        return None
    snips = [s for s, _ in cands]
    result = _dominant_cluster_template(snips)
    if result is None:
        return None
    template, _size = result
    # Guard against "template matches everything" failure mode: if the
    # discovered template has high median correlation against training
    # clips, it's not a kill detector — it's a "sound happens here"
    # detector. Let the pipeline fall back to the generic detector.
    if not _template_is_discriminative(template, clip_paths):
        return None
    return template


# ----- caching --------------------------------------------------------------

def _cache_key(clip_paths: list[Path]) -> str:
    """Stable key for the batch — same clip set → same key → cached template reused."""
    h = hashlib.sha256()
    for p in sorted(str(q) for q in clip_paths):
        h.update(p.encode("utf-8"))
    return h.hexdigest()[:16]


def _cache_path(clips_dir: Path, key: str) -> Path:
    return clips_dir / ".beatreel-cache" / f"valorant_template_{key}.npz"


def load_or_discover_template(clips_dir: Path, clip_paths: list[Path]) -> Optional[np.ndarray]:
    key = _cache_key(clip_paths)
    cache_path = _cache_path(clips_dir, key)
    if cache_path.exists():
        try:
            data = np.load(cache_path)
            if "template" in data:
                return data["template"]
        except Exception:
            pass
    template = discover_template(clip_paths)
    if template is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            np.savez(cache_path, template=template)
        except Exception:
            pass
    return template


# ----- detection via template matching --------------------------------------

def _match_clip(y: np.ndarray, template: np.ndarray) -> tuple[np.ndarray, float]:
    """Slide the template over the clip's log-mel; return (corr_curve, hop_s)."""
    log_mel = _log_mel(y)
    n_frames = log_mel.shape[1]
    if n_frames < TEMPLATE_FRAMES:
        return np.array([]), HOP / SR

    t_mat = template.reshape(N_MELS, TEMPLATE_FRAMES)
    t_vec = _normalize_snippet(t_mat)  # re-normalize defensively

    # Normalized cross-correlation at every time offset. O(n_frames * TEMPLATE_FRAMES * N_MELS)
    # is fine for few-minute clips; avoid an FFT path to keep the normalization correct.
    n_out = n_frames - TEMPLATE_FRAMES + 1
    corr = np.empty(n_out, dtype=np.float64)
    for i in range(n_out):
        window = log_mel[:, i:i + TEMPLATE_FRAMES]
        w_vec = _normalize_snippet(window)
        corr[i] = float(np.dot(w_vec, t_vec))
    return corr, HOP / SR


def detect_kills(clip_path: Path, template: np.ndarray) -> list[Highlight]:
    """Return Highlights for every kill-sound match in the clip."""
    try:
        y = _load_mono(clip_path)
    except Exception:
        return []
    if len(y) == 0:
        return []

    corr, hop_s = _match_clip(y, template)
    if corr.size == 0:
        return []

    clip_duration = len(y) / SR
    distance = max(1, int(CANDIDATE_MIN_SEP_S / hop_s))
    # Adaptive threshold: require the peak to clear BOTH an absolute floor
    # AND stand out from the clip's own correlation baseline. This alone
    # would have killed the 178-false-positive output — a bass-heavy template
    # produces uniformly high baselines, and MATCH_CORR_SIGMA*std above the
    # median catches only genuine outliers even if the baseline is high.
    med = float(np.median(corr))
    std = float(corr.std())
    height = max(MATCH_CORR_MIN, med + MATCH_CORR_SIGMA * std)
    peaks, props = find_peaks(
        corr, height=height, distance=distance, prominence=MATCH_PROMINENCE,
    )

    out: list[Highlight] = []
    half_template_s = (TEMPLATE_FRAMES / 2) * hop_s
    for idx, frame in enumerate(peaks):
        # Peak time = center of the matched template, not its start
        peak_time = float(frame * hop_s + half_template_s)
        if peak_time < 0.5 or peak_time > clip_duration - 0.5:
            continue
        out.append(
            Highlight(
                clip_path=clip_path,
                peak_time=peak_time,
                score=float(props["peak_heights"][idx]),
                clip_duration=clip_duration,
            )
        )
    return out


def score_clips_valorant(
    clips_dir: Path,
    clip_paths: list[Path],
    on_progress=None,
) -> tuple[list[Highlight], Optional[str]]:
    """Top-level: discover or load template, then detect kills in every clip.

    Returns (highlights, template_status) where template_status is one of:
      - "discovered": template was found and used
      - "cached": template loaded from cache and used
      - None: discovery failed → caller should fall back to generic detector
    """
    cache_path = _cache_path(clips_dir, _cache_key(clip_paths))
    cached = cache_path.exists()
    template = load_or_discover_template(clips_dir, clip_paths)
    if template is None:
        return [], None
    status = "cached" if cached else "discovered"

    all_hl: list[Highlight] = []
    total = max(len(clip_paths), 1)
    for i, p in enumerate(clip_paths):
        if on_progress:
            on_progress(i, total, p)
        all_hl.extend(detect_kills(p, template))
    if on_progress:
        on_progress(total, total, None)
    return all_hl, status
