import pytest

from app.services.highlights import score_signals, select_suggestions, transcript_events


def test_score_signals_combines_configurable_weights() -> None:
    score = score_signals(
        {"transcript": 0.9, "audio": 0.4},
        {"transcript": 0.4, "audio": 0.6},
    )

    assert score == pytest.approx(0.6)


def test_missing_signal_is_scored_as_zero_without_renormalizing_its_weight() -> None:
    score = score_signals({"scene": 0.8}, {"transcript": 0.5, "scene": 0.5})

    assert score == pytest.approx(0.4)


def test_segment_selection_respects_duration_ranking_count_and_overlap() -> None:
    events = [
        {
            "start_time": 10,
            "end_time": 11,
            "signal": "transcript",
            "score": 0.9,
            "title": "Strong moment",
            "reason": "A strong statement.",
        },
        {
            "start_time": 14,
            "end_time": 15,
            "signal": "audio",
            "score": 0.8,
            "title": "Audio spike",
            "reason": "High energy.",
        },
        {
            "start_time": 70,
            "end_time": 71,
            "signal": "scene",
            "score": 0.7,
            "title": "Scene change",
            "reason": "A scene transition.",
        },
    ]
    weights = {"transcript": 0.5, "audio": 0.3, "scene": 0.2}

    suggestions = select_suggestions(
        events,
        video_duration=100,
        min_duration=15,
        max_duration=30,
        number_of_clips=2,
        allow_overlap=False,
        weights=weights,
    )

    assert len(suggestions) == 2
    assert suggestions[0]["score"] > suggestions[1]["score"]
    assert all(15 <= item["end_time"] - item["start_time"] <= 30 for item in suggestions)
    assert suggestions[0]["title"] == "Strong moment"
    assert suggestions[0]["signals"] == {"transcript": 0.9, "audio": 0.8}
    assert suggestions[0]["end_time"] <= suggestions[1]["start_time"] or suggestions[1]["end_time"] <= suggestions[0]["start_time"]


def test_overlap_can_be_enabled_and_video_boundaries_are_respected() -> None:
    events = [
        {"start_time": 10, "end_time": 11, "signal": "audio", "score": 0.9, "title": "A", "reason": "a"},
        {"start_time": 20, "end_time": 21, "signal": "audio", "score": 0.8, "title": "B", "reason": "b"},
    ]

    suggestions = select_suggestions(
        events,
        video_duration=50,
        min_duration=15,
        max_duration=18,
        number_of_clips=2,
        allow_overlap=True,
        weights={"audio": 1},
    )

    assert len(suggestions) == 2
    assert suggestions[0]["start_time"] == 1.5
    assert suggestions[0]["end_time"] <= 50


def test_gaming_mode_labels_overlapping_audio_and_motion_action() -> None:
    suggestions = select_suggestions(
        [
            {"start_time": 30, "end_time": 31, "signal": "audio", "score": 0.8, "title": "Audio peak", "reason": "A peak."},
            {"start_time": 30, "end_time": 31, "signal": "motion", "score": 0.9, "title": "Motion", "reason": "Fast motion."},
        ],
        video_duration=90,
        min_duration=15,
        max_duration=30,
        number_of_clips=1,
        weights={"audio": 0.5, "motion": 0.5},
        gaming_mode=True,
    )

    assert suggestions[0]["title"] == "High-action gaming moment"
    assert "possible" in suggestions[0]["reason"]


def test_transcript_heuristics_find_questions_hooks_and_funny_moments() -> None:
    words = [
        {"start_time": 1, "end_time": 1.2, "text": "Wait"},
        {"start_time": 1.2, "end_time": 1.5, "text": "for"},
        {"start_time": 1.5, "end_time": 2, "text": "it?"},
        {"start_time": 12, "end_time": 12.4, "text": "That"},
        {"start_time": 12.4, "end_time": 12.8, "text": "joke"},
        {"start_time": 12.8, "end_time": 13.2, "text": "was"},
        {"start_time": 13.2, "end_time": 13.8, "text": "hilarious!"},
    ]

    events = transcript_events(words)

    assert {event["category"] for event in events} == {"hook", "funny"}
