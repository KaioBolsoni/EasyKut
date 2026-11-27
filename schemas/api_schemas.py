"""
Contratos Pydantic de requisição e resposta da API REST do EasyKut Studio (V3.2).
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

AllowedLayoutMode = Literal[
    "horizontal_16_9",
    "vertical_blur",
    "manual_crop",
    "split_screen",
    "face_tracking_9_16",
]


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


class CleanupRequest(BaseModel):
    remove_raw_downloads: bool = False
    max_age_hours: Optional[float] = Field(default=None, ge=0.0)


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
