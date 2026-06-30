"""SayuStock 财报 / F10 数据接口。

在 EastMoneyRequester 单例上追加 4 个方法（利润表 / 资产负债表 / 现金流量表 /
主要财务指标），均复用 datacenter-web.eastmoney.com 域名 + 1440 分钟缓存。
所有公开方法只接受 str code（6 位数字代码），不强制 secid 解析——因为报表接口
``SECURITY_CODE`` 字段就是纯 6 位代码。

调用范式::

    income = await EASTMONEY_REQUESTER.get_income_statement("600519")
    main = await EASTMONEY_REQUESTER.get_main_financial("600519")
"""

from typing import Any, Dict, List, Literal, Optional, Union

from gsuid_core.logger import logger

from .stock.utils import async_file_cache
from .eastmoney import EASTMONEY_REQUESTER  # noqa: F401  复用单例

# 报表类型字面量
FinanceReport = Literal[
    "RPT_F10_FINANCE_GINCOMEDATA",      # 利润表
    "RPT_F10_FINANCE_GBALANCEATA",      # 资产负债表
    "RPT_F10_FINANCE_GCASHFLOWSTA",     # 现金流量表
    "RPT_F10_FINANCE_MAINFINADATA",     # 主要财务指标
]

# 缓存用 sector key
_REPORT_KEY = {
    "RPT_F10_FINANCE_GINCOMEDATA": "income",
    "RPT_F10_FINANCE_GBALANCEATA": "balance",
    "RPT_F10_FINANCE_GCASHFLOWSTA": "cashflow",
    "RPT_F10_FINANCE_MAINFINADATA": "mainfin",
}


def _make_decorator(report: FinanceReport):
    """为每种报表生成一个带独立 sector key 的 async_file_cache 装饰器"""
    return async_file_cache(
        market="{code}",
        sector=f"eastmoney-{_REPORT_KEY[report]}",
        suffix="json",
        sp="200",
        minutes=1440,
    )


@_make_decorator("RPT_F10_FINANCE_GINCOMEDATA")
async def get_income_statement(code: str) -> List[Dict[str, Any]]:
    """获取利润表（按 REPORT_DATE 倒序）"""
    return await _fetch_finance_report(code, "RPT_F10_FINANCE_GINCOMEDATA")


@_make_decorator("RPT_F10_FINANCE_GBALANCEATA")
async def get_balance_sheet(code: str) -> List[Dict[str, Any]]:
    """获取资产负债表"""
    return await _fetch_finance_report(code, "RPT_F10_FINANCE_GBALANCEATA")


@_make_decorator("RPT_F10_FINANCE_GCASHFLOWSTA")
async def get_cash_flow(code: str) -> List[Dict[str, Any]]:
    """获取现金流量表"""
    return await _fetch_finance_report(code, "RPT_F10_FINANCE_GCASHFLOWSTA")


@_make_decorator("RPT_F10_FINANCE_MAINFINADATA")
async def get_main_financial(code: str) -> List[Dict[str, Any]]:
    """获取主要财务指标（ROE / EPS / 资产负债率 / 毛利率 / 净利率 / 营收同比）"""
    return await _fetch_finance_report(code, "RPT_F10_FINANCE_MAINFINADATA")


async def _fetch_finance_report(code: str, report: FinanceReport) -> List[Dict[str, Any]]:
    """统一报表请求：datacenter-web.eastmoney.com/api/data/v1/get"""
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": report,
        "columns": "ALL",
        "filter": f'(SECURITY_CODE="{code}")',
        "pageNumber": "1",
        "pageSize": "200",
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
        "source": "WEB",
        "client": "WEB",
    }
    resp = await EASTMONEY_REQUESTER.stock_request(url, params=params)
    if isinstance(resp, int):
        logger.warning(f"[SayuStock][Finance] {report}({code}) 请求失败, code={resp}")
        return []
    if not isinstance(resp, dict):
        return []
    result = resp.get("result") or {}
    rows = result.get("data") or []
    return [r for r in rows if isinstance(r, dict)]


# ============================================================
# 便捷：从 main_financial 抽取最新一期关键指标
# ============================================================
async def get_financial_snapshot(code: str) -> Dict[str, Any]:
    """从 main_financial 抽取最近 1~4 期的关键指标，返回扁平 dict。

    字段：
    - roe: 最新一期 ROE
    - revenue_yoy: 最新一期 营业总收入同比
    - profit_yoy: 最新一期 归属母公司净利润同比
    - gross_margin: 最新一期 毛利率
    - net_margin: 最新一期 净利率
    - debt_ratio: 最新一期 资产负债率
    - eps: 最新一期 基本每股收益
    - bps: 最新一期 每股净资产
    - report_date: 报告期 ISO 字符串
    """
    rows = await get_main_financial(code)
    if not rows:
        return {}
    latest = rows[0]

    def _f(key: str) -> Optional[float]:
        v = latest.get(key)
        if v is None or v == "" or v == "-":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {
        "report_date": str(latest.get("REPORT_DATE") or "")[:10],
        "roe": _f("ROE_WEIGHTED_A"),
        "revenue_yoy": _f("TOTAL_OPERATE_INCOME_YOY"),
        "profit_yoy": _f("YSTZ") or _f("PARENT_NETPROFIT_YOY"),
        "gross_margin": _f("XSMLL") or _f("SJSGMGJ"),
        "net_margin": _f("XSLL"),
        "debt_ratio": _f("ZCFZL"),
        "eps": _f("BASIC_EPS"),
        "bps": _f("BPS"),
    }
