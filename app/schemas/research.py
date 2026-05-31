"""Pydantic schemas for the Research API."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


# ── Request ──────────────────────────────────────────────────────────

class ResearchRequest(BaseModel):
    """POST /api/v1/research request body."""
    query: str = Field(..., min_length=5, max_length=2000, description="研究问题")
    domain: str = Field(default="geo_rs", description="领域: geo_rs | general | finance | medical")
    depth: int = Field(default=2, ge=1, le=5, description="研究深度 (1-5)")
    breadth: int = Field(default=4, ge=1, le=10, description="研究广度 (1-10)")
    config_path: str | None = Field(default=None, description="自定义配置文件路径")


# ── Response ─────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ResearchResponse(BaseModel):
    """POST /api/v1/research response (202 Accepted)."""
    task_id: str
    status: TaskStatus
    message: str = "Research task submitted."


class TaskProgress(BaseModel):
    """Real-time progress update."""
    phase: str = ""
    completed_subtasks: int = 0
    total_subtasks: int = 0
    current_task: str = ""
    evidence_collected: int = 0


class TaskDetail(BaseModel):
    """GET /api/v1/tasks/{id} response."""
    task_id: str
    status: TaskStatus
    query: str
    domain: str
    progress: TaskProgress = Field(default_factory=TaskProgress)
    report_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    error: str | None = None


class EvidenceSummary(BaseModel):
    verified: int = 0
    evidence_backed: int = 0
    speculative: int = 0
    rejected: int = 0


class ReportMeta(BaseModel):
    model_used: str = ""
    total_tokens: int = 0
    execution_time_seconds: float = 0.0
    search_rounds: int = 0
    replan_count: int = 0
    confidence: float = 0.0


class CitationItem(BaseModel):
    index: int
    url: str = ""
    title: str = ""
    evidence_level: str = ""
    source_tier: str = ""


class ReportDetail(BaseModel):
    """GET /api/v1/reports/{id} response."""
    task_id: str
    title: str
    content_markdown: str
    content_html: str = ""
    evidence_summary: EvidenceSummary = Field(default_factory=EvidenceSummary)
    citations: list[CitationItem] = Field(default_factory=list)
    metadata: ReportMeta = Field(default_factory=ReportMeta)


# ── Auth ─────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=6)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(default="default", max_length=100)


class ApiKeyResponse(BaseModel):
    key: str
    name: str
    created_at: datetime


# ── Health ───────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    components: dict[str, str] = Field(default_factory=dict)
