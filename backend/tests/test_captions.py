import pytest

from app.services.captions import (
    build_ass,
    build_caption_style,
    build_srt,
    group_caption_words,
)


def _words():
    return [
        {"start_time": 1.0, "end_time": 1.3, "text": "Hello"},
        {"start_time": 1.5, "end_time": 1.9, "text": "world!"},
        {"start_time": 2.0, "end_time": 2.2, "text": "✨"},
    ]


def test_ass_uses_word_karaoke_timing_and_caption_style() -> None:
    style = build_caption_style("bold_yellow", font_size=64, position="top")

    ass = build_ass(_words(), style)

    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass
    assert "Style: Default,Arial,64" in ass
    assert "Dialogue: 0,0:00:01.00,0:00:02.20" in ass
    assert r"{\k50}Hello {\k50}world! {\k20}✨" in ass
    assert "Alignment" in ass and ",8,60,60,192" in ass


def test_caption_groups_split_on_the_requested_readable_line_length() -> None:
    groups = group_caption_words(
        [
            {"start_time": 0, "end_time": 0.2, "text": "one"},
            {"start_time": 0.2, "end_time": 0.4, "text": "two"},
            {"start_time": 0.4, "end_time": 0.6, "text": "three"},
        ],
        max_characters=7,
    )

    assert [[word["text"] for word in group] for group in groups] == [["one", "two"], ["three"]]


def test_ass_escapes_user_supplied_override_syntax() -> None:
    ass = build_ass(
        [{"start_time": 0, "end_time": 0.5, "text": "{\\fs200}Hi"}],
        build_caption_style(),
    )

    assert r"\{\\" in ass
    assert r"\}" in ass
    assert r"{\fs200}" not in ass


def test_caption_text_cannot_inject_additional_ass_dialogues() -> None:
    ass = build_ass(
        [{"start_time": 0, "end_time": 0.5, "text": "Hi\nDialogue: 0,0:00:00.00"}],
        build_caption_style(),
    )

    assert ass.count("\nDialogue:") == 1
    assert "Hi Dialogue: 0,0:00:00.00" in ass


def test_srt_uses_valid_millisecond_timestamps_and_preserves_emoji() -> None:
    srt = build_srt(_words())

    assert srt.startswith("1\n00:00:01,000 --> 00:00:02,200\nHello world!")
    assert "✨" in srt


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"preset": "unknown"}, "preset"),
        ({"font_size": 121}, "font size"),
        ({"color": "yellow"}, "hex color"),
        ({"position": "left"}, "position"),
    ],
)
def test_caption_style_rejects_invalid_values(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build_caption_style(**kwargs)
