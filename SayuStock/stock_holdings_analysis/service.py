"""持仓分析 · 编排：配额 → 分析 agent → render_agent → 发图。"""

from __future__ import annotations

import re
from typing import List
from pathlib import Path
from datetime import date

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment

from .parse import SymbolListResult, build_symbol_list
from .quota import release_quota, try_claim_quota
from ..utils.utils import convert_list
from ..utils.database.models import SsBind

_RES_HANDLE_RE = re.compile(r"res_[a-f0-9]{8,}", re.IGNORECASE)

_EMPTY_HINT = (
    "您还没有自选，也没有在命令后写股票代码/名称。\n请先「添加自选」，或发送：持仓分析 600519 茅台 …（最多 8 只）"
)

_QUOTA_HINT = "今日持仓分析已用过或进行中，请明天再试（按自然日 0 点重置；失败会释放额度）。"


def _user_id(ev: Event) -> str:
    return str(ev.at if ev.at else ev.user_id)


async def resolve_symbols(ev: Event, command_text: str) -> SymbolListResult | str:
    """返回 SymbolListResult 或错误提示字符串。"""
    manual = (command_text or "").strip()
    watchlist: List[str] = []
    if not manual:
        raw = await SsBind.get_uid_list_by_game(_user_id(ev), ev.bot_id)
        if raw:
            watchlist = convert_list(raw)
    result = build_symbol_list(manual_text=manual, watchlist=watchlist)
    if result is None:
        return _EMPTY_HINT
    return result


def build_analysis_task(symbols: List[str], *, user_id: str, day: date) -> str:
    lines = [
        f"用户 {user_id} 请求「持仓分析」（{day.isoformat()}）。",
        "标的来源：用户自选或命令手填（**非模拟盘仓位**）。",
        f"请对以下 {len(symbols)} 只标的做综合分析，并输出**完整 Markdown** 事实包：",
    ]
    for i, s in enumerate(symbols, 1):
        lines.append(f"{i}. {s}")
    lines.extend(
        [
            "",
            "必须覆盖维度：技术面 / 新闻与事件 / 情绪面 / 资金面 / 基本面；",
            "给出每只综合评级与建议，以及组合层面的简要结论。",
            "榜单与增速/ROE 仅辅助，禁止单指标定论。",
            "最终消息只返回 Markdown 正文，不要过程句，不要出图。",
        ]
    )
    return "\n".join(lines)


def build_render_task(markdown: str) -> str:
    return (
        "将下列「持仓综合分析」Markdown **全文**渲成**一张**信息图（竖长高密度简报）。\n"
        "要求：总览表、分票评级、建议与风险必须上图；不要删数字；只调用一次渲染工具；\n"
        "禁止搜索；禁止对用户会话直发；返回摘要时**必须写出 res_ 图片句柄**。\n\n"
        "---\n\n"
        f"{markdown}"
    )


def extract_res_handles(text: str) -> List[str]:
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _RES_HANDLE_RE.findall(text):
        key = m.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


async def load_image_bytes_from_res(res_id: str) -> bytes | None:
    try:
        from gsuid_core.ai_core.planning.models import AIAgentArtifact
    except ImportError as e:
        logger.warning(f"[holdings_analysis] import AIAgentArtifact 失败: {e}")
        return None
    art = await AIAgentArtifact.get_by_id(res_id)
    if art is None:
        return None
    path = (art.payload_path or "").strip()
    if path:
        p = Path(path)
        if p.is_file():
            return p.read_bytes()
    return None


async def render_markdown_fallback(md: str) -> bytes | None:
    try:
        from gsuid_core.utils.html_render import render_md_to_bytes
    except ImportError as e:
        logger.warning(f"[holdings_analysis] render_md_to_bytes import 失败: {e}")
        return None
    try:
        return await render_md_to_bytes(md=md, max_width=800, image_format="jpeg")
    except (OSError, RuntimeError, ValueError, TypeError) as e:
        logger.exception(f"[holdings_analysis] md 兜底渲染失败: {e}")
        return None


def normalize_analysis_markdown(md: str | None) -> str:
    """剥掉 <<NO_BROADCAST>> 后再判空，避免仅 marker 的空分析占额度。"""
    s = (md or "").strip()
    if "<<NO_BROADCAST>>" in s:
        s = s.replace("<<NO_BROADCAST>>", "").strip()
    return s


async def run_holdings_analysis_command(bot: Bot, ev: Event) -> None:
    uid = _user_id(ev)
    bid = ev.bot_id
    day = date.today()

    # on_command 时 ev.text 通常是去掉命令前缀后的参数
    resolved = await resolve_symbols(ev, ev.text or "")
    if isinstance(resolved, str):
        await bot.send(resolved)
        return

    # 解析成功后再原子占坑；仅图片发送成功才保留，其余路径 finally 释放
    if not try_claim_quota(uid, bid, day):
        await bot.send(_QUOTA_HINT)
        return

    success = False
    try:
        prefix_msgs: list[str] = ["⏳ 持仓分析进行中（技术/新闻/情绪/资金/基本面），请稍候…"]
        if resolved.cap_warning:
            prefix_msgs.append(f"ℹ️ {resolved.cap_warning}")
        prefix_msgs.append(
            f"本次标的（{resolved.source}，共 {len(resolved.symbols)} 只）：" + "、".join(resolved.symbols)
        )
        await bot.send("\n".join(prefix_msgs))

        try:
            from gsuid_core.ai_core.capability_agents.runner import (
                CAPABILITY_AGENT_ERROR_PREFIX,
                run_capability_agent,
            )
        except ImportError as e:
            await bot.send(f"⚠️ AI 子系统未就绪，无法分析：{type(e).__name__}: {e}")
            return

        task = build_analysis_task(resolved.symbols, user_id=uid, day=day)
        try:
            md = await run_capability_agent(
                profile_id="holdings_analysis_agent",
                task=task,
                ev=ev,
                bot=bot,
                session_id_suffix=f"holdings_{uid}_{day.isoformat()}",
            )
        except Exception as e:
            # agent 运行期异常类型不可枚举；finally 释放额度
            logger.exception(f"[holdings_analysis] 分析 agent 异常: {e}")
            await bot.send(f"⚠️ 分析失败：{type(e).__name__}: {e}（额度已释放，可重试）")
            return

        md_s = normalize_analysis_markdown(md)
        if not md_s or md_s.startswith(CAPABILITY_AGENT_ERROR_PREFIX):
            await bot.send(f"⚠️ 分析未完成：{(md_s or '空结果')[:300]}（额度已释放，可重试）")
            return

        image_bytes: bytes | None = None
        try:
            render_out = await run_capability_agent(
                profile_id="render_agent",
                task=build_render_task(md_s),
                ev=ev,
                bot=bot,
                session_id_suffix=f"holdings_render_{uid}_{day.isoformat()}",
            )
            for handle in extract_res_handles(render_out or ""):
                image_bytes = await load_image_bytes_from_res(handle)
                if image_bytes:
                    logger.info(f"[holdings_analysis] 使用 render_agent 句柄 {handle}")
                    break
            if image_bytes is None and render_out:
                logger.warning(f"[holdings_analysis] render 无可用句柄，预览: {(render_out or '')[:200]}")
        except Exception as e:
            logger.exception(f"[holdings_analysis] render_agent 异常: {e}")

        if image_bytes is None:
            image_bytes = await render_markdown_fallback(md_s)
            if image_bytes is not None:
                logger.info("[holdings_analysis] 使用 render_md_to_bytes 兜底")

        if image_bytes is None:
            await bot.send("⚠️ 出图失败（分析已完成但渲染不可用）。额度已释放，可稍后重试。")
            return

        # 图片送达即为成功边界；确认文案失败不回滚额度
        try:
            await bot.send(MessageSegment.image(image_bytes))
        except (OSError, RuntimeError, ValueError, TypeError) as e:
            logger.exception(f"[holdings_analysis] 发送图片失败: {e}")
            await bot.send(f"⚠️ 图片发送失败：{e}（额度已释放）")
            return

        success = True
        try:
            await bot.send("✅ 持仓分析图已发送（评级与建议见上图）。非投资建议。")
        except (OSError, RuntimeError, ValueError, TypeError) as e:
            logger.warning(f"[holdings_analysis] 确认文案发送失败（图已达）: {e}")

        logger.info(f"[holdings_analysis] 完成 user={uid} bot={bid} n={len(resolved.symbols)}")
    finally:
        if not success:
            release_quota(uid, bid, day)
