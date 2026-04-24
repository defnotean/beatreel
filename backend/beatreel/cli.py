"""CLI entry point."""
from __future__ import annotations

import sys
from pathlib import Path

import click
from tqdm import tqdm

from .pipeline import PipelineConfig, run


@click.command()
@click.option("--clips", "clips_dir", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--music", "music_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", "output_path", default=Path("reel.mp4"), type=click.Path(dir_okay=False, path_type=Path))
@click.option("--duration", "target_duration", default=60.0, type=float, help="Target reel duration in seconds.")
@click.option("--intensity", type=click.Choice(["chill", "balanced", "hype"]), default="balanced")
def main(clips_dir: Path, music_path: Path, output_path: Path, target_duration: float, intensity: str) -> None:
    """Generate a beat-synced highlight reel."""
    bar = tqdm(total=100, desc="beatreel", bar_format="{desc}: {percentage:3.0f}% |{bar}| {postfix}")
    last_pct = 0

    def on_progress(stage: str, frac: float) -> None:
        nonlocal last_pct
        pct = int(frac * 100)
        if pct > last_pct:
            bar.update(pct - last_pct)
            last_pct = pct
        bar.set_postfix_str(stage)

    try:
        result = run(
            PipelineConfig(
                clips_dir=clips_dir,
                music_path=music_path,
                output_path=output_path,
                target_duration=target_duration,
                intensity=intensity,  # type: ignore[arg-type]
            ),
            on_progress=on_progress,
        )
    except Exception as exc:
        bar.close()
        click.echo(f"\nerror: {exc}", err=True)
        sys.exit(1)

    bar.close()
    click.echo(
        f"\nDone. {result.num_cuts} cuts from {result.num_clips_scanned} clips. "
        f"Tempo {result.tempo:.0f} BPM. → {result.output_path}"
    )


if __name__ == "__main__":
    main()
