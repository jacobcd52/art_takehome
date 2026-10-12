"""Export elicitation runs: every rollback branch, auditor report, overrides and verdict.

Usage: python export_elicit.py LOG.eval OUT_DIR
Writes OUT_DIR/<scenario>__e<epoch>.txt (for reading) and OUT_DIR/runs.json (for the viewer).
"""

import json
import re
import sys
from pathlib import Path

from inspect_ai.log import read_eval_log, resolve_sample_attachments
from inspect_ai.model import ChatMessage, ChatMessageAssistant, ChatMessageTool, ContentReasoning
from inspect_petri._judge.branches import select_timeline, transcript_branches


ATTACH = re.compile(r"attachment://([0-9a-f]{32})")


def msg_dict(m: ChatMessage) -> dict:
    reasoning = ""
    if isinstance(m, ChatMessageAssistant) and isinstance(m.content, list):
        reasoning = "\n\n".join(
            c.summary or ("" if c.redacted else c.reasoning) or ""
            for c in m.content
            if isinstance(c, ContentReasoning)
        ).strip()
    return {
        "role": m.role,
        "text": m.text,
        "reasoning": reasoning,
        "tool_calls": [
            {"function": tc.function, "arguments": tc.arguments}
            for tc in (m.tool_calls or [])
        ]
        if isinstance(m, ChatMessageAssistant)
        else [],
        "tool": m.function if isinstance(m, ChatMessageTool) else None,
        "error": m.error.message if isinstance(m, ChatMessageTool) and m.error else None,
    }


def msg_text(i: int, d: dict) -> str:
    head = f"[M{i}] {d['role'].upper()}" + (f" ({d['tool']})" if d["tool"] else "") + (f" ERROR {d['error']}" if d["error"] else "")
    parts = [head]
    if d["reasoning"]:
        parts.append(f"<reasoning_summary>\n{d['reasoning']}\n</reasoning_summary>")
    if d["text"].strip():
        parts.append(d["text"])
    parts += [f"TOOL CALL {tc['function']}: {json.dumps(tc['arguments'], ensure_ascii=False)}" for tc in d["tool_calls"]]
    return "\n".join(parts)


def main() -> None:
    log_path, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    runs = []
    for s in read_eval_log(log_path).samples or []:
        attachments = dict(s.attachments or {})
        s = resolve_sample_attachments(s, "full")
        md = s.metadata
        bj = s.scores.get("behaviour_judge") if s.scores else None
        verdict = (bj.metadata or {}).get("verdict", {}) if bj else {}
        try:
            branches = [[msg_dict(m) for m in br] for br in transcript_branches(select_timeline(s.timelines, "target"))]
        except Exception:  # noqa: BLE001
            branches = []
        if not branches:
            branches = [[msg_dict(m) for m in s.messages]]
        branches = json.loads(ATTACH.sub(lambda m: json.dumps(attachments.get(m.group(1), m.group(0)))[1:-1], json.dumps(branches)))
        run = {
            "scenario": md["scenario_id"],
            "title": md["title"],
            "epoch": s.epoch,
            "bad_action": verdict.get("bad_action"),
            "verdict": verdict,
            "saw_rubric": (s.scores["rubric_exposure"].value or {}).get("saw_rubric") if s.scores and "rubric_exposure" in s.scores else None,
            "report": md.get("_elicitation_reports", []),
            "overrides": md.get("_overrides", {}),
            "branches": branches,
            "judge_bad": md["judge"]["bad_action"],
        }
        runs.append(run)
        txt = [
            f"RUN {run['scenario']} epoch {run['epoch']}  |  judge bad_action={run['bad_action']}  saw_rubric={run['saw_rubric']}",
            "BAD ACTION DEFINITION:\n" + run["judge_bad"],
            "JUDGE VERDICT:\n" + json.dumps(verdict, indent=1, ensure_ascii=False),
            "AUDITOR ELICITATION REPORT:\n" + json.dumps(run["report"], indent=1, ensure_ascii=False),
            "ENVIRONMENT OVERRIDES:\n" + ("\n".join(f"--- {k}\n{v}" for k, v in run["overrides"].items()) or "none"),
        ]
        for bi, br in enumerate(branches, 1):
            txt.append(f"\n########## BRANCH {bi}/{len(branches)} (full conversation incl. replayed prefix) ##########")
            txt += [msg_text(i, d) for i, d in enumerate(br, 1)]
        (out / f"{run['scenario']}__e{run['epoch']}.txt").write_text("\n\n".join(txt))
    (out / "runs.json").write_text(json.dumps(runs, ensure_ascii=False))
    print(f"exported {len(runs)} runs to {out}")


if __name__ == "__main__":
    main()
