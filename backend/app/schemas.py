from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Project name cannot be blank.")
        return value


class DownloadCreate(BaseModel):
    url: HttpUrl
    quality: Literal["best", "1080p", "720p", "480p"] = "best"


class LiveRecordingCreate(BaseModel):
    url: HttpUrl
    buffer_minutes: int = Field(default=30, ge=1, le=240)
    disk_cap_gb: float = Field(default=20, gt=0, le=500)
    audio_trigger_enabled: bool = False
    audio_peak_dbfs: float = Field(default=-10, ge=-60, le=0)
    chat_trigger_enabled: bool = False
    chat_messages_per_minute: int = Field(default=30, ge=1, le=10000)


class LiveClipCreate(BaseModel):
    seconds: int = Field(default=60, ge=1, le=600)


class UploadCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)


class ClipCreate(BaseModel):
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    mode: Literal["copy", "accurate"] = "accurate"


class ClipRange(BaseModel):
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> "ClipRange":
        if self.end_time <= self.start_time:
            raise ValueError("End time must be greater than start time.")
        return self


class ClipBatchCreate(BaseModel):
    segments: list[ClipRange] = Field(min_length=1, max_length=30)
    mode: Literal["copy", "accurate"] = "accurate"

    @model_validator(mode="after")
    def validate_segments(self) -> "ClipBatchCreate":
        ordered = sorted(self.segments, key=lambda segment: segment.start_time)
        if any(current.start_time < previous.end_time for previous, current in zip(ordered, ordered[1:])):
            raise ValueError("Timeline segments cannot overlap.")
        return self


class VideoRegion(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> "VideoRegion":
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("Selected video regions must stay inside the source frame.")
        return self


class TranscriptionCreate(BaseModel):
    model: Literal["tiny", "base", "small", "medium", "large-v3"] = "small"
    device: Literal["auto", "cpu"] = "auto"


class TranscriptWordUpdate(BaseModel):
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    text: str = Field(max_length=1000)

    @field_validator("text")
    @classmethod
    def trim_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Transcript words cannot be blank.")
        return value


class TranscriptUpdate(BaseModel):
    words: list[TranscriptWordUpdate] = Field(max_length=100000)


class ExportCreate(BaseModel):
    preset: Literal["bold_yellow", "minimal_white", "boxed", "glow"] = "bold_yellow"
    font_name: str = Field(default="Arial", min_length=1, max_length=80)
    font_size: int = Field(default=72, ge=24, le=120)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    outline_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    outline: int | None = Field(default=None, ge=0, le=12)
    position: Literal["top", "middle", "bottom"] = "bottom"
    bitrate: Literal["4M", "8M", "12M", "20M"] = "8M"
    fps: Literal[30, 60] = 30
    width: Literal[720, 1080, 1920] = 1080
    height: Literal[1080, 1280, 1350, 1920] = 1920
    font_file: str | None = Field(default=None, max_length=255)
    aspect_ratio: Literal["9:16", "1:1", "4:5", "16:9"] | None = None
    smart_reframe: bool = False
    background_fill: Literal["blur", "crop", "pad"] = "blur"
    layout: Literal["single", "gaming_split"] = "single"
    facecam_region: VideoRegion | None = None
    gameplay_region: VideoRegion | None = None

    @model_validator(mode="after")
    def validate_dimensions(self) -> "ExportCreate":
        dimensions = {
            "9:16": (1080, 1920),
            "1:1": (1080, 1080),
            "4:5": (1080, 1350),
            "16:9": (1920, 1080),
        }
        if self.aspect_ratio is None:
            legacy_dimensions = {
                (1080, 1920): "9:16",
                (720, 1280): "9:16",
                (1080, 1080): "1:1",
                (1080, 1350): "4:5",
                (1920, 1080): "16:9",
            }
            self.aspect_ratio = legacy_dimensions.get((self.width, self.height))
            if self.aspect_ratio is None:
                raise ValueError("Choose 9:16, 1:1, 4:5, or 16:9 output.")
        else:
            self.width, self.height = dimensions[self.aspect_ratio]
        if self.layout == "gaming_split" and (
            self.facecam_region is None or self.gameplay_region is None
        ):
            raise ValueError("Select both the facecam and gameplay regions for a split-screen export.")
        return self


class LayoutPresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    aspect_ratio: Literal["9:16", "1:1", "4:5", "16:9"] = "9:16"
    facecam_region: VideoRegion
    gameplay_region: VideoRegion

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Preset name cannot be blank.")
        return value


class HighlightWeights(BaseModel):
    transcript: float = Field(default=0.4, ge=0, le=1)
    audio: float = Field(default=0.25, ge=0, le=1)
    scene: float = Field(default=0.15, ge=0, le=1)
    motion: float = Field(default=0.2, ge=0, le=1)

    @model_validator(mode="after")
    def require_weight(self) -> "HighlightWeights":
        if sum((self.transcript, self.audio, self.scene, self.motion)) <= 0:
            raise ValueError("At least one signal weight must be greater than zero.")
        return self


class HighlightAnalyzeCreate(BaseModel):
    min_duration: int = Field(default=20, ge=15, le=180)
    max_duration: int = Field(default=60, ge=15, le=180)
    number_of_clips: int = Field(default=8, ge=1, le=30)
    allow_overlap: bool = False
    gaming_mode: bool = False
    ollama_model: str = Field(default="", max_length=100)
    weights: HighlightWeights | None = None

    @field_validator("ollama_model")
    @classmethod
    def trim_ollama_model(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_durations(self) -> "HighlightAnalyzeCreate":
        if self.max_duration < self.min_duration:
            raise ValueError("Maximum clip duration must be at least the minimum duration.")
        return self


class SuggestionUpdate(BaseModel):
    start_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    status: Literal["accepted", "rejected"] | None = None

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = value.strip()
        if not title:
            raise ValueError("Suggestion title cannot be blank.")
        return title


class SuggestionStatusUpdate(BaseModel):
    status: Literal["accepted", "rejected"]


class SuggestionClipCreate(BaseModel):
    mode: Literal["copy", "accurate"] = "accurate"


class SettingsUpdate(BaseModel):
    cookies_file: str = ""
