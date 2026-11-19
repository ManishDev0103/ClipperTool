import re
from collections.abc import Iterable
from math import floor
from typing import Any


HOOK_WORDS = {"wait", "secret", "truth", "watch", "here's", "imagine", "nobody"}
FUNNY_WORDS = {"laugh", "laughing", "joke", "hilarious", "funny", "comedy"}
EMOTION_WORDS = {"love", "hate", "afraid", "amazing", "terrible", "incredible", "shocked"}
STRONG_WORDS = {"always", "never", "must", "impossible", "exactly", "proof", "definitely"}
STORY_WORDS = {"first", "then", "because", "finally", "story", "happened", "suddenly"}
DEFAULT_WEIGHTS = {"transcript": 0.4, "audio": 0.25, "scene": 0.15, "motion": 0.2}
GAMING_WEIGHTS = {"transcript": 0.15, "audio": 0.35, "scene": 0.15, "motion": 0.35}


def score_signals(
    signals: dict[str, float],
    weights: dict[str, float] | None = None,
) -> float:
    selected_weights = weights or DEFAULT_WEIGHTS
    weighted = [
        (max(0.0, min(1.0, float(signals.get(name, 0.0)))), max(0.0, float(weight)))
        for name, weight in selected_weights.items()
        if weight > 0
    ]
    denominator = sum(weight for _, weight in weighted)
    if denominator <= 0:
        return 0.0
    return sum(value * weight for value, weight in weighted) / denominator


def transcript_events(words: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        if current and (
            len(current) >= 18
            or float(word["start_time"]) - float(current[0]["start_time"]) >= 14
            or str(current[-1]["text"]).rstrip().endswith((".", "!", "?"))
        ):
            batches.append(current)
            current = []
        current.append(word)
    if current:
        batches.append(current)

    events: list[dict[str, Any]] = []
    for batch in batches:
        text = " ".join(str(word["text"]).strip() for word in batch).strip()
        if not text:
            continue
        tokens = set(re.findall(r"[\w']+", text.casefold()))
        categories: list[tuple[str, float, str]] = []
        if tokens & HOOK_WORDS:
            categories.append(("hook", 0.82, "A hook or curiosity phrase creates an immediate reason to keep watching."))
        if tokens & FUNNY_WORDS:
            categories.append(("funny", 0.84, "The transcript contains a humor or laughter cue."))
        if tokens & EMOTION_WORDS:
            categories.append(("emotional_peak", 0.78, "Emotionally charged language may make this moment memorable."))
        if tokens & STRONG_WORDS:
            categories.append(("strong_statement", 0.76, "A confident or emphatic statement stands out."))
        if tokens & STORY_WORDS:
            categories.append(("story_beat", 0.69, "A storytelling transition suggests a clear narrative beat."))
        if text.rstrip().endswith("?") or any(token in {"why", "how", "what"} for token in tokens):
            categories.append(("question", 0.68, "A question can create curiosity and invite a response."))
        if not categories:
            continue
        best_category, best_score, _ = max(categories, key=lambda item: item[1])
        reasons = list(dict.fromkeys(reason for _, _, reason in categories))
        title_text = re.sub(r"\s+", " ", text).strip()
        if len(title_text) > 76:
            title_text = title_text[:73].rsplit(" ", 1)[0] + "..."
        events.append(
            {
                "start_time": float(batch[0]["start_time"]),
                "end_time": float(batch[-1]["end_time"]),
                "signal": "transcript",
                "score": best_score,
                "category": best_category,
                "title": title_text or best_category.replace("_", " ").title(),
                "reason": " ".join(reasons),
            }
        )
    return events


def select_suggestions(
    events: Iterable[dict[str, Any]],
    video_duration: float,
    min_duration: float = 20,
    max_duration: float = 60,
    number_of_clips: int = 8,
    allow_overlap: bool = False,
    weights: dict[str, float] | None = None,
    gaming_mode: bool = False,
) -> list[dict[str, Any]]:
    if video_duration < min_duration or min_duration <= 0 or max_duration < min_duration:
        return []
    if number_of_clips <= 0:
        return []

    usable_events = [
        {
            **event,
            "start_time": max(0.0, float(event["start_time"])),
            "end_time": min(video_duration, float(event["end_time"])),
            "score": max(0.0, min(1.0, float(event["score"]))),
        }
        for event in events
        if 0 <= float(event.get("start_time", -1)) < video_duration
        and float(event.get("end_time", 0)) > float(event.get("start_time", -1))
    ]
    if not usable_events:
        return []
    selected_weights = weights or DEFAULT_WEIGHTS
    available_weights = {
        signal: weight
        for signal, weight in selected_weights.items()
        if any(event["signal"] == signal for event in usable_events)
    }
    event_bins: dict[int, list[dict[str, Any]]] = {}
    for event in usable_events:
        first_bin = floor(event["start_time"])
        last_bin = floor(max(event["start_time"], event["end_time"] - 1e-6))
        for time_bin in range(first_bin, last_bin + 1):
            event_bins.setdefault(time_bin, []).append(event)

    candidates: list[dict[str, Any]] = []
    for event in usable_events:
        event_length = event["end_time"] - event["start_time"]
        duration = min(max_duration, max(min_duration, 45.0, event_length + 12.0))
        duration = min(duration, video_duration)
        if duration < min_duration:
            continue
        center = (event["start_time"] + event["end_time"]) / 2
        start = max(0.0, min(video_duration - duration, center - duration / 2))
        end = start + duration
        covering_ids: set[int] = set()
        covering: list[dict[str, Any]] = []
        for time_bin in range(floor(start), floor(max(start, end - 1e-6)) + 1):
            for item in event_bins.get(time_bin, []):
                item_id = id(item)
                if (
                    item_id not in covering_ids
                    and item["start_time"] < end
                    and item["end_time"] > start
                ):
                    covering_ids.add(item_id)
                    covering.append(item)
        signal_scores: dict[str, float] = {}
        for item in covering:
            name = str(item["signal"])
            signal_scores[name] = max(signal_scores.get(name, 0.0), item["score"])
        score = score_signals(signal_scores, available_weights)
        if score <= 0:
            continue
        reasons = list(
            dict.fromkeys(
                item["reason"]
                for item in sorted(covering, key=lambda candidate: candidate["score"], reverse=True)
                if item.get("reason")
            )
        )
        title_source = max(
            covering,
            key=lambda item: (item.get("signal") == "transcript", item["score"]),
        )
        title = str(title_source.get("title") or "Highlight")
        reason = " ".join(reasons[:3]) or "Multiple analysis signals overlapped in this segment."
        if gaming_mode and signal_scores.get("audio", 0) >= 0.55 and signal_scores.get("motion", 0) >= 0.55:
            title = "High-action gaming moment"
            reason = (
                "A local audio peak overlaps with high visual motion, suggesting a possible "
                "combat, explosion, chase, or other action beat. " + reason
            )
        candidates.append(
            {
                "start_time": round(start, 3),
                "end_time": round(end, 3),
                "score": round(score * 100, 1),
                "title": title,
                "reason": reason,
                "signals": signal_scores,
            }
        )

    candidates.sort(key=lambda item: (-item["score"], item["start_time"]))
    chosen: list[dict[str, Any]] = []
    seen: set[tuple[float, float]] = set()
    for candidate in candidates:
        signature = (candidate["start_time"], candidate["end_time"])
        if signature in seen:
            continue
        if not allow_overlap and any(
            candidate["start_time"] < item["end_time"]
            and candidate["end_time"] > item["start_time"]
            for item in chosen
        ):
            continue
        chosen.append(candidate)
        seen.add(signature)
        if len(chosen) >= number_of_clips:
            break
    return chosen
