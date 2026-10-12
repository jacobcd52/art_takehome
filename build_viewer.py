"""Build the elicitation transcript viewer (single self-contained HTML page).

Usage: python build_viewer.py logs/elicit_export/runs.json OUT.html
Picks, for every scenario with at least one bad-action run, one bad-action run (highest score, preferring
non-induced) and one clean run, and embeds them with any reviewer notes from docs/elicit_review/.
"""

import json
import sys
from pathlib import Path

TEMPLATE = Path(__file__).parent / "viewer_template.html"


def pick(runs: list[dict]) -> dict:
    by: dict[str, list[dict]] = {}
    for r in runs:
        by.setdefault(r["scenario"], []).append(r)
    out = []
    for sid, rs in sorted(by.items()):
        bad = [r for r in rs if (r["bad_action"] or 0) >= 1]
        if not bad:
            continue
        bad.sort(key=lambda r: (-(r["bad_action"] or 0), bool(r["verdict"].get("induced")), r["epoch"]))
        clean = sorted([r for r in rs if (r["bad_action"] or 0) == 0], key=lambda r: r["epoch"])
        review = Path("docs/elicit_review") / f"{sid}.md"
        out.append(
            {
                "id": sid,
                "title": rs[0]["title"],
                "hits": sum(1 for r in rs if (r["bad_action"] or 0) >= 2),
                "any": len(bad),
                "n": len(rs),
                "review": review.read_text() if review.exists() else "",
                "runs": [bad[0]] + ([clean[0]] if clean else []),
            }
        )
    return {"scenarios": out}


def main() -> None:
    runs = json.loads(Path(sys.argv[1]).read_text())
    data = json.dumps(pick(runs), ensure_ascii=False).replace("</", "<\\/")
    Path(sys.argv[2]).write_text(TEMPLATE.read_text().replace("__DATA__", data))
    print("wrote", sys.argv[2])


if __name__ == "__main__":
    main()
