"""
Фиксированные схемы local connector (раздел 11, 14 требований).

"Никаких arbitrary shell commands. Job schema фиксированная." - ConnectorJob
ниже - это ВСЁ, что local_connector может получить от Render; никаких полей
вроде "command"/"script"/"code" не существует, поэтому connector физически
не может быть использован для удалённого выполнения произвольных команд.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

ConnectorPlatform = Literal["instagram", "tiktok"]
ConnectorHeartbeatStatus = Literal["online", "manual_intervention_required"]
ConnectorResultStatus = Literal["ok", "manual_intervention_required", "error"]


class ConnectorRegisterRequest(BaseModel):
    supported_platforms: list[ConnectorPlatform] = Field(default_factory=list)
    # Опциональный shared secret - см. config.settings.connector_shared_secret.
    # Если сервер его требует, а запрос не совпал - регистрация отклоняется.
    shared_secret: Optional[str] = None

    def has_at_least_one_platform(self) -> bool:
        return bool(self.supported_platforms)


class ConnectorRegisterResponse(BaseModel):
    connector_id: str
    connector_token: str
    supported_platforms: list[ConnectorPlatform]


class ConnectorHeartbeatRequest(BaseModel):
    connector_id: str
    connector_token: str
    status: ConnectorHeartbeatStatus = "online"
    detail: Optional[str] = None


class ConnectorHeartbeatResponse(BaseModel):
    ok: bool
    server_time: str


class ConnectorJob(BaseModel):
    """ФИКСИРОВАННАЯ схема job - раздел 14. Только данные, никаких команд."""

    job_id: str
    analysis_id: str
    platform: ConnectorPlatform
    brand: str
    aliases: list[str] = Field(default_factory=list)
    settings: dict = Field(default_factory=dict)
    created_at: datetime


class ConnectorJobsResponse(BaseModel):
    jobs: list[ConnectorJob] = Field(default_factory=list)


class ConnectorResultItem(BaseModel):
    """Один найденный публичный профиль/пост (раздел 15-16 требований) - только
    поля, реально видимые публично. Недоступное поле = null, не придумывается."""

    username: Optional[str] = None
    profile_url: Optional[str] = None
    followers: Optional[int] = None
    post_url: Optional[str] = None
    caption: Optional[str] = None
    published_at: Optional[str] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    views: Optional[int] = None
    hashtags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    brand_mention: bool = False
    paid_partnership_label: bool = False
    collaboration_label: bool = False
    # base64 PNG, опционально - используется VisualEvidenceEnricher, если
    # detector признал случай ambiguous (раздел 17 - social visual analysis).
    screenshot_base64: Optional[str] = None


class ConnectorResultsSubmission(BaseModel):
    connector_id: str
    connector_token: str
    job_id: str
    status: ConnectorResultStatus = "ok"
    detail: Optional[str] = None
    items: list[ConnectorResultItem] = Field(default_factory=list)


class ConnectorResultsAck(BaseModel):
    ok: bool
