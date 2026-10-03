from .base import Runner, Tool
from . import apps, system, web


def default_tools(runner: Runner | None = None) -> list[Tool]:
    runner = runner or Runner()
    return [*web.make_tools(), *apps.make_tools(runner), *system.make_tools(runner)]


__all__ = ["Runner", "Tool", "default_tools"]
