from azure.identity import DefaultAzureCredential
from ..config.settings import settings
try:  # azure-ai-agents 1.x：FunctionTool / ToolSet 位於 azure.ai.agents.models
    from azure.ai.agents import AgentsClient
    from azure.ai.agents.models import FunctionTool, ToolSet
    _AGENT_LIB_OK = True
except ImportError:
    # 若套件缺失，標記不可用；呼叫端需處理
    _AGENT_LIB_OK = False

try:
    from .tools import user_functions
except Exception:  # 避免迴圈或其他錯誤
    user_functions = []

AGENT_SPECS = [
    ("data_qa_agent",
     "你是資料品質代理。你會收到程式已完成的資料品質檢查結果（JSON），"
     "負責說明哪些檢查有問題、對後續回測的影響。不要自行重算數字。"),
    ("pm_risk_agent",
     "你是投組與風控代理。你會收到程式算好的目標權重、回測指標與 Kill-Switch 判斷（JSON），"
     "負責解讀風險重點。不要自行重算或改動數字。"),
    ("execution_agent",
     "你是執行代理。你會收到程式產生的模擬委託（JSON），負責說明委託內容與成本；"
     "現階段只做模擬，不會真的下單。不要自行重算或改動數字。"),
]


def get_client() -> "AgentsClient":
    if not _AGENT_LIB_OK:
        raise RuntimeError("azure-ai-agents 套件不可用或版本不符，請確認 requirements 與安裝。")
    assert settings.project_endpoint, "環境變數 PROJECT_ENDPOINT 未設定"
    return AgentsClient(endpoint=settings.project_endpoint, credential=DefaultAzureCredential())


def _build_toolset():
    toolset = ToolSet()
    if user_functions:
        toolset.add(FunctionTool(set(user_functions)))
    return toolset


def ensure_agents(client=None) -> dict:
    """取得三個代理並回傳 {'name': agent_object}；同名代理已存在就沿用（必要時更新指示），不重複建立。"""
    client = client or get_client()
    toolset = _build_toolset()
    client.enable_auto_function_calls(toolset)
    model = settings.model_deployment or "gpt-4o-mini"

    existing = {}
    for a in client.list_agents():
        existing.setdefault(a.name, a)

    agents = {}
    for name, instructions in AGENT_SPECS:
        agent = existing.get(name)
        if agent is None:
            agent = client.create_agent(model=model, name=name, instructions=instructions, toolset=toolset)
        elif agent.instructions != instructions or agent.model != model:
            agent = client.update_agent(agent.id, model=model, instructions=instructions, toolset=toolset)
        agents[name] = agent
    return agents
