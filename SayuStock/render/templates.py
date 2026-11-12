"""Jinja2 environment for the Takumi page templates."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, StrictUndefined, FileSystemLoader, select_autoescape

from .palette import TOKENS, mix, ramp, rgba

TEMPLATES = Path(__file__).with_name("templates")
_ENV = Environment(
    loader=FileSystemLoader(TEMPLATES),
    autoescape=select_autoescape(["html"]),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
_ENV.filters["px"] = lambda value: f"{float(value):.1f}px"
_ENV.filters["pct"] = lambda value: f"{float(value):.2f}%"
_ENV.globals.update(TOKENS, mix=mix, ramp=ramp, rgba=rgba)


def render_template(name: str, **context: object) -> str:
    return _ENV.get_template(name).render(**context)
