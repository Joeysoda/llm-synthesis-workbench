from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)


class RunCreate(BaseModel):
    project_id: str
    tool_name: str
    input: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False


class SamplePatch(BaseModel):
    question: str | None = None
    answer: str | None = None
    reasoning: str | None = None
    quality: dict[str, Any] | None = None


class ExportRequest(BaseModel):
    format: str = Field(
        default="jsonl",
        pattern="^(jsonl|json|alpaca|openai-ft|ft|chatml|huggingface|csv)$",
    )


ToolId = Literal[
    "synthetic-data-kit", "easy-dataset", "synlogic", "kaqg", "cleanlab", "legacy"
]


class ToolProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)


class ToolProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    archived: bool | None = None


class SyntheticJobCreate(BaseModel):
    workflow_type: Literal[
        "qa", "cot", "summary", "cot-enhance", "multimodal-qa"
    ] = "cot"
    asset_ids: list[str] = Field(min_length=1, max_length=50)
    num_pairs: int = Field(default=30, ge=1, le=500)
    min_retained: int = Field(default=1, ge=1, le=500)
    chunk_size: int = Field(default=4000, ge=500, le=20000)
    chunk_overlap: int = Field(default=200, ge=0, le=2000)
    threshold: float = Field(default=7.0, ge=1, le=10)
    model: str | None = None
    confirmed: bool = False


class EasyDatasetJobCreate(BaseModel):
    workflow_type: Literal[
        "document-qa", "distillation", "image-qa", "evaluation"
    ] = "document-qa"
    asset_ids: list[str] = Field(default_factory=list, max_length=100)
    target_count: int = Field(default=20, ge=1, le=500)
    language: str = Field(default="中文", max_length=30)
    model: str | None = None
    conversation_mode: Literal["single", "multi", "both"] = "single"
    ga_expansion: bool = False
    split_mode: str = Field(default="smart", max_length=50)
    topic: str = Field(default="", max_length=500)
    tag_depth: int = Field(default=2, ge=1, le=5)
    tags_per_level: int = Field(default=3, ge=1, le=20)
    questions_per_tag: int = Field(default=3, ge=1, le=50)
    question_types: list[
        Literal["true-false", "single-choice", "multiple-choice", "short-answer", "open"]
    ] = Field(default_factory=lambda: ["single-choice", "short-answer"])
    questions_per_type: int = Field(default=3, ge=1, le=30)
    questions_per_image: int = Field(default=3, ge=1, le=30)
    vision_model: str | None = None
    confirmed: bool = False


class EasyDatasetPreviewRequest(BaseModel):
    asset_id: str


class SynLogicJobCreate(BaseModel):
    workflow_type: Literal["arrow-maze"] = "arrow-maze"
    num_of_data: int = Field(default=20, ge=1, le=1000)
    width: int = Field(default=5, ge=4, le=12)
    height: int = Field(default=5, ge=4, le=12)
    arrow_fill_rate_min: float = Field(default=0.3, ge=0, le=1)
    arrow_fill_rate_max: float = Field(default=0.9, ge=0, le=1)
    max_attempts: int = Field(default=10000, ge=100, le=100000)
    confirmed: bool = False


class SynLogicVerifyRequest(BaseModel):
    sample_id: str
    answer: list[list[str]]


class DifficultyCounts(BaseModel):
    easy: int = Field(default=3, ge=0, le=10)
    medium: int = Field(default=3, ge=0, le=10)
    hard: int = Field(default=3, ge=0, le=10)


class KaqgJobCreate(BaseModel):
    asset_id: str
    subject_name: str = Field(min_length=1, max_length=120)
    difficulty_counts: DifficultyCounts = Field(default_factory=DifficultyCounts)
    confirmed: bool = False


class CleanlabJobCreate(BaseModel):
    source_type: Literal["asset", "job"] = "asset"
    asset_id: str | None = None
    source_job_id: str | None = None
    pred_probs_asset_id: str | None = None
    id_column: str = Field(default="id", max_length=120)
    text_column: str = Field(default="text", max_length=120)
    label_column: str = Field(default="label", max_length=120)
    confirmed: bool = False


class CleanlabDecisionRequest(BaseModel):
    decision: Literal["accept_suggestion", "keep_original", "manual"]
    corrected_label: str | None = Field(default=None, max_length=120)


class ProbeRequest(BaseModel):
    model: str | None = None
