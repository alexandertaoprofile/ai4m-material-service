from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PipelineTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class PipelineRunRequest(BaseModel):
    prompt: str = Field(..., description="用户自然语言输入")
    user_id: Optional[str] = Field(default=None)
    task_id: Optional[str] = Field(default=None)


class PipelineRunAccepted(BaseModel):
    accepted: bool = True
    task_id: str
    message: str = "task accepted"


class FormulaExtractionResult(BaseModel):
    final_formulas: List[str] = Field(default_factory=list)
    raw_candidates: List[str] = Field(default_factory=list)
    web_snippets: List[str] = Field(default_factory=list)
    llm_suggested_formulas: List[str] = Field(default_factory=list)
    reject_reasons: List[str] = Field(default_factory=list)
    source_trace: Dict[str, Any] = Field(default_factory=dict)


class StageResult(BaseModel):
    stage: str
    formula: str
    ok: bool
    return_code: Optional[int] = None
    command: Optional[List[str]] = None
    stdout_tail: str = ""
    manifest_path: Optional[str] = None
    report_path: Optional[str] = None
    summary_path: Optional[str] = None
    error: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class FormulaPipelineResult(BaseModel):
    formula: str
    mp: Optional[StageResult] = None
    adit: Optional[StageResult] = None
    mace_fast: Optional[StageResult] = None
    mace_md: Optional[StageResult] = None


class PipelineResultResponse(BaseModel):
    task_id: str
    status: PipelineTaskStatus
    prompt: str
    extracted_formulas: FormulaExtractionResult
    results: List[FormulaPipelineResult] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class PipelineStatusResponse(BaseModel):
    task_id: str
    status: PipelineTaskStatus
