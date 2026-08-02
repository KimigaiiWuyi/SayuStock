# 十、CI/CD 与本地开发流程

> **返回主入口**：[`../SKILL.md`](../SKILL.md) · **上一章**：[九、已知坑](./09-developer-pitfalls.md) · **相关**：[八、测试与质量门](./08-testing-and-quality.md)

> 本章记录 **GitHub Actions CI 怎么跑、本地如何对齐、以及已经踩过的导入/配置坑**。  
> 事实源：`.github/workflows/ci.yml`、`test/conftest.py`、`pyproject.toml`、`pyrightconfig.json`、`ruff.toml`、`.pre-commit-config.yaml`。

## 10.1 两条布局（一切问题的源头）

SayuStock 既是 **独立 GitHub 仓库**，又必须嵌在 GsCore 里才能真正跑插件：

```
# 布局 A：嵌套（本地日常开发 / Full test suite / Typecheck CI）
<gsuid_core_repo>/                    # e.g. F:\gsuid_core
├── .venv/                            # 本地 venv 通常在这里
├── gsuid_core/                       # 可 import 的包
│   └── plugins/
│       └── SayuStock/                # 本插件根（含 SayuStock/ 包、test/、pyproject）
│           ├── SayuStock/
│           ├── test/
│           └── .github/              # 注意：CI 定义在插件仓库，不在 Core
└── pyproject.toml                    # 可能含 [tool.pytest.ini_options]！

# 布局 B：扁平（GitHub 上的 SayuStock 仓库本体 / Lint / Indicator math CI）
<SayuStock_repo>/                     # e.g. actions workspace
├── SayuStock/
├── test/
├── pyproject.toml
└── .github/workflows/ci.yml
```

| 场景 | 布局 | 有没有 `gsuid_core` 源码 |
|------|------|--------------------------|
| 本地嵌套开发 | A | 有 |
| CI Lint | B | 无（也不需要） |
| CI Indicator math & AI text | B | **无**（故意轻量） |
| CI Full test suite | A（临时 checkout） | 有 |
| CI Typecheck | A（临时 checkout + submodule） | 有 |

**写测试 / 改 import 时必须同时兼容 A 和 B。** 只在本机嵌套布局里能 `import SayuStock` 不算过。

## 10.2 CI 四个 Job 一览

定义文件：`.github/workflows/ci.yml`  
触发：`push` → `main`、`pull_request`、`workflow_dispatch`  
Python：**3.12**（与 `requires-python = "==3.12.*"` 对齐）

| Job | 名称 | 布局 | 装什么 | 做什么 | 失败是否挡合并 |
|-----|------|------|--------|--------|----------------|
| `lint` | Lint (ruff) | B 扁平 | `ruff==0.14.8` | `ruff check` + `ruff format --check` | **挡** |
| `indicators` | Indicator math & AI text | B 扁平 | `pandas numpy pytest` | 只跑指标/文字相关用例 | **挡** |
| `test` | Full test suite | A 嵌套 | Core `requirements.txt`（去 `-e`）+ 插件绘图依赖 | `pytest test/` | **挡** |
| `typecheck` | Typecheck (pyright) | A 嵌套 + Kronos submodule | 同上 + **basedpyright==1.39.7** | `basedpyright` | 暂不挡（`continue-on-error: true`） |

### 10.2.1 Lint

```text
pip install ruff==0.14.8
ruff check SayuStock/ test/
ruff format --check SayuStock/ test/
```

- 版本必须与 `.pre-commit-config.yaml` 里 `ruff-pre-commit` 的 `rev`（如 `v0.14.8`）一致，避免「本地 pre-commit 过了、CI 不过」。
- 本地一键：`pre-commit run --all-files` 或 `ruff check/format`。

### 10.2.2 Indicator math & AI text（轻量门）

```text
pytest test/test_indicators.py \
       test/test_papertrade_indicators.py \
       test/test_render_text.py -q -p no:cacheprovider
```

设计意图：

- **不装 `gsuid_core`**，几十秒内反馈「指标口径 / 图与 AI 文字一致 / 回撤文字」类回归。
- 这些用例**不能**执行 `SayuStock/__init__.py`（会 `from gsuid_core.sv import Plugins` 并拉起整条注册链）。
- 路径与包壳由 `test/conftest.py` 统一处理；新写的「纯数学 / 纯文字」测也走 conftest，**不要**再 `sys.path` 手搓五层 `parents`。

### 10.2.3 Full test suite（完整门）

```yaml
# 伪结构：先 checkout Core，再把本仓放进 plugins/
path: gsuid_core/gsuid_core/plugins/SayuStock
working-directory: gsuid_core/gsuid_core/plugins/SayuStock
pytest test/ -q -p no:cacheprovider
```

要点：

1. **插件必须落在** `…/gsuid_core/plugins/SayuStock`，与本地 monorepo 层级一致。
2. 上游 `gsuid_core/requirements.txt` 是 **uv export + require-hashes**，且含一行 `-e .`：
   - require-hashes 装不了 editable；
   - `.` 相对 workspace 根也指不到 Core 包。
   - CI 做法：`grep -v '^-e' … > /tmp/…` 只装依赖；**Core 本体靠目录层级 import，不 `pip install -e`。**
3. **不要装 playwright**（出图浏览器只在运行期需要，CI 单测 mock / 不截真浏览器）。
4. 插件侧再装：`pandas mplchart matplotlib plotly pytest`。

### 10.2.4 Typecheck

- 用 **basedpyright**，不用官方 `pyright`：源码里的 `# pyright: ignore[reportAny]` 等是 basedpyright 规则，官方会当成 unknown diagnostic rule 直接炸。
- 版本钉死（workflow 里写死，如 `1.39.7`），与本地尽量一致。
- Kronos：`submodules: true`，保证 `..Kronos.model` 可解析；内容已在 `exclude` 里，不计入错误。
- **存量类型错误未清零**：job 设了 `continue-on-error: true`。PR 上仍会显示结果，但**暂时不挡合并**。清零后应去掉该行改为强制。
- 配置优先读 **`pyrightconfig.json`**（与 `pyproject.toml` 的 `[tool.pyright]` 保持同步字段）；**不要**在 `pyrightconfig.json` 写死本机 `venvPath`/`venv`（见 §10.5.3）。

## 10.3 本地开发流程（对齐 CI）

### 10.3.1 推荐日常布局

把插件 clone / submodule / junction 到：

```text
F:\gsuid_core\gsuid_core\plugins\SayuStock
```

Core venv 在 `F:\gsuid_core\.venv`（或等价路径）。激活后：

```powershell
cd F:\gsuid_core\gsuid_core\plugins\SayuStock

# 与 pre-commit / CI 一致的 lint
ruff check SayuStock/ test/
ruff format SayuStock/ test/

# 轻量指标门（不依赖 Core 也能过——靠 conftest 包壳）
python -m pytest test/test_indicators.py test/test_papertrade_indicators.py test/test_render_text.py -q

# 全量单测
python -m pytest test/ -q

# 类型检查（与 CI 同命令）
basedpyright
```

`pyproject.toml` 已配置：

```toml
[tool.pytest.ini_options]
testpaths = ["test"]
pythonpath = [".", "test"]   # "." → SayuStock 包；"test" → kline_fixtures 等
```

**作用有两层：**

1. `pythonpath` 让 `import SayuStock`、`import kline_fixtures` 在未手写 `sys.path` 的用例里可用。  
2. 提供本仓库的 `[tool.pytest.ini_options]`，**阻止 pytest 向上找到 Core 仓库的 pytest 配置**（Core 的 `testpaths = ["tests"]` 等会把 rootdir 锚到错误位置）。

### 10.3.2 改代码最小回归（与 §8.6 一致，按 CI 视角）

| 改动类型 | 本地至少跑 | 对应 CI Job |
|----------|------------|-------------|
| 纯指标 / `render_text` | indicators 三文件 | indicators |
| market parse / Port | `test/market/` | test |
| 任意业务 + 导入路径 | 全量 `pytest test/` | test |
| 风格 / import 顺序 | ruff | lint |
| 类型注解 / 公共 API | basedpyright | typecheck（目前不挡合并） |

### 10.3.3 提交前检查清单

1. `pre-commit run --all-files`（或至少 ruff check + format）  
2. 相关 pytest 绿（改指标必跑 indicators 三件套）  
3. 未把本机路径写死进测试（如 `F:\…`）  
4. 未在 `pyrightconfig.json` 加仅本机存在的 `venv`  
5. 新测试若 `import SayuStock.*`，依赖 `conftest` 包壳，**不要**假设 `SayuStock/__init__.py` 会成功执行  
6. 脚本直跑需要补 `sys.path` 时：stdlib/三方 import 放顶部，本地包 import 后置并 `# noqa: E402`（见 `utils/update_stocks.py`）

## 10.4 `test/conftest.py` 契约（写测试必读）

```text
test/conftest.py
├── 把插件根加入 sys.path
├── 若检测到嵌套布局，把 gsuid_core 仓库根加入 sys.path
└── 注册包壳 SayuStock / SayuStock.utils（不 exec __init__.py）
```

| 约定 | 说明 |
|------|------|
| **包壳，不跑 Plugins** | `SayuStock/__init__.py` 会 import `gsuid_core.sv.Plugins` 并拉 `stock_agent` / `stock_analysis` / `stock_papertrade`，单测不需要、轻量 CI 也没有 Core。 |
| **子模块正常 import** | `from SayuStock.utils.market…` 在包已在 `sys.modules` 时**不会**再执行包 `__init__`。 |
| **兼容两布局** | 嵌套时补 Core 路径；扁平时只靠插件根 + 包壳。 |
| **新测试默认受益** | 放在 `test/` 下即可；不必每个文件复制 `REPO_ROOT = parents[5]`。 |
| **例外** | 仍有一批旧用例用 `importlib` 自建 `_xxx_test` 包名（如 `test_indicators.py`）——可继续用，新代码优先 conftest。 |

**禁止：**

- 在 indicators 相关测试里 `from SayuStock import …` 触发真 `__init__`（扁平 CI 必挂）。  
- 写只认 `F:\gsuid_core\…` 的绝对路径。  
- 假定「Full suite 里 Core 已 `pip install -e`」——CI 没有，只有路径。

## 10.5 踩坑实录（CI 红了先看这里）

### 10.5.1 `ModuleNotFoundError: No module named 'gsuid_core'`（indicators / 扁平）

**现象**：`test_render_text` 等在 collection 阶段炸在 `SayuStock/__init__.py`。

**原因**：`from SayuStock.utils import …` 会先执行包 `__init__`，而轻量 job 没装 Core。

**正确做法**：依赖 `test/conftest.py` 包壳；或像 `test_indicators` 一样 importlib 搭骨架且**不要** `exec_module` 顶层 `__init__.py`。

### 10.5.2 `ModuleNotFoundError: No module named 'SayuStock'`（Full suite / market 测试）

**现象**：`test/market/test_*.py` collection 失败；其它手写 `sys.path` 的用例可能仍过。

**原因组合：**

1. market 测试没有自己插 `sys.path`。  
2. 本仓库若**没有** `[tool.pytest.ini_options]`，pytest 会向上找到 **Core 的 pyproject**，`rootdir` 变成 `gsuid_core` 仓库根 → 插件根不在 `sys.path` → 找不到 `SayuStock`。

**正确做法：**

- 保留 `pyproject.toml` 里：

  ```toml
  [tool.pytest.ini_options]
  testpaths = ["test"]
  pythonpath = [".", "test"]
  ```

- 保留 `test/conftest.py` 路径注入。  
- **不要删除**「看起来多余」的 pytest 配置——它是为了对抗 Core 的配置上浮。

### 10.5.3 basedpyright exit code 3：`venv .venv subdirectory not found`

**现象**：

```text
venv .venv subdirectory not found in venv path …/gsuid_core.
0 errors, 0 warnings, 0 notes
Error: Process completed with exit code 3.
```

**原因**：`pyrightconfig.json` 写了 `"venvPath": "../../../", "venv": ".venv"`。本地 monorepo 有该目录，CI 嵌套 checkout **没有** `.venv`（依赖装在 runner 全局/环境）。

**正确做法：**

- `pyrightconfig.json` **不要**写死 `venvPath` / `venv`。  
- 本地 IDE 用「所选 Python 解释器」即可；CI 用 setup-python + pip。  
- 若必须本地强制 venv，用用户级 / 工作区 settings，勿提交进仓库配置。

### 10.5.4 官方 pyright 报 unknown diagnostic rule

**原因**：代码里 basedpyright 专用 ignore（`reportAny`、`reportUnusedParameter` 等）。

**正确做法：** CI 与本地类型检查统一用 **basedpyright**，版本与 workflow 对齐。

### 10.5.5 安装 Core 依赖时 `-e .` / require-hashes 失败

**原因**：见 §10.2.3。

**正确做法：** 过滤 `-e` 行；不要在 CI 对 Core 做 editable install。

### 10.5.6 ruff E402：脚本里先改 `sys.path` 再 import

**场景**：`utils/update_stocks.py` 等「可被包 import、也可 `python path/to/script.py` 直跑」的文件。

**正确做法：**

```python
import os
import sys
# ... 其它 stdlib ...
import pandas as pd

# 补路径
sys.path.insert(0, ...)

from gsuid_core.logger import logger  # noqa: E402
from SayuStock.utils.constant import market_dict  # noqa: E402
```

- 与路径无关的 import（stdlib / 纯三方）放顶部。  
- 依赖路径补丁的本地包 import 后置 + `# noqa: E402`。  
- 不要为了消 E402 删掉脚本直跑所需的 path 逻辑。

### 10.5.7 pre-commit 本地过、CI lint 不过

常见原因：

- ruff 版本不一致（CI 钉版本，本地旧/新）。  
- 只跑了 `ruff check` 没跑 `ruff format --check`。  
- 改了 `test/` 但 hook 范围与 CI 的 `SayuStock/ test/` 不一致。

对齐：`.pre-commit-config.yaml` 的 `rev` ↔ CI `pip install ruff==…`。

### 10.5.8 嵌套路径 `parents[N]` 算错

历史测试常见：

```python
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
```

从 `test/foo.py` 起 5 层 parent 在**嵌套**布局下是 Core 仓库根；在**扁平** CI 布局下会指到 workspace 之外。

**新代码**：优先 `conftest` / `pythonpath`，避免魔法 `parents[N]`。若必须手写，注明假设的布局，并在两种布局下各跑一遍相关 job 的命令。

### 10.5.9 Typecheck 绿了但 exit ≠ 0

- 有类型错误 → exit 1（当前 job 不挡合并，但仍应逐步消）。  
- 缺 venv → exit 3（配置问题，必须修，见 §10.5.3）。  
- 勿把「0 errors」和「exit 0」当成一回事。

## 10.6 改 CI 本身时的注意点

1. **Python 版本**：只动 `env.PYTHON_VERSION` 与 `requires-python` 时两边一起改。  
2. **新增轻量测**：若可无 Core，考虑是否加入 `indicators` job 的文件列表（保持 job 快速）。  
3. **新增依赖 Core 的测**：只进 Full suite；确保 `conftest` 路径足够，勿在测试里 `pip` 装东西。  
4. **钉版本**：ruff、basedpyright 与 pre-commit / 本地文档同步 bump。  
5. **submodules**：只有 typecheck（或真要解析 Kronos 源码）需要 `submodules: true`；全量测试默认不必，省时间。  
6. **`continue-on-error`**：仅 typecheck 历史债；新加的挡合并门不要默认 continue。  
7. **密钥**：CI 不注入东财 Cookie；依赖 fixture 的 parse 测，禁止要求真网。

## 10.7 与章节交叉引用

| 主题 | 章节 |
|------|------|
| 测什么、fixtures、最小回归表 | [八、测试与质量门](./08-testing-and-quality.md) |
| 业务红线、有图必有文字、指标口径 | [九、已知坑](./09-developer-pitfalls.md) |
| 指标单源与 render_text | [四、渲染管线](./04-render-pipeline.md) |
| Port / 禁止 f* | [三、MarketDataPort](./03-market-data-port.md) |

## 10.8 事故速查表（CI 向）

| ID | 症状 | 处理 |
|----|------|------|
| C-1 | indicators：`No module named 'gsuid_core'` | 包壳 / 勿 exec `SayuStock/__init__` |
| C-2 | full suite：`No module named 'SayuStock'` | `[tool.pytest.ini_options]` + conftest + pythonpath |
| C-3 | basedpyright exit 3 缺 `.venv` | 去掉 pyrightconfig 的 venvPath/venv |
| C-4 | pyright unknown rule | 改用 basedpyright |
| C-5 | Core reqs 安装失败 | 过滤 `-e .`，靠路径 import Core |
| C-6 | E402 脚本 import | 分顶栏 import + noqa 后置本地 import |
| C-7 | pre-commit ≠ CI ruff | 版本与 format 检查对齐 |
| C-8 | parents[N] 仅嵌套可用 | 改 conftest / 双布局验证 |
