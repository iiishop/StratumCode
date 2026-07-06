"""Plugin loader — scan filesystem for user-defined tools."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path


_logger = logging.getLogger(__name__)


def load_from_dir(directory: str | Path) -> int:
    """Scan directory for *.py files, import them, register any ToolDef exports.

    Each file should export:
        tools: list[ToolDef]   or
        tool: ToolDef
    """
    d = Path(directory)
    if not d.is_dir():
        return 0

    count = 0
    for pyfile in sorted(d.glob("*.py")):
        if pyfile.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(pyfile.stem, pyfile)
            if not spec or not spec.loader:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            items = getattr(mod, "tools", None) or []
            if not isinstance(items, list):
                items = [items]
            single = getattr(mod, "tool", None)
            if single:
                items.append(single)

            from ..tools import registry
            for t in items:
                registry.register(t)
                count += 1
        except Exception:
            _logger.exception("failed to load plugin: %s", pyfile)
    return count
