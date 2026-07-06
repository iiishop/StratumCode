"""System prompt assembler — sections composed into a single prompt."""

from __future__ import annotations

from .tools import registry


PERSONA = """\
You are StratumCode, a software engineering agent.
You have access to the workspace file system and can search, read, and analyse code.
Your job is to understand the user's intent, explore the codebase, and answer questions."""

RULES = """\
## Rules
- Always read the relevant files before proposing changes.
- Prefer searching with grep and glob before asking the user.
- Write concise, correct code. Avoid unnecessary comments.
- If you are unsure, ask before acting.
- Report errors honestly; never fabricate results.
"""

TOOLS_HEADER = """\
## Available tools

You have access to the following tools. Call them by returning a tool-use block.
Each tool expects a JSON object as arguments.
"""

WORKSPACE_SECTION = """\
## Workspace
- Platform: {platform}
- Directory: {directory}
"""


def build(*, directory: str = ".", platform: str = "") -> str:
    sections = [
        PERSONA,
        RULES,
        WORKSPACE_SECTION.format(platform=platform or "unknown", directory=directory),
        TOOLS_HEADER,
        registry.describe(),
    ]
    return "\n\n".join(s.strip() for s in sections if s.strip())


def build_compact(*, directory: str = ".", platform: str = "") -> str:
    sections = [
        PERSONA,
        WORKSPACE_SECTION.format(platform=platform or "unknown", directory=directory),
        TOOLS_HEADER,
        registry.describe(),
    ]
    return "\n\n".join(s.strip() for s in sections if s.strip())
