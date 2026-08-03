# 五、AI 工具与能力代理

> **返回主入口**：[`../SKILL.md`](../SKILL.md) · **上一章**：[四](./04-render-pipeline.md) · **下一章**：[六、模拟盘](./06-papertrade.md)

SayuStock 通过三条路径服务 AI：

1. **触发器桥接**（`to_ai=`）— 用户命令变成可调工具  
2. **`@ai_tools`** — 纯函数工具（返回文本/JSON，不一定出图）  
3. **AgentNode 能力代理** — 专职研究/模拟盘子 Agent  

框架侧细节见 `gscore-development` §07；本章只写插件约定。

## 5.1 触发器 → AI（to_ai）

在 `@sv.on_command(..., to_ai="""...""")` 中写清：

- 何时调用（用户意图）  
- Args 格式  
- 限制（如 VIX 不支持日 K）  

Core 的 `trigger_bridge` 会把触发器包装成工具；执行时用 `MockBot` 收集 `bot.send` 内容。

**出图触发器**要注意：图要发得出去，同时 `ai_return` 已在 render 内注入文字。

## 5.2 `@ai_tools` 注册

主要文件：

| 文件 | 能力域示例 |
|------|------------|
| `stock_ai_func/ai_tools.py` | 大盘概览、板块热力、新闻、VIX、涨跌幅、加密、搜索… |
| `stock_papertrade/ai_tools.py` | `capability_domain="AI模拟盘"` 账户/持仓/持仓简图/交易/指标… |

```python
from gsuid_core.ai_core.register import ai_tools

@ai_tools(category="common", capability_domain="…")
async def get_vix_index(ctx: RunContext[ToolContext], …) -> str:
    """docstring 必须写在 def 下一行，不能写在 logger 之后！"""
    …
```

红线：

1. **docstring 紧贴函数头** — 否则 `__doc__ is None`，向量库只有函数名，中文永远召不回。  
2. 取数用 `get_market()`，返回语义字段或可读文本，不要吐 `f*` JSON。  
3. `category="self"` 对插件会被降级；靠 docstring + `capability_domain` + 实体别名召回。  
4. 模块必须被 import（`stock_ai_func` / `papertrade` 的 `__init__` 链）。

## 5.3 常用研报向工具名（stock_agent 白名单）

`stock_agent` 的 `tool_names` 包括但不限于：

- 出图类：`send_stock_info`、`send_my_stock`、`send_my_stock_img`、`send_cloudmap_img`、`send_stock_PB_info`  
- 数据类：`search_stock`、`get_stock_change_rate`、`get_vix_index`、`get_latest_news`、`get_crypto_prices`  
- 概览：`get_market_overview`、`get_sector_heatmap`  

名称以 `register.py` / `ai_tools` 实际注册为准；改名要同步 AgentNode 白名单。

## 5.4 `stock_agent`（研究分析代理）

注册：`stock_agent/__init__.py` → `register_agent_node(AgentNode(...))`。

- **职责**：技术面/价值面/宏观研究，**不执行实盘交易，不承接模拟盘记账**  
- **模拟盘**：明确委派 `papertrade_*` Agent / `papertrade_*` 工具；禁止 `record_*` 第二套账本  
- **交付**：结论 + 数据依据（工具/字段/数值）+ 风险  

同文件还注册：

| node_id | 用途 |
|---------|------|
| `papertrade_setup_agent` | 建账 / Kanban 树，走 trigger `send_init_command` |
| `papertrade_decision_agent` | 周期买卖决策 + 撮合写库 |
| `papertrade_pool_refresh_agent` | 候选池轮换 |
| `papertrade_snapshot_agent` | 收盘净值快照 |
| `papertrade_reporter_agent` | 月度等复盘 |

决策代理最终输出纪律：**用户侧播报由系统冒泡**；agent 侧常以 `<<NO_BROADCAST>>` 收尾（以源码为准）。

## 5.5 实体与别名

```python
from gsuid_core.ai_core.register import ai_alias, ai_entity

ai_alias("papertrade", ["模拟盘", "虚拟盘", "模拟炒股"], scope="SayuStock")
ai_entity(KnowledgeBase(id=…, content=PAPERTRADE_GUIDE.md, …))
```

- `ai_alias`：自然语言 → 能力域  
- `ai_entity`：L0 实体/知识库，帮助确定性路由到本插件  

## 5.6 Kronos 预测（`stock_ai/`）

- 命令 SV：`模型预测`  
- `draw_ai_map.draw_ai_kline_with_forecast`：`get_market().kline(..., M30)` → DataFrame → Kronos  
- **文字** `_ai_return_kronos_data(series, df)` 在缓存装饰器**之外**  
- 模型依赖 torch；测试 mock `gdf` / `render_image_by_pw`  
- `Kronos/` 为 vendored submodule，pyright exclude  

## 5.7 指标工具与 papertrade

`papertrade/ai_tools` 中 `stock_indicators` 类工具：

- 拉 `KlineSeries` → `kline_to_df` → `compute_indicators`  
- 返回 JSON 字符串给 LLM  

与图共用 `utils/indicators.py` / 或 `papertrade/indicators.py` 包装（改口径两边一起看）。

### 模拟盘自选 / 模拟盘持仓（命令 + 工具）

| 入口 | 名称 |
|------|------|
| 用户命令 | **`模拟盘自选`** / **`模拟盘持仓`**（`send_holdings`，`to_ai` 桥接，**无需 agent**） |
| `@ai_tools` | `papertrade_holdings_image`（与命令同一渲染） |

- **简化版**：账户摘要 + 持仓条（**今日涨跌** + **持仓收益率**）；**无**流水 / 决策日志。  
- **渲染**：`render.build_holdings_snapshot_image` → `draw_holdings_snapshot`；纹理复用 `stock_info/texture2d`。  
- **有图必有文字**：命令 / 工具内 `ai_return`。  
- 完整账本：`模拟盘查看` / `模拟盘记录` 或 JSON 工具。

## 5.8 加工具 checklist

1. 选文件：`stock_ai_func`（通用）或 `papertrade/ai_tools`（模拟盘）。  
2. `@ai_tools` + 完整中文 docstring。  
3. 仅 `get_market()` / 已有 db API。  
4. 需要进能力代理：加入对应 `AgentNode.tool_names`。  
5. 需要稳定召回：`capability_domain` + 必要时 `ai_alias` / 实体。  
6. 单测或手测一条；确认模块被 import。  
