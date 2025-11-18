# 風控 Kill-Switch（實務應連動監控與委託撤單）
def should_halt(pnl_today: float, drawdown: float, pnl_limit=-0.06, dd_limit=-0.15) -> bool:
    return (pnl_today <= pnl_limit) or (drawdown <= dd_limit)
