"""模拟盘 · 主动消息播报 builder + 副作用 Δ 计算（共享模块）。

本模块从原 ``admin.send_dry_run`` 内嵌的 ``_build_proactive_text`` +
``_snapshot`` / ``_delta`` 抽出，目的：

  1. **三条调用方共享同一段拼装逻辑**——
     - ``commands._kick_immediate_decision``：init-time 立即踢一次决策后，
       算 Δ → 拼文本 → ``emit_proactive_message`` 推群（**这就是用户 7-1 踩到的
       缺失播报的修复点**：原代码 fire-and-forget 调 ``run_capability_agent``
       但从不消费返回值，所以买入京东方A 后群消息是哑的）。
     - ``admin.send_dry_run``：压测 5 段决策流的播报构建。
     - 未来 cron-path 想要更结构化的播报也可直接复用。

  2. **避开在 admin.py 留"重复定义"的技术债**——之前 build_proactive_text /
     snapshot / delta 全是 ``send_dry_run`` 内的局部函数；外部无法复用，
     init-time kick 想用就只能整段复制粘贴。

不覆盖的链路：
  - ``kanban_executor._run_one_task_node`` 在 cron 触发后调
    ``_persona_relay + _notify`` 推群——那条链路走的是 persona 转译而非
    "📈 模拟盘·操盘播报"结构化文本，本 builder 不参与。

设计要点：
  - ``variant`` 参数而非 step_no 数字——把"② / ③ / ④ / ⑤ / ⑥"这种"压测段编号"
    换成有语义的文案模板名（``auto`` / ``force_buy`` / ``force_sell`` /
    ``force_hold`` / ``kb_web``），压测方只在 "force_*" / "kb_web" 自爆身份，
    生产路径永远走 "auto"。
  - "auto" variant 文案不带 ② 数字前缀——避免生产群消息里出现测试段编号。
  - 所有 DB 读取失败 / 账户不存在时，回退到调用方传入的 ``fallback_text``；
    调用方再决定发不发（这层 policy 由调用方掌握）。
"""

from __future__ import annotations

import re
import json
from typing import Tuple, Literal, Optional

from . import db as _db
from ..utils.database.papertrade_models import (
    SayuPaperTrade,
    SayuPaperAccount,
    SayuPaperDecision,
    SayuPaperPosition,
)

# ============================================================
# 类型：variant 文案模板选择
# ============================================================
Variant = Literal[
    "auto",       # 自主决策（生产路径主用；action 自 latest_decision 推断）
    "force_buy",  # 强制买入（仅 admin.send_dry_run 用）
    "force_sell", # 强制平仓（仅 admin.send_dry_run 用）
    "force_hold", # 强制持币（仅 admin.send_dry_run 用）
    "kb_web",     # KB + Web 通路验证（仅 admin.send_dry_run 用）
]


# ============================================================
# 常量：reason 净化白名单 + indicator key 白名单
# ============================================================
# papertrade_decision_insert 在 ai_tools.py 那一层已经把 reason 限到 200；
# 这里再做一道"删违规嵌入段"防止 LLM 在 reason 里塞行情 / 持仓 / JSON 块。
_REASON_DROPS: tuple[str, ...] = (
    "账户现金",
    "📊",
    "📈",
    "💰",
    "当前持仓",
    "剩余持仓",
    "持仓已清空",
    "持仓变动",
    "decision_id=",
    "trade_id=",
    "pos_id=",
    "\n📋",
    "\n🔔",
    "\n## ",
    "---",
)
_REASON_DISPLAY_LIMIT: int = 200

# 白名单 indicator key（财报 + 技术 + 银行股专属）
# 2026-07-02 调整：财报字段与 get_financial_snapshot 真实输出对齐（旧的
# jroa/npl_ratio/provision_coverage/core_capital_adequacy_ratio 在东财
# MAINFINADATA 接口里根本没有，永远 None，已从财报 snapshot 移除）。
_INDICATOR_KEYS_WHITELIST: tuple[str, ...] = (
    # 财报（跨行业通用）
    "roe", "revenue_yoy", "profit_yoy", "gross_margin", "net_margin",
    "debt_ratio", "eps", "bps",
    # 财报（银行专属：净息差）
    "net_interest_margin",
    # 技术（已有）
    "ma5", "ma20", "ma60", "rsi6", "rsi14", "macd_dif",
    # 技术（P0 新增）
    "boll20_mid", "boll20_upper", "boll20_lower",
    "boll20_bandwidth", "boll20_pct_b",
    "boll60_mid", "boll60_bandwidth", "boll_opening_ratio_short_vs_mid",
    "cci14", "bbi",
)


# ============================================================
# 副作用 Δ 工具（从 admin.py 抽出）
# ============================================================
async def snapshot_decision_state(
    group_id: str, bot_id: str
) -> Tuple[int, int, int]:
    """拿当前群在该 bot 上的 ``(trades, positions, decisions)`` 计数快照。

    返回 ``(trades_count, positions_count, decisions_count)``；任何异常退回
    ``(0, 0, 0)`` 让调用方走 fallback。
    """
    try:
        t = await _db.PaperTradeRepo.list_by_account(group_id, bot_id, limit=200)
        p = await _db.PaperPositionRepo.list_by_account(group_id, bot_id)
        d = await _db.PaperDecisionRepo.list_recent(group_id, bot_id, limit=200)
        return (len(t), len(p), len(d))
    except Exception:
        return (0, 0, 0)


async def decision_state_delta(
    baseline: Tuple[int, int, int],
    group_id: str,
    bot_id: str,
) -> Tuple[int, int, int]:
    """``snapshot - baseline``，返回本轮的 trades / positions / decisions Δ。"""
    cur = await snapshot_decision_state(group_id, bot_id)
    return (cur[0] - baseline[0], cur[1] - baseline[1], cur[2] - baseline[2])


# ============================================================
# 内部 helper：reason 净化 + indicators 摘要 + 持仓格式化
# ============================================================
def _clean_reason(reason: str) -> str:
    """把 LLM 写的 reason 净化为可推群的"决策理由"行。

    防御：
      1. 删违规嵌入段（避免群消息糊成一坨）；
      2. 控制字符归一 + 多空格压缩；
      3. 上限 200 字。
    """
    raw: str = reason or ""
    for marker in _REASON_DROPS:
        idx = raw.find(marker)
        if idx >= 0:
            raw = raw[:idx]
    raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) > _REASON_DISPLAY_LIMIT:
        return raw[:_REASON_DISPLAY_LIMIT].rstrip() + "…"
    return raw


def _indicator_summary(indicators: str) -> str:
    """从 ``papertrade_decision_insert.indicators`` (JSON 字符串) 取白名单字段拼摘要。

    任何解析失败 / 非 dict 形态 / 空都返回 ``""``，让 caller 不写"行情快照"行。
    """
    if not indicators:
        return ""
    try:
        ip = json.loads(indicators)
    except Exception:
        return ""
    if not isinstance(ip, dict):
        return ""
    parts: list[str] = []
    for k in _INDICATOR_KEYS_WHITELIST:
        if k in ip and ip[k] is not None:
            v = ip[k]
            if isinstance(v, bool):
                parts.append(f"{k}={'✓' if v else '✗'}")
            elif isinstance(v, (int, float)):
                parts.append(f"{k}={v:.3g}")
            else:
                # 字符串值（行业类型等）截断到 20 字
                parts.append(f"{k}={str(v)[:20]}")
    if parts:
        return " · ".join(parts[:10])
    # 兜底：白名单都没命中（如 LLM 把市场环境 dict 直接塞进 indicators）
    extra = [k for k in ip.keys() if not str(k).startswith("_")][:8]
    if extra:
        return f"（{len(ip)} 个字段，白名单未命中，键名={','.join(extra)}）"
    return "（指标为空）"


def _format_positions(positions: list[SayuPaperPosition], max_show: int = 5) -> str:
    """把持仓列表拼成"  - 000001 平安银行 × 100 @ ¥10.50"格式的多行文本。"""
    return "\n".join(
        f"  - {pp.stock_code} {pp.stock_name} × {pp.qty:,} @ ¥{pp.avg_cost:.2f}"
        for pp in positions[:max_show]
    )


# ============================================================
# 主入口：build_papertrade_proactive_text
# ============================================================
async def build_papertrade_proactive_text(
    group_id: str,
    bot_id: str,
    *,
    variant: Variant,
    trades_d: int,
    positions_d: int,
    decisions_d: int,
    fallback_text: str = "",
) -> str:
    """根据 ``variant`` + DB 当前状态拼"📈 模拟盘·操盘播报"格式文本。

    Args:
        group_id / bot_id: 定位账户。
        variant: ``"auto"``（生产）/ ``"force_buy"`` 等（压测）。
        trades_d / positions_d / decisions_d: 本轮副作用 Δ 计数（用于决定
            是否展示 recent trade / position）。
        fallback_text: DB 查询失败 / 账户不存在时退化到的元文本。

    Returns:
        推群文本。失败时回退到 ``fallback_text``。
    """
    try:
        acc: Optional[SayuPaperAccount] = await _db.PaperAccountRepo.get(group_id, bot_id)
        latest_decision: Optional[SayuPaperDecision] = None
        latest_trade: Optional[SayuPaperTrade] = None
        if decisions_d > 0:
            ds = await _db.PaperDecisionRepo.list_recent(group_id, bot_id, limit=1)
            if ds:
                latest_decision = ds[0]
        if trades_d > 0:
            ts = await _db.PaperTradeRepo.list_by_account(group_id, bot_id, limit=1)
            if ts:
                latest_trade = ts[0]
        positions_now: list[SayuPaperPosition] = (
            await _db.PaperPositionRepo.list_by_account(group_id, bot_id)
        )
    except Exception:
        return fallback_text

    if acc is None:
        return fallback_text

    # ── 选 variant 决定标题 + 页脚 + 内部渲染分支 ──
    if variant == "auto":
        action_map = {"buy": "🟢 买入", "sell": "🔴 卖出", "hold": "⏸️ 持币"}
        action = latest_decision.action if latest_decision else "hold"
        title_action = action_map.get(action, "⏸️ 持币")
        header_block = f"【自主决策】{title_action}"
        footer = "（模拟盘心跳 · 自主决策播报，非投资建议）"
    elif variant == "force_buy":
        header_block = "【强制买入】🟢"
        footer = "🔔 DRY_RUN · 仅压测链路验证，非投资建议"
    elif variant == "force_sell":
        header_block = "【强制平仓】🔴"
        footer = "🔔 DRY_RUN · 仅压测链路验证，非投资建议"
    elif variant == "force_hold":
        header_block = "【强制 HOLD】⏸️"
        footer = "🔔 DRY_RUN · 仅压测链路验证，非投资建议"
    elif variant == "kb_web":
        header_block = "【KB + Web 通路验证】✅"
        footer = "（模拟盘 · KB/Web 通路验证，非投资建议）"
    else:
        return fallback_text

    lines: list[str] = [
        "📈 【模拟盘 · 操盘播报】",
        f"群 {group_id} · 模式 {acc.mode}",
        "",
        header_block,
    ]

    # ── variant 内部细节渲染 ──
    if variant == "auto":
        if latest_decision:
            lines.append(f"决策 ID: #{latest_decision.id}")
            clean = _clean_reason(latest_decision.reason or "")
            if clean:
                lines.append(f"📝 决策理由：{clean}")
            ind = _indicator_summary(latest_decision.indicators or "")
            if ind:
                lines.append(f"📈 行情快照：{ind}")
        lines.append(f"💰 账户现金：¥{acc.cash:,.2f}")
        lines.append("")
        if positions_now:
            lines.append("📊 当前持仓：")
            lines.append(_format_positions(positions_now))
        else:
            lines.append("📊 当前持仓：无")

    elif variant == "force_buy":
        if latest_trade:
            t = latest_trade
            lines.append(f"成交：{t.stock_code} {t.stock_name} × {t.qty:,} @ ¥{t.price:.2f}")
            lines.append(f"买入金额：¥{t.amount:,.2f}")
            lines.append(f"手续费（佣金 + 印花税）：¥{t.fee:.2f}")
            lines.append(f"账户现金：¥{acc.cash:,.2f}（已扣 {t.amount + t.fee:,.2f}）")
        lines.append("")
        lines.append("📊 持仓变动：")
        if positions_now:
            lines.append(_format_positions(positions_now))
        if latest_decision:
            lines.append("")
            lines.append(f"📝 决策记录 #{latest_decision.id}（action=buy）")

    elif variant == "force_sell":
        if latest_trade:
            t = latest_trade
            lines.append(f"成交：{t.stock_code} {t.stock_name} × {t.qty:,} @ ¥{t.price:.2f}")
            lines.append(f"卖出金额：¥{t.amount:,.2f}")
            lines.append(f"手续费（佣金 + 印花税）：¥{t.fee:.2f}")
            lines.append(f"已实现盈亏：¥{t.realized_pnl:+,.2f}")
            lines.append(f"账户现金：¥{acc.cash:,.2f}")
        lines.append("")
        if positions_now:
            lines.append("📊 剩余持仓：")
            lines.append(_format_positions(positions_now))
        else:
            lines.append("📊 持仓已清空（全部卖出）")
        if latest_decision:
            lines.append("")
            lines.append(f"📝 决策记录 #{latest_decision.id}（action=sell）")

    elif variant == "force_hold":
        lines.append("LLM 决策：HOLD（不调撮合 / 流水 / 持仓，仅写决策日志）")
        if latest_decision:
            lines.append(f"决策 ID: #{latest_decision.id}")
            clean = _clean_reason(latest_decision.reason or "")
            if clean:
                lines.append(f"📝 决策理由：{clean}")
            ind = _indicator_summary(latest_decision.indicators or "")
            if ind:
                lines.append(f"📈 行情快照：{ind}")
        lines.append(f"💰 账户现金：¥{acc.cash:,.2f}（无变动）")
        lines.append("")
        if positions_now:
            lines.append("📊 当前持仓（维持）：")
            lines.append(_format_positions(positions_now))
        else:
            lines.append("📊 当前持仓：无")

    elif variant == "kb_web":
        lines.append("已验证 search_knowledge / web_search_tool / get_latest_news / papertrade_account_query")
        lines.append(f"账户现金：¥{acc.cash:,.2f}（不变）")

    lines.append("")
    lines.append(footer)
    return "\n".join(lines)
