"""fib_gate 规则链 — 斐波那契信号转订单的门禁 + 仓位计算。"""


def check_direction(signal: dict, ctx: dict, cfg: dict) -> tuple[bool, str]:
    if signal.get("direction") in (None, "", "neutral", "flat"):
        return False, "neutral"
    return True, ""


def check_strength(signal: dict, ctx: dict, cfg: dict) -> tuple[bool, str]:
    strength = signal.get("strength", 0)
    min_s = cfg.get("min_strength", 0.6)
    if strength < min_s:
        return False, f"strength({strength:.2f})<{min_s}"
    return True, ""


def check_no_duplicate(signal: dict, ctx: dict, cfg: dict) -> tuple[bool, str]:
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
    return {
        "side": side, "quantity": cfg.get("position_size", 0.1),
        "order_type": "market",
        "price": signal.get("price", signal.get("touch_price", 0)),
    }


GATES = [check_direction, check_strength, check_no_duplicate]
SIZING = compute_order_params
