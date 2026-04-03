"""Outline data model, pure operations, and editor protocol for adapters (CLI / web)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from deeppresenter.utils.config import get_json_from_response


class OutlineSlide(BaseModel):
    index: int
    title: str
    context: str


class Outline(BaseModel):
    slides: list[OutlineSlide]

    # ── Serialization ──────────────────────────────────────────────────

    def to_json(self) -> str:
        return json.dumps(
            [s.model_dump() for s in self.slides], ensure_ascii=False, indent=2
        )

    @classmethod
    def from_json(cls, data: str | list | dict) -> Outline:
        if isinstance(data, str):
            text = data.strip()
            if not text:
                raise ValueError("Outline JSON is empty or whitespace-only.")
            data = get_json_from_response(text)
        if isinstance(data, dict):
            if "slides" in data:
                data = data["slides"]
            else:
                raise ValueError(
                    "Outline JSON object must contain a 'slides' array, "
                    "or use a top-level JSON array of slides."
                )
        if not isinstance(data, list):
            raise ValueError(
                f"Outline slides must be a JSON array, got {type(data).__name__}."
            )
        slides: list[OutlineSlide] = []
        for i, item in enumerate(data, start=1):
            if isinstance(item, str):
                slides.append(OutlineSlide(index=i, title=item, context=""))
            elif isinstance(item, dict):
                d = dict(item)
                d.setdefault("index", i)
                d.setdefault("title", "")
                d.setdefault("context", "")
                slides.append(OutlineSlide.model_validate(d))
            else:
                raise ValueError(
                    f"Each slide must be an object or string, got {type(item).__name__}."
                )
        return cls(slides=slides)

    def save(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Outline:
        return cls.from_json(path.read_text(encoding="utf-8-sig"))

    # ── Pure operations (return new Outline, do not mutate) ────────────

    def reindex(self) -> Outline:
        """Return a new Outline with slides re-numbered from 1."""
        new_slides = [
            OutlineSlide(index=i, title=s.title, context=s.context)
            for i, s in enumerate(self.slides, start=1)
        ]
        return Outline(slides=new_slides)

    def update_slide(self, index: int, title: str = "", context: str = "") -> Outline:
        """Return a new Outline with the given slide's title/context updated."""
        new_slides = []
        for s in self.slides:
            if s.index == index:
                new_slides.append(
                    OutlineSlide(
                        index=s.index,
                        title=title or s.title,
                        context=context or s.context,
                    )
                )
            else:
                new_slides.append(s.model_copy())
        return Outline(slides=new_slides)

    def delete_slide(self, index: int) -> Outline:
        """Return a new Outline with the given slide removed and re-indexed."""
        new_slides = [s for s in self.slides if s.index != index]
        return Outline(slides=new_slides).reindex()

    def add_slide(self, after_index: int, title: str, context: str) -> Outline:
        """Return a new Outline with a slide inserted after after_index (0 = prepend)."""
        new_slide = OutlineSlide(index=0, title=title, context=context)
        slides = list(self.slides)
        slides.insert(after_index, new_slide)
        return Outline(slides=slides).reindex()

    def swap_slides(self, index_a: int, index_b: int) -> Outline:
        """Return a new Outline with two slides swapped."""
        slides = list(self.slides)
        pos_a = next((i for i, s in enumerate(slides) if s.index == index_a), None)
        pos_b = next((i for i, s in enumerate(slides) if s.index == index_b), None)
        if pos_a is None or pos_b is None:
            raise ValueError(f"Slide index {index_a} or {index_b} not found.")
        slides[pos_a], slides[pos_b] = slides[pos_b], slides[pos_a]
        return Outline(slides=slides).reindex()


class OutlineEditor(Protocol):
    """Async callable: edit or confirm outline before research; may call ai_modify for LLM revisions."""

    async def __call__(
        self,
        outline: Outline,
        ai_modify: Callable[[Outline, str], Awaitable[Outline]],
    ) -> Outline: ...
