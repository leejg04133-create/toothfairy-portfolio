"""Create lightweight gallery media while retaining the original detail images.

Requires Pillow with WebP support. Run from any directory:
    python scripts/optimize_media.py
Use --force after changing encoding settings to regenerate every preview.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path

from PIL import Image, ImageSequence, features


ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {".png", ".gif", ".webp", ".jpg", ".jpeg"}


def media_key(path: Path) -> tuple[int, str]:
    return (int(path.stem) if path.stem.isdigit() else 10**9, path.name)


def frame_durations(image: Image.Image) -> list[int]:
    durations = []
    for frame in ImageSequence.Iterator(image):
        frame.load()
        durations.append(int(frame.info.get("duration", 100)))
    return durations


def optimize_media(task: tuple[str, bool]) -> dict:
    relative_src, force = task
    src = ROOT / relative_src
    group = src.parent.name
    output = ROOT / "previews" / group / f"{src.stem}.webp"
    output.parent.mkdir(parents=True, exist_ok=True)
    needs_build = force or not output.exists() or output.stat().st_size == 0
    if not needs_build:
        needs_build = output.stat().st_mtime < src.stat().st_mtime
    generated = output.with_suffix(".tmp.webp") if needs_build else output

    with Image.open(src) as original:
        original_count = getattr(original, "n_frames", 1)
        original_loop = original.info.get("loop", 1)
        source_duration = frame_durations(original) if original_count > 1 else []
        original.seek(0)
        if needs_build:
            if group == "image":
                bound = (1200, max(1, round(original.height * min(1, 1200 / original.width))))
            else:
                bound = (512, 512)
            frames = []
            for frame in ImageSequence.Iterator(original):
                rgba = frame.convert("RGBA")
                rgba.thumbnail(bound, Image.Resampling.LANCZOS)
                frames.append(rgba)

            settings = {"format": "WEBP", "quality": 84 if group == "image" else 82,
                        "method": 5 if group == "image" else 4}
            if original_count > 1:
                settings.update(save_all=True, append_images=frames[1:],
                                duration=source_duration, loop=original_loop,
                                minimize_size=True, allow_mixed=True)
            frames[0].save(generated, **settings)

    with Image.open(generated) as preview:
        preview_count = getattr(preview, "n_frames", 1)
        if original_count > 1:
            actual_duration = frame_durations(preview)
            if preview_count <= 1 or sum(actual_duration) != sum(source_duration):
                raise ValueError(f"Animation timing lost in {output}")
            if preview.info.get("loop", 1) != original_loop:
                raise ValueError(f"Animation loop changed in {output}")
        preview.verify()
    if needs_build:
        generated.replace(output)

    return {"source": relative_src, "preview": output.relative_to(ROOT).as_posix(),
            "source_bytes": src.stat().st_size, "preview_bytes": output.stat().st_size,
            "source_frames": original_count, "preview_frames": preview_count,
            "duration_ms": sum(source_duration)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Regenerate existing previews")
    parser.add_argument("--workers", type=int, default=2, choices=(1, 2, 3))
    args = parser.parse_args()
    if not features.check("webp"):
        raise SystemExit("Pillow must include WebP support")

    manifest_path = ROOT / "media-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = []
    for group in ("image", "item"):
        paths = sorted((path for path in (ROOT / group).iterdir()
                        if path.is_file() and path.suffix.lower() in EXTENSIONS), key=media_key)
        manifest[group] = [path.name for path in paths]
        sources.extend(path.relative_to(ROOT).as_posix() for path in paths)

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        results = list(executor.map(optimize_media, ((src, args.force) for src in sources)))

    manifest["previews"] = {row["source"]: row["preview"] for row in results}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for group in ("image", "item"):
        rows = [row for row in results if row["source"].startswith(group + "/")]
        before = sum(row["source_bytes"] for row in rows)
        after = sum(row["preview_bytes"] for row in rows)
        print(f"{group}: {len(rows)} previews, {before:,} -> {after:,} bytes ({100 * (1 - after / before):.1f}% smaller)")
    animations = [row for row in results if row["source_frames"] > 1]
    print(f"Verified {len(animations)} animated previews with preserved duration and loop count.")
    print(json.dumps(animations, indent=2))


if __name__ == "__main__":
    main()
