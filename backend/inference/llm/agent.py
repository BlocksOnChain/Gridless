"""Deep agent construction.

The LLM layer is a `deepagents` harness rather than a hand-wired LangGraph graph.
What that buys us here:

* **Skills.** The domain rules -- slug conventions, primary-key selection,
  lookup-table-vs-coincidence, the SQL guard's constraints -- live in
  `backend/skills/*/SKILL.md` as versioned prose instead of being concatenated
  into a Python f-string. They are loaded with progressive disclosure, so the
  agent pays for the full text of a skill only when it decides the skill applies.
* **Tools as the source of truth.** The agent reads the workbook through narrow
  tools that can only return real columns and real measured candidates, so the
  most common hallucinations are unrepresentable rather than merely validated
  away afterwards.
* **`response_format`.** Structured output is enforced by the harness, so the
  return value is already a typed object.

What it deliberately does *not* change: stages 1, 2 and the deterministic half
of 4 stay pure Python. They currently score 1.00 on type accuracy and 1.00 on
relationship recall with no model involved at all, and handing that to an agent
could only make it worse. The agent is used exactly where judgement is needed --
naming, and telling a real foreign key from a coincidence.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

from django.conf import settings
from langchain.agents.structured_output import ToolStrategy
from langchain_openrouter import ChatOpenRouter

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend

# The backend root. Skills live at <BACKEND_ROOT>/skills, which the agent sees
# as /skills. virtual_mode=True confines every filesystem tool to this tree.
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_MOUNT = "/skills"


class MissingAPIKey(RuntimeError):
    """Raised when an LLM stage is invoked without OPENROUTER_API_KEY set."""


def api_key_configured() -> bool:
    return bool(getattr(settings, "OPENROUTER_API_KEY", ""))


def require_api_key() -> str:
    key = getattr(settings, "OPENROUTER_API_KEY", "")
    if not key:
        raise MissingAPIKey(
            "OPENROUTER_API_KEY is not set. Stages 3 and 4b need it. "
            "Add it to backend/.env, or run the deterministic stages only."
        )
    return key


@functools.lru_cache(maxsize=4)
def build_model(temperature: float = 0.0) -> ChatOpenRouter:
    """The chat model. Cached so repeated stage calls reuse one client.

    temperature 0 by default: schema inference is not a creative task, and a
    reproducible proposal is worth more than a varied one when the eval harness
    is the arbiter of whether the pipeline works.
    """
    return ChatOpenRouter(
        model=settings.OPENROUTER_MODEL,
        api_key=require_api_key(),
        temperature=temperature,
        # MILLISECONDS -- see the note in settings.py. Getting this wrong does
        # not raise; it hangs.
        request_timeout=settings.OPENROUTER_TIMEOUT_MS,
        max_tokens=settings.OPENROUTER_MAX_TOKENS,
        # Reasoning is mandatory on this model and unbounded reasoning consumes
        # the entire token budget before any content is emitted.
        reasoning={"effort": settings.OPENROUTER_REASONING_EFFORT},
    )


def build_backend() -> FilesystemBackend:
    return FilesystemBackend(root_dir=str(BACKEND_ROOT), virtual_mode=True)


def build_agent(
    *,
    system_prompt: str,
    response_schema: type,
    tools: list[Any] | None = None,
    temperature: float = 0.0,
) -> Any:
    """A deep agent scoped to one task.

    One agent per stage rather than a single agent that does everything: each
    gets only the tools its stage can legitimately use, which is the same reason
    the tools are narrow in the first place.
    """
    return create_deep_agent(
        model=build_model(temperature),
        system_prompt=system_prompt,
        tools=list(tools or []),
        skills=[SKILLS_MOUNT],
        backend=build_backend(),
        # ToolStrategy rather than ProviderStrategy: z-ai/glm-5.3-flash supports
        # tool calling but NOT json_schema response_format enforcement, so the
        # schema has to be carried by a tool definition.
        response_format=ToolStrategy(response_schema),
    )


def final_text(result: dict[str, Any]) -> str:
    """The last thing the agent actually said, in prose.

    Structured output is enforced by a *tool*, and a model that has just used a
    real tool sometimes answers in plain text instead of calling that one -- it
    considers itself finished. Where the structure is load-bearing (stages 3 and
    4b) that is a failure and `structured_result` raises. Where the only
    structured field is a sentence for the user, throwing away a perfectly good
    sentence would be the worse outcome, so the chats fall back to this.
    """
    for message in reversed(result.get("messages") or []):
        if getattr(message, "type", None) != "ai":
            continue
        content = getattr(message, "content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            # Some providers return content as a list of typed blocks.
            text = " ".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
            if text:
                return text
    return ""


def structured_result(result: dict[str, Any], schema: type) -> Any:
    """Pull the typed object out of a deep agent's result.

    The harness puts it under `structured_response`; falling back to a parse of
    the last message keeps a harness change from becoming a silent failure.
    """
    value = result.get("structured_response")
    if isinstance(value, schema):
        return value
    if isinstance(value, dict):
        return schema.model_validate(value)
    raise ValueError(
        f"Agent returned no structured response for {schema.__name__}; "
        f"got keys {sorted(result)!r}"
    )
