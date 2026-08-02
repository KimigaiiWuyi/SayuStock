"""pytest 公共路径与包骨架。

两套布局都要能跑：

1. **嵌套**（Full test suite CI / 本地开发）::
   ``<gsuid_core_repo>/gsuid_core/plugins/SayuStock``
2. **扁平**（Indicator math CI / 单仓库 checkout）::
   ``<SayuStock_repo>/``

另外 ``SayuStock/__init__.py`` 会 ``from gsuid_core.sv import Plugins`` 并拉起
一串插件注册；单测只关心子模块，这里用包壳占位，避免执行该文件。
"""

from __future__ import annotations

import sys
from types import ModuleType
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

# 嵌套布局：.../gsuid_core/gsuid_core/plugins/SayuStock → parents[2] 是仓库根
_GSU_ROOT = _PLUGIN_ROOT.parents[2]
if (_GSU_ROOT / "gsuid_core").is_dir() and str(_GSU_ROOT) not in sys.path:
    sys.path.insert(0, str(_GSU_ROOT))


def _ensure_pkg_shell(name: str, path: Path) -> None:
    """注册包壳但不执行 ``__init__.py``（避免 Plugins 注册链）。"""
    existing = sys.modules.get(name)
    if existing is not None and getattr(existing, "__path__", None) is not None:
        return
    mod = ModuleType(name)
    mod.__path__ = [str(path)]  # type: ignore[attr-defined]
    mod.__package__ = name
    mod.__file__ = str(path / "__init__.py")
    sys.modules[name] = mod


_PKG = _PLUGIN_ROOT / "SayuStock"
_ensure_pkg_shell("SayuStock", _PKG)
_ensure_pkg_shell("SayuStock.utils", _PKG / "utils")
