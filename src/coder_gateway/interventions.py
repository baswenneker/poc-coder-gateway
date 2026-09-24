"""The Interventions that change the model's reply: a Proposal appended to the final reply of a Turn,
and a Block that replaces a tool call before the client runs it (DECISIONS.md #28).

Each function returns an `Amendment`; `coder_gateway.reply` applies it to the upstream reply, streamed
or not, so the OpenAI SDK and the ai-sdk openai-compatible provider (used by opencode) parse the
result like a normal model reply.
"""

from __future__ import annotations

import json

from coder_gateway.domain import ProposalSpec, Rule
from coder_gateway.reply import Amendment, ToolCall

QUESTION_TOOL = "question"
TEXT_MODE_SUFFIX = "(answer yes or no)"


def _separator(model_text: str) -> str:
    return "\n\n" if model_text.strip() else ""


def proposal_tool_amendment(spec: ProposalSpec, proposal_id: str) -> Amendment:
    """Proposal as a call to the client's `question` tool (opencode shows it with choice buttons),
    added to the model's final reply. The finish becomes 'tool_calls' so the client runs it."""
    args = {
        "questions": [
            {
                "question": spec.question.strip(),
                "header": spec.header,
                "options": [
                    {"label": spec.accept_label, "description": spec.accept_description},
                    {"label": spec.decline_label, "description": spec.decline_description},
                ],
            }
        ]
    }
    call = ToolCall(id=proposal_id, name=QUESTION_TOOL, arguments=json.dumps(args, ensure_ascii=False))
    return Amendment(finish_reason="tool_calls", tool_call=call)


def proposal_text_amendment(spec: ProposalSpec, model_text: str) -> Amendment:
    """Proposal as plain text after the model's final reply, for clients without a `question` tool.
    The Developer's next message is the answer."""
    return Amendment(
        finish_reason="stop",
        append_text=f"{_separator(model_text)}{spec.question.strip()} {TEXT_MODE_SUFFIX}",
    )


def block_amendment(rule: Rule, model_text: str) -> Amendment:
    """Block: the model's tool calls are left out and the explanation ends the reply (and the Turn)."""
    message = (rule.block_message or f"Blocked by rule {rule.id}.").strip()
    return Amendment(
        finish_reason="stop", append_text=f"{_separator(model_text)}{message}", drop_tool_calls=True
    )
