# 七、配置 / 数据库 / 缓存 / 资源路径

> **返回主入口**：[`../SKILL.md`](../SKILL.md) · **上一章**：[六](./06-papertrade.md) · **下一章**：[八、测试与质量门](./08-testing-and-quality.md)

## 7.1 资源路径

```python
# utils/resource_path.py
MAIN_PATH = get_res_path() / "SayuStock"
CONFIG_PATH = MAIN_PATH / "config.json"
DATA_PATH = MAIN_PATH / "data"   # 自动 mkdir
```

`get_res_path()` 来自 GsCore `data_store`，通常在部署数据目录下，**不要**写死盘符。

## 7.2 `STOCK_CONFIG`

```python
# stock_config/stock_config.py
STOCK_CONFIG = StringConfig("SayuStock", CONFIG_PATH, CONFIG_DEFAULT)
```

默认项见 `config_default.py`：

| 键 | 含义 | 默认 |
|----|------|------|
| ~~`papertrade_multi_group`~~ | **已废弃**（多盘制后无意义），运行时不再读 | False |
| ~~`papertrade_broadcast_group`~~ | **已废弃**，只在 v2 迁移时读一次转成 `SayuPaperBroadcastTarget` | `""` |
| `mapcloud_viewport` | 云图截图分辨率 | 2500 |
| `mapcloud_scale` | 云图放大倍数 | 2 |
| `mapcloud_refresh_minutes` | 图/数据缓存 TTL | 3 |
| `stock_cache_retention_days` | 每日清理保留天数 | 7 |
| `eastmoney_cookie` | 东财 Cookie | 内置字符串 |

读取：

```python
minutes = int(STOCK_CONFIG.get_config("mapcloud_refresh_minutes").data)
```

新增配置：在 `CONFIG_DEFAULT` 加 `Gs*Config`，消费侧「每次用时读」即可热更新（与 GsCore 插件配置习惯一致）。

## 7.3 数据库

### 自选 `SsBind`

- 继承 `Bind`，`table=True`  
- WebConsole：`SsPushAdmin`  
- 列表用 `_` 拼接；`convert_list` 处理无 `.` 的拼接段  

### 模拟盘表

见 [§06](./06-papertrade.md)；全部 SQLModel，Admin 一并注册。

### 约定

- 不写死 `__tablename__`（与 Core 习惯一致，除非表已存在特殊名）  
- 异步方法用 Core 的 session 装饰器约定  
- Schema 变更需考虑已有用户库升级  

## 7.4 文件缓存

### 行情 / 图缓存

- 路径：`DATA_PATH` 下 JSON/PNG/HTML  
- 键生成：`utils/stock/utils.get_file`、`async_file_cache` 装饰器  
- TTL：`mapcloud_refresh_minutes` 比对 mtime  
- 清理：每日 00:20 删超过 `stock_cache_retention_days` 的文件  

### 装饰器注意

`@async_file_cache` 序列化结果多为 JSON 友好结构。  
**不要**把 frozen dataclass 直接丢进需要 JSON dump 的缓存（除非装饰器已支持）；  
领域模型取数缓存在 adapter/requester 层更合适。

### Kronos

`@async_file_cache(..., minutes=150, suffix="html")` — 仅缓存出图产物，文字在外。

## 7.5 常量 `utils/constant.py`

- `ErroText`：用户可见错误短句字典（`notData` / `notStock` / `notOpen`…）  
- `market_dict` / `bk_dict`：市场/板块别名 → 东财 fs  
- `VIX_LIST`：VIX 别名映射  
- 改错误文案时保持键稳定，避免调用方 KeyError  

## 7.6 证券主数据

- `utils/load_data.py` + `chinese_stocks.json`：代码/名称解析辅助  
- `get_code_id`（`stock/request_utils.py`）：名称/代码 → secid  

解析失败时 Port 返回 `not_found` / 业务 `ErroText["notStock"]`。

## 7.7 依赖安装

README 建议：`playwright`、`plotly`、`pandas`；并执行 `playwright install`。  
`pyproject.toml` `[project].dependencies` 含 `mplchart` 等。

Core「自动安装依赖」或手动 pdm/poetry/uv 安装均可。
