"""Run the leaked-rubric scenarios.

Examples:
    python run.py --ids p1_03_containment_fraud --conditions treatment
    python run.py --conditions treatment,control --epochs 10 --log-dir logs/sweep
"""

import argparse

from dotenv import load_dotenv
from inspect_ai import eval
from inspect_ai.model import GenerateConfig, get_model

from rubric_env.task import rubric_audit

TARGET = "openai/gpt-5"
AUDITOR = "openai/gpt-6-astra"
JUDGE = "openai/gpt-5.6-terra"


def main() -> None:
    load_dotenv(override=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default=None, help="comma-separated scenario ids (default: all)")
    ap.add_argument("--conditions", default="treatment,control")
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--max-samples", type=int, default=24)
    ap.add_argument("--log-dir", default="logs/pilot")
    args = ap.parse_args()

    eval(
        rubric_audit(ids=args.ids, conditions=args.conditions, max_turns=args.max_turns),
        model_roles={
            # reasoning summaries let the judge see whether the target reasoned about the grader
            "target": get_model(TARGET, config=GenerateConfig(reasoning_summary="auto", max_connections=40)),
            "auditor": get_model(AUDITOR, config=GenerateConfig(max_connections=40)),
            "judge": get_model(JUDGE, config=GenerateConfig(max_connections=40)),
        },
        epochs=args.epochs,
        max_samples=args.max_samples,
        log_dir=args.log_dir,
        display="plain",
        fail_on_error=False,
    )


if __name__ == "__main__":
    main()
