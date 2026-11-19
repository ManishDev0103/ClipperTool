import io
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.cutting import ClipCutError, cut_clip


def _fake_process(return_code: int = 0, stderr: str = ""):
    process = type("FakeProcess", (), {})()
    process.stdout = io.StringIO("out_time_ms=500000\nprogress=continue\n")
    process.stderr = io.StringIO(stderr)
    process.wait = lambda: return_code
    return process


def _successful_process(command: list[str], **_kwargs):
    Path(command[-1]).write_bytes(b"clip")
    return _fake_process()


def test_copy_mode_uses_stream_copy_and_reports_progress(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    output = tmp_path / "clips" / "clip.mkv"
    progress: list[float] = []

    with patch("app.services.cutting.subprocess.Popen", side_effect=_successful_process) as popen:
        cut_clip(source, 1, 3, output, "copy", progress.append, "ffmpeg-test")

    command = popen.call_args.args[0]
    assert command[0] == "ffmpeg-test"
    assert command[command.index("-c") + 1] == "copy"
    assert command[command.index("-ss") + 1] == "1"
    assert progress == [25.0, 100.0]
    assert output.is_file()


def test_accurate_mode_reencodes_after_input_seek(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.touch()

    with patch("app.services.cutting.subprocess.Popen", side_effect=_successful_process) as popen:
        cut_clip(source, 2, 5, tmp_path / "clip.mp4", "accurate")

    command = popen.call_args.args[0]
    assert command.index("-i") < command.index("-ss")
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-c:a") + 1] == "aac"


@pytest.mark.parametrize(
    ("start", "end", "mode", "message"),
    [
        (-1, 2, "copy", "Start time"),
        (2, 2, "copy", "End time"),
        (3, 2, "copy", "End time"),
        (0, 2, "unknown", "Mode"),
    ],
)
def test_invalid_arguments_are_rejected(
    tmp_path: Path,
    start: float,
    end: float,
    mode: str,
    message: str,
) -> None:
    source = tmp_path / "source.mp4"
    source.touch()

    with pytest.raises(ClipCutError, match=message):
        cut_clip(source, start, end, tmp_path / "clip.mp4", mode)


def test_ffmpeg_failure_is_reported(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    with patch(
        "app.services.cutting.subprocess.Popen",
        return_value=_fake_process(return_code=1, stderr="codec error"),
    ):
        with pytest.raises(ClipCutError, match="codec error"):
            cut_clip(source, 0, 1, tmp_path / "clip.mp4")


def test_missing_output_is_not_reported_as_success(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    with patch("app.services.cutting.subprocess.Popen", return_value=_fake_process()):
        with pytest.raises(ClipCutError, match="without creating"):
            cut_clip(source, 0, 1, tmp_path / "clip.mp4")
