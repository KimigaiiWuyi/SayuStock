# SayuStock AI 开发指南：注意事项、项目规范与可改进方案

> 本文档面向 AI 辅助开发者（及人类开发者），总结项目中的关键约束、隐含约定、常见陷阱，以及可落地的改进方向。

---

## 一、项目架构总览

```
SayuStock/
├── __init__.py              # 插件入口，注册 Plugins
├── __full__.py              # 全量导入（目前为空）
├── version.py               # 版本信息
├── stock_info/              # 大盘概览、基金、全天候板块
├── stock_user/              # 用户自选增删查
├── stock_cloudmap/          # 云图、个股、对比（核心可视化）
├── stock_news/              # 雪球新闻订阅与推送
├── stock_sina/              # 市盈率/市净率对比
├── stock_ai/                # Kronos AI 模型预测
├── stock_ai_func/           # AI Agent Tools 注册
├── stock_config/            # 插件配置管理
├── utils/                   # 工具层
│   ├── stock/               # 股票数据请求与缓存
│   ├── database/            # 数据库模型
│   ├── image.py             # Playwright 截图封装
│   ├── request.py           # 雪球新闻请求
│   ├── constant.py          # 全局常量字典
│   ├── models.py            # Pydantic 模型
│   └── ...
└── Kronos/                  # Git子模块：Kronos预测模型
```

**运行框架：** `gsuid_core` —— 一个异步机器人插件框架，提供：
- `SV`（Service）命令路由注册
- `Bot` + `Event` 消息上下文
- `scheduler` 定时任务
- `gs_subscribe` 订阅系统
- 内置 WebConsole (`GsAdminModel`)
- 数据库基类 (`Bind`, `sqlmodel`)

---

## 二、AI 开发必须注意的要点

### 2.1 命令路由注册的隐含规则

**文件：** 各模块 `__init__.py`

```python
sv_stock_cloudmap = SV("大盘云图")

@sv_stock_cloudmap.on_command(("大盘云图"))
async def send_cloudmap_img(bot: Bot, ev: Event):
    ...
```

**注意：**
- `SV` 的 `name` 参数仅用于日志和权限管理，**不影响命令匹配**
- `on_command` 匹配时会**自动去除前缀**（如插件配置的 `force_prefix=["a", "股票"]`）
- `on_fullmatch` 要求**完全匹配**，不能带任何额外参数
- `on_prefix` 匹配以指定文本**开头**的消息
- `block=True` 表示匹配成功后阻止后续处理器执行
- `priority` 数值越小优先级越高（默认中间值）

**常见陷阱：**
- 在 `on_command` 中，用户输入 `个股 600000` 时，`ev.text` 只包含 `600000`（命令本身被剥离）
- 在 `on_prefix` 中，用户输入 `市盈率对比 600000` 时，`ev.text` 只包含 `600000`
- 如果命令需要多参数，必须手动 `split()` 解析，框架不提供参数分割

### 2.2 股票代码解析的复杂性

**文件：** `utils/stock/request_utils.py` 的 `get_code_id()`

股票代码不是简单的字符串，涉及：
- **市场后缀**：`.h` (港股), `.hk` (港股), `.us` (美股), `.a` (A股)
- **优先级参数**：`priority="h"` 强制匹配港股
- **债券特殊处理**：`us10y`, `cn30y` 等国债代码走债券逻辑
- **点号前缀**：`0.xxxxxx` = 深市, `1.xxxxxx` = 沪市, `100.xxx` = 国际指数
- **别名映射**：`上证指数` -> `1.000001`，在 `code_id_dict` 中维护
- **VIX别名**：`300VIX` -> `vix300`，在 `VIX_LIST` 中维护

**AI 开发建议：**
- 永远不要假设用户输入的是标准代码，必须经过 `get_code_id()` 转换
- 涉及VIX查询时，先调用 `get_vix_name()` 判断是否是VIX别名
- 涉及加密货币时，先调用 `analyze_market_target()` 判断类型

### 2.3 数据缓存的双层机制

项目使用**两层缓存**：

**第一层：文件缓存（`@async_file_cache`）**
- 装饰器定义在 `utils/stock/utils.py`
- 缓存文件存储在 `DATA_PATH`（由 `resource_path.py` 定义）
- 默认缓存 150 分钟（可在装饰器参数覆盖）
- 缓存键由 `market`, `sector`, `suffix`, `sp` 组合生成文件名

**第二层：内存缓存（全局变量）**
- `MENU_CACHE`：板块菜单按天缓存
- `DC_TOKEN`：东财 Cookie
- `XUEQIU_TOKEN`：雪球 Cookie
- `NEWS`：新闻数据全局对象

**AI 开发建议：**
- 修改数据请求逻辑时，必须考虑缓存是否会导致测试时返回旧数据
- 调试时建议手动删除 `DATA_PATH` 下的缓存文件
- 新增数据接口时，优先使用 `@async_file_cache` 避免频繁请求被封

### 2.4 Playwright 的并发与资源管理

项目多处使用 Playwright：
1. `utils/image.py` - 将 Plotly HTML 截图转为 PNG
2. `utils/stock/request.py` - 获取东财 Cookie
3. `utils/request.py` - 获取雪球 Cookie

**关键约束：**
- 每次调用都 `launch()` 新浏览器实例，**没有复用**
- 截图时等待 `.plot-container` 元素出现（`wait_for_selector`）
- 浏览器参数 `--disable-blink-features=AutomationControlled` 用于反检测

**AI 开发建议：**
- 不要在高频路径中新增 Playwright 调用（启动成本高）
- 如需修改截图尺寸，注意 `render_image_by_pw()` 的 `w=0, h=0` 会使用配置默认值
- Kronos预测已很耗时（约3分钟），不要再叠加Playwright开销

### 2.5 Kronos 模型的特殊导入方式

**文件：** `stock_ai/draw_ai_map.py`

```python
@contextmanager
def temp_sys_path(path: str):
    old_path = list(sys.path)
    sys.path.insert(0, path)
    try:
        yield
    finally:
        sys.path[:] = old_path

base_dir = Path(__file__).parent
kronos_dir = base_dir.parent / "Kronos"

with temp_sys_path(str(kronos_dir)):
    from ..Kronos.model import Kronos, KronosPredictor, KronosTokenizer
```

**注意：**
- Kronos 是 Git 子模块，路径为 `SayuStock/Kronos/`
- 使用 `sys.path` 临时注入是因为 Kronos 内部使用**相对导入**，不能直接作为子包导入
- 模型从 HuggingFace 下载：`NeoQuasar/Kronos-mini` 和 `NeoQuasar/Kronos-Tokenizer-base`
- 预测在 **CPU** 上运行，每次预测约 3 分钟
- 使用全局队列 `NOW_QUEUE` 做并发控制（单队列，最多1个预测任务）

**AI 开发建议：**
- 不要重构此导入逻辑，除非同时修改 Kronos 子模块的导入结构
- 新增模型功能时，注意 `device="cpu"` 的硬编码
- 预测采样次数 `sample_count=5` 和 `max_context=512` 是性能与精度的平衡点

### 2.6 绘图与可视化的隐式约定

**颜色约定（A股传统）：**
- 红色 = 上涨 / 正值
- 绿色 = 下跌 / 负值
- 黄色 = 开盘价/基准线
- 灰色 = 无变化

**图表类型：**
- 分时图：黑色背景，白色价格线，红绿量能柱
- K线图：白色背景，红绿蜡烛图，紫色换手率，橙/蓝均线
- 云图：黑色背景，Treemap，红绿渐变（-10% 到 +10%）
- 对比图：黑色背景，多股涨跌幅百分比对比
- AI预测：白色背景，蓝色历史线，绿色回测区，橙色未来区

**AI 开发建议：**
- 新增图表必须保持红涨绿跌的约定，否则用户会困惑
- 使用 `plotly` 生成 HTML，再通过 Playwright 截图，不要直接生成图片（保证清晰度）
- 注意中文字体设置，`textfont_family="MiSans"` 在部分环境可能缺失

### 2.7 数据库操作的异步约束

**文件：** `utils/database/models.py`

```python
class SsBind(Bind, table=True):
    uid: str = Field(default=None, title="自选股票")
    push: Optional[str] = Field(default="off", ...)
```

**注意：**
- 使用 `sqlmodel` + `gsuid_core` 的数据库基类
- 所有数据库操作都是**异步**的（`async def`）
- `uid` 字段存储多个股票时用 `_` 连接（如 `600000_000001`）
- `convert_list()` 函数用于拆分/合并这种格式

**AI 开发建议：**
- 新增数据库字段时，必须继承 `Bind` 基类并设置 `table=True`
- 如需注册 WebConsole 管理页面，使用 `@site.register_admin` 装饰器
- 批量操作自选股时，注意 `convert_list()` 的合并逻辑（不含点的项会合并到前一项）

### 2.8 定时任务的时区与竞争条件

**文件：** 各模块 `__init__.py` 中的 `@scheduler.scheduled_job`

```python
@scheduler.scheduled_job("cron", hour=23, minute=0)
async def save_data_sayustock():
    await draw_info_img(is_save=True)
```

**注意：**
- 使用 `gsuid_core.aps.scheduler`（基于 APScheduler）
- 时区默认跟随系统，未显式设置
- 多个定时任务可能同时触发，但项目未使用分布式锁
- 新闻推送任务每5分钟执行一次，内部有 `asyncio.sleep(15 + random.random() * 10)` 做抖动

**AI 开发建议：**
- 新增定时任务时，考虑是否需要在任务内部加锁（如文件锁或数据库锁）
- 高频任务（如分钟级）注意对上游API的调用频率限制

---

## 三、项目代码规范

### 3.1 现有规范（已遵守的）

1. **类型注解**：大量使用 `typing` 模块（`Dict`, `List`, `Optional`, `Union`, `Tuple`）
2. **异步编程**：所有IO操作使用 `async/await`
3. **日志记录**：统一使用 `gsuid_core.logger.logger`，分级为 `info`, `debug`, `warning`, `error`, `success`
4. **常量集中**：市场代码、字段映射、错误文本集中在 `constant.py`
5. **配置分离**：可配置项放在 `stock_config/config_default.py`
6. **ruff 格式化**：项目配置了 `ruff.toml`

### 3.2 规范缺陷（需要改进的）

1. **魔法数字泛滥**：
   ```python
   # 不好的示例
   if len(uid) > 5: uid = uid[:5]
   if len(uid) > 12: uid = uid[:12]
   ```
   这些限制值（5, 12）没有命名常量。

2. **错误码不统一**：
   - 有的返回 `-999`
   - 有的返回 `-400016`
   - 有的返回字符串错误信息
   - 有的返回 `ErroText` 字典中的值

3. **函数职责过重**：
   - `render_html()` 超过 150 行，同时处理数据获取、缓存检查、图表路由、文件写入
   - `gdf()` 超过 280 行，包含数据准备、模型推理、绘图、布局

4. **缺少 Docstring**：
   - 大量函数只有类型注解，没有文档字符串
   - 复杂的正则表达式没有注释说明

5. **全局状态管理粗糙**：
   ```python
   NOW_QUEUE = []  # AI预测队列
   NOW_QUEUE = 0   # HTTP请求并发计数
   MENU_CACHE = {} # 菜单缓存
   ```

6. **异常处理不一致**：
   - 有的用 `try/except Exception` 全捕获
   - 有的让异常直接抛出
   - 有的返回错误字符串代替抛出异常

---

## 四、可改进方案（按优先级排序）

### 4.1 🔴 高优先级：稳定性与性能

#### 改进1：统一错误处理与返回码

**现状问题：** 调用方需要判断返回值是 `Dict`, `str`, `int`, 还是 `Path`。

**建议方案：**
```python
from enum import Enum
from dataclasses import dataclass

class ErrorCode(Enum):
    SUCCESS = 0
    NETWORK_ERROR = -1
    API_RATE_LIMIT = -2
    STOCK_NOT_FOUND = -3
    DATA_NOT_ENOUGH = -4
    PREDICTION_QUEUE_FULL = -5

@dataclass
class Result:
    code: ErrorCode
    data: Any = None
    message: str = ""
    
    @property
    def is_ok(self) -> bool:
        return self.code == ErrorCode.SUCCESS
```

**收益：** 调用方可以用统一模式处理错误，避免 `isinstance` 判断。

#### 改进2：Playwright 浏览器池复用

**现状问题：** 每次截图都启动新浏览器，耗时 1-3 秒。

**建议方案：**
```python
class BrowserPool:
    def __init__(self, max_size=2):
        self._pool = asyncio.Queue()
        self._max_size = max_size
        
    async def acquire(self):
        if self._pool.empty():
            return await self._create_browser()
        return await self._pool.get()
        
    async def release(self, browser):
        await self._pool.put(browser)
```

**收益：** 截图响应时间从 3s 降至 500ms 以内。

#### 改进3：Kronos 预测异步化与队列优化

**现状问题：** 预测阻塞在 `asyncio.to_thread(gdf, ...)`，且单队列限制吞吐量。

**建议方案：**
- 使用 `asyncio.Semaphore` 替代列表队列
- 将模型加载提取为单例（避免每次预测重新加载 tokenizer + model）
- 支持批量预测请求排队

```python
class KronosService:
    _instance = None
    _semaphore = asyncio.Semaphore(2)  # 允许2个并发
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_model()
        return cls._instance
```

**收益：** 预测吞吐量提升，内存占用降低（模型只加载一次）。

### 4.2 🟡 中优先级：代码质量

#### 改进4：提取命名常量

**需要提取的魔法数字：**
```python
# 建议新增 constants.py
MAX_WATCHLIST_STOCKS = 5      # 我的个股显示上限
MAX_COMPARE_STOCKS = 12       # 对比个股上限
MAX_LOOKBACK_WINDOW = 470     # Kronos最大回看
PREDICTION_SAMPLES = 5        # 预测采样次数
DEFAULT_PREDICTION_LEN = 30   # 默认预测长度
CACHE_TTL_MINUTES = 150       # 文件缓存时长
CLOUDMAP_REFRESH_MINUTES = 3  # 云图刷新间隔
```

#### 改进5：拆分巨型函数

**`render_html()` 拆分建议：**
```python
async def render_html(...):
    raw_data = await _fetch_data(market, sector, ...)
    if isinstance(raw_data, str):
        return raw_data
    
    fig = await _generate_figure(raw_data, market, sector)
    if isinstance(fig, str):
        return fig
        
    return await _save_html(fig, market, sector)
```

**`gdf()` 拆分建议：**
```python
def gdf(df, raw_data):
    predictor = _get_predictor_singleton()
    freq_label, pred_len = _analyze_frequency(df)
    
    backtest_data = _prepare_backtest(df, pred_len)
    backtest_preds = _run_prediction(predictor, backtest_data, "backtest")
    
    future_data = _prepare_future(df, pred_len)
    future_preds = _run_prediction(predictor, future_data, "future")
    
    return _build_figure(df, backtest_preds, future_preds, raw_data)
```

#### 改进6：增加单元测试

**建议测试覆盖：**
- `get_code_id()` 的各种输入（代码、名称、后缀、别名）
- `convert_list()` 的边界情况
- `fill_kline()` 的数据解析
- 常量字典的完整性检查

**测试框架：** `pytest` + `pytest-asyncio`

### 4.3 🟢 低优先级：功能扩展

#### 改进7：支持更多K线周期

**现状：** 支持 5/15/30/60分钟、日/周/月/季/半年/年线。

**可扩展：**
- 1分钟K线（需评估数据量）
- 120分钟K线
- 自定义周期组合

#### 改进8：AI预测结果持久化

**现状：** 预测结果只返回图片，不保存原始数据。

**建议：**
- 将预测结果（均值、min、max、时间戳）保存为 JSON
- 支持历史预测查询与准确率回测统计
- 在 WebConsole 中展示预测历史

#### 改进9：多市场同时监控

**现状：** 大盘概览只展示A股主要指数。

**建议：**
- 增加国际市场概览（美股、港股、日股）
- 增加大宗商品概览（黄金、原油）
- 增加汇率概览（美元指数、离岸人民币）

#### 改进10：配置热重载

**现状：** 配置修改后需要重启插件。

**建议：**
- 监听配置文件变化
- 或使用 WebConsole 动态修改配置

---

## 五、AI 辅助开发的最佳实践

### 5.1 修改前的检查清单

- [ ] 是否理解 `gsuid_core` 的 `SV` 路由机制？
- [ ] 是否确认命令匹配方式（`on_command` vs `on_fullmatch` vs `on_prefix`）？
- [ ] 是否处理了 `ev.text` 为空的情况？
- [ ] 是否考虑了股票代码的多种输入格式？
- [ ] 新增数据接口是否添加了 `@async_file_cache`？
- [ ] 是否使用了 `logger` 而非 `print`？
- [ ] 异步函数是否都加了 `async` 关键字？
- [ ] 是否测试了错误路径（如股票不存在、网络超时）？

### 5.2 常见 AI 生成代码的修正模式

**模式1：同步IO修正**
```python
# AI 可能生成（错误）
import requests
resp = requests.get(url)

# 应修正为（正确）
from aiohttp import ClientSession
async with ClientSession() as sess:
    async with sess.get(url) as resp:
        data = await resp.json()
```

**模式2：路径处理修正**
```python
# AI 可能生成（错误）
path = "./data/file.json"

# 应修正为（正确）
from pathlib import Path
from ..utils.resource_path import DATA_PATH
path = DATA_PATH / "file.json"
```

**模式3：数据库操作修正**
```python
# AI 可能生成（错误）
result = SsBind.get_uid_list_by_game(user_id, bot_id)

# 应修正为（正确）
result = await SsBind.get_uid_list_by_game(user_id, bot_id)
```

**模式4：缓存清理修正**
```python
# AI 可能生成（错误）
@async_file_cache(...)
async def get_data():
    return await fetch()

# 如果需要强制刷新，应修正为（正确）
# 手动删除缓存文件或添加 bypass_cache 参数
```

### 5.3 安全注意事项

1. **Cookie 安全**：`eastmoney_cookie` 和 `DC_COOKIES` 包含敏感信息，不要提交到公共仓库
2. **路径遍历**：用户输入的股票代码可能包含 `../`，需在使用前清理
3. **DoS 防护**：`NOW_QUEUE` 限制了AI预测并发，新增耗时操作也应加类似限制
4. **SQL 注入**：虽然使用 ORM，但手写 SQL 时仍需参数化查询

---

## 六、快速参考：关键文件与函数

| 需求 | 应查看的文件 | 关键函数 |
|------|------------|---------|
| 新增命令 | `stock_xxx/__init__.py` | `SV.on_command()` |
| 获取股票数据 | `utils/stock/request.py` | `get_gg()`, `get_mtdata()` |
| 代码转ID | `utils/stock/request_utils.py` | `get_code_id()` |
| 绘制图表 | `stock_cloudmap/get_cloudmap.py` | `to_single_fig()`, `to_fig()` |
| 截图输出 | `utils/image.py` | `render_image_by_pw()` |
| AI预测 | `stock_ai/draw_ai_map.py` | `draw_ai_kline_with_forecast()` |
| 注册AI工具 | `stock_ai_func/ai_tools.py` | `@ai_tools()` |
| 用户数据 | `utils/database/models.py` | `SsBind` |
| 全局常量 | `utils/constant.py` | `market_dict`, `code_id_dict` |
| 配置文件 | `stock_config/config_default.py` | `CONFIG_DEFAULT` |

---

## 七、总结

SayuStock 是一个功能丰富但代码复杂度较高的项目，核心挑战在于：
1. **多数据源整合**（东财、雪球、OKX、HuggingFace）
2. **可视化链路长**（数据 -> Plotly -> HTML -> Playwright -> PNG）
3. **AI模型集成特殊**（子模块 + sys.path hack + CPU推理）

AI 开发时应特别注意：
- 保持异步一致性
- 尊重红涨绿跌的A股约定
- 利用现有缓存机制
- 避免在高频路径引入重量级操作（如Playwright启动）
- 优先复用 `get_code_id()` 等已有工具函数，不要重新实现股票代码解析
