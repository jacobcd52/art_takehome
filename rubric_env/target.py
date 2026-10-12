"""Target agent for fixed-environment scenarios.

Differs from Petri's default `target_agent` in three ways:

1. The system prompt and first user message come from sample metadata, not from the auditor, so they are identical in every run of a scenario.
2. Tool calls that read/write the virtual filesystem (or have fixed results) are answered in code (see `vfs.py`). The auditor only sees a short note about them and never has to reproduce file contents.
3. The auditor's view of each target turn is stripped of the target's reasoning, so the simulated environment cannot react to private reasoning.
"""

import json
import re

from inspect_ai.agent import Agent, AgentState, agent
from inspect_ai.model import (
    CachePolicy,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    ModelOutput,
    get_model,
)
from inspect_ai.tool import ToolCall
from inspect_petri.target import SYSTEM, TOOL_RESULT, USER, ExitSignal, TargetContext

from .vfs import Resolution, VirtualFS, resolve


class ScriptedEvents:
    """Messages injected by the environment right after a matching target tool call.

    Each event is `{id, after_tool_regex, message}`. The regex is searched in `"<function> <json args>"` of every tool call the target makes; the first time an event matches, its message is appended as a user turn immediately after that turn's tool results. Events fire at most once.
    """

    def __init__(self, events: list[dict[str, str]]) -> None:
        self._events = events
        self._fired: set[str] = set()

    def fire(self, tool_calls: list[ToolCall]) -> list[dict[str, str]]:
        fired: list[dict[str, str]] = []
        for ev in self._events:
            if ev["id"] in self._fired:
                continue
            pattern = re.compile(ev["after_tool_regex"], re.DOTALL)
            if any(pattern.search(f"{tc.function} {json.dumps(tc.arguments)}") for tc in tool_calls):
                self._fired.add(ev["id"])
                fired.append(ev)
        return fired

MAX_AUTO_STEPS = 25  # consecutive target turns answered purely in code before handing to auditor


def _auditor_view(
    message: ChatMessageAssistant, forwarded: list[ToolCall], notes: list[str]
) -> ModelOutput:
    """What the auditor sees: target text (no reasoning), env notes, and the calls it must answer."""
    parts: list[str] = []
    if notes:
        parts.append(
            "[ENVIRONMENT NOTE: the following target tool calls were answered automatically from the "
            "fixed environment. Do NOT respond to them.]\n" + "\n".join(f"- {n}" for n in notes)
        )
    if message.text.strip():
        parts.append(message.text)
    view = ChatMessageAssistant(
        id=message.id,
        content="\n\n".join(parts) if parts else "",
        tool_calls=forwarded or None,
        model=message.model,
    )
    return ModelOutput.from_message(view)


@agent(name="target")
def fixed_env_target(cache: bool | CachePolicy = False) -> Agent:
    """Target agent whose system prompt, first message and filesystem are fixed by the scenario."""

    async def execute(state: AgentState, context: TargetContext) -> AgentState:
        md = context.metadata
        model = get_model(role="target")
        generate = context.replayable(model.generate, surface_errors=True)
        target_cache = context.scoped_cache(cache)
        vfs = VirtualFS(
            files=dict(md["files"]), cwd=md.get("cwd", "/"), roots=list(md.get("vfs_roots", []))
        )
        fixed_results: dict[str, str] = dict(md.get("fixed_results", {}))
        events = ScriptedEvents(list(md.get("events", [])))

        def deliver_events(tool_calls: list[ToolCall], notes: list[str]) -> None:
            for ev in events.fire(tool_calls):
                state.messages.append(ChatMessageUser(content=ev["message"]))
                notes.append(f"scripted event '{ev['id']}' fired: its message was delivered to the target as a user turn")

        try:
            await context.wait_for_resume()
            # the environment owns the opening; discard anything the auditor staged
            context.take(SYSTEM)
            context.take(USER)
            state.messages[:] = [
                ChatMessageSystem(content=md["target_system"]),
                ChatMessageUser(content=md["first_user"]),
            ]

            notes: list[str] = []
            auto_steps = 0
            while True:
                state.output = await generate(
                    input=state.messages, tools=context.tools, cache=target_cache
                )
                message = state.output.message
                state.messages.append(message)
                tool_calls = message.tool_calls or []

                resolved: dict[str, Resolution] = {}
                for tc in tool_calls:
                    if (r := resolve(tc, vfs, fixed_results)) is not None:
                        resolved[tc.id] = r
                forwarded = [tc for tc in tool_calls if tc.id not in resolved]
                for tc in forwarded:
                    command = (tc.arguments or {}).get("command") or (tc.arguments or {}).get("cmd")
                    if isinstance(command, str):
                        vfs.note_simulated_command(command)
                notes.extend(r.note for r in resolved.values())

                # every call answered in code: keep going without the auditor
                if tool_calls and not forwarded and auto_steps < MAX_AUTO_STEPS:
                    auto_steps += 1
                    state.messages.extend(
                        ChatMessageTool(
                            content=resolved[tc.id].content, tool_call_id=tc.id, function=tc.function
                        )
                        for tc in tool_calls
                    )
                    deliver_events(tool_calls, notes)
                    continue

                auto_steps = 0
                context.expect({TOOL_RESULT: {tc.id for tc in forwarded}})
                await context.send_output(_auditor_view(message, forwarded, notes))
                notes = []

                if tool_calls:
                    auditor_results = (
                        {m.tool_call_id: m for m in await context.tool_results(forwarded)}
                        if forwarded
                        else {}
                    )
                    for tc in tool_calls:
                        if tc.id in resolved:
                            state.messages.append(
                                ChatMessageTool(
                                    content=resolved[tc.id].content,
                                    tool_call_id=tc.id,
                                    function=tc.function,
                                )
                            )
                        else:
                            state.messages.append(auditor_results[tc.id])
                    deliver_events(tool_calls, notes)
                    if user_message := await context.user_message(required=False):
                        state.messages.append(user_message)
                else:
                    state.messages.append(await context.user_message())
        except ExitSignal:
            return state

    return execute
