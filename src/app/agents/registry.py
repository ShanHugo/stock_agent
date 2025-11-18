from azure.identity import DefaultAzureCredential
from ..config.settings import settings
try:  # 動態相容 azure-ai-agents 版本差異
    from azure.ai.agents import AgentsClient, FunctionTool
    try:
        from azure.ai.agents import ToolSet  # 舊版或新版可能不存在
    except ImportError:
        class ToolSet(list):  # 簡易後備實作
            def add(self, obj):
                self.append(obj)
    _AGENT_LIB_OK = True
except ImportError:
    # 若套件缺失，標記不可用；呼叫端需處理
    _AGENT_LIB_OK = False

try:
    from .tools import user_functions
except Exception:  # 避免迴圈或其他錯誤
    user_functions = []

def get_client() -> AgentsClient:
    if not _AGENT_LIB_OK:
        raise RuntimeError("azure-ai-agents 套件不可用或版本不符，請確認 requirements 與安裝。")
    assert settings.project_endpoint, "環境變數 PROJECT_ENDPOINT 未設定"
    return AgentsClient(endpoint=settings.project_endpoint, credential=DefaultAzureCredential())

def ensure_agents() -> dict:
    """建立或回收三個樣板代理並回傳 {'name': agent_object}。"""
    client = get_client()
    # 若無函式或代理庫不可用則直接回傳空
    if not _AGENT_LIB_OK:
        raise RuntimeError("Agents SDK 不可用，無法建立代理。")
    fn_tool = FunctionTool(user_functions) if user_functions else None
    toolset = ToolSet()
    if fn_tool:
        if hasattr(toolset, 'add'):
            toolset.add(fn_tool)
        else:
            toolset.append(fn_tool)  # 後備
        # enable_auto_function_calls 若不存在則忽略
        if hasattr(client, 'enable_auto_function_calls'):
            try:
                client.enable_auto_function_calls(toolset)
            except Exception:
                pass

    created = {}
    for name, instructions in [
        ("data_qa_agent", "你是資料品質代理，負責檢查缺漏、異常、特徵一致性。"),
        ("pm_risk_agent", "你是投組與風控代理，根據策略輸出目標權重與風控限制。"),
        ("execution_agent", "你是執行代理，未來會透過 OpenAPI/MCP 下單；現階段模擬輸出委託建議。"),
    ]:
        agent = client.create_agent(
            model=settings.model_deployment or "gpt-4o-mini",
            name=name,
            instructions=instructions,
            toolset=toolset
        )
        created[name] = agent
    return created
