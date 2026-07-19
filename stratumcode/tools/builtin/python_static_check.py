from __future__ import annotations

import ast
import json
from pathlib import Path

from ..spec import ToolDef, ToolResult
from .common import _ignored, _resolve


async def _python_static_check(params: dict, ctx: dict) -> ToolResult:
    root = _resolve(params.get("path") or ".", ctx)
    base = _resolve(".", ctx)
    files = [root] if root.is_file() else [
        path for path in root.rglob("*.py")
        if path.is_file() and not _ignored(path, base)
    ]
    modules = [_module_facts(path, base) for path in sorted(files)]
    used_names = set().union(*(set(item.get("_used_names", [])) for item in modules))
    imported_names = set().union(*(set(item.get("_imported_names", [])) for item in modules))
    attribute_refs = set().union(*(set(item.get("_attribute_refs", [])) for item in modules))
    project_refs = used_names | imported_names
    for item in modules:
        if item.get("error"):
            continue
        unused_defs = []
        for definition in item["top_level_defs"]:
            qualified = f"{item['_module']}.{definition['name']}"
            used = definition["name"] in project_refs or qualified in attribute_refs
            definition["used_in_project"] = used
            if not used and not definition["name"].startswith("_"):
                unused_defs.append({key: definition[key] for key in ("name", "kind", "line")})
        item["unused_top_level_defs"] = unused_defs
        for key in ("_used_names", "_imported_names", "_attribute_refs", "_module"):
            item.pop(key, None)
    output = {
        "files": modules,
        "summary": {
            "files_checked": len(modules),
            "unused_imports": sum(len(item["unused_imports"]) for item in modules),
            "unused_top_level_defs": sum(len(item["unused_top_level_defs"]) for item in modules),
        },
    }
    return ToolResult.ok(
        "python_static_check",
        json.dumps(output, ensure_ascii=False, indent=2),
        files_checked=len(modules),
        unused_imports=output["summary"]["unused_imports"],
        unused_top_level_defs=output["summary"]["unused_top_level_defs"],
    )


def _module_facts(path: Path, root: Path) -> dict:
    rel = path.relative_to(root).as_posix()
    try:
        tree = _set_parents(ast.parse(path.read_text(encoding="utf-8", errors="replace")))
    except SyntaxError as exc:
        return {"path": rel, "error": f"SyntaxError: {exc}"}
    imports: dict[str, int] = {}
    defs: dict[str, tuple[str, int]] = {}
    used: dict[str, list[int]] = {}
    imported_names: set[str] = set()
    attribute_refs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".", 1)[0]
                imports[name] = node.lineno
                imported_names.add(name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    name = alias.asname or alias.name
                    imports[name] = node.lineno
                    imported_names.add(alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if isinstance(node.parent, ast.Module):
                defs[node.name] = (type(node).__name__, node.lineno)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.setdefault(node.id, []).append(node.lineno)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            attribute_refs.add(f"{node.value.id}.{node.attr}")
    unused_imports = [
        {"name": name, "line": line}
        for name, line in imports.items()
        if name not in used
    ]
    return {
        "path": rel,
        "_module": path.stem,
        "_used_names": sorted(used),
        "_imported_names": sorted(imported_names),
        "_attribute_refs": sorted(attribute_refs),
        "imports": [{"name": name, "line": line, "used": name in used} for name, line in imports.items()],
        "top_level_defs": [{"name": name, "kind": kind, "line": line, "used_in_file": name in used} for name, (kind, line) in defs.items()],
        "unused_imports": unused_imports,
        "unused_top_level_defs": [],
    }


class _ParentSetter(ast.NodeVisitor):
    def generic_visit(self, node):
        for child in ast.iter_child_nodes(node):
            child.parent = node
        super().generic_visit(node)


def _set_parents(tree: ast.AST) -> ast.AST:
    tree.parent = None
    _ParentSetter().visit(tree)
    return tree


python_static_check_tool = ToolDef(
    name="python_static_check",
    description="Batch AST scan for Python imports, top-level definitions, and likely unused items. Use before many grep/code_nav calls for dead-code or duplicate-definition audits.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Python file or directory to scan. Defaults to workspace root."},
        },
    },
    execute=_python_static_check,
)

TOOL = python_static_check_tool
