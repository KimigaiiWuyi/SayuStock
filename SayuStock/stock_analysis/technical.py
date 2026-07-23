"""技术面分析 —— 一等公民：可解释评分 + 结构结论。"""

from __future__ import annotations

from typing import Any
from dataclasses import field, dataclass

from ..utils.kline import klines_to_df
from ..utils.indicators import compute_indicators

# 禁止单字「日/周/月」：会误吞「日经225」「周大福」「月城」
PERIOD_ALIASES: dict[str, str] = {
    "日k": "101",
    "日线": "101",
    "周k": "102",
    "周线": "102",
    "月k": "103",
    "月线": "103",
    "60k": "60",
    "60分钟": "60",
    "30k": "30",
    "15k": "15",
    "5k": "5",
}

PERIOD_LABELS: dict[str, str] = {
    "5": "5分钟",
    "15": "15分钟",
    "30": "30分钟",
    "60": "60分钟",
    "101": "日K",
    "102": "周K",
    "103": "月K",
}


def parse_period_and_query(text: str) -> tuple[str, str]:
    """解析「技术分析 日k 茅台」→ (kline_code, query)。默认日K。"""
    raw = text.strip()
    if not raw:
        return "101", ""
    lower = raw.lower()
    for alias, code in sorted(PERIOD_ALIASES.items(), key=lambda x: -len(x[0])):
        if lower.startswith(alias):
            return code, raw[len(alias) :].strip()
    return "101", raw


@dataclass(slots=True)
class TechnicalReport:
    name: str
    code: str
    period_code: str
    period_label: str
    last_close: float | None
    score: int
    trend: str
    momentum: str
    volume: str
    position: str
    signals: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    levels: dict[str, float | None] = field(default_factory=dict)
    indicators: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "code": self.code,
            "period": self.period_label,
            "last_close": self.last_close,
            "score": self.score,
            "trend": self.trend,
            "momentum": self.momentum,
            "volume": self.volume,
            "position": self.position,
            "signals": list(self.signals),
            "risk_flags": list(self.risk_flags),
            "levels": dict(self.levels),
            "summary": self.summary,
        }


def _f(ind: dict[str, Any], key: str) -> float | None:
    if key not in ind:
        return None
    v = ind[key]
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _truthy(ind: dict[str, Any], key: str) -> bool:
    return bool(ind[key]) if key in ind else False


def build_technical_report(
    *,
    name: str,
    code: str,
    period_code: str,
    klines: list[str],
) -> TechnicalReport | str:
    """从东财 klines 字符串列表构建技术报告。"""
    if not klines:
        return "❌暂无K线数据"
    df = klines_to_df(klines)
    if df.empty or len(df) < 5:
        return "❌K线数据不足，无法分析"
    ind = compute_indicators(df)
    signals: list[str] = []
    risks: list[str] = []

    # —— 趋势 0–35（信号分已并入各维度，避免双重计分）——
    trend = "震荡"
    trend_pts = 17
    if _truthy(ind, "ma_bull_alignment"):
        trend = "偏多"
        trend_pts = 32
        signals.append("均线多头排列(MA5>MA10>MA20)")
    elif _truthy(ind, "ma_bear_alignment"):
        trend = "偏空"
        trend_pts = 6
        risks.append("均线空头排列(MA5<MA10<MA20)")
    elif _truthy(ind, "close_above_ma20"):
        trend = "偏多"
        trend_pts = 25
        signals.append("收盘站上MA20")
    elif _truthy(ind, "close_below_ma20"):
        trend = "偏空"
        trend_pts = 12
        risks.append("收盘跌破MA20")
    if _truthy(ind, "close_above_bbi"):
        trend_pts = min(35, trend_pts + 2)
    if _truthy(ind, "close_below_bbi"):
        trend_pts = max(0, trend_pts - 2)
    score = trend_pts

    # —— 动量 0–30 ——
    mom_pts = 14
    momentum = "中性"
    rsi6 = _f(ind, "rsi6")
    macd_bar = _f(ind, "macd_bar")
    if _truthy(ind, "macd_golden_cross_in_3d"):
        mom_pts += 7
        signals.append("MACD近3日金叉")
    if _truthy(ind, "macd_death_cross_in_3d"):
        mom_pts -= 7
        risks.append("MACD近3日死叉")
    if macd_bar is not None:
        if macd_bar > 0:
            mom_pts += 3
            momentum = "偏强"
        else:
            mom_pts -= 3
            momentum = "偏弱"
    if rsi6 is not None:
        if rsi6 >= 80:
            mom_pts -= 4
            risks.append(f"RSI6超买({rsi6:.1f})")
            momentum = "过热"
        elif rsi6 <= 20:
            mom_pts += 4
            signals.append(f"RSI6超卖({rsi6:.1f})")
            momentum = "超卖反弹区"
        elif rsi6 >= 55:
            mom_pts += 2
            momentum = "偏强" if momentum == "中性" else momentum
        elif rsi6 <= 45:
            mom_pts -= 2
            momentum = "偏弱" if momentum == "中性" else momentum
    if _truthy(ind, "kdj_golden_cross_in_3d"):
        mom_pts += 3
        signals.append("KDJ近3日金叉")
    if _truthy(ind, "kdj_death_cross_in_3d"):
        mom_pts -= 3
        risks.append("KDJ近3日死叉")
    if _truthy(ind, "kdj_overbought"):
        risks.append("KDJ超买")
    if _truthy(ind, "kdj_oversold"):
        signals.append("KDJ超卖")
    mom_pts = max(0, min(30, mom_pts))
    score += mom_pts

    # —— 量价 0–20 ——
    vol_pts = 10
    volume = "平稳"
    vr = _f(ind, "volume_ratio")
    cmf = _f(ind, "cmf20")
    if vr is not None:
        if vr >= 1.8:
            vol_pts += 5
            volume = "放量"
            signals.append(f"量比偏高({vr:.2f})")
        elif vr <= 0.7:
            vol_pts -= 3
            volume = "缩量"
    if cmf is not None:
        if cmf > 0.05:
            vol_pts += 4
            signals.append(f"CMF资金流入({cmf:.2f})")
        elif cmf < -0.05:
            vol_pts -= 4
            risks.append(f"CMF资金流出({cmf:.2f})")
            volume = "流出" if volume == "平稳" else volume
    vol_pts = max(0, min(20, vol_pts))
    score += vol_pts

    # —— 位置 0–15 ——
    pos_pts = 8
    position = "中性区"
    pct_b = _f(ind, "boll20_pct_b")
    if _truthy(ind, "boll20_breakout_up"):
        pos_pts += 2
        position = "突破上轨"
        signals.append("BOLL上破")
    elif _truthy(ind, "boll20_breakout_down"):
        pos_pts -= 4
        position = "跌破下轨"
        risks.append("BOLL下破")
    elif pct_b is not None:
        if pct_b >= 0.8:
            pos_pts -= 1
            position = "靠近上轨"
        elif pct_b <= 0.2:
            pos_pts += 2
            position = "靠近下轨"
    last = _f(ind, "last_close")
    support = _f(ind, "support")
    resistance = _f(ind, "resistance")
    if last is not None and support is not None and support > 0:
        dist_s = (last - support) / support * 100
        if dist_s < 2:
            signals.append(f"接近支撑({support:.2f})")
    if last is not None and resistance is not None and resistance > 0:
        dist_r = (resistance - last) / last * 100
        if dist_r < 2:
            risks.append(f"接近压力({resistance:.2f})")
    pos_pts = max(0, min(15, pos_pts))
    score += pos_pts
    score = int(max(0, min(100, score)))

    atr = _f(ind, "atr_pct")
    stop_ref = None
    target_ref = None
    if last is not None and atr is not None:
        stop_ref = last * (1 - max(atr, 0.01) * 1.5)
        target_ref = last * (1 + max(atr, 0.01) * 1.5)

    levels: dict[str, float | None] = {
        "support": support,
        "resistance": resistance,
        "stop_ref": stop_ref,
        "target_ref": target_ref,
        "ma20": _f(ind, "ma20"),
        "ma60": _f(ind, "ma60"),
    }

    summary = (
        f"{trend}·{momentum}·{volume}，技术分 {score}。"
        f"{'；'.join(signals[:3]) if signals else '暂无强信号'}"
        f"{('；风险：' + '；'.join(risks[:2])) if risks else ''}"
    )

    return TechnicalReport(
        name=name,
        code=code,
        period_code=period_code,
        period_label=PERIOD_LABELS[period_code] if period_code in PERIOD_LABELS else period_code,
        last_close=last,
        score=score,
        trend=trend,
        momentum=momentum,
        volume=volume,
        position=position,
        signals=signals,
        risk_flags=risks,
        levels=levels,
        indicators=ind,
        summary=summary,
    )


def report_to_text(report: TechnicalReport) -> str:
    lv = report.levels
    ind = report.indicators
    lines = [
        f"【{report.name}({report.code}) {report.period_label}技术分析】",
        f"技术分: {report.score}/100  |  趋势:{report.trend}  动量:{report.momentum}  "
        f"量能:{report.volume}  位置:{report.position}",
    ]
    if report.last_close is not None:
        lines.append(f"现价: {report.last_close:.2f}")
    lines.append(
        f"关键位: 支撑 {_fmt(lv['support'] if 'support' in lv else None)}  "
        f"压力 {_fmt(lv['resistance'] if 'resistance' in lv else None)}  "
        f"MA20 {_fmt(lv['ma20'] if 'ma20' in lv else None)}  "
        f"MA60 {_fmt(lv['ma60'] if 'ma60' in lv else None)}"
    )
    stop = lv["stop_ref"] if "stop_ref" in lv else None
    target = lv["target_ref"] if "target_ref" in lv else None
    if stop is not None or target is not None:
        lines.append(f"参考: 止损 {_fmt(stop)}  目标 {_fmt(target)} (ATR×1.5)")
    lines.append(
        f"MACD: DIF {_fmt(ind['macd_dif'] if 'macd_dif' in ind else None)} "
        f"DEA {_fmt(ind['macd_dea'] if 'macd_dea' in ind else None)} "
        f"BAR {_fmt(ind['macd_bar'] if 'macd_bar' in ind else None)}"
    )
    lines.append(
        f"RSI6/12/24: {_fmt(ind['rsi6'] if 'rsi6' in ind else None)} / "
        f"{_fmt(ind['rsi12'] if 'rsi12' in ind else None)} / "
        f"{_fmt(ind['rsi24'] if 'rsi24' in ind else None)}  "
        f"KDJ: K {_fmt(ind['kdj_k'] if 'kdj_k' in ind else None)} "
        f"D {_fmt(ind['kdj_d'] if 'kdj_d' in ind else None)} "
        f"J {_fmt(ind['kdj_j'] if 'kdj_j' in ind else None)}"
    )
    if report.signals:
        lines.append("信号: " + "；".join(report.signals))
    if report.risk_flags:
        lines.append("风险: " + "；".join(report.risk_flags))
    lines.append(f"摘要: {report.summary}")
    return "\n".join(lines)


def _fmt(v: object, d: int = 2) -> str:
    if v is None or isinstance(v, bool):
        return "N/A"
    if isinstance(v, (int, float)):
        return f"{float(v):.{d}f}"
    return str(v)
