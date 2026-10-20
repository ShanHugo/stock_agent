"""代理講評：把每個階段由程式算好的 JSON 交給對應的 Foundry 代理解讀。

原則：數字全部由 Python 算出，代理只負責用文字說明，不得重算或改動數字；
代理講評失敗不影響流程結果。三個代理共用同一個 thread，後面的代理看得到前面的講評。
"""
from __future__ import annotations
import json
from typing import Any, Protocol

PROMPT_TEMPLATE = (
    "以下是「{stage}」階段由程式計算出的結果（JSON）。\n"
    "請用繁體中文 3–5 句說明重點與需要注意的地方。\n"
    "規則：不要自行計算或改動任何數字，只能引用 JSON 內的數值；不要呼叫任何工具；"
    "不要給投資建議。\n\n```json\n{payload}\n```"
)


class Narrator(Protocol):
    def narrate(self, agent_name: str, stage: str, payload: dict) -> str: ...


class FoundryNarrator:
    """以 azure-ai-agents 1.x 的 threads/messages/runs API 取得代理講評。"""

    def __init__(self, client: Any, agents: dict):
        self.client = client
        self.agents = agents
        self._thread_id: str | None = None

    @classmethod
    def from_settings(cls) -> 'FoundryNarrator':
        from .registry import get_client, ensure_agents
        client = get_client()
        return cls(client, ensure_agents(client))

    def narrate(self, agent_name: str, stage: str, payload: dict) -> str:
        from azure.ai.agents.models import MessageRole
        if self._thread_id is None:
            self._thread_id = self.client.threads.create().id
        content = PROMPT_TEMPLATE.format(
            stage=stage, payload=json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        self.client.messages.create(thread_id=self._thread_id, role=MessageRole.USER, content=content)
        run = self.client.runs.create_and_process(
            thread_id=self._thread_id, agent_id=self.agents[agent_name].id, tool_choice='none')
        if str(run.status).lower().endswith('failed'):
            raise RuntimeError(f'代理 {agent_name} 執行失敗: {run.last_error}')
        msg = self.client.messages.get_last_message_text_by_role(
            thread_id=self._thread_id, role=MessageRole.AGENT)
        return msg.text.value if msg else ''
