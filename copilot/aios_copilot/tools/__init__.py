from .base import Runner, Tool
from . import apps, settings, system, web
from ..xdg import resolve_folder


def default_tools(runner: Runner | None = None) -> list[Tool]:
    runner = runner or Runner()
    return [
        *web.make_tools(),
        *apps.make_tools(runner),
        *system.make_tools(runner),
        *settings.make_tools(runner, pictures_dir=lambda: resolve_folder("PICTURES")),
    ]


__all__ = ["Runner", "Tool", "default_tools"]
