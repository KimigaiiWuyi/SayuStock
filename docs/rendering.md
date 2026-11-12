# 图片渲染：Jinja + pytakumi

SayuStock 的行情图和业务卡片由 Jinja 模板生成 HTML/SVG，交给 Core 的公开接口
`gsuid_core.utils.html_render.render_html_to_bytes` 输出图片。
行情仍来自 `get_market()`，指标仍由 `utils/indicators.py` 计算；命令、配置和数据目录不变。
插件继续放在 `gsuid_core/plugins/SayuStock/`，无需调整 Core 的目录结构。

## 在 Core 的 Python 环境中安装

使用 Core 的自动安装依赖功能，或按插件的 `pyproject.toml` 在运行 Core 的 Python 3.12 环境中安装依赖。
绘图依赖版本由 Core 管理，插件复用 Core 的 PyTakumi 渲染实例、字体和字形缓存。
不需要安装 Node.js、npm 或单独的绘图运行时。

如提示绘图依赖缺失，先用 **Core 服务实际使用的解释器** 检查包是否能导入：

```sh
python -c "import jinja2; from gsuid_core.utils.html_render import render_html_to_bytes"
```

如果检查失败，在同一环境同步 Core 和插件依赖，随后重启 Core。导入插件或响应消息时不会下载依赖。

绘图不再启动浏览器。**Playwright 仍用于雪球自动 Cookie 获取**，使用该功能时仍需
安装对应浏览器；Core 自己的帮助、Markdown 等图片沿用 Core 的渲染入口。

## 修改页面

- `SayuStock/render/templates/`：Jinja 页面、公共样式和 SVG 模板。
- `SayuStock/render/palette.py`：颜色单源。红涨、绿跌、灰色平盘；黄色表示警告或失败。
- `SayuStock/render/charts.py`：坐标变换、路径和矩形树图几何，不计算业务指标。
- 页面模块：接收上游领域模型或有类型的展示结构，组织标签、图表和模板上下文。

```python
from SayuStock.render import render_html
from SayuStock.render.chart_page import ChartPage, Panel, page_html

page = ChartPage(
    title="示例个股", eyebrow="行情", subtitle="暂无有效分时数据",
    panels=[Panel(label="分时", height=260, axis=[], empty="行情暂缺")],
    footer="离线示例",
)
png = await render_html(page_html(page), width=1240, scale=1.5, label="个股")
```

`width` 是 CSS 像素，`scale` 是输出倍率，内容决定高度。Jinja 开启自动转义和
`StrictUndefined`；行情名称等普通文字不要标记为 `safe`。
SVG 几何只能由本地构造器生成。模板不执行 JavaScript，也不依赖浏览器布局。

`render.render_html` 只适配尺寸和输出参数；SVG 预处理、字体、字形缓存和线程调度由 Core
统一管理。插件不另建 Renderer、线程池、子进程、并发槽或绘图超时。取消请求遵循 Core 的
异步接口语义：取消等待不保证立即终止已在工作线程中的原生绘制。

Core 会适配 SVG 和 CSS；图元布局不兼容时调整模板，不绕过公开接口。比如绝对定位的 SVG
要为其容器保留高度，表格交替底色使用显式行类，不依赖 `:nth-child`。

默认输出 PNG。原先由 PIL 经 Core 转成 JPEG 的分析页面、模拟盘账户与排行，仍使用 Core
的 `PicQuality` 配置，直接由 Core 渲染接口编码为 JPEG；模拟盘持仓仍为 PNG。
实际出图失败由业务入口保留可用文字并报告，失败结果不写图片缓存。

云图默认宽度 `2500`、倍率 `2`。遇到 PyTakumi 的 64 百万像素视口限制时，沿用 Core 的
DPR 降档重试，逻辑布局宽度不变，实际图片尺寸可能小于请求倍率。

## 验证

在 Core 的 Python 环境中，从插件根目录运行真实出图测试。样图使用合成行情，不请求实时数据：

```sh
export SAYUSTOCK_REQUIRE_RENDER_RUNTIME=1
export SAYUSTOCK_RENDER_EVIDENCE_DIR=/path/to/render-evidence
python -m pytest test/ -q
```

PowerShell 使用 `$env:SAYUSTOCK_REQUIRE_RENDER_RUNTIME = '1'` 设置同名变量。
`SAYUSTOCK_RENDER_EVIDENCE_DIR` 可选，用于保存测试样图。
CI 的完整测试复用 Core 的依赖并强制实际出图；缺少依赖或渲染失败会使检查失败。

检查 PNG 中的字体、完整名称、数值精度、日期轴、末端标签和失败行；单测通过不能代替
查看图片。改变模板后更新相应页面测试，保留指标、AI 文字与冷/热缓存行为的断言。

## 页面样例

下图由测试中的合成数据生成。完整 PNG（包含空态、部分失败、三股缺测、90 根跨年日 K）
由 CI 的 `takumi-render-evidence` artifact 提供；这里保留缩放后的展示图。
Kronos 示例使用预先构造的预测数组，验收展示和缓存链路，不下载或运行模型权重。

<details><summary>我的自选：全宽表情包，下方四张指数卡</summary>

![我的自选](images/watchlist.webp)

</details>

<details><summary>K 线与股息率：共享指标、独立标签与缺测断点</summary>

![K 线](images/kline.webp)
![股息率](images/valuation.webp)

</details>

<details><summary>Kronos：回测与预测区间</summary>

![预测展示](images/forecast.webp)

</details>

<details><summary>模拟盘与技术分析</summary>

![模拟盘持仓](images/paper-holdings.webp)
![技术分析](images/analysis.webp)

</details>

## 回退

先停止 Core，切回已知可用的插件版本，按该版本的安装说明恢复依赖后再启动 Core。
保留 `data/SayuStock/` 下的配置和行情缓存，以及 Core 的自选和模拟盘数据库。
