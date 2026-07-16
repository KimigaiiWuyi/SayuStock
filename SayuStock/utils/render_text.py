"""图表数据 → 文字 —— 给**看不到图**的 AI 用。

## 为什么必须有这个模块

插件的输出主体是图片，但并非所有模型都有视觉能力。对看不到图的 AI 来说，
``ai_return`` 的这段文字**就是它拿到的全部信息**。所以这里的原则是：

> **图上画了什么，文字里就必须有什么。**

历史上这层只吐最近 10 根 K 线的 OHLC —— 图上画着 MA / BOLL / BBI / KDJ / RSI /
MACD / CMF，AI 一个都拿不到，等于让它裸看 K 线数字瞎猜。对比图更是连刚修好的
「区间最大涨幅/回撤」都没进文字。

指标一律走 ``utils/indicators.py``（图表画的是同一份），所以文字与图**不会漂**。

新增图表元素时，请同步在这里补上对应文字，并在 ``test/test_render_text.py`` 加断言。
"""

from .kline import klines_to_df
from .indicators import swing_stats, normalize_pct, compute_indicators

__all__ = [
    "cloudmap_text",
    "compare_text",
    "kline_text",
    "single_stock_text",
]

# 东财 K 线接口的周期码 → 中文名
PERIOD_NAMES = {
    "5": "5分钟",
    "15": "15分钟",
    "30": "30分钟",
    "60": "60分钟",
    "100": "K线",
    "101": "日K",
    "102": "周K",
    "103": "月K",
    "104": "季K",
    "105": "半年K",
    "106": "年K",
}


def _fmt(value: object, digits: int = 2, suffix: str = "") -> str:
    """数值 → 字符串；None（指标数据不足）显示为 N/A 而不是 0，避免 AI 误读。"""
    if value is None or isinstance(value, bool):
        return "N/A"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{suffix}"
    return str(value)


def _pct(value: object, digits: int = 2, signed: bool = True) -> str:
    """小数比率 → 百分数文字（0.0325 → +3.25%）。

    ``signed=False`` 用于恒非负的量（带宽、ATR%），带 ``+`` 号反而像在表示方向。
    """
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return "N/A"
    return f"{value * 100:{'+' if signed else ''}.{digits}f}%"


def _flags(pairs: list[tuple[str, object]]) -> str:
    """把命中的形态标签拼成一串；一个都没命中返回空串。"""
    hit = [name for name, on in pairs if on is True]
    return "，".join(hit)


def period_name(sector: str) -> str:
    """'single-stock-kline-101' → '日K'。"""
    code = sector.replace("single-stock-kline-", "")
    return PERIOD_NAMES.get(code, "K线")


def kline_text(raw_data: dict, sector: str) -> str:
    """K 线图的全量文字版：图上每一条线都在这里有对应读数。

    含：最新价/区间极值与涨跌幅/均线与排列/BBI/BOLL 双轨/KDJ/RSI 三线/MACD/
    CMF/量比/乖离/ATR/支撑压力/叉信号，外加最近 10 根明细。
    """
    data = raw_data.get("data") or {}
    klines = list(data.get("klines") or [])
    if not klines:
        return ""

    name = data.get("name") or data.get("f58") or "N/A"
    df = klines_to_df(klines)
    if df.empty:
        return ""

    ind = compute_indicators(df)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    lines: list[str] = [
        f"【{name} {period_name(sector)}数据】共 {len(df)} 根，{df['date'].iloc[0]} ~ {df['date'].iloc[-1]}"
    ]

    # —— 最新一根 ——
    last_chg = df["chg_pct"].iloc[-1]
    lines.append(
        f"最新: 收盘 {_fmt(ind['last_close'])}  涨跌幅 {last_chg:+.2f}%  "
        f"换手率 {_fmt(ind['turnover_pct'])}%  成交量 {df['volume'].iloc[-1]:.0f}"
    )

    # —— 区间极值 + 区间涨跌（与对比图同一套 swing_stats 口径）——
    hi_idx = int(high.idxmax())
    lo_idx = int(low.idxmin())
    pct_series = normalize_pct(close) * 100
    runup, drawdown = swing_stats(pct_series)
    lines.append(
        f"区间: 最高 {high.max():.2f}({df['date'].iloc[hi_idx]})  最低 {low.min():.2f}({df['date'].iloc[lo_idx]})  "
        f"区间最大涨幅 +{runup:.2f}%  区间最大回撤 {drawdown:.2f}%  "
        f"首尾累计 {pct_series.iloc[-1]:+.2f}%"
    )

    # —— 均线 ——
    align = _flags(
        [
            ("多头排列(MA5>MA10>MA20)", ind["ma_bull_alignment"]),
            ("空头排列(MA5<MA10<MA20)", ind["ma_bear_alignment"]),
            ("站上MA20", ind["close_above_ma20"]),
            ("跌破MA20", ind["close_below_ma20"]),
        ]
    )
    lines.append(
        f"均线: MA5 {_fmt(ind['ma5'])}  MA10 {_fmt(ind['ma10'])}  "
        f"MA20 {_fmt(ind['ma20'])}  MA60 {_fmt(ind['ma60'])}" + (f"  [{align}]" if align else "")
    )

    # —— BBI ——
    bbi_flag = _flags(
        [("收盘在BBI上方(多头占优)", ind["close_above_bbi"]), ("收盘在BBI下方(空头占优)", ind["close_below_bbi"])]
    )
    lines.append(f"BBI: {_fmt(ind['bbi'])}" + (f"  [{bbi_flag}]" if bbi_flag else ""))

    # —— BOLL 双轨 ——
    boll_flag = _flags([("上破上轨", ind["boll20_breakout_up"]), ("下破下轨", ind["boll20_breakout_down"])])
    lines.append(
        f"BOLL(20,2): 上 {_fmt(ind['boll20_upper'])}  中 {_fmt(ind['boll20_mid'])}  下 {_fmt(ind['boll20_lower'])}  "
        f"带宽 {_pct(ind['boll20_bandwidth'], signed=False)}  %B {_fmt(ind['boll20_pct_b'])}"
        + (f"  [{boll_flag}]" if boll_flag else "")
    )
    lines.append(
        f"BOLL(60,3): 上 {_fmt(ind['boll60_upper'])}  中 {_fmt(ind['boll60_mid'])}  下 {_fmt(ind['boll60_lower'])}  "
        f"带宽 {_pct(ind['boll60_bandwidth'], signed=False)}  "
        f"短/中敞口比 {_fmt(ind['boll_opening_ratio_short_vs_mid'])}"
    )

    # —— KDJ ——
    kdj_flag = _flags(
        [
            ("超买", ind["kdj_overbought"]),
            ("超卖", ind["kdj_oversold"]),
            ("近3日金叉", ind["kdj_golden_cross_in_3d"]),
            ("近3日死叉", ind["kdj_death_cross_in_3d"]),
        ]
    )
    lines.append(
        f"KDJ(9,3,3): K {_fmt(ind['kdj_k'])}  D {_fmt(ind['kdj_d'])}  J {_fmt(ind['kdj_j'])}"
        + (f"  [{kdj_flag}]" if kdj_flag else "")
    )

    # —— RSI（6/12/24，国内口径）——
    lines.append(
        f"RSI: RSI6 {_fmt(ind['rsi6'])}  RSI12 {_fmt(ind['rsi12'])}  RSI24 {_fmt(ind['rsi24'])}  (>80 超买 / <20 超卖)"
    )

    # —— MACD（BAR 为通达信口径，已 ×2）——
    bar = ind["macd_bar"]
    bar_color = ""
    if isinstance(bar, (int, float)) and not isinstance(bar, bool):
        bar_color = "红柱" if bar >= 0 else "绿柱"
    macd_flag = _flags([("近3日金叉", ind["macd_golden_cross_in_3d"]), ("近3日死叉", ind["macd_death_cross_in_3d"])])
    lines.append(
        f"MACD(12,26,9): DIF {_fmt(ind['macd_dif'])}  DEA {_fmt(ind['macd_dea'])}  "
        f"BAR {_fmt(bar)}{f'({bar_color})' if bar_color else ''}" + (f"  [{macd_flag}]" if macd_flag else "")
    )

    # —— 资金/波动/支撑压力 ——
    lines.append(
        f"CMF(20): {_fmt(ind['cmf20'], 4)} ({'资金流入' if _is_pos(ind['cmf20']) else '资金流出'})  "
        f"量比 {_fmt(ind['volume_ratio'])}  乖离率(20) {_pct(ind['bias'])}  "
        f"ATR% {_pct(ind['atr_pct'], signed=False)}  CCI(14) {_fmt(ind['cci14'])}"
    )
    lines.append(f"支撑(20日最低) {_fmt(ind['support'])}  压力(20日最高) {_fmt(ind['resistance'])}")

    # —— 明细 ——
    lines.append("")
    lines.append("最近 10 根:")
    lines.append("日期        开盘      收盘      最高      最低      涨跌幅    换手率")
    for _, row in df.tail(10).iterrows():
        lines.append(
            f"{row['date']} {row['open']:>8.2f} {row['close']:>9.2f} {row['high']:>9.2f} "
            f"{row['low']:>9.2f} {row['chg_pct']:>8.2f}% {row['turnover_rate']:>8.2f}%"
        )
    return "\n".join(lines)


def _is_pos(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def compare_text(raw_datas: list[dict]) -> str:
    """个股对比图的文字版。

    图上每条线标了「最高点/最低点/区间最大涨幅/区间最大回撤/末点累计涨跌」，
    这里逐条给出同样的读数 —— 用的是与图完全相同的 ``swing_stats``。
    """
    blocks: list[str] = []
    for rd in raw_datas:
        data = rd.get("data") or {}
        name = data.get("name") or data.get("f58") or "N/A"
        klines = list(data.get("klines") or [])
        if not klines:
            continue
        df = klines_to_df(klines)
        if df.empty:
            continue

        close = df["close"].astype(float)
        pct = normalize_pct(close) * 100
        runup, drawdown = swing_stats(pct)
        hi_loc = int(pct.idxmax())
        lo_loc = int(pct.idxmin())
        blocks.append(
            f"{name}: 末点累计 {pct.iloc[-1]:+.2f}%  收盘 {close.iloc[-1]:.2f}\n"
            f"  最高点 {pct.max():+.2f}% ({df['date'].iloc[hi_loc]})  "
            f"最低点 {pct.min():+.2f}% ({df['date'].iloc[lo_loc]})\n"
            f"  区间最大涨幅 +{runup:.2f}%  区间最大回撤 {drawdown:.2f}%"
        )

    if not blocks:
        return ""
    header = (
        "【个股对比数据】各标的按首日收盘归一化为 0%，下列百分比均为相对首日的累计涨跌幅。\n"
        "区间最大涨幅/回撤以区间内的历史谷/峰为分母（非首日价），故回撤不会超过 100%。"
    )
    return header + "\n" + "\n".join(blocks)


def single_stock_text(raw_data: dict | list[dict], is_multi: bool = False) -> str:
    """分时图的文字版（单只或多只）。"""
    if is_multi or isinstance(raw_data, list):
        items = raw_data if isinstance(raw_data, list) else [raw_data]
        parts: list[str] = []
        for rd in items:
            data = rd.get("data") or {}
            parts.append(
                f"{data.get('f58', 'N/A')}: 最新 {data.get('f43', 'N/A')}  "
                f"涨跌幅 {data.get('f170', 'N/A')}%  换手率 {data.get('f168', 'N/A')}%  "
                f"成交额 {data.get('f48', 'N/A')}"
            )
        if not parts:
            return ""
        return "【多股分时行情】\n" + "\n".join(parts)

    data = raw_data.get("data") or {}
    return (
        f"【{data.get('f58', 'N/A')} 分时行情】\n"
        f"最新价: {data.get('f43', 'N/A')}  涨跌幅: {data.get('f170', 'N/A')}%\n"
        f"开盘价: {data.get('f60', 'N/A')}  "
        f"最高价: {data.get('f44', 'N/A')}  最低价: {data.get('f45', 'N/A')}\n"
        f"换手率: {data.get('f168', 'N/A')}%  成交额: {data.get('f48', 'N/A')}  "
        f"成交量: {data.get('f47', 'N/A')}"
    )


def cloudmap_text(raw_data: dict, market: str, sector: str | None = None, top_n: int = 10) -> str:
    """云图（大盘/行业/概念）的文字版：领涨领跌 + 涨跌家数分布。"""
    data = raw_data.get("data") or {}
    diff = list(data.get("diff") or [])
    if not diff:
        return ""

    valid = [i for i in diff if i.get("f3") != "-" and i.get("f14")]
    if not valid:
        return ""
    valid.sort(key=lambda x: float(x["f3"]), reverse=True)

    title = market or "板块云图"
    if sector and market not in ("大盘云图",):
        title = f"{market} - {sector}"

    def _row(item: dict) -> str:
        return f"  {item['f14']}({item.get('f100', '-')}): {item['f3']}%"

    lines = [f"【{title}】共 {len(valid)} 个标的"]
    if len(valid) <= top_n * 2:
        # 标的太少时切 Top/Bottom 会两边重叠 —— 涨停股同时出现在「领跌」里，
        # 看不到图的 AI 只会照单全收。直接给全量排名。
        lines.append("全部（按涨跌幅降序）:")
        lines.extend(_row(i) for i in valid)
    else:
        lines.append(f"领涨 Top{top_n}:")
        lines.extend(_row(i) for i in valid[:top_n])
        lines.append(f"领跌 Top{top_n}:")
        lines.extend(_row(i) for i in valid[-top_n:][::-1])

    up = sum(1 for i in valid if float(i["f3"]) > 0)
    down = sum(1 for i in valid if float(i["f3"]) < 0)
    limit_up = sum(1 for i in valid if float(i["f3"]) >= 9.8)
    limit_down = sum(1 for i in valid if float(i["f3"]) <= -9.8)
    avg = sum(float(i["f3"]) for i in valid) / len(valid)
    lines.append(
        f"统计: 上涨 {up} 家, 下跌 {down} 家, 平盘 {len(valid) - up - down} 家；"
        f"涨停约 {limit_up} 家, 跌停约 {limit_down} 家；平均涨跌幅 {avg:+.2f}%"
    )
    return "\n".join(lines)
