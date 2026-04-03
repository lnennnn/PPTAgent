"""Planner agent — generates the slide outline before research."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from deeppresenter.agents.agent import Agent
from deeppresenter.utils.config import get_json_from_response
from deeppresenter.utils.log import info
from deeppresenter.utils.outline import Outline
from deeppresenter.utils.typings import ChatMessage, InputRequest, Role

OUTLINE_FILENAME = "outline.json"


class Planner(Agent):
    """
    Planner agent: researches the topic and produces a slide outline.

    The agent writes outline.json to the workspace, then calls finalize()
    with the file path. The loop() detects the finalize return value (a str)
    and yields the loaded Outline as its final item.
    """

    async def loop(
        self, req: InputRequest
    ) -> AsyncGenerator[ChatMessage | Outline]:
        while True:
            agent_message = await self.action(
                prompt=req.deepresearch_prompt,
                attachments=req.attachments,
            )
            yield agent_message

            if not agent_message.tool_calls:
                nudge = ChatMessage(
                    role=Role.USER,
                    content=(
                        "Please write the outline as a JSON array to "
                        f"`{self.workspace / OUTLINE_FILENAME}`, "
                        "then call `finalize` with that file path."
                    ),
                )
                self.chat_history.append(nudge)
                continue

            outcome = await self.execute(agent_message.tool_calls)

            if isinstance(outcome, str):
                # finalize was called — outcome is the outline.json path
                break
            for item in outcome:
                yield item

        outline_path = self.workspace / OUTLINE_FILENAME
        if not outline_path.exists():
            raise RuntimeError("Planner did not produce outline.json.")

        info(f"Planner finished, outline at {outline_path}")
        yield Outline.load(outline_path)

    async def revise_outline(self, outline: Outline, instruction: str) -> Outline:
        """One-shot LLM call to revise the outline. Does not affect loop history."""
        system = self.role_config.system[self.language]
        user_content = (
            f"Here is the current slide outline:\n```json\n{outline.to_json()}\n```\n\n"
            f"User's modification request: {instruction}\n\n"
            "Return ONLY a valid JSON array with the same structure "
            "(index, title, context for each slide). No extra commentary."
        )
        messages = [
            ChatMessage(role=Role.SYSTEM, content=system),
            ChatMessage(role=Role.USER, content=user_content),
        ]
        response = await self.llm.run(messages=messages)
        raw = response.choices[0].message.content or ""

        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        data = get_json_from_response(raw.strip())
        if isinstance(data, list):
            new_outline = Outline.from_json(data)
        elif isinstance(data, dict) and "slides" in data:
            new_outline = Outline.from_json(data["slides"])
        else:
            raise ValueError(f"Unexpected LLM response: {raw[:300]}")

        new_outline.save(self.workspace / OUTLINE_FILENAME)
        return new_outline

    async def finish(self, result: str):
        pass
