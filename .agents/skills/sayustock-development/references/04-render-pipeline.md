# 四、渲染管线

> **返回主入口**：[`../SKILL.md`](../SKILL.md) · **上一章**：[三](./03-market-data-port.md) · **下一章**：[五、AI 工具与能力代理](./05-ai-tools-and-agents.md)

出图链路的设计目标：**数据模型统一、计算单源、图与文字一致、缓存不丢 AI 文字。**

## 4.1 两套出图后端

| 场景 | 包 | 技术 | 产物 |
|------|-----|------|------|
| 大盘/行业/概念云图（主路径） | `stock_cloudmap/` | plotly → HTML → playwright | HTML + PNG/bytes |
| 个股分时 / K 线 / 对比 / 备用云图 | `stock_stockinfo/` | matplotlib + mplchart | PNG |
| 全天候板块 | `stock_info/draw_future.py` | pytakumi `render_html_to_bytes`（`utils/all_weather_html.py`） | PNG bytes |

两者 **共享**：

- `utils/render_data.py`（算轴、补点、指标列…）
- `utils/render_text.py`（给 AI 的文字）
- `utils/indicators.py`（指标）
- `utils/market`（取数）

`stock_*/render_data.py` 只是 re-export，**禁止再各写一份**。

## 4.2 数据服务 `CloudMapDataService`

两处实现（云图包精简版 / 个股包完整版）：

| 文件 | 覆盖 |
|------|------|
| `stock_stockinfo/data.py` | 云图 + 分时 + K 线 + 对比 + 多股 + 板块展开 |
| `stock_cloudmap/data.py` | 仅 大盘/行业/概念云图 |

```python
@dataclass
class CloudMapDataResult:
    raw_data: BoardSnapshot | IntradaySeries | KlineSeries | str
    raw_datas: list[IntradaySeries | KlineSeries]  # 多标的 / 对比
    sector: str | None
    special_cache_key: str | None
```

- `raw_data` 为 **str** → 业务错误文案，上层直接 `bot.send`  
- 否则为领域模型，进入 render  

**禁止**再往 `raw_data` 塞东财形 `{"data": {"f58": …}}`。

### fetch 分流（个股包）

| 条件 | 取数 |
|------|------|
| market ∈ 大盘云图/行业云图 | `hotmap` 或 `board(sector)` |
| 概念云图 | `sector_menu` + `board` |
| sector 以 `single-stock-kline` 开头 | `kline` + `KlinePeriod` |
| sector == `compare-stock` | 多标的 `kline(D1_YEAR)`（默认约一年日 K） |
| sector == `single-stock` | `intraday`；若名含「(板块)」则展开成分股分时 |
| sector == `single-stock-ndays-5` | `intraday(..., ndays=5)`，五日分时（图上按交易日分隔） |
| 其它 | `board(market)` |

板块展开：`_fetch_sector_codes` → 并行 `intraday`，最多约 13 只。

## 4.3 `utils/render_data.py` 入口

| 函数 | 输入 | 输出 |
|------|------|------|
| `build_single_stock_render_data` | `IntradaySeries` | `SingleStockRenderData` 或错误 str |
| `build_multi_stock_render_data` | `list[IntradaySeries]` | `MultiStockRenderData` |
| `build_kline_render_data` | `KlineSeries` | `KlineRenderData` |
| `build_compare_render_data` | `list[KlineSeries]` | `CompareRenderData` |
| `build_cloudmap_render_data` | `BoardSnapshot` + market/sector/layer | `CloudmapRenderData` |

内部会做：

- 分时：会话模板补齐（`time_range.get_trading_datetimes_bjt`）、跨天 HH:MM 锚定  
- K 线：`kline_to_cn_df` + 均线 + 缺口 breaks  
- 云图：按市值抽样、treemap path  

## 4.4 chart 层（matplotlib）

```
stock_stockinfo/
├── chart_base.py      # Agg 后端、颜色、线程画图 _draw_in_thread
├── chart_intraday.py  # 单股 / 多股分时
├── chart_kline.py     # 日K…；指标来自 utils.indicators
├── chart_compare.py   # 归一化对比 + swing_stats
├── chart_cloudmap.py  # 矩形树图（mpl 备用）
└── render_mpl.py      # 对外入口 + _emit_ai_text
```

`render_mpl.render_image_file` 伪代码：

```text
result = await CLOUDMAP_DATA_SERVICE.fetch(...)
if str: return
_emit_ai_text(...)          # 先文字
if cache fresh: return file
fig = await to_*(模型)
save PNG
```

## 4.5 云图 plotly 层

```
stock_cloudmap/
├── data.py
├── render.py          # to_fig + render_html + render_image
├── get_cloudmap.py     # 对外 facade
└── render_data.py     # re-export
```

`layer`：大盘常用 2，行业/概念常用 1（影响 treemap 深度与抽样）。

## 4.6 `utils/render_text.py`：有图必有文字

| 函数 | 输入 |
|------|------|
| `single_stock_text` | `IntradaySeries` 或 list |
| `kline_text` | `KlineSeries` + sector |
| `compare_text` | `list[KlineSeries]` |
| `cloudmap_text` | `BoardSnapshot` |

K 线文字必须覆盖图上指标：MA/BOLL/BBI/KDJ/RSI/MACD/CMF/量比/支撑压力/区间涨回撤等，
数值与 `compute_indicators` **同源**。

调用方式：

```python
from gsuid_core.ai_core.trigger_bridge import ai_return
ai_return(render_text.kline_text(series, sector))
```

## 4.7 缓存与「有图必有文字」

- 缓存键：`utils/stock/utils.get_file(market, suffix, sector, special_cache_key)`  
- 目录：`DATA_PATH`（`{res}/SayuStock/data`）  
- TTL：`STOCK_CONFIG.mapcloud_refresh_minutes`（默认 3 分钟）  
- Kronos：`@async_file_cache(minutes=150)` 缓 PNG  

**铁律**：`ai_return` / `_emit_ai_text` 必须在 **「缓存命中直接 return 文件」之前**。  
命中缓存时绘图函数体不执行——若文字写在函数体里，热缓存时 AI 零输入。

回归：`test/test_ai_text_delivery.py`（冷/热缓存两次都发文字）。

## 4.8 指标 `utils/indicators.py`

- 图表 `chart_kline` 与 `render_text` / papertrade / AI 工具共用  
- 口径：通达信式 MACD 柱（DIF-DEA）×2、国内 RSI 周期等  
- **不要**用 mplchart 自带 MACD/RSI/BBANDS 替换主读数（西方口径会漂）  

## 4.9 分时时间轴 `utils/time_range.py`

- 按 `provider_symbol` / secid 前缀选会话（A 股 / 美期 103. / 港股…）  
- 跨天品种：锚在「会话开盘日」，禁止把 HH:MM 暴力贴到「次日模板」  
- 回归：`test/test_intraday_align.py`  

## 4.10 新增一种图 checklist

1. Port 能否返回已有模型？不能则扩展 Port/adapter。  
2. `data.fetch` 增加分支，只放模型进 `CloudMapDataResult`。  
3. `render_data` 增加 `build_xxx_render_data(模型)`。  
4. `chart_xxx` 或 plotly 绘制。  
5. `render_text.xxx_text` + `render_mpl._emit_ai_text` 分支。  
6. 缓存前发文字；补测试。  
