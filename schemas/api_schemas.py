"""
Contratos Pydantic de requisição e resposta da API REST do EasyKut Studio (V4.0).
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

AllowedLayoutMode = Literal[
    "horizontal_16_9",
    "vertical_blur",
    "manual_crop",
    "split_screen",
    "face_tracking_9_16",
    "square_1_1",
]
AllowedSubtitlePreset = Literal["classic", "hormozi", "neon", "minimal", "boxed"]
AllowedTransition = Literal["fade", "wipeleft", "slideright", "dissolve", "smoothleft"]
AllowedPlatform = Literal["youtube_shorts", "tiktok", "instagram_reels"]
AllowedExportFormat = Literal["9:16", "1:1", "16:9"]
AllowedLogoPosition = Literal["top_left", "top_right", "bottom_left", "bottom_right"]


class AnalyzeRequest(BaseModel):
    url: str
    provider: Literal["gemini", "groq"] = "gemini"
    max_cuts: int = Field(default=5, ge=1, le=20)


class FrameRequest(BaseModel):
    video_id: Optional[int] = None
    source: Optional[str] = None
    timestamp_sec: float = Field(default=1.0, ge=0.0)


class PreviewClipRequest(BaseModel):
    video_id: Optional[int] = None
    source: Optional[str] = None
    start_time: float = Field(default=0.0, ge=0.0)
    end_time: float = Field(default=30.0, gt=0.0)


class CreateCorteRequest(BaseModel):
    video_id: int = Field(..., gt=0)
    title: str = Field(default="Novo Corte Manual", min_length=1, max_length=160)
    start_time: float = Field(..., ge=0.0)
    end_time: float = Field(..., gt=0.0)
    virality_score: float = Field(default=80.0, ge=0.0, le=100.0)
    justification: str = ""
    tipo: Optional[Literal["short", "longo"]] = None
    layout_mode: AllowedLayoutMode = "vertical_blur"
    layout_params: Optional[Dict[str, Any]] = None
    burn_subtitles: bool = True

    @model_validator(mode="after")
    def validate_time_order(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time deve ser maior que start_time.")
        return self


class UpdateCorteRequest(BaseModel):
    title: Optional[str] = None
    start_time: Optional[float] = Field(default=None, ge=0.0)
    end_time: Optional[float] = Field(default=None, gt=0.0)
    tipo: Optional[Literal["short", "longo"]] = None
    layout_mode: Optional[AllowedLayoutMode] = None
    layout_params: Optional[Dict[str, Any]] = None
    burn_subtitles: Optional[bool] = None

    @model_validator(mode="after")
    def validate_time_order(self):
        if self.start_time is not None and self.end_time is not None and self.end_time <= self.start_time:
            raise ValueError("end_time deve ser maior que start_time.")
        return self


class RenderCutItem(BaseModel):
    id: Optional[int] = None
    title: Optional[str] = None
    titulo: Optional[str] = None
    start_time: float = Field(..., ge=0.0)
    end_time: float = Field(..., gt=0.0)
    layout_mode: AllowedLayoutMode = "vertical_blur"
    layout_params: Optional[Dict[str, Any]] = None
    burn_subtitles: Optional[bool] = True
    subtitle_preset: AllowedSubtitlePreset = "hormozi"
    karaoke_subtitles: bool = True
    dynamic_face_tracking: bool = True
    remove_silences: bool = False
    min_silence_sec: float = Field(default=0.6, ge=0.25, le=3.0)
    punch_in_zoom: bool = False
    subtitle_overrides: Optional[List[Dict[str, Any]]] = None

    @model_validator(mode="after")
    def validate_time_order(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time deve ser maior que start_time.")
        return self

    def get_effective_title(self, fallback: str = "Corte") -> str:
        return self.titulo or self.title or fallback


class RenderRequest(BaseModel):
    video_id: int
    cuts: List[RenderCutItem]
    max_resolution: Literal[720, 1080] = 1080
    use_gpu: bool = False
    brand_kit_id: Optional[int] = None
    normalize_audio: bool = False
    denoise_audio: bool = False
    music_filename: Optional[str] = None
    music_volume: float = Field(default=0.18, ge=0.02, le=0.6)
    export_formats: Optional[List[AllowedExportFormat]] = None


class CleanupRequest(BaseModel):
    remove_raw_downloads: bool = False
    max_age_hours: Optional[float] = Field(default=None, ge=0.0)


class BrandKitCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    logo_path: str = ""
    logo_position: AllowedLogoPosition = "top_right"
    logo_opacity: float = Field(default=0.85, ge=0.0, le=1.0)
    logo_scale: float = Field(default=0.14, ge=0.05, le=0.40)
    subtitle_preset: AllowedSubtitlePreset = "hormozi"
    progress_bar_enabled: bool = True
    progress_bar_color: str = Field(default="#6366F1", pattern=r"^#[0-9A-Fa-f]{6}$")
    hook_banner_enabled: bool = False
    hook_banner_duration: float = Field(default=3.0, ge=0.5, le=10.0)


class SeoKitRequest(BaseModel):
    provider: Literal["gemini", "groq"] = "gemini"


class BrollSuggestRequest(BaseModel):
    query: Optional[str] = None
    transcript_excerpt: Optional[str] = None
    max_results: int = Field(default=6, ge=1, le=12)
    orientation: Literal["portrait", "landscape", "square"] = "portrait"


class HighlightReelRequest(BaseModel):
    corte_ids: List[int] = Field(..., min_length=2, max_length=20)
    transition: AllowedTransition = "fade"
    transition_duration: float = Field(default=0.5, ge=0.2, le=2.0)
    title: str = Field(default="Highlight Reel", min_length=1, max_length=120)
    use_gpu: bool = False


class PublishScheduleRequest(BaseModel):
    corte_id: int = Field(..., gt=0)
    platform: AllowedPlatform
    title: str = Field(..., min_length=1, max_length=150)
    caption: str = Field(default="", max_length=2200)
    hashtags: str = Field(default="", max_length=500)
    scheduled_at: str = Field(..., min_length=8, max_length=40)
    publish_now: bool = False


class JobAcceptedResponse(BaseModel):
    job_id: str
    message: str


class ConfigResponse(BaseModel):
    ready: bool
    keys: Dict[str, bool]
    default_provider: Literal["gemini", "groq"]
    status_details: Dict[str, Any]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    db_ok: bool
    ffmpeg_available: bool
    disk_free_mb: float
    active_jobs: Dict[str, int]
    gpu_encoder: Optional[str] = None


class FrameResponse(BaseModel):
    success: bool
    frame_url: str
    timestamp_sec: float


class PreviewClipResponse(BaseModel):
    success: bool
    clip_url: str
    start_time: float
    end_time: float


class CleanupResponse(BaseModel):
    success: bool
    deleted_count: int
    freed_mb: float
    pruned_jobs: int
    pruned_cache: int


class MessageResponse(BaseModel):
    success: bool
    message: str


class CorteMutationResponse(BaseModel):
    success: bool
    corte: Dict[str, Any]


class WorkspaceResponse(BaseModel):
    video_id: int
    url: str
    title: str
    duration: float
    thumbnail: str = ""
    uploader: str = ""
    is_local: bool = False
    cortes: List[Dict[str, Any]]
    transcript_segments: List[Dict[str, Any]]
    transcript_words: List[Dict[str, Any]] = Field(default_factory=list)
