from gsuid_core.sv import Plugins

from . import stock_agent  # noqa: F401

Plugins(
    name="SayuStock",
    force_prefix=["a", "股票"],
    allow_empty_prefix=True,
)
