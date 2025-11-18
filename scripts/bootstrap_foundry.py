# 連到 Azure AI Foundry 專案並建立三個樣板代理
from src.app.agents.registry import ensure_agents

if __name__ == "__main__":
    agents = ensure_agents()
    for name, a in agents.items():
        print(f"[OK] created agent: {name} (id={a.id})")
