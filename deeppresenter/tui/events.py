from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from deeppresenter.utils.typings import ChatMessage, Role

EventKind = Literal[
    "user_message",
    "assistant_message",
    "assistant_reasoning",
    "tool_call",
    "tool_result",
    "tool_error",
    "phase_change",
    "artifact_ready",
    "system_notice",
]


class StreamEvent(BaseModel):
    kind: EventKind
    title: str
    body: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)
    path: Path | None = None
    compact: bool = False


def _format_tool_args(arguments: str | None) -> str:
    if not arguments:
        return ""
    try:
        parsed = json.loads(arguments)
    except Exception:
        return arguments
    if isinstance(parsed, dict):
        payload = json.dumps(parsed, ensure_ascii=False, indent=2)
    else:
        payload = str(parsed)
    return payload


def _content_text(msg: ChatMessage) -> str:
    parts: list[str] = []
    if isinstance(msg.content, list):
        for block in msg.content:
            if block.get("type") == "text":
                text = str(block.get("text", "")).strip()
                if text:
                    parts.append(text)
    elif isinstance(msg.content, str):
        text = msg.content.strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def adapt_runtime_item(item: ChatMessage | str | Path) -> list[StreamEvent]:
    if isinstance(item, (str, Path)):
        path = Path(item)
        return [
            StreamEvent(
                kind="artifact_ready",
                title="Artifact ready",
                body=str(path),
                path=path,
            )
        ]

    msg = item
    event_name = msg.extra_info.get("event")
    if event_name == "phase_change":
        phase = str(msg.extra_info.get("phase", "running"))
        return [
            StreamEvent(
                kind="phase_change",
                title=f"Phase: {phase}",
                body=msg.text,
                meta={"phase": phase},
            )
        ]

    events: list[StreamEvent] = []
    text = _content_text(msg)
    if msg.role == Role.SYSTEM:
        events.append(StreamEvent(kind="system_notice", title="System", body=text))
        return events

    if msg.role == Role.USER:
        events.append(StreamEvent(kind="user_message", title="User", body=text))
        return events

    if msg.role == Role.ASSISTANT:
        if text:
            events.append(
                StreamEvent(
                    kind="assistant_message",
                    title="Assistant",
                    body=text,
                    meta={
                        "usage_total": msg.cost.total_tokens if msg.cost else None,
                    },
                )
            )
        if msg.reasoning:
            events.append(
                StreamEvent(
                    kind="assistant_reasoning",
                    title="Reasoning",
                    body=msg.reasoning.strip(),
                )
            )
        for tool_call in msg.tool_calls or []:
            events.append(
                StreamEvent(
                    kind="tool_call",
                    title=f"Tool: {tool_call.function.name}",
                    body=_format_tool_args(tool_call.function.arguments),
                    meta={
                        "tool_call_id": tool_call.id,
                        "tool_name": tool_call.function.name,
                    },
                    compact=True,
                )
            )
        return events

    if msg.role == Role.TOOL:
        title = "Tool error" if msg.is_error else "Tool result"
        kind: EventKind = "tool_error" if msg.is_error else "tool_result"
        events.append(
            StreamEvent(
                kind=kind,
                title=title,
                body=text,
                meta={
                    "tool_call_id": msg.tool_call_id,
                    "tool_name": msg.from_tool.name if msg.from_tool else None,
                    "usage_total": msg.cost.total_tokens if msg.cost else None,
                },
                compact=True,
            )
        )
        return events

    return [StreamEvent(kind="system_notice", title="Runtime", body=text)]
