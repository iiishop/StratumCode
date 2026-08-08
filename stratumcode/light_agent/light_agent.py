from __future__ import annotations

from .. import model_settings
from ..agent_runtime import call_model, content_text


def ask(prompt: str) -> str:
    setting = model_settings.resolve(model_settings.LIGHT_AGENT)
    if setting is None:
        raise ValueError("no model configured for light_agent stage")

    assistant = call_model(
        setting["provider"],
        setting["model_id"],
        [{"role": "user", "content": prompt}],
        use_skills=False,
    )
    return content_text(assistant.get("content") or "")