"""Retracement 模块配置：模块级常量，ALGO_* 用于算法计算，TOUCH_* 用于触碰/突破检测。
外部通过 apply_overrides(dict) 覆盖，使用时 config.XXX 直接引用。
"""

# ═══════ ALGO — swing 拐点 ═══════

# pivot 窗口对：[left, right] 双侧比较长度
ALGO_PIVOT_WINDOWS: list = [[5, 5], [8, 8]]
# zigzag 最小波幅阈值（百分比），越大越只保留主趋势
ALGO_ZIGZAG_THRESHOLDS: list = [0.05, 0.10]
# 线性回归通道窗口长度列表
ALGO_REGRESSION_WINDOWS: list = [50, 100]
# 各指标权重：名称 → 权重，用于计算聚合置信度
ALGO_WEIGHTS: dict = {
    'pivot_5': 0.5, 'pivot_8': 1.0,
    'zigzag_5': 0.5, 'zigzag_10': 1.0,
    'reg_50': 0.5, 'reg_100': 1.0,
}
# 聚类容差：价格百分比距离 ≤ 此值归为同一 cluster
ALGO_CLUSTER_TOLERANCE_PCT: float = 0.005
# cluster 最低置信度，低于此值忽略
ALGO_MIN_CLUSTER_CONF: float = 0.3

# ═══════ ALGO — fib 回撤 ═══════

# 趋势腿最小跨度（占价格百分比），过滤微小波动
ALGO_MIN_LEG_SPAN_PCT: float = 0.03
# fib 水平拟合最大误差
ALGO_MAX_RATIO_ERROR: float = 0.05
# 标准 Fibonacci 比率序列
ALGO_STD_RATIOS: list = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
# 每步保留的 top-N 趋势腿数量
ALGO_TOP_N: int = 6
# 近期窗口基准长度（日K: 60-120, 小时K: 168-336）
ALGO_RECENT_BARS: int = 90
# 从最新 bar 往旧方向跳过 N 根，不参与分析
ALGO_SKIP_RECENT: int = 10

# ═══════ TOUCH — 触碰检测 ═══════

# 触碰容差：price 距 level 占 ATR 的倍数以内视为触碰
TOUCH_TOLERANCE: float = 0.5
# 同一水平连续触碰冷却时间（秒）
TOUCH_COOLDOWN_SEC: float = 60.0
# 突破容差：price 偏离 group 边界占 ATR 的倍数以上视为突破
TOUCH_BREAKOUT_TOLERANCE: float = 1.0
# 冷却 bar 数（bar 级触碰去重）
TOUCH_COOLDOWN_BARS: int = 5
# 接近方向回溯 bar 数
TOUCH_APPROACH_LOOKBACK: int = 5
# 历史回测窗口（bar 数）
TOUCH_HISTORY_LOOKBACK_BARS: int = 200
# 成交量回溯窗口
TOUCH_VOLUME_LOOKBACK: int = 20
# 量能确认阈值（当前量 / 均量 ≥ 此值视为放量）
TOUCH_VOLUME_THRESHOLD: float = 1.5

# ═══════ TOUCH — 评分权重 ═══════

# 共振得分权重
TOUCH_W_CONSENSUS: float = 2.0
# 反弹率权重
TOUCH_W_BOUNCE_RATE: float = 1.5
# 触碰次数权重
TOUCH_W_TOUCH_COUNT: float = 0.1
# 量能确认权重
TOUCH_W_VOLUME: float = 1.0
# 逆势因子权重
TOUCH_W_COUNTER_TREND: float = 0.5
# K 线形态权重
TOUCH_W_CANDLE: float = 1.0

# ═══════ TOUCH — 信号等级阈值 ═══════

# 强信号阈值
TOUCH_STRONG_THRESHOLD: float = 5.0
# 中等信号阈值
TOUCH_MEDIUM_THRESHOLD: float = 3.5
# 弱信号阈值
TOUCH_WEAK_THRESHOLD: float = 2.0
# 扫描范围（0 = 全量，>0 = 最近 N 根）
TOUCH_SCAN_BARS: int = 0


def apply_overrides(overrides: dict) -> list:
    """用外部 dict 覆盖模块常量，key 须与常量名一致（大写）。"""
    if not overrides: return []
    g = globals()
    applied = []
    for k, v in overrides.items():
        key = k if k.isupper() else k.upper()
        if key not in g or key.startswith('_'): continue
        g[key] = v
        applied.append(key)
    return applied
