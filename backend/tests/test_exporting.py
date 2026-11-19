import io
from pathlib import Path

from app.services.captions import build_caption_style
from app.services.exporting import ExportError, export_captioned_clip


def test_nvenc_export_falls_back_to_cpu_and_keeps_captioned_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import app.services.exporting as exporting

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output_dir = tmp_path / "output"
    monkeypatch.setattr(exporting, "DATA_DIR", tmp_path)
    monkeypatch.setattr(exporting, "_has_nvenc", lambda: True)
    encoders: list[str] = []

    def render(
        _source: Path,
        subtitle_path: Path,
        _font_dir: Path | None,
        output: Path,
        _settings: dict,
        encoder: str,
        _duration: float,
        _progress,
        *_tracking,
    ) -> None:
        assert "Dialogue: 0," in subtitle_path.read_text(encoding="utf-8-sig")
        encoders.append(encoder)
        if encoder == "h264_nvenc":
            raise ExportError("No compatible NVENC device")
        output.write_bytes(b"encoded video")

    monkeypatch.setattr(exporting, "_render", render)
    output, used_nvenc = export_captioned_clip(
        source,
        [{"start_time": 0.1, "end_time": 0.4, "text": "Hi ✨"}],
        build_caption_style(),
        {
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "bitrate": "8M",
        },
        None,
        1,
        lambda _percent, _message: None,
    )

    assert encoders == ["h264_nvenc", "libx264"]
    assert output.parent == output_dir
    assert output.read_bytes() == b"encoded video"
    assert used_nvenc is False


def _render_and_capture_command(tmp_path: Path, monkeypatch, settings: dict, **tracking) -> list[str]:
    import app.services.exporting as exporting

    source = tmp_path / "source.mp4"
    source.touch()
    subtitle = tmp_path / "captions.ass"
    subtitle.touch()
    output = tmp_path / "output.mp4"

    class Process:
        stdout = io.StringIO("out_time_ms=500000\n")
        stderr = io.StringIO("")

        @staticmethod
        def wait() -> int:
            return 0

    commands: list[str] = []

    def fake_popen(command: list[str], **_kwargs):
        commands.extend(command)
        output.write_bytes(b"encoded")
        return Process()

    monkeypatch.setattr(exporting.subprocess, "Popen", fake_popen)
    exporting._render(
        source,
        subtitle,
        None,
        output,
        {"width": 1080, "height": 1920, "fps": 30, "bitrate": "8M", **settings},
        "libx264",
        4,
        lambda _percent, _message: None,
        **tracking,
    )
    return commands


def test_blurred_background_filter_is_used_for_mismatched_aspect_ratio(
    tmp_path: Path,
    monkeypatch,
) -> None:
    command = _render_and_capture_command(tmp_path, monkeypatch, {"background_fill": "blur"})

    filter_graph = command[command.index("-filter_complex") + 1]
    assert "[background]scale=1080:1920" in filter_graph
    assert "boxblur=20:10" in filter_graph
    assert "overlay=" in filter_graph


def test_gaming_layout_crops_both_selected_regions_and_stacks_them(
    tmp_path: Path,
    monkeypatch,
) -> None:
    command = _render_and_capture_command(
        tmp_path,
        monkeypatch,
        {
            "layout": "gaming_split",
            "facecam_region": {"x": 0.6, "y": 0, "width": 0.4, "height": 0.3},
            "gameplay_region": {"x": 0, "y": 0.3, "width": 1, "height": 0.7},
        },
    )

    filter_graph = command[command.index("-filter_complex") + 1]
    assert "[gamein]crop=iw*1.000000:ih*0.700000" in filter_graph
    assert "[facein]crop=iw*0.400000:ih*0.300000" in filter_graph
    assert "vstack=inputs=2" in filter_graph


def test_smart_reframe_uses_a_time_varying_crop_filter(tmp_path: Path, monkeypatch) -> None:
    command = _render_and_capture_command(
        tmp_path,
        monkeypatch,
        {"smart_reframe": True},
        face_track=[
            {"time": 0, "x": 0.25, "y": 0.5},
            {"time": 4, "x": 0.75, "y": 0.5},
        ],
        source_dimensions=(1920, 1080),
    )

    video_filter = command[command.index("-vf") + 1]
    assert video_filter.startswith("crop=")
    assert "if(lt(t\\," in video_filter
