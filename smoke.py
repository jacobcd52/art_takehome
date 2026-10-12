"""Petri smoke test: GPT-5 (Azure) as auditor, target and judge on one seed."""
import sys
from dotenv import load_dotenv
from inspect_ai import eval
from inspect_ai.model import get_model
from inspect_petri import audit

load_dotenv(override=True)
DEPLOYMENT = sys.argv[2] if len(sys.argv) > 2 else "openai/azure/gpt-5-Alignment-Interview"
gpt5 = get_model(DEPLOYMENT, responses_api=False)  # Azure deployment only serves chat completions

eval(
    audit(seed_instructions=sys.argv[1] if len(sys.argv) > 1 else "manic_episode_sycophancy", max_turns=4),
    model_roles={"auditor": gpt5, "target": gpt5, "judge": gpt5},
    log_dir="logs",
    display="plain",
)
