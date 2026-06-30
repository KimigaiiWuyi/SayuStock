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

    字段（标准行业）：
    - roe: 最新一期 ROE
    - revenue_yoy: 最新一期 营业总收入同比
    - profit_yoy: 最新一期 归属母公司净利润同比
    - gross_margin: 最新一期 毛利率
    - net_margin: 最新一期 净利率
    - debt_ratio: 最新一期 资产负债率
    - eps: 最新一期 基本每股收益
    - bps: 最新一期 每股净资产
    - report_date: 报告期 ISO 字符串

    字段（银行/保险/券商专属，识别到行业后追加）：
    - jroa: 加权平均净资产收益率（银行偏好口径，扣非）
    - net_interest_margin: 净息差（仅银行；保险/券商为 None）
    - npl_ratio: 不良贷款率（仅银行）
    - provision_coverage: 拨备覆盖率（仅银行）
    - core_capital_adequacy_ratio: 核心一级资本充足率（仅银行）

    字段（行业识别元信息）：
    - _industry_type: "standard" | "bank" | "insurance" | "broker" | "unknown"
    - _raw_keys_present: 接口真实给到的字段名（让 LLM 知道为什么某些字段是 None）
    - _gap: 本次未取到值的关键字段名（用于 LLM 决策时说明）
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

    # 行业识别 —— 看 main_financial 表里有什么特征字段
    # 银行：会有 JROA / NPL_RATIO / PROVISION_COVERAGE_RATIO / NEW_CAPITAL_ADEQUACY_RATIO
    # 保险：会有 SOLVENCY_AR / PREMIUM_INCOME
    # 券商：会有 MAIN_BUSINESS_INCOME
    industry_type: str = "standard"
    if latest.get("JROA") is not None or latest.get("NPL_RATIO") is not None:
        industry_type = "bank"
    elif latest.get("SOLVENCY_AR") is not None or latest.get("PREMIUM_INCOME") is not None:
        industry_type = "insurance"
    elif latest.get("MAIN_BUSINESS_INCOME") is not None and latest.get("NETPROFIT") is not None:
        # 券商：通常有 NETPROFIT 但缺 XSMLL 毛利率
        industry_type = "broker"

    result: dict[str, Any] = {
        "roe": _f("ROE_WEIGHTED_A"),
        "revenue_yoy": _f("TOTAL_OPERATE_INCOME_YOY"),
        "profit_yoy": _f("YSTZ") or _f("PARENT_NETPROFIT_YOY"),
        "gross_margin": _f("XSMLL") or _f("SJSGMGJ"),
        "net_margin": _f("XSLL"),
        "debt_ratio": _f("ZCFZL"),
        "eps": _f("BASIC_EPS"),
        "bps": _f("BPS"),
    }

    # 银行/保险/券商专属字段（识别到才填）
    if industry_type == "bank":
        result["jroa"] = _f("JROA")  # 银行 ROE（扣非后）
        result["net_interest_margin"] = _f("NET_INTEREST_MARGIN") or _f("JXCJ")
        result["npl_ratio"] = _f("NPL_RATIO")  # 不良率
        result["provision_coverage"] = _f("PROVISION_COVERAGE_RATIO")  # 拨备覆盖率
        result["core_capital_adequacy_ratio"] = _f("NEW_CAPITAL_ADEQUACY_RATIO") or _f("CORE_CAPITAL_ADEQUACY_RATIO")
        # 银行股 roe / revenue_yoy / gross_margin / net_margin 通常是 None，
        # 用 jroa 替代 roe 更合规
    elif industry_type == "insurance":
        result["solvency_ar"] = _f("SOLVENCY_AR")  # 偿付能力充足率
        result["premium_income"] = _f("PREMIUM_INCOME")  # 保费收入
    elif industry_type == "broker":
        result["main_business_income"] = _f("MAIN_BUSINESS_INCOME")  # 主营营收
        result["roe"] = _f("ROE_WEIGHTED_A")  # 券商 ROE 通常有

    result["report_date"] = str(latest.get("REPORT_DATE") or "")[:10]
    result["_industry_type"] = industry_type

    # 列出 main_financial 接口真实给到的字段（不管值是不是 None）——
    # 银行股 revenue_yoy/gross_margin 为 None 是因为接口表里没这列，
    # 不代表"数据缺失"。
    all_target_keys = [
        "ROE_WEIGHTED_A", "TOTAL_OPERATE_INCOME_YOY", "YSTZ", "PARENT_NETPROFIT_YOY",
        "XSMLL", "SJSGMGJ", "XSLL", "ZCFZL", "BASIC_EPS", "BPS",
        "JROA", "NET_INTEREST_MARGIN", "JXCJ", "NPL_RATIO",
        "PROVISION_COVERAGE_RATIO", "NEW_CAPITAL_ADEQUACY_RATIO", "CORE_CAPITAL_ADEQUACY_RATIO",
        "SOLVENCY_AR", "PREMIUM_INCOME", "MAIN_BUSINESS_INCOME", "NETPROFIT",
    ]
    result["_raw_keys_present"] = sorted(
        k for k in all_target_keys
        if latest.get(k) is not None and latest.get(k) != "" and latest.get(k) != "-"
    )
    # 数据缺口：列出所有 None 的关键字段，让 LLM 在决策时知道
    gap_candidates = [
        "roe", "revenue_yoy", "profit_yoy", "gross_margin",
        "net_margin", "debt_ratio", "eps", "bps",
        "jroa", "net_interest_margin", "npl_ratio", "provision_coverage",
    ]
    result["_gap"] = sorted(
        k for k in gap_candidates
        if k in result and result[k] is None
    )
    return result
