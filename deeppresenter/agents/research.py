from deeppresenter.utils.typings import InputRequest

from .agent import Agent


class Research(Agent):
    async def loop(self, req: InputRequest):
        OUTLINE_FILENAME = "outline.json"
        outline_path = self.workspace / OUTLINE_FILENAME if req.enable_planner else None

        while True:
            agent_message = await self.action(
                prompt=req.deepresearch_prompt,
                attachments=req.attachments,
                outline_path=outline_path,
            )
            yield agent_message
            outcome = await self.execute(self.chat_history[-1].tool_calls)
            if isinstance(outcome, list):
                for item in outcome:
                    yield item
            else:
                yield outcome
                break
