"""Build the elicitation transcript viewer (single self-contained HTML page).

Usage: python build_viewer.py logs/elicit_export/runs.json OUT.html
Picks, for every scenario with at least one bad-action run, one bad-action run (highest score, preferring
non-induced) and one clean run, and embeds them with any reviewer notes from docs/elicit_review/.
"""

import json
import yaml
import sys
from pathlib import Path

TEMPLATE = Path(__file__).parent / "viewer_template.html"


KEEP = [
    "p1_09c_consent_autorenew", "p1_03_containment_fraud", "p3_10_deceive_approver", "p2_07_resist_replacement",
    "p3_06_self_proliferation", "p2_04_human_review_oversight", "p1_05b_photo_dedupe", "p1_05a_flight_rebook",
    "p1_05d_crm_import", "p1_06a_lonely_user", "p2_03_output_filter", "p1_05e_research_citations",
]


def pick(runs: list[dict], scores: list[dict]) -> dict:
    """Per scenario: the single branch with the clearest bad action, and one branch without it."""
    bscore = {(x["scenario"], x["epoch"], x["branch"]): x for x in scores}
    by: dict[str, list[dict]] = {}
    for r in runs:
        by.setdefault(r["scenario"], []).append(r)
    out = []
    for sid in KEEP:
        rs = by[sid]
        cands = []
        for r in rs:
            for bi, br in enumerate(r["branches"]):
                sc = bscore.get((sid, r["epoch"], bi), {})
                cands.append((sc.get("score"), r, bi, br, sc))
        bad = sorted([c for c in cands if (c[0] or 0) >= 1], key=lambda c: (-(c[0] or 0), c[1]["epoch"], c[2]))
        clean = sorted([c for c in cands if c[0] == 0], key=lambda c: ((c[1]["bad_action"] or 0) > 0, c[1]["epoch"], -len(c[3])))
        shown = []
        for label, c in (("Bad action", bad[0] if bad else None), ("No bad action", clean[0] if clean else None)):
            if c is None:
                continue
            score, r, bi, br, sc = c
            shown.append({**{k: v for k, v in r.items() if k != "branches"}, "label": label, "branch_index": bi,
                          "n_branches": len(r["branches"]), "branch_score": score, "branch_evidence": sc.get("evidence", ""),
                          "branch_message": sc.get("message"), "messages": br})
        review = Path("docs/elicit_review") / f"{sid}.md"
        short = yaml.safe_load(open(f"scenarios/{sid}.yaml"))["short_name"]
        out.append({"id": sid, "short": short, "title": rs[0]["title"], "n": len(rs),
                    "hits": sum(1 for r in rs if (r["bad_action"] or 0) >= 2), "any": sum(1 for r in rs if (r["bad_action"] or 0) >= 1),
                    "branch_hits": sum(1 for c in cands if (c[0] or 0) >= 1), "branches": len(cands),
                    "review": review.read_text() if review.exists() else "", "runs": shown})
    return {"scenarios": out}


def main() -> None:
    runs = json.loads(Path(sys.argv[1]).read_text())
    scores = json.loads((Path(sys.argv[1]).parent / "branch_scores.json").read_text())
    data = json.dumps(pick(runs, scores), ensure_ascii=False).replace("</", "<\\/")
    Path(sys.argv[2]).write_text(TEMPLATE.read_text().replace("__DATA__", data))
    print("wrote", sys.argv[2])


if __name__ == "__main__":
    main()
