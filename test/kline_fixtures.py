"""测试用的东财 K 线生成器 —— 生成**有形态**的行情，不是常数。

为什么单独抽出来：早先各测试里的夹具把成交量写死成 200000、换手率写死成 2.20，
于是：

- 画出来的图量能柱一样高、换手率是条直线，看图的人根本判断不了渲染对不对；
- 量比 = 今日量 / 前5日均量 恒等于 1.00，这条指标等于没测；
- 换手率极值标注（``is_max`` 的 rolling max）在常数序列上全是极值，也等于没测；
- CMF 的成交量加权在常数量下退化。

这里按真实行情的统计特征造数：价格随机游走，**成交量对数正态 + 与当日振幅正相关
+ 偶发放量**，换手率由成交量除以流通股本得到（两者本就同源）。
"""

import numpy as np

__all__ = ["make_klines"]

# 假想流通股本，用来把成交量换算成换手率（%）
_FLOAT_SHARES = 8e6


def make_klines(
    n: int = 120,
    seed: int = 5,
    *,
    start: str = "2025-01-02",
    drift: float = 0.001,
    vol: float = 0.02,
    minute: bool = False,
) -> list[str]:
    """生成 n 根东财格式 K 线字符串。

    格式：``日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率``

    Args:
        n: 根数。
        seed: 随机种子，保证可复现。
        start: 起始日期。
        drift/vol: 日收益的漂移与波动。
        minute: True 时首列带 ``HH:MM``（分钟 K 的格式）。
    """
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = 100 * np.cumprod(1 + rets)

    opens = np.empty(n)
    opens[0] = close[0]
    opens[1:] = close[:-1]
    # 日内影线
    high = np.maximum(opens, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(opens, close) * (1 - np.abs(rng.normal(0, 0.006, n)))

    # 成交量：对数正态底噪 × 振幅放大，再叠加 5% 概率的放量日
    volume = rng.lognormal(mean=12.0, sigma=0.35, size=n)
    volume *= 1 + 3.0 * np.abs(rets)
    spike = rng.random(n) < 0.05
    volume[spike] *= rng.uniform(2.5, 5.0, int(spike.sum()))
    volume = np.round(volume)

    amount = volume * close
    turnover = volume / _FLOAT_SHARES * 100.0
    amplitude = (high - low) / np.where(opens == 0, 1, opens) * 100.0
    chg_pct = np.zeros(n)
    chg_pct[1:] = (close[1:] / close[:-1] - 1) * 100.0
    chg_amt = close - opens

    days = np.arange(n)
    out: list[str] = []
    base = np.datetime64(start)
    for i in range(n):
        ts = base + np.timedelta64(int(days[i]), "D")
        stamp = f"{ts} 10:00" if minute else str(ts)
        out.append(
            f"{stamp},{opens[i]:.2f},{close[i]:.2f},{high[i]:.2f},{low[i]:.2f},"
            f"{volume[i]:.0f},{amount[i]:.0f},{amplitude[i]:.2f},"
            f"{chg_pct[i]:.2f},{chg_amt[i]:.2f},{turnover[i]:.2f}"
        )
    return out
