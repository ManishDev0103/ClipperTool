import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

PRESETS = {"bold_yellow", "minimal_white", "boxed", "glow"}
POSITIONS = {"top", "middle", "bottom"}
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_LINE_CHARACTERS = 32


def font_family(path: Path) -> str:
    try:
        from fontTools.ttLib import TTFont
    except ImportError as exc:
        raise ValueError("Font parsing is unavailable; reinstall backend requirements.") from exc
    try:
        font = TTFont(path, lazy=True)
        try:
            family = font["name"].getDebugName(16) or font["name"].getDebugName(1)
        finally:
            font.close()
    except Exception as exc:
        raise ValueError(f"Could not read the uploaded font: {exc}") from exc
    if not family or any(character in family for character in "\r\n,{}"):
        raise ValueError("The uploaded font has no supported family name.")
    return family


@dataclass(frozen=True)
class CaptionStyle:
    font_name: str
    font_size: int
    color: str
    outline_color: str
    outline: int
    bold: bool
    boxed: bool
    glow: bool
    position: str


PRESET_STYLES = {
    "bold_yellow": {"color": "#FFD84D", "outline_color": "#111111", "outline": 3, "bold": True},
    "minimal_white": {"color": "#FFFFFF", "outline_color": "#111111", "outline": 1, "bold": False},
    "boxed": {"color": "#FFFFFF", "outline_color": "#111111", "outline": 1, "bold": True},
    "glow": {"color": "#FFFFFF", "outline_color": "#FFD84D", "outline": 4, "bold": True},
}


def build_caption_style(
    preset: str = "bold_yellow",
    font_name: str = "Arial",
    font_size: int = 72,
    color: str | None = None,
    outline_color: str | None = None,
    outline: int | None = None,
    position: str = "bottom",
) -> CaptionStyle:
    if preset not in PRESETS:
        raise ValueError(f"Unsupported caption preset: {preset}")
    if position not in POSITIONS:
        raise ValueError(f"Unsupported caption position: {position}")
    if not 24 <= font_size <= 120:
        raise ValueError("Caption font size must be between 24 and 120.")
    if color is not None and not COLOR_PATTERN.fullmatch(color):
        raise ValueError("Caption color must be a six-digit hex color.")
    if outline_color is not None and not COLOR_PATTERN.fullmatch(outline_color):
        raise ValueError("Caption outline color must be a six-digit hex color.")
    if outline is not None and not 0 <= outline <= 12:
        raise ValueError("Caption outline must be between 0 and 12.")
    if not font_name.strip() or any(character in font_name for character in "\r\n,{}"):
        raise ValueError("Caption font name contains unsupported characters.")

    preset_style = PRESET_STYLES[preset]
    return CaptionStyle(
        font_name=font_name.strip(),
        font_size=font_size,
        color=color or preset_style["color"],
        outline_color=outline_color or preset_style["outline_color"],
        outline=outline if outline is not None else preset_style["outline"],
        bold=bool(preset_style["bold"]),
        boxed=preset == "boxed",
        glow=preset == "glow",
        position=position,
    )


def group_caption_words(
    words: Iterable[dict[str, Any]],
    max_characters: int = MAX_LINE_CHARACTERS,
) -> list[list[dict[str, Any]]]:
    if max_characters < 1:
        raise ValueError("Caption line length must be greater than zero.")
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_length = 0
    for word in words:
        text = " ".join(str(word.get("text", "")).split())
        start = float(word.get("start_time", 0))
        end = float(word.get("end_time", 0))
        if not text:
            continue
        if start < 0 or end <= start:
            raise ValueError("Every caption word needs a valid positive time range.")
        added_length = len(text) + (1 if current else 0)
        if current and current_length + added_length > max_characters:
            groups.append(current)
            current = []
            current_length = 0
            added_length = len(text)
        current.append({**word, "text": text, "start_time": start, "end_time": end})
        current_length += added_length
    if current:
        groups.append(current)
    return groups


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    whole_seconds, fractional = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{whole_seconds:02}.{fractional:02}"


def _ass_color(hex_color: str, alpha: str = "00") -> str:
    red, green, blue = (hex_color[index : index + 2] for index in (1, 3, 5))
    return f"&H{alpha}{blue}{green}{red}".upper()


def _ass_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def build_ass(
    words: Iterable[dict[str, Any]],
    style: CaptionStyle,
    width: int = 1080,
    height: int = 1920,
) -> str:
    if width <= 0 or height <= 0:
        raise ValueError("ASS video dimensions must be positive.")
    alignment = {"bottom": 2, "middle": 5, "top": 8}[style.position]
    margin_vertical = max(60, round(height * 0.1))
    border_style = 3 if style.boxed else 1
    back_color = _ass_color("#111111", "70") if style.boxed else "&H80000000"
    blur = 2 if style.glow else 0
    primary = _ass_color(style.color)
    secondary = _ass_color("#FFFFFF")
    outline = _ass_color(style.outline_color)
    header = f"""[Script Info]
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style.font_name},{style.font_size},{primary},{secondary},{outline},{back_color},{-1 if style.bold else 0},0,0,0,100,100,0,0,{border_style},{style.outline},0,{alignment},60,60,{margin_vertical},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogues: list[str] = []
    for group in group_caption_words(words):
        karaoke_text: list[str] = []
        for index, word in enumerate(group):
            karaoke_end = (
                group[index + 1]["start_time"]
                if index + 1 < len(group)
                else word["end_time"]
            )
            duration = max(1, round((karaoke_end - word["start_time"]) * 100))
            if karaoke_text:
                karaoke_text.append(" ")
            karaoke_text.append(f"{{\\k{duration}}}{_ass_text(word['text'])}")
        if style.glow:
            karaoke_text.insert(0, "{\\blur2}")
        start = float(group[0]["start_time"])
        end = float(group[-1]["end_time"])
        dialogues.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{''.join(karaoke_text)}"
        )
    return header + "\n".join(dialogues) + ("\n" if dialogues else "")


def build_srt(words: Iterable[dict[str, Any]]) -> str:
    entries: list[str] = []
    for index, group in enumerate(group_caption_words(words), start=1):
        start = max(0, round(float(group[0]["start_time"]) * 1000))
        end = max(start + 1, round(float(group[-1]["end_time"]) * 1000))

        def stamp(milliseconds: int) -> str:
            hours, remainder = divmod(milliseconds, 3_600_000)
            minutes, remainder = divmod(remainder, 60_000)
            seconds, millis = divmod(remainder, 1000)
            return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"

        text = " ".join(str(word["text"]).strip() for word in group)
        entries.append(f"{index}\n{stamp(start)} --> {stamp(end)}\n{text}")
    return "\n\n".join(entries) + ("\n" if entries else "")
