import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.config import DATA_DIR, FFMPEG_BIN
from app.services.captions import CaptionStyle, build_ass
from app.services.reframing import build_crop_plan

LOGGER = logging.getLogger("clipper.export")


class ExportError(RuntimeError):
    """Raised when an export cannot be rendered locally."""


def _filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    value = value.replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")
    return value


def _has_nvenc() -> bool:
    try:
        result = subprocess.run(
            [FFMPEG_BIN, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return "h264_nvenc" in result.stdout


def _render(
    source: Path,
    subtitle_path: Path,
    font_dir: Path | None,
    output: Path,
    settings: dict[str, Any],
    encoder: str,
    duration: float,
    progress: Callable[[float, str], None],
    face_track: list[dict[str, float]] | None = None,
    source_dimensions: tuple[int, int] | None = None,
) -> None:
    width = int(settings["width"])
    height = int(settings["height"])
    fps = int(settings["fps"])
    subtitle_filter = f"subtitles=filename='{_filter_path(subtitle_path)}'"
    if font_dir is not None:
        subtitle_filter += f":fontsdir='{_filter_path(font_dir)}'"
    layout = settings.get("layout", "single")
    complex_filter = layout == "gaming_split"
    if layout == "gaming_split":
        facecam = settings.get("facecam_region")
        gameplay = settings.get("gameplay_region")
        if not facecam or not gameplay:
            raise ExportError("Select both the facecam and gameplay regions before exporting.")
        half_height = height // 2

        def region_filter(region: dict[str, float], stream: str, label: str) -> str:
            return (
                f"[{stream}]crop=iw*{region['width']:.6f}:ih*{region['height']:.6f}:"
                f"iw*{region['x']:.6f}:ih*{region['y']:.6f},"
                f"scale={width}:{half_height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{half_height}:(ow-iw)/2:(oh-ih)/2,setsar=1[{label}]"
            )

        filter_graph = (
            "[0:v]split=2[gamein][facein];"
            + region_filter(gameplay, "gamein", "game")
            + ";"
            + region_filter(facecam, "facein", "face")
            + ";[game][face]vstack=inputs=2,setsar=1,"
            + f"fps={fps},{subtitle_filter}[v]"
        )
    elif settings.get("smart_reframe"):
        if face_track is None or source_dimensions is None:
            raise ExportError("Smart reframe could not get face tracking data for this clip.")
        crop = build_crop_plan(
            source_dimensions[0],
            source_dimensions[1],
            width,
            height,
            duration,
            face_track,
        )
        filter_graph = (
            f"crop={crop['width']}:{crop['height']}:x='{crop['x']}':y='{crop['y']}':exact=1,"
            f"scale={width}:{height},setsar=1,fps={fps},{subtitle_filter}"
        )
        complex_filter = False
    elif settings.get("background_fill", "pad") == "blur":
        complex_filter = True
        filter_graph = (
            f"[0:v]split=2[background][foreground];"
            f"[background]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=20:10[blurred];"
            f"[foreground]scale={width}:{height}:force_original_aspect_ratio=decrease[sharp];"
            f"[blurred][sharp]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps},"
            f"{subtitle_filter}[v]"
        )
    elif settings.get("background_fill") == "crop":
        filter_graph = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps},{subtitle_filter}"
        )
    else:
        filter_graph = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},{subtitle_filter}"
        )
    command = [
        FFMPEG_BIN,
        "-y",
        "-nostdin",
        "-nostats",
        "-loglevel",
        "error",
        "-i",
        str(source),
    ]
    if complex_filter:
        command.extend(["-filter_complex", filter_graph, "-map", "[v]"])
    else:
        command.extend(["-map", "0:v:0", "-vf", filter_graph])
    command.extend(["-map", "0:a?", "-c:v", encoder])
    if encoder == "h264_nvenc":
        command.extend(["-preset", "p5", "-rc", "vbr", "-b:v", settings["bitrate"]])
    else:
        command.extend(["-preset", "veryfast", "-b:v", settings["bitrate"]])
    command.extend(
        [
            "-maxrate",
            settings["bitrate"],
            "-bufsize",
            str(int(settings["bitrate"][:-1]) * 2) + "M",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-af",
            "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            str(output),
        ]
    )
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            key, separator, value = line.strip().partition("=")
            if separator and key == "out_time_ms":
                try:
                    elapsed = int(value) / 1_000_000
                    progress(min(99.0, elapsed / duration * 100), f"Rendering video ({encoder})")
                except ValueError:
                    continue
        stderr = process.stderr.read().strip() if process.stderr is not None else ""
        exit_code = process.wait()
    except OSError as exc:
        raise ExportError(f"Could not start ffmpeg: {exc}") from exc
    if exit_code:
        raise ExportError(stderr or f"ffmpeg exited with code {exit_code}.")
    if not output.is_file() or output.stat().st_size == 0:
        raise ExportError("ffmpeg finished without creating a non-empty export.")


def export_captioned_clip(
    source: Path,
    words: list[dict[str, Any]],
    style: CaptionStyle,
    settings: dict[str, Any],
    font_path: Path | None,
    duration: float,
    progress: Callable[[float, str], None],
    face_track: list[dict[str, float]] | None = None,
    source_dimensions: tuple[int, int] | None = None,
) -> tuple[Path, bool]:
    if not source.is_file():
        raise ExportError(f"Clip file does not exist: {source}")
    output_dir = DATA_DIR / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", source.stem).strip("-") or "clip"
    output = output_dir / f"{safe_name}-captioned-{uuid4().hex[:8]}.mp4"
    font_dir = font_path.parent if font_path is not None else None
    progress(0, "Generating word-timed subtitles")

    with tempfile.TemporaryDirectory(prefix="clipper-export-") as temp_dir:
        temp_path = Path(temp_dir)
        subtitle_path = temp_path / "captions.ass"
        subtitle_path.write_text(
            build_ass(words, style, int(settings["width"]), int(settings["height"])),
            encoding="utf-8-sig",
        )
        # libass locates custom font files in fontsdir; copy the selected font
        # beside the subtitle so the export does not depend on OS font installs.
        if font_path is not None:
            local_fonts = temp_path / "fonts"
            local_fonts.mkdir()
            local_font = local_fonts / font_path.name
            local_font.write_bytes(font_path.read_bytes())
            font_dir = local_fonts
        used_nvenc = _has_nvenc()
        try:
            _render(
                source,
                subtitle_path,
                font_dir,
                output,
                settings,
                "h264_nvenc" if used_nvenc else "libx264",
                duration,
                progress,
                face_track,
                source_dimensions,
            )
        except ExportError as exc:
            if not used_nvenc:
                raise
            LOGGER.warning("NVENC export failed; retrying with CPU encoder: %s", exc)
            output.unlink(missing_ok=True)
            progress(0, "NVENC unavailable; retrying with CPU")
            _render(
                source,
                subtitle_path,
                font_dir,
                output,
                settings,
                "libx264",
                duration,
                progress,
                face_track,
                source_dimensions,
            )
            used_nvenc = False
    progress(100, "Export complete")
    return output, used_nvenc
