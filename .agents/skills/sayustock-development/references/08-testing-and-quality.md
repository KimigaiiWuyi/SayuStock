# 八、测试与质量门

> **返回主入口**：[`../SKILL.md`](../SKILL.md) · **上一章**：[七](./07-config-database-cache.md) · **下一章**：[九、已知坑](./09-developer-pitfalls.md)  
> **CI / 双布局 / conftest 契约**：见 [十、CI/CD 与本地开发流程](./10-cicd-and-dev-workflow.md)。

## 8.1 测试布局

```
test/
├── conftest.py                    # 双布局路径 + SayuStock 包壳（勿 exec Plugins）
├── kline_fixtures.py              # 合成 K 线字符串
├── market/
│   ├── fixtures/                  # 东财样例 JSON
│   ├── test_parse_quote.py
│   ├── test_parse_kline_board_finance.py
│   ├── test_okx_parse.py
│   └── test_composite_routing.py
├── test_render_text.py            # 文字完整性 + 与 indicators 一致（进 CI 轻量 job）
├── test_ai_text_delivery.py       # 冷/热缓存都发 ai_return
├── test_intraday_align.py         # 跨天分时轴
├── test_indicators.py             # 进 CI 轻量 job
├── test_sparkline.py              # 自选分时 SVG
├── test_my_stock_html.py          # 自选 HTML 卡片
├── test_all_weather_html.py       # 全天候 HTML
├── test_offline_cache.py          # 空 DATA_PATH 不算行情缓存
├── test_offline_card_render.py    # 本地缓存出 PNG；无 JSON 则 skip
├── test_stock_analysis_unit.py
├── test_papertrade_*.py           # 日历/撮合/策略/候选池/账户 scope…
│                                  # test_papertrade_indicators 进 CI 轻量 job
└── test_end_label_dodge.py        # 末端标注避让；假包加载 chart_base
```

## 8.2 怎么跑

推荐在 **嵌套布局**下工作（与 Full suite CI 一致）：

```powershell
cd F:\gsuid_core\gsuid_core\plugins\SayuStock
# pyproject [tool.pytest.ini_options] 已设 pythonpath = [".", "test"]
# conftest 会补 gsuid_core 仓库根；通常不必再手设 PYTHONPATH
python -m pytest test/ -q
```

对齐 CI 的三组命令：

```powershell
# 与 CI indicators job 相同
python -m pytest test/test_indicators.py test/test_papertrade_indicators.py test/test_render_text.py -q

# 与 CI full suite 相同
python -m pytest test/ -q -p no:cacheprovider

# lint
ruff check SayuStock/ test/
ruff format --check SayuStock/ test/
```

当前规模约 **465 passed**（无东财 JSON 时离线 PNG 冒烟 skip；本地缓存缺失时 `test_intraday_align` 部分 skip）。  
**为什么 Full suite 要嵌套 checkout、为什么不能 `import SayuStock` 触发 `__init__`**：见 [§10](./10-cicd-and-dev-workflow.md)。

## 8.3 分层测什么

| 层 | 测什么 | 不要测什么 |
|----|--------|------------|
| market parse | fixture JSON → 模型字段 | 真网 HTTP（CI 默认） |
| composite | query 路由到哪个 fake port | |
| render_text | 指标标签齐全、数值 ≈ compute_indicators | 真 LLM |
| ai_text_delivery | mock fetch + 真 render_image_file 缓存路径 | 真行情 |
| indicators | 纯函数数学 | |
| papertrade | 日历/撮合/策略纯逻辑 | 真下单 |

构造模型：优先直接 `Quote`/`KlineSeries`/`BoardSnapshot` dataclass；adapter 测试可用
`compat` 仅做对照。

## 8.4 静态检查

```powershell
ruff check SayuStock/ test/
ruff format --check SayuStock/ test/
basedpyright --pythonpath <Core venv>/python   # 须指向装了 Core 依赖的 3.12；见 §10.2.4
```

CI 用 **basedpyright**（钉版本），不要用官方 pyright（见 §10.5.4）。typecheck **挡合并**。

配置要点（细节见 [§10.2.4](./10-cicd-and-dev-workflow.md) / [§10.5.3](./10-cicd-and-dev-workflow.md)）：

- `pyproject.toml` `[tool.pyright]` 与 `pyrightconfig.json` 字段保持同步  
- `include = ["SayuStock"]`，`exclude` 含 `SayuStock/Kronos`  
- `extraPaths` 指向嵌套布局下的 Core  
- **`pyrightconfig.json` 禁止提交本机 `venvPath` / `venv`**（CI 无 `.venv` 会直接 exit 3）  
- ruff 版本与 `.pre-commit-config.yaml` / CI `ruff==…` 对齐  

## 8.5 代码风格红线（与 GsCore 对齐）

框架红线以 GsCore 根 `AGENTS.md` 为准。插件侧实践：

1. **禁止**用 `try/except` 吞类型错误；外部 JSON 可解析处按需收窄。  
2. **禁止**业务里 `dict.get`/`getattr` 兜底读 `f*` —— 用模型字段。  
3. 函数参数/返回值有类型注解。  
4. 可能阻塞的网络/画图用 `async` + `asyncio.to_thread`。  
5. `#` 注释宜短（≤2 行、行宽克制）。

## 8.6 改核心链路时的最小回归集

| 改动 | 必跑 |
|------|------|
| market parse / Port | `test/market/` |
| render_data / 分时轴 | `test_intraday_align` + `test_render_text` |
| ai_return / 缓存 | `test_ai_text_delivery` |
| indicators | `test_indicators` + `test_render_text` 一致性用例 |
| papertrade | 对应 `test_papertrade_*` |
| 自选 / 全天候 HTML、sparkline | `test_sparkline` / `test_my_stock_html` / `test_all_weather_html` |
| 本地缓存出 PNG | `test_offline_card_render`（无 `*_single-stock*_data.json` 必须 skip） |
| `chart_base` 相对 import / 末端标注 | `test_end_label_dodge`（collection 即校验 compat 桩） |
| 大范围重构 | 全量 `pytest test/` + `basedpyright --pythonpath <venv>/python` |

## 8.7 Fixtures 约定

- `test/market/fixtures/*.json`：真实响应裁剪，勿提交密钥 Cookie  
- `kline_fixtures.make_klines(n, seed)`：可复现 OHLC 序列  
- 测试写缓存到用户 `DATA_PATH` 时：**前后 unlink**，勿污染真实数据目录  

## 8.8 手工冒烟（改出图后）

1. `个股 茅台` / `个股 日k 茅台`  
2. `大盘云图` / `行业云图 半导体` / `概念云图 xxx`  
3. `对比个股 沪深300 中证白酒`  
4. `我的个股`（需先添加自选）  
5. 若有 AI：同命令问两次，确认第二次仍有文字（热缓存）  
