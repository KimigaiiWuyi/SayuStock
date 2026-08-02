# 二、插件布局与命令层

> **返回主入口**：[`../SKILL.md`](../SKILL.md) · **上一章**：[一](./01-architecture-and-modules.md) · **下一章**：[三、MarketDataPort](./03-market-data-port.md)

## 2.1 Plugins 与前缀

```python
# SayuStock/__init__.py
Plugins(
    name="SayuStock",
    force_prefix=["a", "股票"],
    allow_empty_prefix=True,
)
```

用户可用 `a个股 茅台`、`股票个股 茅台`，或在允许空前缀时直接匹配 SV 触发词（仍受 Core 全局
前缀策略约束）。

## 2.2 SV 与装饰器

每个功能子包在 `__init__.py`（或被其 import 的模块）里：

```python
from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event

sv_xxx = SV("服务中文名", priority=…, pm=…, area=…)

@sv_xxx.on_command(("个股",), to_ai="""…给 AI 的工具说明…""")
async def send_stock_img(bot: Bot, ev: Event) -> …:
    …
    await bot.send(im)
```

常用装饰器：

| 装饰器 | 用途 |
|--------|------|
| `on_command` | 前缀命令（如「个股 xxx」） |
| `on_fullmatch` | 整句匹配（如「我的个股」） |
| `block=True` | 匹配后阻断其它触发器 |

**`to_ai=`**：把触发器暴露给 AI 工具桥。docstring/文案要写清「何时调用、Args 格式」；
空 docstring 会导致工具向量召不回（见 GsCore §工具召回）。

## 2.3 子包必须在 `__init__.py` 挂载装饰器

GsCore `load_dir_plugins` **只 import 各子目录的 `__init__.py`**，不会自动 import
`commands.py` 等同级文件。

示例：`stock_papertrade/__init__.py` 必须：

```python
from . import commands, admin, ai_tools  # noqa: F401
# 以及 ai_alias / ai_entity / recurring gate 注册
```

新增命令文件后若「发了指令没反应」，先查是否被 `__init__.py` import。

## 2.4 主要 SV 与命令地图

| SV / 子包 | 典型命令 | 渲染/服务入口 |
|-----------|----------|----------------|
| `sv_stock_cloudmap` | 大盘云图、行业云图、概念云图 | `stock_cloudmap.render_image` |
| `sv_stock_stockinfo` | 个股、我的个股 | `stock_stockinfo.render_mpl` |
| `sv_stock_compare` | 对比个股 / 个股对比 | 同上，sector=`compare-stock` |
| `sv_stock_info` | 大盘相关概览 | `stock_info/draw_*` |
| `sv_my_stock` | 我的自选相关 | 列表/卡片 |
| `sv_user_info` | 添加/删除自选 | `SsBind` |
| `sv_stock_sina` | 估值对比 | `eastmoney_value` |
| `sv_analysis` | 技术分析、股票卡片、选股、组合体检 | `stock_analysis/*` |
| `sv_stock_kronos` | 模型预测 | `draw_ai_map` |
| `sv_papertrade` | AI操盘初始化/查看/收益/记录… | `papertrade/commands` |
| `sv_papertrade_admin` | master 压测等 | `papertrade/admin` |
| `sv_stock_subscribe` | 订阅新闻 | `stock_news` |
| `sv_stock_help` | 股票帮助 | `get_help` |

> 完整触发字符串以各文件 `@sv_*.on_*` 为准；本表只做导航。

## 2.5 个股命令的 sector 约定

`stock_stockinfo` 用 **sector 字符串** 区分图种（data / render 共用）：

| sector | 含义 |
|--------|------|
| `single-stock` | 分时（可多标的空格分隔） |
| `single-stock-kline-{code}` | K 线；code 见 `MS_MAP`（5/15/30/60/100/101…106） |
| `compare-stock` | 多标的日 K 对比 |

`MS_MAP` 示例：`日k`→`101`，`周k`→`102`，`月k`→`103`，`k线`→`100`。

解析逻辑在 `send_stock_img`：前缀命中则 K 线，否则分时。VIX 别名仅支持分时。

## 2.6 自选股

- 表：`SsBind`（`utils/database/models.py`），继承 GsCore `Bind`
- 字段：`uid` 存股票名/代码列表（`_` 拼接）、`push` 推送开关
- 读写：`SsBind.get_uid_list_by_game` / `delete_uid` / `update_data`
- 工具函数：`utils.utils.convert_list` 合并无点片段

「我的个股」最多取前 5 只拼成空格分隔字符串再 `render_image(..., "single-stock")`。

## 2.7 帮助系统

```python
# stock_help/__init__.py
register_help("SayuStock", f"{get_plugin_available_prefix('SayuStock')}帮助", Image.open(ICON))
```

帮助内容来自 `help.json` + `get_help.py` 出图。新增用户可见命令时应同步帮助条目。

## 2.8 定时任务（插件内）

| 位置 | 调度 | 行为 |
|------|------|------|
| `stock_cloudmap/__init__.py` | cron 00:20 | 按 `stock_cache_retention_days` 清理 `DATA_PATH` 过期文件 |
| papertrade | Kanban + APScheduler | 交易日周期决策 / 快照 / 复盘（见 [§06](./06-papertrade.md)） |

## 2.9 新增命令的推荐步骤

1. 在对应子包 `__init__.py`（或被其 import 的模块）加 `@sv.on_command` / `on_fullmatch`。
2. 写好 `to_ai=` 说明（中文场景、Args）。
3. 业务只调 `get_market()` 或已有 data/render 入口，**不**解析供应商字段。
4. 若出图：走 `render_data` + 现有 chart；文字用 `render_text` + `ai_return`（缓存前）。
5. 更新 `help.json`（若用户可见）。
6. 补 pytest 或至少本地手动验一条。
7. 若新模块文件：确保 `__init__.py` import 到装饰器。
