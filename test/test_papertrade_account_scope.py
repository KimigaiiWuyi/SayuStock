"""模拟盘作用域单测：盘名校验 / 账户解析 / 写入鉴权。

多盘改造后，账户由**盘名**（全库唯一）标识，与"在哪个群提问"彻底解耦。因此
这里覆盖的三件事互相正交，任何一个串味都会出线上事故：

1. 盘名校验 —— 挡住会让命令解析歧义的名字（周期词 / 命令词 / 纯数字）。
2. 账户解析 —— 读路径可以信盘名，写路径**只认 root_task_id**，防 LLM 把 A 盘的
   成交写进 B 盘。
3. 写入鉴权 —— 心跳树 / 显式发票之外一律拒绝，且发票是**记名**的。

``db`` 与 ``papertrade_models`` 都替换成桩：前者用来精确摆布库里有哪些盘，后者
是为了不让 SQLModel 表在合成包里被二次定义（会污染真实 metadata）。
"""

import sys
import asyncio
import importlib.util
from types import ModuleType
from typing import List, Optional
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

PKG_ROOT = Path(__file__).resolve().parent.parent / "SayuStock"
PKG_NAME = "_papertrade_scope_test"


# ============================================================
# 桩：模型 + db
# ============================================================
class _StubAccount:
    """替身 SayuPaperAccount：只保留 account_scope 会读的字段。"""

    def __init__(
        self,
        account_id: int,
        name: str,
        *,
        strategy_id: str = "multi_factor",
        init_root: Optional[str] = None,
        period_root: Optional[str] = None,
    ) -> None:
        self.id = account_id
        self.name = name
        self.strategy_id = strategy_id
        self.kanban_init_root_id = init_root
        self.kanban_period_root_id = period_root
        self.enabled = 1


class _StubAccountRepo:
    """替身 PaperAccountRepo：accounts 按插入顺序模拟 created_at asc。"""

    accounts: List[_StubAccount] = []

    @classmethod
    async def get_by_id(cls, account_id: int) -> Optional[_StubAccount]:
        for a in cls.accounts:
            if a.id == account_id:
                return a
        return None

    @classmethod
    async def get_by_name(cls, name: str) -> Optional[_StubAccount]:
        for a in cls.accounts:
            if a.name == name:
                return a
        return None

    @classmethod
    async def get_by_kanban_root(cls, root_task_id: str) -> Optional[_StubAccount]:
        if not root_task_id:
            return None
        for a in cls.accounts:
            if root_task_id in (a.kanban_init_root_id, a.kanban_period_root_id):
                return a
        return None

    @classmethod
    async def search(cls, keyword: str) -> List[_StubAccount]:
        return [a for a in cls.accounts if keyword in a.name]

    @classmethod
    async def list_all(cls) -> List[_StubAccount]:
        return list(cls.accounts)


def _install_stubs() -> ModuleType:
    """造合成包 + 注入桩，然后加载真实的 account_scope.py。"""
    for name, path in (
        (PKG_NAME, PKG_ROOT),
        (f"{PKG_NAME}.stock_papertrade", PKG_ROOT / "stock_papertrade"),
        (f"{PKG_NAME}.utils", PKG_ROOT / "utils"),
        (f"{PKG_NAME}.utils.database", PKG_ROOT / "utils" / "database"),
    ):
        mod = ModuleType(name)
        mod.__path__ = [str(path)]
        sys.modules[name] = mod

    db_stub = ModuleType(f"{PKG_NAME}.stock_papertrade.db")
    setattr(db_stub, "PaperAccountRepo", _StubAccountRepo)
    sys.modules[db_stub.__name__] = db_stub

    # 模型桩：真模块会 declare SQLModel 表，在合成包里再 import 一次会把同名表
    # 塞进同一份 metadata 而报 "Table already defined"。
    models_stub = ModuleType(f"{PKG_NAME}.utils.database.papertrade_models")
    setattr(models_stub, "DEFAULT_ACCOUNT_NAME", "默认模拟盘")
    setattr(models_stub, "DEFAULT_STRATEGY_ID", "multi_factor")
    setattr(models_stub, "SayuPaperAccount", _StubAccount)
    sys.modules[models_stub.__name__] = models_stub

    spec = importlib.util.spec_from_file_location(
        f"{PKG_NAME}.stock_papertrade.account_scope",
        PKG_ROOT / "stock_papertrade" / "account_scope.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


scope = _install_stubs()

DEFAULT = "默认模拟盘"


def _reset(*accounts: _StubAccount) -> None:
    _StubAccountRepo.accounts = list(accounts)


# ============================================================
# 1) 盘名归一 + 校验
# ============================================================
def test_normalize_strips_and_folds_fullwidth():
    """手机输入法很容易带出全角；不归一会让「放量盘」和「放量盘 」变成两个盘。"""
    assert scope.normalize_account_name("  放量盘  ") == "放量盘"
    assert scope.normalize_account_name("ＡＢＣ１２") == "ABC12"
    assert scope.normalize_account_name("放量盘\u3000") == "放量盘"
    assert scope.normalize_account_name("") == ""


def test_validate_accepts_normal_names():
    assert scope.validate_account_name("放量盘") == ""
    assert scope.validate_account_name("test_2") == ""
    assert scope.validate_account_name(" 激进盘 ") == ""  # 归一后合法


def test_validate_rejects_length_violations():
    assert "不能为空" in scope.validate_account_name("   ")
    assert "长度" in scope.validate_account_name("A")
    assert "长度" in scope.validate_account_name("盘" * 17)


def test_validate_rejects_bad_charset():
    assert "空格" in scope.validate_account_name("放量 盘")
    assert "非法字符" in scope.validate_account_name("放量-盘")


def test_validate_rejects_pure_digits():
    """纯数字盘名会和「模拟盘查询 <群号>」撞车。"""
    assert "纯数字" in scope.validate_account_name("123456")


def test_validate_rejects_period_and_command_tokens():
    """周期词当盘名会让「模拟盘收益 <盘名> <周期>」无法分词；命令词同理。"""
    assert "周期" in scope.validate_account_name("本周")
    assert "保留词" in scope.validate_account_name("持仓")


# ============================================================
# 2) 参数分词
# ============================================================
def test_split_name_and_period():
    assert scope.split_name_and_period("") == ("", "")
    assert scope.split_name_and_period("放量盘") == ("放量盘", "")
    assert scope.split_name_and_period("本周") == ("", "本周")
    assert scope.split_name_and_period("放量盘 本周") == ("放量盘", "本周")
    # 顺序反了也认：周期词的身份是靠词表判定的，不靠位置
    assert scope.split_name_and_period("本周 放量盘") == ("放量盘", "本周")


# ============================================================
# 3) 读路径解析
# ============================================================
def test_resolve_prefers_explicit_id_then_name():
    _reset(_StubAccount(1, DEFAULT), _StubAccount(2, "放量盘"))
    assert asyncio.run(scope.resolve_account(account_id=2)).name == "放量盘"
    assert asyncio.run(scope.resolve_account(name="放量盘")).id == 2


def test_resolve_falls_back_to_fuzzy_single_match():
    """精确名没中时做一次包含匹配，用户少打两个字也能查到。"""
    _reset(_StubAccount(1, DEFAULT), _StubAccount(2, "放量极值盘"))
    assert asyncio.run(scope.resolve_account(name="放量")).id == 2


def test_resolve_refuses_to_guess_when_ambiguous():
    """命中多个盘时必须返回 None——猜一个等于把用户引到错误的账本上。"""
    _reset(_StubAccount(1, "放量A盘"), _StubAccount(2, "放量B盘"))
    assert asyncio.run(scope.resolve_account(name="放量")) is None


def test_resolve_default_when_name_omitted():
    _reset(_StubAccount(1, "放量盘"), _StubAccount(2, DEFAULT))
    assert asyncio.run(scope.resolve_account()).name == DEFAULT


def test_resolve_single_account_when_no_default():
    """全库只有一个盘时，它显然就是用户问的那个。"""
    _reset(_StubAccount(7, "放量盘"))
    assert asyncio.run(scope.resolve_account()).id == 7


def test_resolve_returns_none_when_ambiguous_without_default():
    _reset(_StubAccount(1, "放量盘"), _StubAccount(2, "价值盘"))
    assert asyncio.run(scope.resolve_account()) is None


def test_resolve_respects_fallback_flag():
    _reset(_StubAccount(1, DEFAULT))
    assert asyncio.run(scope.resolve_account(fallback_default=False)) is None


def test_read_empty_name_binds_to_kanban_tree():
    """心跳树上空名必须落到本盘，不能回落默认盘。"""
    _reset(
        _StubAccount(1, DEFAULT, period_root="period_default"),
        _StubAccount(2, "放量盘", period_root="period_vol"),
    )
    acc = asyncio.run(scope.resolve_account_for_read(name="", root_task_id="period_vol"))
    assert acc is not None and acc.id == 2


def test_read_explicit_name_beats_tree():
    _reset(
        _StubAccount(1, DEFAULT, period_root="period_default"),
        _StubAccount(2, "放量盘", period_root="period_vol"),
    )
    acc = asyncio.run(scope.resolve_account_for_read(name="默认模拟盘", root_task_id="period_vol"))
    assert acc is not None and acc.id == 1


def test_read_empty_name_binds_to_grant():
    _reset(_StubAccount(1, DEFAULT), _StubAccount(9, "压测临时盘"))

    async def _run():
        with scope.grant_write(9):
            return await scope.resolve_account_for_read(name="")

    acc = asyncio.run(_run())
    assert acc is not None and acc.id == 9


def test_disabled_reason():
    acc = _StubAccount(1, DEFAULT)
    acc.enabled = 0
    assert "已停用" in scope.account_disabled_reason(acc)
    acc.enabled = 1
    assert scope.account_disabled_reason(acc) == ""


# ============================================================
# 4) 写路径解析：只认 root_task_id
# ============================================================
def test_write_resolves_by_root_task_id():
    _reset(_StubAccount(1, DEFAULT, init_root="init_1", period_root="period_1"))
    acc, note = asyncio.run(scope.resolve_account_for_write("period_1"))
    assert acc.id == 1 and note == ""


def test_write_corrects_instead_of_rejecting_wrong_name():
    """LLM 把盘名拼错不该让整轮心跳报废——按任务归属的盘执行 + 提示纠正。"""
    _reset(_StubAccount(1, "放量盘", period_root="period_1"))
    acc, note = asyncio.run(scope.resolve_account_for_write("period_1", account_name="价值盘"))
    assert acc.name == "放量盘"
    assert "放量盘" in note and "价值盘" in note


def test_write_never_falls_back_to_name_without_grant():
    """关键回归：盘名不能成为写路径的解析依据，否则拼错名字就写错账本。"""
    _reset(_StubAccount(1, "放量盘", period_root="period_1"), _StubAccount(2, "价值盘"))
    acc, note = asyncio.run(scope.resolve_account_for_write("adhoc_x", account_name="价值盘"))
    assert acc is None and note != ""


def test_write_denied_without_task_context():
    _reset(_StubAccount(1, DEFAULT, period_root="period_1"))
    acc, note = asyncio.run(scope.resolve_account_for_write(""))
    assert acc is None and "无任务上下文" in note


# ============================================================
# 5) 记名发票
# ============================================================
def test_grant_write_resolves_to_the_granted_account_only():
    """压测/init 的写工具大多不暴露 account_name，发票必须自己带 account_id。"""
    _reset(_StubAccount(1, DEFAULT), _StubAccount(9, "压测临时盘"))

    async def _run():
        with scope.grant_write(9):
            return await scope.resolve_account_for_write("adhoc_x")

    acc, _ = asyncio.run(_run())
    assert acc.id == 9  # 不是默认盘


def test_grant_write_opens_and_closes_the_gate():
    _reset(_StubAccount(1, DEFAULT, period_root="period_1"))
    acc = _StubAccountRepo.accounts[0]

    async def _run():
        with scope.grant_write(1):
            inside = await scope.deny_write_reason("adhoc_x", acc)
        outside = await scope.deny_write_reason("adhoc_x", acc)
        return inside, outside

    inside, outside = asyncio.run(_run())
    assert inside == ""
    assert outside != ""


def test_grant_write_survives_child_task():
    """capagent 常在子任务里调工具，contextvar 必须继承下去。"""
    _reset(_StubAccount(1, DEFAULT))
    acc = _StubAccountRepo.accounts[0]

    async def _run() -> str:
        with scope.grant_write(1):
            return await asyncio.create_task(scope.deny_write_reason("adhoc_x", acc))

    assert asyncio.run(_run()) == ""


def test_grant_write_is_scoped_to_one_account():
    """记名发票不能被拿去写别的盘。"""
    _reset(_StubAccount(1, DEFAULT), _StubAccount(2, "放量盘"))
    other = _StubAccountRepo.accounts[1]

    async def _run() -> str:
        with scope.grant_write(1):
            return await scope.deny_write_reason("adhoc_x", other)

    assert "只被授权" in asyncio.run(_run())


# ============================================================
# 6) 写入鉴权
# ============================================================
def test_write_allowed_from_own_kanban_tree():
    acc = _StubAccount(1, DEFAULT, init_root="init_1", period_root="period_1")
    _reset(acc)
    assert asyncio.run(scope.deny_write_reason("period_1", acc)) == ""
    assert asyncio.run(scope.deny_write_reason("init_1", acc)) == ""


def test_write_denied_from_adhoc_delegation():
    """用户一句「帮我买 xx」能把写工具委派出来，执行层必须自己挡住。"""
    acc = _StubAccount(1, DEFAULT, init_root="init_1", period_root="period_1")
    _reset(acc)
    assert asyncio.run(scope.deny_write_reason("adhoc_abc", acc)) != ""


def test_write_denied_across_accounts():
    """多盘后的新风险：A 盘的心跳树不能写 B 盘的账本。"""
    a = _StubAccount(1, "放量盘", period_root="period_a")
    b = _StubAccount(2, "价值盘", period_root="period_b")
    _reset(a, b)
    reason = asyncio.run(scope.deny_write_reason("period_a", b))
    assert "禁止跨盘写入" in reason


def test_write_denied_when_account_missing():
    _reset()
    assert "账户不存在" in asyncio.run(scope.deny_write_reason("period_1", None))


# ============================================================
# 7) 文案：绝不能说「本群未开通」
# ============================================================
def test_not_opened_message_never_blames_the_group():
    """说成「本群未开通」会被 Agent 复读成跨群误报——盘根本不属于群。"""
    for msg in (scope.not_opened_message(), scope.not_opened_message(name="放量盘")):
        assert "本群" not in msg
    assert "放量盘" in scope.not_opened_message(name="放量盘")
    assert DEFAULT in scope.not_opened_message()


def test_scope_note_warns_against_false_unopened():
    acc = _StubAccount(3, "放量盘", strategy_id="volume_extremum")
    note = scope.scope_note_for_llm(acc)
    assert "放量盘" in note
    assert "volume_extremum" in note
    assert "严禁" in note
    assert "命名账户" in note

    assert "命名账户" in scope.scope_note_for_llm(None)


def test_account_label_falls_back_to_id():
    assert scope.account_label(_StubAccount(5, "放量盘")) == "放量盘"
    assert scope.account_label(_StubAccount(5, "")) == "#5"


def test_enabled_account_cap_exists():
    """成本闸：N 个启用盘 = N 倍 LLM 账单，必须有上限常量兜底。"""
    assert isinstance(scope.MAX_ENABLED_ACCOUNTS, int)
    assert scope.MAX_ENABLED_ACCOUNTS >= 1
