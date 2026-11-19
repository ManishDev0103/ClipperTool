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
