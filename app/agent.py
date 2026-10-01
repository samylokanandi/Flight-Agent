"""The agent loop, written by hand on purpose.

Every agent framework is a dressed up version of this:
  ask model -> model requests tools -> run them -> send results back -> repeat
"""

from dataclasses import dataclass, field

import anthropic

from app.config import settings
from app.tools.registry import TOOLS, run_tool

SYSTEM_PROMPT = """You are a flight tracking assistant.

You have tools for live data. Rules:
* Never guess flight times, gates, delays, or positions. If you need live data, call a tool.
* If a tool returns an error, tell the user plainly what went wrong and what they can try.
* If a question is ambiguous (e.g. no date, or a flight number that could match several days), \
use the most recent result and say which one you used.
* Give times in the airport's local time when the data includes it, and say so.
* Keep answers short and practical."""


@dataclass
class AgentResult:
    answer: str
    trace: list[dict] = field(default_factory=list)  # every tool call, for debugging/evals
    iterations: int = 0
    messages: list = field(default_factory=list)  # full history, pass back for follow ups
    input_tokens: int = 0
    output_tokens: int = 0


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def run_agent(question: str, history: list | None = None) -> AgentResult:
    client = _get_client()
    messages = list(history or []) + [{"role": "user", "content": question}]
    result = AgentResult(answer="", messages=messages)

    for i in range(1, settings.max_agent_iterations + 1):
        result.iterations = i
        response = client.messages.create(
            model=settings.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        result.input_tokens += response.usage.input_tokens
        result.output_tokens += response.usage.output_tokens
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            result.answer = "".join(b.text for b in response.content if b.type == "text")
            return result

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            content, is_error = run_tool(block.name, block.input)
            result.trace.append(
                {
                    "step": i,
                    "tool": block.name,
                    "input": block.input,
                    "is_error": is_error,
                    "output_chars": len(content),
                    "output_preview": content[:300],
                }
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                }
            )
        messages.append({"role": "user", "content": tool_results})

    result.answer = "I hit my step limit before finishing. Try asking a narrower question."
    return result
