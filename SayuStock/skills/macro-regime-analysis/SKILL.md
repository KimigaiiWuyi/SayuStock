---
name: macro-regime-analysis
description: 股票 / 模拟盘的「宏观定调」技能。做模拟盘买卖决策、持仓分析、个股研究、写研报之前，先用它判断「现在钱多还是钱少、市场处于成长相还是价值相、有哪些重大事件在进行」，得出 进攻/中性/防御 三档仓位结论。用户问「现在能不能加仓 / 宏观怎么看 / 关税战怎么办 / 油价涨了买什么」时也用它。
---

# 宏观定调（Macro Regime Analysis）

## 何时使用

- 模拟盘决策心跳：**每轮 Phase 3 必用**，先定档再看个股。
- 「持仓分析」「个股分析」「写研报」：开头先给一段宏观档位，再谈技术 / 估值。
- 用户直接问宏观：现在钱多还是钱少、成长还是价值、某件大事对 A 股影响。

不要用它回答纯技术问题（MACD 金叉没金叉）或纯公司问题（某公司毛利率）。

## 步骤（照做即可，不需要自己发明框架）

1. `macro_event_list(status="open")` → 有哪些大事在进行。表为空 ≠ 没事，继续第 2 步。
2. `get_market_overview` + `get_latest_news(limit=8)` + `get_vix_index`（可选）→ 看大盘、快讯、恐慌度。
3. 需要油价 / 美债 / 美元 / 国债 / PPI 的最新方向时：优先插件行情工具
   （`search_stock` 定代码 → `stock_indicators` / `get_stock_change_rate`），
   拿不到再 `web_search_tool`，query 写具体（例：`布伦特原油 价格 本周`、`10年期美债收益率 今日`）。
4. 回答「宏观三问」（见 `references/05-papertrade-macro-procedure.md`）：
   - 钱多还是钱少？
   - 成长相 / 价值相 / 轮动相？
   - 表里每件 open 事件：还能更坏吗？到顶了吗？
5. 定档：进攻 / 中性 / 防御 + 一句依据；写成 `宏观:{档位}|{相}|{依据}`。
6. 发现表里没有的大事（关税 / 战争 / 央行 / 油价急变 / 重大政策）→ `macro_event_upsert` 登记。
7. 判断拿不准时 `search_cognition("宏观 <关键词>")` 回想知识库里的规则，不要凭直觉编。

## 结论怎么用

- 档位决定仓位上限，个股逻辑不能越档（规则见 `references/05`）。
- 防御档只买高股息 / 公用 / 必需消费 / 上游资源；进攻档才主攻成长与科技。
- 事件到「政策顶 = 情绪底」时，允许对被错杀宽基 / 龙头做 ≤5% 试探反向。

## 参考资料（按需 `search_cognition` 或直接读）

| 文件 | 内容 |
|------|------|
| `references/01-money-liquidity-ppi.md` | 钱多钱少怎么判：垄断要素、油价、PPI、汇率三支柱 |
| `references/02-market-regime-rotation.md` | A 股三相、大类资产轮动、成长牛 / 价值牛的下一站 |
| `references/03-oil-gold-fed-stagflation.md` | 油价与滞胀、金油比、美联储双约束、美国两条路径 |
| `references/04-event-reversal-playbook.md` | 重大事件反转规律、三层信息可得性、左侧价值右侧成长 |
| `references/05-papertrade-macro-procedure.md` | 模拟盘 / 持仓分析的三问三档操作手册与写法示例 |

资料是「沧海一土狗」2026 年宏观系列与「充电频道」投资心得的**思想提炼**，不是原文；
所有数字（油价、利率、PPI）以工具实时返回为准，资料里的数字只是历史例子。
