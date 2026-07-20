"""fib_gate 规则链 — 条件门禁 + 分级仓位。

基于 research.md 实证结论:
- proximity: 动态容差下的归一化距离 [0,1]
- bounce_rate: 该 level 历史反弹率 (最强预测因子)
- touch_count: level 新鲜度 (越高越失效)
"""


def check_direction(signal: dict, ctx: dict, cfg: dict) -> tuple[bool, str]:
    if signal.get("direction") in (None, "", "neutral", "flat"):
        return False, "neutral"
    return True, ""


def check_proximity(signal: dict, ctx: dict, cfg: dict) -> tuple[bool, str]:
    prox = signal.get("proximity", 0)
    min_p = cfg.get("min_proximity", 0.7)
    if prox < min_p:
        return False, f"proximity({prox:.2f})<{min_p}"
    return True, ""


def check_bounce_rate(signal: dict, ctx: dict, cfg: dict) -> tuple[bool, str]:
    br = signal.get("bounce_rate", 0)
    min_br = cfg.get("min_bounce_rate", 0.5)
    if br < min_br:
        return False, f"bounce_rate({br:.2f})<{min_br}"
    return True, ""


def check_freshness(signal: dict, ctx: dict, cfg: dict) -> tuple[bool, str]:
    tc = signal.get("touch_count", 999)
    max_tc = cfg.get("max_touch_count", 50)
    if tc > max_tc:
        return False, f"touch_count({tc})>{max_tc}"
    return True, ""


def check_no_duplicate(signal: dict, ctx: dict, cfg: dict) -> tuple[bool, str]:
    if cfg.get("allow_same_side", True):
        return True, ""
    pos = ctx.get("position")
    if not pos or pos.get("side") in (None, "flat"):
        return True, ""
    direction = signal.get("direction", "")
    signal_side = "long" if direction in ("long", "up") else "short"
    if pos["side"] == signal_side:
        return False, f"has_{pos['side']}"
    return True, ""


def compute_order_params(signal: dict, ctx: dict, cfg: dict) -> dict:
    direction = signal.get("direction", "")
    side = "buy" if direction in ("long", "up") else "sell"
    price = signal.get("level_price", signal.get("close", 0))
    bounce = signal.get("bounce_rate", 0)
    prox = signal.get("proximity", 0)
    if bounce >= 0.7 and prox >= 0.9:
        qty = cfg.get("full_size", 0.10)
    elif bounce >= 0.5:
        qty = cfg.get("half_size", 0.05)
    else:
        qty = cfg.get("min_size", 0.02)
    return {"side": side, "quantity": qty, "order_type": "market", "price": price}


GATES = [check_direction, check_proximity, check_bounce_rate, check_freshness, check_no_duplicate]
SIZING = compute_order_params
