"""Interactive CLI editor for the slide outline."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from deeppresenter.utils.outline import Outline

console = Console()


def render_outline(outline: Outline) -> None:
    table = Table(
        title="Current Outline",
        show_header=True,
        header_style="bold cyan",
        show_lines=True,
    )
    table.add_column("#", style="bold", width=4)
    table.add_column("Title", style="bold green", min_width=20)
    table.add_column("Context", min_width=40)
    for slide in outline.slides:
        table.add_row(str(slide.index), slide.title, slide.context)
    console.print(table)


def _menu() -> None:
    console.print(
        "\n[bold yellow]Outline Actions:[/bold yellow]\n"
        "  [cyan]e[/cyan] <N>        — Edit slide N\n"
        "  [cyan]d[/cyan] <N>        — Delete slide N\n"
        "  [cyan]a[/cyan] <N>        — Add new slide after N (0 = prepend)\n"
        "  [cyan]s[/cyan] <N> <M>    — Swap slides N and M\n"
        "  [cyan]m[/cyan] <text>     — Let AI modify the outline (natural language)\n"
        "  [cyan]ok[/cyan]           — Approve outline and continue\n"
    )


async def interactive_edit_outline(
    outline: Outline,
    ai_modify: Callable[[Outline, str], Awaitable[Outline]],
) -> Outline:
    """
    Async interactive CLI editor.

    Parameters
    ----------
    outline:
        The initial outline produced by the Planner.
    ai_modify:
        Async callable: (outline, instruction) -> Outline.
        Called when the user types the 'm' command.
    """
    render_outline(outline)
    _menu()

    while True:
        try:
            raw = Prompt.ask("[bold]> Command[/bold]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Keeping outline as-is.[/yellow]")
            break

        if not raw:
            continue

        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        # ── Approve ──────────────────────────────────────────────
        if cmd in ("ok", "approve", "done", "q"):
            console.print("[bold green]✓ Outline approved.[/bold green]")
            break

        # ── Edit ─────────────────────────────────────────────────
        elif cmd == "e":
            if not rest.isdigit():
                console.print("[red]Usage: e <slide_number>[/red]")
                continue
            idx = int(rest)
            slide = next((s for s in outline.slides if s.index == idx), None)
            if slide is None:
                console.print(f"[red]Slide {idx} not found.[/red]")
                continue
            console.print(f"Editing slide [bold]{idx}[/bold]: {slide.title}")
            new_title = Prompt.ask("  New title (leave blank to keep)", default=slide.title)
            new_context = Prompt.ask("  New context (leave blank to keep)", default=slide.context)
            outline = outline.update_slide(idx, title=new_title.strip(), context=new_context.strip())
            render_outline(outline)
            _menu()

        # ── Delete ────────────────────────────────────────────────
        elif cmd == "d":
            if not rest.isdigit():
                console.print("[red]Usage: d <slide_number>[/red]")
                continue
            outline = outline.delete_slide(int(rest))
            render_outline(outline)
            _menu()

        # ── Add ───────────────────────────────────────────────────
        elif cmd == "a":
            after_idx = int(rest) if rest.isdigit() else 0
            new_title = Prompt.ask("  Title for new slide")
            new_context = Prompt.ask("  Context for new slide")
            outline = outline.add_slide(after_idx, title=new_title.strip(), context=new_context.strip())
            render_outline(outline)
            _menu()

        # ── Swap ──────────────────────────────────────────────────
        elif cmd == "s":
            tokens = rest.split()
            if len(tokens) != 2 or not tokens[0].isdigit() or not tokens[1].isdigit():
                console.print("[red]Usage: s <N> <M>[/red]")
                continue
            try:
                outline = outline.swap_slides(int(tokens[0]), int(tokens[1]))
            except ValueError as e:
                console.print(f"[red]{e}[/red]")
                continue
            render_outline(outline)
            _menu()

        # ── AI modify ─────────────────────────────────────────────
        elif cmd == "m":
            instruction = rest.strip()
            if not instruction:
                instruction = Prompt.ask("  Enter your modification instruction")
            if not instruction:
                continue
            console.print("[dim]Requesting AI revision…[/dim]")
            try:
                outline = await ai_modify(outline, instruction)
            except Exception as exc:
                console.print(f"[red]AI revision failed: {exc}[/red]")
                continue
            render_outline(outline)
            _menu()

        else:
            console.print(
                f"[red]Unknown command '{cmd}'.[/red] Type [cyan]ok[/cyan] to continue."
            )

    return outline
