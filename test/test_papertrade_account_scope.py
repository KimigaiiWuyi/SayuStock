"""模拟盘作用域单测：账户键钉死 / 播报改向 / 写入鉴权。

默认是"全服共用一个盘"；``papertrade_multi_group=True`` 才回到旧的一群一盘。

``db`` 与 ``STOCK_CONFIG`` 都替换成桩，这样能精确摆布"开没开多群模式"和
"库里有没有账户"，不碰真实数据库。
"""

import sys
import asyncio
import importlib.util
from types import ModuleType
from typing import Optional
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

PKG_ROOT = Path(__file__).resolve().parent.parent / "SayuStock"
PKG_NAME = "_papertrade_scope_test"

from gsuid_core.models import Event  # noqa: E402


# ============================================================
# 桩：配置 + db
# ============================================================
class _StubConfigItem:
    def __init__(self, data: object) -> None:
        self.data = data


class _StubConfig:
    """替身 STOCK_CONFIG：用 set() 直接摆布配置值。"""

    def __init__(self) -> None:
        self._d: dict[str, object] = {
            "papertrade_multi_group": False,  # 默认 = 全服共用一个盘
            "papertrade_broadcast_group": "",
        }

    def set(self, key: str, value: object) -> None:
        self._d[key] = value

    def get_config(self, key: str) -> _StubConfigItem:
        return _StubConfigItem(self._d[key])


class _StubAccount:
    def __init__(
        self,
        group_id: str,
        bot_id: str,
        init_root: Optional[str] = None,
        period_root: Optional[str] = None,
    ) -> None:
        self.group_id = group_id
        self.bot_id = bot_id
        self.kanban_init_root_id = init_root
        self.kanban_period_root_id = period_root


class _StubAccountRepo:
    """替身 PaperAccountRepo：accounts 列表按插入顺序模拟 created_at asc。"""

    accounts: list[_StubAccount] = []

    @classmethod
    async def get_earliest(cls) -> Optional[_StubAccount]:
        return cls.accounts[0] if cls.accounts else None

    @classmethod
    async def get(cls, group_id: str, bot_id: str) -> Optional[_StubAccount]:
        for a in cls.accounts:
            if a.group_id == group_id and a.bot_id == bot_id:
                return a
        return None


def _install_stubs() -> tuple[_StubConfig, ModuleType]:
    """造合成包 + 注入桩，然后加载真实的 account_scope.py。"""
    for name, path in (
        (PKG_NAME, PKG_ROOT),
        (f"{PKG_NAME}.stock_papertrade", PKG_ROOT / "stock_papertrade"),
        (f"{PKG_NAME}.stock_config", PKG_ROOT / "stock_config"),
    ):
        mod = ModuleType(name)
        mod.__path__ = [str(path)]
        sys.modules[name] = mod

    db_stub = ModuleType(f"{PKG_NAME}.stock_papertrade.db")
    setattr(db_stub, "PaperAccountRepo", _StubAccountRepo)
    sys.modules[db_stub.__name__] = db_stub

    cfg = _StubConfig()
    cfg_stub = ModuleType(f"{PKG_NAME}.stock_config.stock_config")
    setattr(cfg_stub, "STOCK_CONFIG", cfg)
    sys.modules[cfg_stub.__name__] = cfg_stub

    spec = importlib.util.spec_from_file_location(
        f"{PKG_NAME}.stock_papertrade.account_scope",
        PKG_ROOT / "stock_papertrade" / "account_scope.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return cfg, mod


CFG, scope = _install_stubs()


def _reset(multi_group: bool = False, broadcast: str = "") -> None:
    """默认 multi_group=False，即"全服共用一个盘"这个新默认。"""
    CFG.set("papertrade_multi_group", multi_group)
    CFG.set("papertrade_broadcast_group", broadcast)
    _StubAccountRepo.accounts = []
    scope.invalidate_home_cache()


def _ev(group_id: str, bot_id: str = "onebot") -> Event:
    return Event(bot_id=bot_id, group_id=group_id, user_id="u1", user_type="group")


# ============================================================
# 1) 账户键
# ============================================================
def test_shared_mode_is_the_default():
    """默认（未配任何东西）= 全服共用：任意群都解析到最早那个账户。"""
    _reset()
    _StubAccountRepo.accounts = [
        _StubAccount("111", "onebot"),  # 最早 = 开盘的原群
        _StubAccount("999", "onebot"),  # 升级前遗留的账户 → 孤儿
    ]
    assert asyncio.run(scope.resolve_account_key(_ev("222"))) == ("111", "onebot")
    assert asyncio.run(scope.resolve_account_key(_ev("999"))) == ("111", "onebot")


def test_multi_group_restores_legacy_behaviour():
    """开 papertrade_multi_group = 回到旧的一群一盘：各群解析到自己的键。"""
    _reset(multi_group=True)
    _StubAccountRepo.accounts = [_StubAccount("111", "onebot")]
    assert asyncio.run(scope.resolve_account_key(_ev("111"))) == ("111", "onebot")
    assert asyncio.run(scope.resolve_account_key(_ev("222"))) == ("222", "onebot")


def test_shared_mode_ignores_foreign_bot_id():
    """跨平台提问不能让 bot_id 跟着 ev 跑，否则键对不上 → 查成"未开户"。"""
    _reset()
    _StubAccountRepo.accounts = [_StubAccount("111", "onebot")]
    got = asyncio.run(scope.resolve_account_key(_ev("222", bot_id="discord")))
    assert got == ("111", "onebot")


def test_shared_mode_without_account_falls_back():
    """还没人开盘时退回会话上下文，否则第一个「模拟盘初始化」就建不出账户。"""
    _reset()
    assert asyncio.run(scope.resolve_account_key(_ev("111"))) == ("111", "onebot")


# ============================================================
# 2) 播报改向
# ============================================================
def test_broadcast_unchanged_when_not_configured():
    _reset(broadcast="")
    ev = _ev("111")
    assert asyncio.run(scope.broadcast_event(ev)).group_id == "111"


def test_broadcast_redirects_when_configured():
    _reset(broadcast="555")
    ev = _ev("111")
    out = asyncio.run(scope.broadcast_event(ev))
    assert out.group_id == "555"
    assert out.bot_id == "onebot"  # 同一个 bot，只换群
    assert ev.group_id == "111"  # 原 ev 不被就地改写


def test_broadcast_redirect_is_hot_and_ignored_in_multi_group():
    """改配置立刻生效；多群模式下配了也不改向（各群播各群的）。"""
    _reset(multi_group=True, broadcast="555")
    assert asyncio.run(scope.broadcast_event(_ev("111"))).group_id == "111"
    CFG.set("papertrade_multi_group", False)
    assert asyncio.run(scope.broadcast_event(_ev("111"))).group_id == "555"
    CFG.set("papertrade_broadcast_group", "777")
    assert asyncio.run(scope.broadcast_event(_ev("111"))).group_id == "777"


# ============================================================
# 3) 写入鉴权
# ============================================================
def test_write_allowed_from_own_kanban_tree():
    """cron 心跳：root_task_id 命中账户自己的树 → 放行。"""
    _reset()
    _StubAccountRepo.accounts = [_StubAccount("111", "onebot", "init_root", "period_root")]
    assert asyncio.run(scope.deny_write_reason("period_root", "111", "onebot")) == ""
    assert asyncio.run(scope.deny_write_reason("init_root", "111", "onebot")) == ""


def test_write_denied_from_adhoc_delegation():
    """用户指使 persona 委派出来的 ad-hoc 执行体 → 拒。"""
    _reset()
    _StubAccountRepo.accounts = [_StubAccount("111", "onebot", "init_root", "period_root")]
    reason = asyncio.run(scope.deny_write_reason("adhoc_abc123", "111", "onebot"))
    assert reason and "未经授权" in reason


def test_write_denied_without_task_context():
    """主 persona 直聊（无任务上下文）→ 拒。"""
    _reset()
    _StubAccountRepo.accounts = [_StubAccount("111", "onebot", "init_root", "period_root")]
    reason = asyncio.run(scope.deny_write_reason("", "111", "onebot"))
    assert reason != ""


def test_grant_write_opens_the_gate():
    """init 立即决策 / dry_run 显式发票 → 放行。"""
    _reset()
    _StubAccountRepo.accounts = [_StubAccount("111", "onebot", "init_root", "period_root")]

    async def _run() -> tuple[str, str]:
        with scope.grant_write():
            granted = await scope.deny_write_reason("adhoc_abc", "111", "onebot")
        after = await scope.deny_write_reason("adhoc_abc", "111", "onebot")
        return granted, after

    granted, after = asyncio.run(_run())
    assert granted == ""  # 票内放行
    assert after != ""  # 出了 with 立刻恢复拒绝


def test_grant_write_survives_child_task():
    """capagent 内部若在子任务里调工具，contextvar 必须能继承下去。"""
    _reset()
    _StubAccountRepo.accounts = [_StubAccount("111", "onebot", "init_root", "period_root")]

    async def _run() -> str:
        with scope.grant_write():
            return await asyncio.create_task(scope.deny_write_reason("adhoc_x", "111", "onebot"))

    assert asyncio.run(_run()) == ""


def test_write_denied_when_account_missing():
    _reset()
    reason = asyncio.run(scope.deny_write_reason("period_root", "111", "onebot"))
    assert "账户不存在" in reason


# ============================================================
# 4) 缓存失效
# ============================================================
def test_invalidate_cache_after_clear():
    """清盘后缓存必须重算，否则会一直指向已删掉的账户键。"""
    _reset()
    _StubAccountRepo.accounts = [_StubAccount("111", "onebot")]
    assert asyncio.run(scope.resolve_account_key(_ev("222"))) == ("111", "onebot")

    _StubAccountRepo.accounts = []
    scope.invalidate_home_cache()
    assert asyncio.run(scope.resolve_account_key(_ev("222"))) == ("222", "onebot")


def test_is_home_context():
    _reset()
    _StubAccountRepo.accounts = [_StubAccount("111", "onebot")]
    assert asyncio.run(scope.is_home_context(_ev("111"))) is True
    assert asyncio.run(scope.is_home_context(_ev("222"))) is False

    _reset(multi_group=True)
    _StubAccountRepo.accounts = [_StubAccount("111", "onebot")]
    assert asyncio.run(scope.is_home_context(_ev("222"))) is True  # 多群模式不设限
