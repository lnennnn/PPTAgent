from __future__ import annotations

from typing import Any

from pydantic import RootModel, model_validator


class Outline(RootModel[list[dict[str, Any]]]):
    @model_validator(mode="before")
    @classmethod
    def normalize_input(cls, data: Any) -> list[dict[str, Any]]:
        if isinstance(data, dict):
            if "slides" not in data:
                raise ValueError(
                    "Outline JSON object must contain a 'slides' array, "
                    "or use a top-level JSON array of slides."
                )
            data = data["slides"]

        if not isinstance(data, list):
            raise ValueError(
                f"Outline slides must be a JSON array, got {type(data).__name__}."
            )

        return [cls._normalize_slide(item, i) for i, item in enumerate(data, start=1)]

    @property
    def slides(self) -> list[dict[str, Any]]:
        return self.root

    @staticmethod
    def _normalize_slide(item: str | dict[str, Any], index: int) -> dict[str, Any]:
        if isinstance(item, str):
            return {"index": index, "title": item, "context": ""}
        if not isinstance(item, dict):
            raise ValueError(
                f"Each slide must be an object or string, got {type(item).__name__}."
            )

        raw_index = item.get("index", index)
        try:
            slide_index = int(raw_index)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Slide index must be an integer, got {raw_index!r}."
            ) from exc

        title = item.get("title", "")
        context = item.get("context", "")
        if title is None:
            title = ""
        if context is None:
            context = ""
        return {
            "index": slide_index,
            "title": str(title),
            "context": str(context),
        }

    # ── Pure operations (return new Outline, do not mutate) ────────────

    def reindex(self) -> Outline:
        """Return a new Outline with slides re-numbered from 1."""
        new_slides = [
            {"index": i, "title": s["title"], "context": s["context"]}
            for i, s in enumerate(self.slides, start=1)
        ]
        return Outline.model_validate(new_slides)

    def update_slide(self, index: int, title: str = "", context: str = "") -> Outline:
        """Return a new Outline with the given slide's title/context updated."""
        new_slides = []
        for s in self.slides:
            if s["index"] == index:
                new_slides.append(
                    {
                        "index": s["index"],
                        "title": title or s["title"],
                        "context": context or s["context"],
                    }
                )
            else:
                new_slides.append(dict(s))
        return Outline.model_validate(new_slides)

    def delete_slide(self, index: int) -> Outline:
        """Return a new Outline with the given slide removed and re-indexed."""
        new_slides = [s for s in self.slides if s["index"] != index]
        return Outline.model_validate(new_slides).reindex()

    def add_slide(self, after_index: int, title: str, context: str) -> Outline:
        """Return a new Outline with a slide inserted after after_index (0 = prepend)."""
        new_slide = {"index": 0, "title": title, "context": context}
        slides = list(self.slides)
        slides.insert(after_index, new_slide)
        return Outline.model_validate(slides).reindex()

    def swap_slides(self, index_a: int, index_b: int) -> Outline:
        """Return a new Outline with two slides swapped."""
        slides = list(self.slides)
        pos_a = next((i for i, s in enumerate(slides) if s["index"] == index_a), None)
        pos_b = next((i for i, s in enumerate(slides) if s["index"] == index_b), None)
        if pos_a is None or pos_b is None:
            raise ValueError(f"Slide index {index_a} or {index_b} not found.")
        slides[pos_a], slides[pos_b] = slides[pos_b], slides[pos_a]
        return Outline.model_validate(slides).reindex()
