# SayuStock AI Tools 文档

本文档介绍 SayuStock 插件为 AI 提供的工具函数。

## 概述

SayuStock 通过 `@ai_tools` 装饰器向 AI 系统注册了以下工具函数，直接引用现有功能模块。

---

## 工具函数列表

### 1. get_stock_basic - 获取股票基本信息

**引用函数**: [`get_code_id()`](SayuStock/utils/stock/request_utils.py:40), [`get_gg()`](SayuStock/utils/stock/request.py:191)

**功能**: 获取股票实时价格和涨跌幅

**参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| stock_code | str | 是 | 股票代码或名称 |

**返回值**: 股票名称、价格、涨跌幅、换手率

---

### 2. get_market_summary - 获取大盘概览

**引用函数**: [`get_mtdata()`](SayuStock/utils/stock/request.py:90)

**功能**: 获取主要指数行情（上证指数、深证成指等）

**参数**: 无

**返回值**: 主要指数列表及涨跌幅

---

### 3. get_sector_leader - 获取领涨板块

**引用函数**: [`get_mtdata()`](SayuStock/utils/stock/request.py:90)

**功能**: 获取行业或概念板块领涨排行

**参数**:
| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| sector_type | str | 否 | "行业板块" | "行业板块" 或 "概念板块" |

**返回值**: 领涨板块及领涨股

---

### 4. get_fund_holdings - 获取基金持仓

**引用函数**: [`get_code_id()`](SayuStock/utils/stock/request_utils.py:40), [`get_fund_pos_list()`](SayuStock/utils/stock/request_utils.py:17)

**功能**: 获取基金重仓股及持仓比例

**参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| fund_code | str | 是 | 基金代码 |

**返回值**: 基金持仓列表

---

### 5. get_latest_news - 获取财经新闻

**引用函数**: [`get_news()`](SayuStock/utils/request.py:89)

**功能**: 获取雪球7x24财经新闻

**参数**:
| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| limit | int | 否 | 5 | 新闻条数 |

**返回值**: 新闻列表

---

### 6. get_crypto_prices - 获取加密货币价格

**引用函数**: [`get_all_crypto_price()`](SayuStock/utils/get_OKX.py:150)

**功能**: 获取主流加密货币行情

**参数**: 无

**返回值**: BTC、ETH等币种价格和涨跌幅

---

### 7. get_vix_index - 获取VIX指数

**引用函数**: [`get_vix()`](SayuStock/utils/stock/request.py:124)

**功能**: 获取波动率指数

**参数**:
| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| vix_type | str | 否 | "300" | 可选: 300/50/1000/kcb/cyb |

**返回值**: VIX当前值和涨跌幅

---

### 8. search_stock - 搜索股票

**引用函数**: [`get_code_id()`](SayuStock/utils/stock/request_utils.py:40)

**功能**: 根据名称搜索股票代码

**参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| query | str | 是 | 股票名称或代码 |

**返回值**: 股票代码和类型

---

### 9. get_my_watchlist - 获取用户自选

**引用函数**: [`SsBind.get_uid_list_by_game()`](SayuStock/utils/database/models.py:21), [`get_gg()`](SayuStock/utils/stock/request.py:191), [`get_vix()`](SayuStock/utils/stock/request.py:124)

**功能**: 获取用户自选股票列表及行情

**参数**: 无（自动获取当前用户）

**返回值**: 自选股票涨跌幅列表

---

## 数据来源

| 数据类型 | 来源 |
|---------|------|
| A股/港股/美股 | 东方财富 |
| 加密货币 | OKX交易所 |
| 财经新闻 | 雪球7x24 |
| VIX指数 | 中国波指 |

---

## 相关文件

- AI工具注册: [`SayuStock/stock_ai/ai_tools.py`](SayuStock/stock_ai/ai_tools.py)
- 别名和知识库: [`SayuStock/stock_ai/__init__.py`](SayuStock/stock_ai/__init__.py)
- 数据请求: [`SayuStock/utils/stock/request.py`](SayuStock/utils/stock/request.py)
- 工具函数: [`SayuStock/utils/stock/request_utils.py`](SayuStock/utils/stock/request_utils.py)
