import pytest

from app.services.ollama import OllamaError, _validate_local_url, generate_clip_metadata


def test_ollama_urls_must_be_local_or_private() -> None:
    assert _validate_local_url("http://127.0.0.1:11434/") == "http://127.0.0.1:11434"
    assert _validate_local_url("http://ollama:11434") == "http://ollama:11434"
    with pytest.raises(OllamaError, match="loopback or a local Ollama Docker host"):
        _validate_local_url("http://example.com:11434")
    with pytest.raises(OllamaError, match="local HTTP URL"):
        _validate_local_url("https://127.0.0.1:11434")


def test_clip_metadata_uses_local_fallback_when_ollama_is_offline(monkeypatch) -> None:
    import app.services.ollama as ollama

    def offline(*_args, **_kwargs):
        raise OllamaError("connection refused")

    monkeypatch.setattr(ollama, "_generate", offline)
    metadata, used_ollama = generate_clip_metadata(
        "That amazing story happened suddenly!",
        "Amazing story",
        "qwen2.5:7b",
    )

    assert used_ollama is False
    assert metadata["title"] == "Amazing story"
    assert "#amazing" in metadata["hashtags"]
