import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.live_buffer import (
    BufferedSegment,
    LiveBufferError,
    cut_last_n,
    list_buffer_segments,
    prune_segments,
    select_last_segments,
)


def _segment(path: Path, duration: float, size: int, modified: float) -> BufferedSegment:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return BufferedSegment(path, duration, size, modified)


def test_select_last_segments_includes_enough_recent_media(tmp_path: Path) -> None:
    segments = [
        _segment(tmp_path / "1.ts", 2, 4, 1),
        _segment(tmp_path / "2.ts", 3, 5, 2),
        _segment(tmp_path / "3.ts", 4, 6, 3),
    ]

    selected, duration = select_last_segments(segments, 5)

    assert [segment.path.name for segment in selected] == ["2.ts", "3.ts"]
    assert duration == 7


def test_select_last_segments_reports_short_buffer(tmp_path: Path) -> None:
    segments = [_segment(tmp_path / "1.ts", 4, 4, 1)]

    with pytest.raises(LiveBufferError, match="Available buffer: 4 seconds"):
        select_last_segments(segments, 5)


def test_select_last_segments_can_anchor_a_candidate_to_its_trigger_time(tmp_path: Path) -> None:
    segments = [
        _segment(tmp_path / "1.ts", 2, 4, 1),
        _segment(tmp_path / "2.ts", 2, 4, 2),
        _segment(tmp_path / "3.ts", 2, 4, 3),
    ]

    selected, duration = select_last_segments(segments, 3, end_before=2)

    assert [segment.path.name for segment in selected] == ["1.ts", "2.ts"]
    assert duration == 4


def test_prune_segments_obeys_time_and_disk_caps(tmp_path: Path) -> None:
    segments = [
        _segment(tmp_path / "1.ts", 2, 6, 1),
        _segment(tmp_path / "2.ts", 2, 6, 2),
        _segment(tmp_path / "3.ts", 2, 6, 3),
    ]

    retained, size_bytes, duration = prune_segments(segments, buffer_seconds=4, disk_cap_bytes=12)

    assert [segment.path.name for segment in retained] == ["2.ts", "3.ts"]
    assert size_bytes == 12
    assert duration == 4
    assert not (tmp_path / "1.ts").exists()


def test_buffer_listing_ignores_missing_or_out_of_directory_segments(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    segment = session / "segment.ts"
    segment.write_bytes(b"segment")
    outside = tmp_path / "outside.ts"
    outside.write_bytes(b"outside")
    listing = session / "segments.csv"
    with listing.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["segment.ts", "0", "2"])
        writer.writerow([str(outside), "0", "2"])
        writer.writerow(["missing.ts", "2", "4"])

    result = list_buffer_segments(tmp_path)

    assert [item.path for item in result] == [segment]


def test_cut_last_n_seeks_into_concat_and_removes_temporary_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segments = [
        _segment(tmp_path / "1.ts", 4, 4, 1),
        _segment(tmp_path / "2.ts", 4, 4, 2),
    ]
    output = tmp_path / "clips" / "instant.mp4"
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        captured["command"] = command
        captured["concat"] = Path(command[command.index("-i") + 1])
        Path(command[-1]).write_bytes(b"clip")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("app.services.live_buffer.subprocess.run", fake_run)

    cut_last_n(segments, 5, output, ffmpeg_bin="ffmpeg-test")

    command = captured["command"]
    assert command[0] == "ffmpeg-test"
    assert command[command.index("-ss") + 1] == "3.000"
    assert command[command.index("-t") + 1] == "5"
    assert output.read_bytes() == b"clip"
    assert not captured["concat"].exists()
