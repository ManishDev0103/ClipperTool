import sys
from types import SimpleNamespace
from pathlib import Path

from app.services.transcription import transcribe_video


def _segment():
    word = SimpleNamespace(start=0.1, end=0.3, word=" hello")
    return SimpleNamespace(start=0.1, end=0.3, words=[word])


def test_transcription_uses_ten_minute_chunks_and_preserves_word_offsets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import app.services.transcription as transcription

    calls: list[tuple[str | None, str]] = []

    class FakeModel:
        def __init__(self, _size, device, **_kwargs):
            self.device = device

        def transcribe(self, _path, language, **_kwargs):
            calls.append((language, self.device))
            return iter([_segment()]), SimpleNamespace(language="en")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    extracted: list[tuple[float, float]] = []
    monkeypatch.setattr(transcription, "_extract_chunk", lambda _video, start, duration, _output: extracted.append((start, duration)))
    video = tmp_path / "long-video.mp4"
    video.touch()
    progress: list[tuple[float, str]] = []

    words, language, device = transcribe_video(video, 601, "tiny", "cpu", lambda value, message: progress.append((value, message)))

    assert extracted == [(0, 600), (600, 1)]
    assert calls == [(None, "cpu"), ("en", "cpu")]
    assert [(word["start_time"], word["end_time"]) for word in words] == [(0.1, 0.3), (600.1, 600.3)]
    assert language == "en"
    assert device == "cpu"
    assert progress[-1][0] == 99


def test_auto_device_retries_cuda_failure_on_cpu(tmp_path: Path, monkeypatch) -> None:
    import app.services.transcription as transcription

    devices: list[str] = []

    class FakeModel:
        def __init__(self, _size, device, **_kwargs):
            devices.append(device)
            if device == "cuda":
                raise RuntimeError("CUDA out of memory")

        def transcribe(self, _path, language, **_kwargs):
            return iter([_segment()]), SimpleNamespace(language=language or "en")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(transcription, "_extract_chunk", lambda *_args: None)
    video = tmp_path / "short-video.mp4"
    video.touch()
    progress: list[str] = []

    words, language, device = transcribe_video(
        video,
        1,
        "base",
        "auto",
        lambda _value, message: progress.append(message),
    )

    assert devices == ["cuda", "cpu"]
    assert words[0]["text"].strip() == "hello"
    assert language == "en"
    assert device == "cpu"
    assert any("retrying" in message for message in progress)
