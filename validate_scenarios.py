"""Static checks for scenario YAML files.

Usage: python validate_scenarios.py [scenario_id ...]
"""

import json
import re
import sys

import yaml
from inspect_ai.tool import ToolInfo
from pydantic import TypeAdapter

from rubric_env.scenarios import CONDITIONS, GRADING_TOKEN, build_sample, load_scenarios


def check(sc: dict) -> list[str]:
    errs: list[str] = []
    canary = sc["canary"]
    if not re.fullmatch(r"rb-[0-9a-f]{8}", canary):
        errs.append(f"canary {canary!r} should look like rb-xxxxxxxx")
    try:
        TypeAdapter(list[ToolInfo]).validate_python(sc.get("tools", []))
    except Exception as e:  # noqa: BLE001
        errs.append(f"tools invalid: {e}")
    names = [t["name"] for t in sc.get("tools", [])]
    for n in sc.get("fixed_results", {}):
        if n not in names:
            errs.append(f"fixed_results for unknown tool {n!r}")
    for ev in sc.get("events", []):
        for k in ("id", "after_tool_regex", "message"):
            if k not in ev:
                errs.append(f"event missing {k}: {ev}")
        try:
            re.compile(ev.get("after_tool_regex", ""))
        except re.error as e:
            errs.append(f"event {ev.get('id')} bad regex: {e}")
    for k in ("bad_action", "good_action"):
        if not sc["judge"].get(k):
            errs.append(f"judge.{k} missing")
    for cond in CONDITIONS:
        s = build_sample(sc, cond)
        md = s.metadata
        blobs = {"target_system": md["target_system"], "first_user": md["first_user"], **md["files"], **md["fixed_results"]}
        for name, text in blobs.items():
            if GRADING_TOKEN in text:
                errs.append(f"[{cond}] placeholder left in {name}")
            stripped = text.strip()
            try:
                if name.endswith(".json") or (name in md["fixed_results"] and stripped[:1] in "{["):
                    json.loads(stripped)
                elif name.endswith((".yaml", ".yml")):
                    yaml.safe_load(text)
            except Exception as e:  # noqa: BLE001
                errs.append(f"[{cond}] {name} does not parse: {e}")
        has_canary = any(canary in t for t in blobs.values())
        if cond == "treatment" and not has_canary:
            errs.append("treatment does not contain canary")
        if cond != "treatment" and has_canary:
            errs.append(f"{cond} contains canary")
        if canary in s.input:
            errs.append("auditor seed leaks the canary / grading block")
    return errs


def main() -> None:
    ids = sys.argv[1:] or None
    bad = 0
    for sc in load_scenarios("scenarios", ids):
        errs = check(sc)
        print(("OK  " if not errs else "FAIL") + f" {sc['id']}")
        for e in errs:
            print(f"     - {e}")
        bad += bool(errs)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
