"""CI wrapper around the Inspect AI routing eval (evals/inspect_routing.py).

Every trial here starts a *real, paid* Claude Code session, so this module is
**opt-in**: it is skipped unless ``RUN_ROUTING_EVAL`` is truthy, and it also
skips cleanly when ``inspect-ai`` or the ``claude`` CLI are unavailable. That
keeps the normal ``pytest`` run (the library's unit tests) fast and free, while
letting CI run the eval on demand via the routing-eval workflow.

The eval runs once per test session (module-scoped fixture); the tests then
assert on its metrics. The headline gate is **negative safety**: over-delegation
is the primary failure mode, and a negative case that delegates is a regression
regardless of CLAUDE.md wording. The positive-rate gate is opt-in because the
shipped (baseline) wording is intentionally permissive and its positive rate is
expected to be low (see evals/README.md).

Configuration (environment variables):

    RUN_ROUTING_EVAL              gate; must be 1/true/yes to run (default: off)
    ROUTING_EVAL_VARIANT          baseline | strict           (default: baseline)
    ROUTING_EVAL_EPOCHS           trials per case              (default: 2)
    ROUTING_EVAL_MAX_SAMPLES      concurrent claude sessions   (default: 3)
    ROUTING_EVAL_MODEL            optional `claude --model`    (default: unset)
    ROUTING_EVAL_FILTER           only run case ids containing this substring
    ROUTING_EVAL_POSITIVE_THRESHOLD
                                  if set, assert positive_rate >= this value
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = PROJECT_ROOT / "evals"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- #
# Skip gates -- decided at import/collection time so an un-runnable eval is a
# clean skip, never a hard error.
# --------------------------------------------------------------------------- #

if not _truthy(os.environ.get("RUN_ROUTING_EVAL")):
    pytest.skip(
        "routing eval is opt-in; set RUN_ROUTING_EVAL=1 to run it "
        "(it starts real Claude Code sessions)",
        allow_module_level=True,
    )

if shutil.which("claude") is None:
    pytest.skip("`claude` CLI not found on PATH", allow_module_level=True)

try:  # noqa: SIM105
    import inspect_ai  # noqa: F401
except ImportError:
    pytest.skip(
        "inspect-ai not installed (pip install -e '.[evals-inspect]')",
        allow_module_level=True,
    )


# --------------------------------------------------------------------------- #
# Run the eval once for the whole module.
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def eval_metrics() -> dict:
    """Run the Inspect routing eval once and return its metrics + status."""
    if str(EVALS_DIR) not in sys.path:
        sys.path.insert(0, str(EVALS_DIR))

    from inspect_ai import eval as inspect_eval
    from inspect_routing import coworker_routing

    variant = os.environ.get("ROUTING_EVAL_VARIANT", "baseline")
    epochs = int(os.environ.get("ROUTING_EVAL_EPOCHS", "2"))
    max_samples = int(os.environ.get("ROUTING_EVAL_MAX_SAMPLES", "3"))
    model_cli = os.environ.get("ROUTING_EVAL_MODEL") or None

    task = coworker_routing(variant=variant, model_cli=model_cli, epochs=epochs)

    filter_substr = os.environ.get("ROUTING_EVAL_FILTER")
    if filter_substr:
        task.dataset = task.dataset.filter(lambda s: filter_substr in str(s.id))
        if len(task.dataset) == 0:
            pytest.skip(f"no cases match ROUTING_EVAL_FILTER={filter_substr!r}")

    with tempfile.TemporaryDirectory(prefix="routing-eval-ci-") as log_dir:
        # mockllm satisfies Inspect's model requirement; the solver drives the
        # real `claude` CLI itself and never calls a model through Inspect.
        logs = inspect_eval(
            task,
            model="mockllm/model",
            max_samples=max_samples,
            log_dir=log_dir,
        )

    log = logs[0] if isinstance(logs, list) else logs
    if log.status != "success":
        err = getattr(log, "error", None)
        pytest.fail(f"eval did not complete: status={log.status} error={err}")

    score = log.results.scores[0]
    metrics = {name: m.value for name, m in score.metrics.items()}
    metrics["_variant"] = variant
    metrics["_epochs"] = epochs
    return metrics


# --------------------------------------------------------------------------- #
# Assertions
# --------------------------------------------------------------------------- #


def test_negatives_never_delegate(eval_metrics: dict):
    """The primary regression gate: no negative case delegates to a coworker.

    Over-delegation is the failure mode that matters most and it should hold
    for any CLAUDE.md wording, so this is a hard assertion in both variants.
    """
    negative_safety = eval_metrics["negative_safety"]
    assert negative_safety == 1.0, (
        f"a negative case delegated at least once "
        f"(negative_safety={negative_safety:.3f}, variant={eval_metrics['_variant']}); "
        "over-delegation is a regression"
    )


def test_positive_rate_meets_threshold(eval_metrics: dict):
    """Optional gate on positive delegation rate.

    Skipped unless ROUTING_EVAL_POSITIVE_THRESHOLD is set: the shipped baseline
    wording is intentionally permissive and its positive rate is expected to be
    low, so this is only meaningful when you opt in (e.g. on the strict variant
    as an achievable upper bound).
    """
    raw = os.environ.get("ROUTING_EVAL_POSITIVE_THRESHOLD")
    if raw is None:
        pytest.skip("set ROUTING_EVAL_POSITIVE_THRESHOLD to enable this gate")
    threshold = float(raw)
    positive_rate = eval_metrics["positive_rate"]
    assert positive_rate >= threshold, (
        f"positive routing rate {positive_rate:.3f} < threshold {threshold:.3f} "
        f"(variant={eval_metrics['_variant']})"
    )
