from deeppresenter.agents.agent import Agent
from deeppresenter.utils.log import info
from deeppresenter.utils.typings import ChatMessage, InputRequest, Role


class Planner(Agent):
    async def loop(self, req: InputRequest):
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

        info(f"Planner finished initial outline at {outcome}")

        while True:
            self.chat_history.append(ChatMessage(role=Role.USER, content=(yield outcome)))
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
