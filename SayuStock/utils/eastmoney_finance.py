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
    """从 main_financial 抽取最近一期的关键指标，返回扁平 dict。

    ⚠️ 2026-07-02 重大修复：``RPT_F10_FINANCE_MAINFINADATA`` 报表的真实字段名
    与旧实现假设的（``ROE_WEIGHTED_A`` / ``TOTAL_OPERATE_INCOME_YOY`` /
    ``BASIC_EPS`` …）**完全不同**，导致 roe / revenue_yoy / profit_yoy /
    net_margin / eps **对所有股票恒为 None**（不是"部分股票拿不到"，是全拿不到）。
    这里改用接口真实字段名（``ROEJQ`` / ``TOTALOPERATEREVETZ`` /
    ``PARENTNETPROFITTZ`` / ``XSJLL`` / ``EPSJB`` …）。

    字段（所有行业通用，本报表就是一张跨行业主指标表）：
    - roe: 加权净资产收益率（``ROEJQ``，%）
    - revenue_yoy: 营业总收入同比（``TOTALOPERATEREVETZ``，%）
    - profit_yoy: 归母净利润同比（``PARENTNETPROFITTZ``，%）
    - gross_margin: 销售毛利率（``XSMLL``，%；银行/保险/券商无此口径 → None 正常）
    - net_margin: 销售净利率（``XSJLL``，%）
    - debt_ratio: 资产负债率（``ZCFZL``，%）
    - eps: 基本每股收益（``EPSJB``，元）
    - bps: 每股净资产（``BPS``，元）
    - report_date: 报告期 ISO 字符串

    字段（银行专属，识别到才追加）：
    - net_interest_margin: 净息差（``NET_INTEREST_MARGIN``，仅银行本报表有此列）

    字段（行业识别元信息）：
    - _industry_type: "standard" | "bank"
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

    # 行业识别 —— 本报表里唯一稳定的行业特征列是 NET_INTEREST_MARGIN（净息差），
    # 只有银行会给值；NPL_RATIO / JROA / 拨备 / 资本充足率 / 偿付能力等在本报表
    # **根本没有这些列**（它们在专门的 F10 银行指标接口里），旧实现按它们判行业
    # 永远命中不到 → 所有股票都被判成 standard。这里只区分 bank / standard。
    industry_type: str = "bank" if _f("NET_INTEREST_MARGIN") is not None else "standard"

    result: dict[str, Any] = {
        "roe": _f("ROEJQ"),
        "revenue_yoy": _f("TOTALOPERATEREVETZ"),
        "profit_yoy": _f("PARENTNETPROFITTZ"),
        "gross_margin": _f("XSMLL"),
        "net_margin": _f("XSJLL"),
        "debt_ratio": _f("ZCFZL"),
        "eps": _f("EPSJB"),
        "bps": _f("BPS"),
    }

    # 银行专属：净息差（本报表唯一给到的银行特色指标）。银行的 gross_margin
    # 为 None 是正常的（银行没有"毛利率"口径），roe / net_margin / debt_ratio
    # 一样从通用列取得，无需特殊处理。
    if industry_type == "bank":
        result["net_interest_margin"] = _f("NET_INTEREST_MARGIN")

    result["report_date"] = str(latest.get("REPORT_DATE") or "")[:10]
    result["_industry_type"] = industry_type

    # 列出 main_financial 接口真实给到的字段（不管值是不是 None）——
    # 让 LLM 知道某字段为 None 是"接口没这列"还是"这期没数据"。
    all_target_keys = [
        "ROEJQ", "TOTALOPERATEREVETZ", "PARENTNETPROFITTZ",
        "XSMLL", "XSJLL", "ZCFZL", "EPSJB", "BPS", "NET_INTEREST_MARGIN",
    ]
    result["_raw_keys_present"] = sorted(
        k for k in all_target_keys
        if latest.get(k) is not None and latest.get(k) != "" and latest.get(k) != "-"
    )
    # 数据缺口：列出所有 None 的关键字段，让 LLM 在决策时知道
    gap_candidates = [
        "roe", "revenue_yoy", "profit_yoy", "gross_margin",
        "net_margin", "debt_ratio", "eps", "bps", "net_interest_margin",
    ]
    result["_gap"] = sorted(
        k for k in gap_candidates
        if k in result and result[k] is None
    )
    return result
