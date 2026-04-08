from deeppresenter.agents.agent import Agent
from deeppresenter.utils.log import info
from deeppresenter.utils.outline import Outline
from deeppresenter.utils.typings import ChatMessage, InputRequest, Role


class Planner(Agent):
    async def loop(self, req: InputRequest):
        outline_path = self.workspace / "outline.json"

        while True:
            agent_message = await self.action(
                prompt=req.deepresearch_prompt,
                attachments=req.attachments,
            )
            yield agent_message
            outcome = await self.execute(agent_message.tool_calls)
            if isinstance(outcome, str):
                break
            for item in outcome:
                yield item

        if not outline_path.exists():
            raise RuntimeError("Planner did not produce outline.json.")
        info(f"Planner finished initial outline at {outline_path}")
        outline = Outline.model_validate_json(
            outline_path.read_text(encoding="utf-8-sig")
        )

        while True:
            instruction = yield outline
            if not instruction:
                return
            self.chat_history.append(
                ChatMessage(
                    role=Role.USER,
                    content=(
                        f"Please revise the outline according to this instruction: {instruction}\n"
                        f"Update `{outline_path}` and call `finalize` with that file path."
                    ),
                )
            )
            while True:
                agent_message = await self.action(
                    prompt=req.deepresearch_prompt,
                    attachments=req.attachments,
                )
                yield agent_message
                outcome = await self.execute(agent_message.tool_calls)
                if isinstance(outcome, str):
                    break
                for item in outcome:
                    yield item
            outline = Outline.model_validate_json(
                outline_path.read_text(encoding="utf-8-sig")
            )
            info(f"Planner revised outline at {outline_path}")
