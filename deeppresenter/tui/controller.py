from __future__ import annotations

import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel, Field

from deeppresenter.agents.env import AgentEnv
from deeppresenter.main import AgentLoop, InputRequest
from deeppresenter.utils.config import DeepPresenterConfig
from deeppresenter.utils.constants import WORKSPACE_BASE
from deeppresenter.utils.typings import ChatMessage, ConvertType

from .events import StreamEvent, adapt_runtime_item
from .state import ComposerState, PreviewState, SessionState

EventCallback = Callable[[StreamEvent], Awaitable[None]]


class TurnSummary(BaseModel):
    instruction: str
    attachments: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)


class SessionController:
    def __init__(
        self,
        config: DeepPresenterConfig,
        language: str = "en",
        session_id: str | None = None,
        workspace: Path | None = None,
    ):
        self.config = config
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.workspace = (workspace or WORKSPACE_BASE / self.session_id).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.state = SessionState(
            session_id=self.session_id,
            workspace=self.workspace,
            language=language,
            model=config.research_agent.model_name,
        )
        self.preview = PreviewState()
        self.composer = ComposerState()
        self.turns: list[TurnSummary] = []
        self.running = False
        self._turn_started_at = 0.0
        self.agent_env: AgentEnv | None = None
        self._warmup_started = False

    def import_path(self, path: Path) -> Path:
        source = path.expanduser().resolve()
        if source.is_relative_to(self.workspace):
            return source

        attachments_dir = self.workspace / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)

        destination = attachments_dir / source.name
        if destination.exists():
            stem = source.stem
            suffix = source.suffix
            index = 1
            while destination.exists():
                destination = attachments_dir / f"{stem}-{index}{suffix}"
                index += 1

        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
        return destination.resolve()

    def normalize_picker_path(self, raw_path: Path) -> Path:
        candidate = raw_path.expanduser()
        if not candidate.is_absolute():
            candidate = (self.workspace / candidate).resolve()
        else:
            candidate = candidate.resolve()
        return candidate

    def build_turn_instruction(self, instruction: str) -> str:
        if not self.turns:
            return instruction

        lines = ["Session continuation context:"]
        for index, turn in enumerate(self.turns[-3:], start=1):
            lines.append(f"{index}. Previous request: {turn.instruction}")
            if turn.attachments:
                lines.append(
                    f"   Attachments: {', '.join(turn.attachments[:4])}"
                    + (" ..." if len(turn.attachments) > 4 else "")
                )
            if turn.artifacts:
                lines.append(
                    f"   Artifacts: {', '.join(turn.artifacts[:4])}"
                    + (" ..." if len(turn.artifacts) > 4 else "")
                )
        lines.append("Current request:")
        lines.append(instruction)
        return "\n".join(lines)

    def update_runtime_counters(self, event: StreamEvent) -> None:
        self.state.elapsed_seconds = max(0.0, time.time() - self._turn_started_at)
        usage_total = event.meta.get("usage_total")
        if isinstance(usage_total, int):
            self.state.token_summary = f"{usage_total / 1000:.1f}K"
        if event.kind == "phase_change":
            self.state.phase = str(event.meta.get("phase", "running"))
        if event.kind == "tool_error":
            self.state.last_error = event.body
            self.state.mode = "error"

    async def warmup(self) -> None:
        if self.agent_env is not None or self._warmup_started:
            return
        self._warmup_started = True
        self.state.mode = "running"
        self.state.phase = "initializing"
        self.agent_env = await AgentEnv(self.workspace, self.config).__aenter__()
        self.state.mode = "awaiting_input"
        self.state.phase = "idle"

    async def close(self) -> None:
        if self.agent_env is not None:
            await self.agent_env.__aexit__(None, None, None)
            self.agent_env = None

    async def run_turn(
        self,
        instruction: str,
        attachments: list[Path],
        on_event: EventCallback,
    ) -> Path | None:
        if self.running:
            raise RuntimeError("A generation turn is already running")

        self.running = True
        self.state.mode = "running"
        self.state.phase = "research"
        self.state.last_error = ""
        self.state.tool_summary = "-"
        self._turn_started_at = time.time()

        imported = [self.import_path(path) for path in attachments]
        request = InputRequest(
            instruction=self.build_turn_instruction(instruction),
            attachments=[str(path) for path in imported],
            convert_type=ConvertType.DEEPPRESENTER,
        )

        current_turn = TurnSummary(
            instruction=instruction,
            attachments=[str(path.relative_to(self.workspace)) for path in imported],
        )

        loop = AgentLoop(
            config=self.config,
            session_id=self.session_id,
            workspace=self.workspace,
            language=self.state.language,
        )

        final_path: Path | None = None
        try:
            async for item in loop.run(request, agent_env=self.agent_env):
                for event in adapt_runtime_item(item):
                    self.update_runtime_counters(event)
                    if event.path is not None:
                        current_turn.artifacts.append(str(event.path))
                        final_path = event.path
                    await on_event(event)
            self.state.mode = "awaiting_input"
            self.state.phase = "idle"
            self.turns.append(current_turn)
            return final_path
        except Exception as exc:
            self.state.mode = "error"
            self.state.last_error = str(exc)
            await on_event(
                StreamEvent(
                    kind="system_notice",
                    title="Runtime error",
                    body=str(exc),
                )
            )
            raise
        finally:
            self.running = False
            self.state.elapsed_seconds = max(0.0, time.time() - self._turn_started_at)
