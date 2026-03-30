from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

SessionMode = Literal["idle", "running", "awaiting_input", "error"]
PreviewKind = Literal["empty", "text", "html", "image", "file"]


class SessionState(BaseModel):
    session_id: str
    workspace: Path
    language: str = "en"
    mode: SessionMode = "awaiting_input"
    phase: str = "idle"
    model: str = ""
    elapsed_seconds: float = 0.0
    token_summary: str = "-"
    tool_summary: str = "-"
    last_error: str = ""


class PreviewState(BaseModel):
    visible: bool = False
    path: Path | None = None
    kind: PreviewKind = "empty"
    title: str = "Files"
    breadcrumb: str = ""
    body: str = "Select a file to preview."
    footer: str = ""


class ComposerState(BaseModel):
    attachments: list[Path] = Field(default_factory=list)
