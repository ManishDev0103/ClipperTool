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


def _emphasis_expression(
    words: list[dict[str, Any]],
    fps: int,
    zoom_strength: float,
    intro_duration: float,
) -> str:
    events: list[str] = []
    ramp = max(1, round(fps * 0.15))
    for word in words:
        text = str(word.get("text", "")).strip()
        letters = "".join(character for character in text if character.isalpha())
        emphasized = (
            (len(letters) >= 2 and letters.isupper())
            or text.endswith(("!", "?"))
        )
        if not emphasized:
            continue
        start = max(0, round((float(word["start_time"]) + intro_duration) * fps))
        end = max(start + 1, round((float(word["end_time"]) + intro_duration) * fps))
        events.append(
            f"max(0,min(1,min((on-{start})/{ramp},({end}-on)/{ramp})))"
        )
    if not events:
        return "1"
    envelope = events[0] if len(events) == 1 else f"max({','.join(events)})"
    return f"1+{zoom_strength:.4f}*({envelope})"


def _render_composed(
    source: Path,
    subtitle_path: Path,
    font_dir: Path | None,
    output: Path,
    settings: dict[str, Any],
    encoder: str,
    duration: float,
    progress: Callable[[float, str], None],
    words: list[dict[str, Any]],
    face_track: list[dict[str, float]] | None,
    source_dimensions: tuple[int, int] | None,
    intro_source: Path | None,
    intro_duration: float,
    intro_has_audio: bool,
    outro_source: Path | None,
    outro_duration: float,
    outro_has_audio: bool,
    source_has_audio: bool,
    logo_path: Path | None,
    music_path: Path | None,
) -> None:
    width = int(settings["width"])
    height = int(settings["height"])
    fps = int(settings["fps"])
    main_duration = float(settings["_main_duration"])
    subtitle_filter = f"subtitles=filename='{_filter_path(subtitle_path)}'"
    if font_dir is not None:
        subtitle_filter += f":fontsdir='{_filter_path(font_dir)}'"

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
    input_indexes: dict[str, int] = {}
    next_input = 1
    for name, path in (("intro", intro_source), ("outro", outro_source)):
        if path is not None:
            command.extend(["-i", str(path)])
            input_indexes[name] = next_input
            next_input += 1
    if music_path is not None:
        command.extend(["-i", str(music_path)])
        input_indexes["music"] = next_input
        next_input += 1
    if logo_path is not None:
        command.extend(["-loop", "1", "-i", str(logo_path)])
        input_indexes["logo"] = next_input
        next_input += 1

    silence_indexes: dict[str, int] = {}
    audio_sources = {
        "intro": (intro_source, intro_duration, intro_has_audio),
        "main": (source, main_duration, source_has_audio),
        "outro": (outro_source, outro_duration, outro_has_audio),
    }
    sequence = [
        name for name, path in (("intro", intro_source), ("main", source), ("outro", outro_source))
        if path is not None
    ]
    for name in sequence:
        path, segment_duration, has_audio = audio_sources[name]
        if path is not None and not has_audio:
            command.extend(
                [
                    "-f",
                    "lavfi",
                    "-t",
                    f"{segment_duration:.3f}",
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                ]
            )
            silence_indexes[name] = next_input
            next_input += 1

    graphs: list[str] = []
    layout = settings.get("layout", "single")
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

        graphs.append(
            f"[0:v]trim=duration={main_duration:.3f},setpts=PTS-STARTPTS,split=2[gamein][facein];"
            + region_filter(gameplay, "gamein", "game")
            + ";"
            + region_filter(facecam, "facein", "face")
            + f";[game][face]vstack=inputs=2,setsar=1,fps={fps}[mainlayout]"
        )
    elif settings.get("smart_reframe"):
        if face_track is None or source_dimensions is None:
            raise ExportError("Smart reframe could not get face tracking data for this clip.")
        crop = build_crop_plan(
            source_dimensions[0],
            source_dimensions[1],
            width,
            height,
            main_duration,
            face_track,
        )
        graphs.append(
            f"[0:v]trim=duration={main_duration:.3f},setpts=PTS-STARTPTS,"
            f"crop={crop['width']}:{crop['height']}:x='{crop['x']}':y='{crop['y']}':exact=1,"
            f"scale={width}:{height},setsar=1,fps={fps}[mainlayout]"
        )
    elif settings.get("background_fill", "pad") == "blur":
        graphs.append(
            f"[0:v]trim=duration={main_duration:.3f},setpts=PTS-STARTPTS,split=2[background][foreground];"
            f"[background]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=20:10[blurred];"
            f"[foreground]scale={width}:{height}:force_original_aspect_ratio=decrease[sharp];"
            f"[blurred][sharp]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps}[mainlayout]"
        )
    elif settings.get("background_fill") == "crop":
        graphs.append(
            f"[0:v]trim=duration={main_duration:.3f},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps}[mainlayout]"
        )
    else:
        graphs.append(
            f"[0:v]trim=duration={main_duration:.3f},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[mainlayout]"
        )

    video_sequence: list[str] = []
    if intro_source is not None:
        index = input_indexes["intro"]
        graphs.append(
            f"[{index}:v]trim=duration={intro_duration:.3f},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[introvideo]"
        )
        video_sequence.append("[introvideo]")
    video_sequence.append("[mainlayout]")
    if outro_source is not None:
        index = input_indexes["outro"]
        graphs.append(
            f"[{index}:v]trim=duration={outro_duration:.3f},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[outrovideo]"
        )
        video_sequence.append("[outrovideo]")
    if len(video_sequence) > 1:
        graphs.append(
            "".join(video_sequence)
            + f"concat=n={len(video_sequence)}:v=1:a=0[sequencevideo]"
        )
        video_label = "sequencevideo"
    else:
        video_label = "mainlayout"

    graphs.append(f"[{video_label}]{subtitle_filter}[captioned]")
    video_label = "captioned"
    if settings.get("auto_zoom"):
        zoom = _emphasis_expression(
            words,
            fps,
            float(settings.get("zoom_strength", 0.12)),
            intro_duration,
        )
        graphs.append(
            f"[{video_label}]zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps}[zoomed]"
        )
        video_label = "zoomed"
    if logo_path is not None:
        logo_width = max(1, int(width * int(settings.get("watermark_size", 15)) / 100))
        opacity = float(settings.get("watermark_opacity", 0.8))
        graphs.append(
            f"[{input_indexes['logo']}:v]scale={logo_width}:-1,format=rgba,"
            f"colorchannelmixer=aa={opacity:.3f}[logo]"
        )
        margin = max(8, int(width * 0.02))
        position = settings.get("watermark_position", "bottom_right")
        coordinates = {
            "top_left": (str(margin), str(margin)),
            "top_right": (f"W-w-{margin}", str(margin)),
            "bottom_left": (str(margin), f"H-h-{margin}"),
            "bottom_right": (f"W-w-{margin}", f"H-h-{margin}"),
            "center": ("(W-w)/2", "(H-h)/2"),
        }
        x, y = coordinates[position]
        graphs.append(
            f"[{video_label}][logo]overlay=x='{x}':y='{y}':eof_action=repeat:format=auto[vwatermarked]"
        )
        video_label = "vwatermarked"
    if settings.get("progress_bar"):
        bar_height = max(4, height // 120)
        graphs.append(
            f"[{video_label}]drawbox=x=0:y=ih-{bar_height}:"
            f"w='iw*min(1,t/{duration:.3f})':h={bar_height}:"
            "color=0x67E8C1@0.9:t=fill[vprogress]"
        )
        video_label = "vprogress"
    graphs.append(f"[{video_label}]format=yuv420p[vout]")

    audio_sequence: list[str] = []
    for name in sequence:
        input_index = silence_indexes.get(name)
        if input_index is None:
            input_index = 0 if name == "main" else input_indexes.get(name)
        if input_index is None:
            raise ExportError(f"Could not prepare {name} audio for the export.")
        segment_duration = audio_sources[name][1]
        volume = float(settings.get("source_volume", 1.0)) if name == "main" else 1.0
        label = f"{name}audio"
        graphs.append(
            f"[{input_index}:a:0]atrim=duration={segment_duration:.3f},"
            f"asetpts=PTS-STARTPTS,aresample=48000,volume={volume:.3f}[{label}]"
        )
        audio_sequence.append(f"[{label}]")
    if len(audio_sequence) > 1:
        graphs.append(
            "".join(audio_sequence)
            + f"concat=n={len(audio_sequence)}:v=0:a=1[audiobase]"
        )
        audio_label = "audiobase"
    else:
        audio_label = audio_sequence[0][1:-1]
    if music_path is not None:
        music_index = input_indexes["music"]
        graphs.append(
            f"[{music_index}:a:0]atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,"
            f"aresample=48000,volume={float(settings.get('music_volume', 0.2)):.3f}[music]"
        )
        if settings.get("auto_duck", True):
            graphs.append(
                f"[{audio_label}]asplit=2[speech][sidechain];"
                "[music][sidechain]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=250[ducked];"
                "[speech][ducked]amix=inputs=2:duration=first:dropout_transition=0[amixed]"
            )
        else:
            graphs.append(
                f"[{audio_label}][music]amix=inputs=2:duration=first:dropout_transition=0[amixed]"
            )
        audio_label = "amixed"
    graphs.append(f"[{audio_label}]loudnorm=I=-14:TP=-1.5:LRA=11[aout]")

    command.extend(
        [
            "-filter_complex",
            ";".join(graphs),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            encoder,
        ]
    )
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
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            str(output),
        ]
    )
    _run_command(command, output, duration + intro_duration + outro_duration, encoder, progress)


def _run_command(
    command: list[str],
    output: Path,
    duration: float,
    encoder: str,
    progress: Callable[[float, str], None],
) -> None:
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
    words: list[dict[str, Any]] | None = None,
    intro_source: Path | None = None,
    intro_duration: float = 0,
    intro_has_audio: bool = True,
    outro_source: Path | None = None,
    outro_duration: float = 0,
    outro_has_audio: bool = True,
    source_has_audio: bool = True,
    logo_path: Path | None = None,
    music_path: Path | None = None,
) -> None:
    total_duration = duration + intro_duration + outro_duration
    composed = (
        intro_source is not None
        or outro_source is not None
        or logo_path is not None
        or music_path is not None
        or settings.get("progress_bar")
        or settings.get("auto_zoom")
        or float(settings.get("source_volume", 1)) != 1
    )
    if composed:
        composed_settings = {**settings, "_main_duration": duration}
        _render_composed(
            source,
            subtitle_path,
            font_dir,
            output,
            composed_settings,
            encoder,
            duration,
            progress,
            words or [],
            face_track,
            source_dimensions,
            intro_source,
            intro_duration,
            intro_has_audio,
            outro_source,
            outro_duration,
            outro_has_audio,
            source_has_audio,
            logo_path,
            music_path,
        )
        return
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
    intro_source: Path | None = None,
    intro_duration: float = 0,
    intro_has_audio: bool = True,
    outro_source: Path | None = None,
    outro_duration: float = 0,
    outro_has_audio: bool = True,
    source_has_audio: bool = True,
    logo_path: Path | None = None,
    music_path: Path | None = None,
) -> tuple[Path, bool]:
    if not source.is_file():
        raise ExportError(f"Clip file does not exist: {source}")
    output_dir = DATA_DIR / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", source.stem).strip("-") or "clip"
    output = output_dir / f"{safe_name}-captioned-{uuid4().hex[:8]}.mp4"
    font_dir = font_path.parent if font_path is not None else None
    total_duration = duration + intro_duration + outro_duration
    adjusted_words = [
        {
            **word,
            "start_time": float(word["start_time"]) + intro_duration,
            "end_time": float(word["end_time"]) + intro_duration,
        }
        for word in words
    ]
    progress(0, "Generating word-timed subtitles")

    with tempfile.TemporaryDirectory(prefix="clipper-export-") as temp_dir:
        temp_path = Path(temp_dir)
        subtitle_path = temp_path / "captions.ass"
        subtitle_path.write_text(
            build_ass(adjusted_words, style, int(settings["width"]), int(settings["height"])),
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
        used_nvenc = bool(settings.get("use_gpu", True)) and _has_nvenc()
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
                words,
                intro_source,
                intro_duration,
                intro_has_audio,
                outro_source,
                outro_duration,
                outro_has_audio,
                source_has_audio,
                logo_path,
                music_path,
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
                words,
                intro_source,
                intro_duration,
                intro_has_audio,
                outro_source,
                outro_duration,
                outro_has_audio,
                source_has_audio,
                logo_path,
                music_path,
            )
            used_nvenc = False
    progress(100, "Export complete")
    return output, used_nvenc
