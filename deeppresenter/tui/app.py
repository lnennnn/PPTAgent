from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import subprocess
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from PIL import Image as PILImage
from rich.align import Align
from rich.console import Group, RenderableType
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import DirectoryTree, Footer, Input, RichLog, Static, TextArea

from .controller import SessionController
from .events import StreamEvent


class PathPickerScreen(ModalScreen[Path | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, workspace: Path):
        super().__init__()
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        yield Container(
            Vertical(
                Static("Attach file or directory", id="picker-title"),
                Input(
                    placeholder="Relative path in workspace or absolute local path",
                    id="picker-input",
                ),
                DirectoryTree(str(self.workspace), id="picker-tree"),
                Static(
                    "Browse the current workspace or paste an absolute path and press Enter.",
                    id="picker-help",
                ),
            ),
            id="picker-dialog",
        )

    def on_mount(self) -> None:
        tree = self.query_one("#picker-tree", DirectoryTree)
        tree.root.expand()
        self.query_one("#picker-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (self.workspace / path).resolve()
        if path.exists():
            self.dismiss(path)
        else:
            self.notify(f"Path not found: {path}", severity="error")

    def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected
    ) -> None:
        self.dismiss(event.path)

    def on_directory_tree_directory_selected(
        self, event: DirectoryTree.DirectorySelected
    ) -> None:
        self.dismiss(event.path)


class Composer(TextArea):
    async def _on_key(self, event: Key) -> None:
        key = event.key
        aliases = set(getattr(event, "aliases", []) or [])
        modified_enter = key in {"ctrl+enter", "ctrl+m", "meta+enter", "cmd+enter"} or (
            key == "enter"
            and any(
                alias in {"ctrl+enter", "meta+enter", "cmd+enter"} for alias in aliases
            )
        )

        if key == "enter" and not modified_enter:
            event.stop()
            event.prevent_default()
            await self.app.action_send()
            return
        if modified_enter:
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)

    def clear_and_focus(self) -> None:
        self.text = ""
        self.focus()


class DeepPresenterTUI(App[None]):
    TITLE = "DeepPresenter"
    SUB_TITLE = "Agent Workspace"
    BINDINGS = [
        Binding("@", "attach", "Attach", show=False),
        Binding("backspace", "back", "Back", show=False),
        Binding("h", "back", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("/", "search_files", "Search", show=False),
        Binding("o", "open_external", "Open", show=False),
    ]
    CSS = """
    Screen {
        layout: vertical;
    }

    #status-bar {
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
    }

    #body {
        height: 1fr;
    }

    #stream-pane {
        width: 2fr;
        margin-right: 1;
        padding: 0 1;
    }

    #right-pane {
        width: 1fr;
        padding: 0 1;
        border-left: solid $panel;
    }

    .pane-title {
        height: 1;
        padding: 0 1;
        background: $surface;
        color: $text;
    }

    #files-view, #preview-view {
        height: 1fr;
    }

    #preview-view {
        display: none;
        height: 1fr;
    }

    #preview-breadcrumb {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        text-align: right;
    }

    #preview-content {
        height: 1fr;
        padding: 0;
    }

    #composer-pane {
        height: 8;
        border-top: solid $panel;
        padding: 0 1;
    }

    #attachment-bar {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }

    #composer {
        height: 1fr;
    }

    #picker-dialog {
        width: 80%;
        height: 80%;
        align: center middle;
        background: $surface;
        border: round $accent;
        padding: 1;
    }

    #picker-title {
        height: 1;
        content-align: center middle;
        text-style: bold;
    }

    #picker-tree {
        height: 1fr;
        margin: 1 0;
        border: solid $panel;
    }

    #picker-help {
        height: auto;
        color: $text-muted;
    }
    """

    def __init__(self, controller: SessionController):
        super().__init__()
        self.controller = controller
        self._run_task: asyncio.Task | None = None
        self._search_mode = False
        self._stream_entries: list[RenderableType] = []
        self._tool_entry_index: dict[str, int] = {}

    def compose(self) -> ComposeResult:
        yield Static(id="status-bar")
        yield Horizontal(
            Vertical(
                RichLog(id="stream", wrap=True, markup=False, highlight=True),
                id="stream-pane",
            ),
            Vertical(
                Static("Files", classes="pane-title", id="right-title"),
                DirectoryTree(str(self.controller.workspace), id="files-view"),
                Container(
                    Static("Files", classes="pane-title", id="preview-title"),
                    Static(id="preview-breadcrumb"),
                    RichLog(id="preview-content", wrap=True, markup=False),
                    id="preview-view",
                ),
                id="right-pane",
            ),
            id="body",
        )
        yield Vertical(
            Static("Attachments: none", id="attachment-bar"),
            Composer(id="composer", soft_wrap=True),
            id="composer-pane",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#files-view", DirectoryTree).root.expand()
        self.query_one("#composer", Composer).focus()
        self._refresh_status()
        self.set_interval(1.0, self._refresh_status)
        self.run_worker(self._warmup_env(), exclusive=True)

    async def _warmup_env(self) -> None:
        try:
            await self.controller.warmup()
        finally:
            self._refresh_status()

    def _refresh_status(self) -> None:
        state = self.controller.state
        left = (
            f"DeepPresenter  session={state.session_id}  cwd={state.workspace}  "
            f"mode={state.mode}"
        )
        right_prefix = (
            f"model={state.model}  phase={state.phase}  "
            f"elapsed={int(state.elapsed_seconds)}s  tokens={state.token_summary}"
        )
        width = max(1, self.size.width - len(right_prefix) - 2)
        padded_left = left[:width].ljust(width)
        self.query_one("#status-bar", Static).update(padded_left + right_prefix)
        self._sync_attachments()

    def _sync_attachments(self) -> None:
        attachments = self.controller.composer.attachments
        if attachments:
            relative = [self._short_path(path) for path in attachments]
            text = "Attachments: " + ", ".join(relative[:4])
            if len(relative) > 4:
                text += " ..."
        else:
            text = "Attachments: none"
        self.query_one("#attachment-bar", Static).update(text)

    def _short_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.controller.workspace))
        except ValueError:
            return str(path)

    def _write_stream_line(self, title: str, body: str) -> None:
        label = Text(title, style="bold cyan" if title == "User" else "bold green")
        content = Text(body or " ", overflow="fold")
        if title == "User":
            width = max(20, self.query_one("#stream-pane").size.width - 4)
            label_plain = label.plain
            content_plain = content.plain
            renderable = Group(
                Text(
                    " " * max(0, width - len(label_plain)) + label_plain,
                    style="bold cyan",
                ),
                Text(" " * max(0, width - len(content_plain)) + content_plain),
            )
        else:
            renderable = Group(label, content)
        self._stream_entries.append(renderable)
        self._render_stream()

    def _render_stream(self) -> None:
        stream = self.query_one("#stream", RichLog)
        stream.clear()
        for entry in self._stream_entries:
            stream.write(entry)

    async def _handle_event(self, event: StreamEvent) -> None:
        if event.kind in {"assistant_reasoning", "system_notice", "phase_change"}:
            return
        if event.kind == "tool_call":
            tool_name = str(
                event.meta.get("tool_name") or event.title.replace("Tool: ", "", 1)
            )
            tool_call_id = str(event.meta.get("tool_call_id") or tool_name)
            self._stream_entries.append(
                Text.assemble(("●", "green blink"), f" {tool_name} running")
            )
            self._tool_entry_index[tool_call_id] = len(self._stream_entries) - 1
            self._render_stream()
            return
        if event.kind == "tool_result":
            tool_name = str(event.meta.get("tool_name") or "tool")
            tool_call_id = str(event.meta.get("tool_call_id") or "")
            if tool_call_id and tool_call_id in self._tool_entry_index:
                self._stream_entries[self._tool_entry_index[tool_call_id]] = (
                    Text.assemble(("●", "green"), f" {tool_name} succeeded")
                )
                self._tool_entry_index.pop(tool_call_id, None)
            else:
                self._stream_entries.append(
                    Text.assemble(("●", "green"), f" {tool_name} succeeded")
                )
            self._render_stream()
            return
        if event.kind == "tool_error":
            tool_name = str(event.meta.get("tool_name") or "tool")
            tool_call_id = str(event.meta.get("tool_call_id") or "")
            if tool_call_id and tool_call_id in self._tool_entry_index:
                self._stream_entries[self._tool_entry_index[tool_call_id]] = (
                    Text.assemble(("●", "red"), f" {tool_name} failed")
                )
                self._tool_entry_index.pop(tool_call_id, None)
            else:
                self._stream_entries.append(
                    Text.assemble(("●", "red"), f" {tool_name} failed")
                )
            self._render_stream()
            return
        self._write_stream_line(event.title, event.body or event.title)
        if event.path:
            await self._reload_files()

    async def _reload_files(self) -> None:
        tree = self.query_one("#files-view", DirectoryTree)
        await tree.reload()

    async def action_send(self) -> None:
        if self.focused is not self.query_one("#composer", Composer):
            return
        composer = self.query_one("#composer", Composer)
        instruction = composer.text.strip()
        attachments = list(self.controller.composer.attachments)
        if self.controller.running:
            self.notify("A run is already in progress.", severity="warning")
            return
        if not instruction and not attachments:
            return

        message = instruction or "(attachment-only request)"
        if attachments:
            message += "\n\nAttachments:\n" + "\n".join(
                f"- {self._short_path(path)}" for path in attachments
            )
        self._write_stream_line("User", message)

        composer.clear_and_focus()
        self.controller.composer.attachments = []
        self._tool_entry_index = {}
        self._render_stream()
        self._sync_attachments()

        async def runner() -> None:
            await self.controller.run_turn(
                instruction=instruction or "Process the attached files.",
                attachments=attachments,
                on_event=self._handle_event,
            )
            await self._reload_files()

        self._run_task = asyncio.create_task(runner())

    async def action_attach(self) -> None:
        if self.focused is not self.query_one("#composer", Composer):
            return
        path = await self.push_screen_wait(PathPickerScreen(self.controller.workspace))
        if path is None:
            return
        normalized = self.controller.normalize_picker_path(path)
        if not normalized.exists():
            self.notify(f"Path not found: {normalized}", severity="error")
            return
        imported = self.controller.import_path(normalized)
        if imported not in self.controller.composer.attachments:
            self.controller.composer.attachments.append(imported)
        composer = self.query_one("#composer", Composer)
        composer.insert(f" @{self._short_path(imported)}")
        self._sync_attachments()
        composer.focus()

    def action_back(self) -> None:
        preview = self.query_one("#preview-view", Container)
        files = self.query_one("#files-view", DirectoryTree)
        if preview.display:
            preview.display = False
            self.query_one("#right-title", Static).display = True
            files.display = True
            files.focus()
            self.controller.preview.visible = False
            self.controller.preview.path = None

    async def on_unmount(self) -> None:
        await self.controller.close()

    def action_search_files(self) -> None:
        tree = self.query_one("#files-view", DirectoryTree)
        tree.focus()
        self.notify("Use the picker with @ for direct path selection.", timeout=2)

    def action_open_external(self) -> None:
        path = self.controller.preview.path
        if path is None:
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])

    async def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected
    ) -> None:
        await self._show_preview(event.path)

    def on_directory_tree_directory_selected(
        self, event: DirectoryTree.DirectorySelected
    ) -> None:
        event.node.toggle()

    async def _show_preview(self, path: Path) -> None:
        preview_kind, renderable, footer = self._render_preview(path)
        files = self.query_one("#files-view", DirectoryTree)
        files.display = False
        self.query_one("#right-title", Static).display = False
        preview = self.query_one("#preview-view", Container)
        preview.display = True
        self.query_one("#preview-title", Static).update(f"Preview | {preview_kind}")
        self.query_one("#preview-breadcrumb", Static).update(
            f"{self._short_path(path)}    [backspace/h: back]"
        )

        content = self.query_one("#preview-content", RichLog)
        content.clear()
        content.write(renderable)
        if footer:
            content.write(Panel.fit(footer, title="Preview Actions"))

        self.controller.preview.visible = True
        self.controller.preview.path = path
        self.controller.preview.kind = preview_kind
        self.controller.preview.breadcrumb = self._short_path(path)
        self.controller.preview.footer = footer

    def _render_preview(self, path: Path) -> tuple[str, object, str]:
        suffix = path.suffix.lower()
        mime, _ = mimetypes.guess_type(path.name)
        footer = "Press backspace or h to return. Press o to open externally."

        if suffix in {".html", ".htm"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            soup = BeautifulSoup(text, "html.parser")
            title = (soup.title.string or "").strip() if soup.title else path.name
            body_text = soup.get_text("\n", strip=True)
            excerpt = (
                body_text[:3000] if body_text else "(No textual content extracted)"
            )
            renderable = Panel(
                Text(excerpt, overflow="fold"),
                title=f"HTML Preview | {title}",
                border_style="green",
            )
            return "html", renderable, footer

        if mime and mime.startswith("image/"):
            with PILImage.open(path) as image:
                info = {
                    "format": image.format,
                    "size": f"{image.width}x{image.height}",
                    "mode": image.mode,
                }
            renderable = Panel.fit(
                json.dumps(info, ensure_ascii=False, indent=2),
                title=f"Image Preview | {path.name}",
                border_style="magenta",
            )
            return "image", renderable, footer

        if suffix in {
            ".md",
            ".txt",
            ".log",
            ".json",
            ".yaml",
            ".yml",
            ".py",
            ".ts",
            ".js",
            ".css",
            ".sh",
        }:
            text = path.read_text(encoding="utf-8", errors="ignore")
            syntax = Syntax(
                text[:5000],
                lexer=(suffix.lstrip(".") or "text"),
                word_wrap=True,
                line_numbers=True,
            )
            return "text", syntax, footer

        stats = path.stat()
        lines = [
            f"name: {path.name}",
            f"path: {path}",
            f"size: {stats.st_size} bytes",
            f"modified: {stats.st_mtime}",
            f"mime: {mime or 'unknown'}",
        ]
        renderable = Panel.fit(
            "\n".join(lines), title="File Details", border_style="blue"
        )
        return "file", renderable, footer
