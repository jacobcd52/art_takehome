"""Dump readable transcripts (target conversation + scores) from an eval log.

Usage: python dump_transcripts.py LOG.eval OUT_DIR [--auditor]
"""

import json
import sys
from pathlib import Path

from inspect_ai.log import read_eval_log
from inspect_ai.model import ChatMessage, ChatMessageAssistant, ChatMessageTool, ContentReasoning


def render_message(i: int, m: ChatMessage) -> str:
    head = f"--- [M{i}] {m.role.upper()}"
    if isinstance(m, ChatMessageTool):
        head += f" (result of {m.function}, call_id={m.tool_call_id})"
        if m.error:
            head += f" ERROR: {m.error.message}"
    lines = [head]
    if isinstance(m, ChatMessageAssistant) and isinstance(m.content, list):
        for c in m.content:
            if isinstance(c, ContentReasoning):
                text = c.summary or ("" if c.redacted else c.reasoning)
                if text:
                    lines.append(f"<reasoning_summary>\n{text}\n</reasoning_summary>")
    if m.text.strip():
        lines.append(m.text)
    if isinstance(m, ChatMessageAssistant):
        for tc in m.tool_calls or []:
            lines.append(f"TOOL CALL {tc.function} (id={tc.id}): {json.dumps(tc.arguments, ensure_ascii=False)}")
    return "\n".join(lines)


def main() -> None:
    log_path, out_dir = sys.argv[1], Path(sys.argv[2])
    show_auditor = "--auditor" in sys.argv
    out_dir.mkdir(parents=True, exist_ok=True)
    log = read_eval_log(log_path)
    for s in log.samples or []:
        md = s.metadata
        parts = [
            f"SAMPLE {s.id} (epoch {s.epoch})",
            f"scenario: {md.get('title')}",
            f"condition: {md.get('condition')}   canary: {md.get('canary')}",
            "",
            "=== SCORES ===",
        ]
        for name, sc in (s.scores or {}).items():
            parts.append(f"[{name}] value={json.dumps(sc.value)}")
            if sc.metadata and "verdict" in sc.metadata:
                parts.append("verdict=" + json.dumps(sc.metadata["verdict"], indent=1, ensure_ascii=False))
            elif sc.explanation:
                parts.append(f"explanation: {sc.explanation[:3000]}")
        if s.error:
            parts.append(f"ERROR: {s.error.message}")
        parts += ["", "=== TARGET CONVERSATION (what the target saw and did) ==="]
        parts += [render_message(i, m) for i, m in enumerate(s.messages, 1)]
        if show_auditor:
            parts += ["", "=== AUDITOR SEED / SPEC ===", s.input if isinstance(s.input, str) else str(s.input)]
        (out_dir / f"{s.id}__e{s.epoch}.txt").write_text("\n\n".join(parts))
    print(f"wrote {len(log.samples or [])} transcripts to {out_dir}")


if __name__ == "__main__":
    main()
