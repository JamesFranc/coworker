"""Inspect AI port of the coworker-tools routing eval.

This is a *thin adapter* over the existing ``run_eval.py`` harness, re-expressed
in Inspect AI's ``Dataset -> Solver -> Scorer`` model. It deliberately reuses
``run_eval``'s workspace setup, stream parsing, tool detection, and -- crucially
-- its ``score_run`` scoring logic, so results are directly comparable with the
bespoke runner. What Inspect adds: a recognized eval vocabulary, the
``inspect view`` log viewer, per-sample transcripts, and result tracking, while
replacing the hand-rolled ThreadPoolExecutor / timeout / epoch plumbing.

Mapping from the original harness to Inspect concepts:

    cases.json case          -> Sample (input=prompt, id, metadata=case)
    --variant baseline|strict -> task param `variant`
    --repeat N                -> epochs (default 3, "mean" reducer == per-case rate)
    --jobs N                  -> `inspect eval ... --max-samples N`
    run_eval.run_trial(...)   -> solver (run Claude Code in an isolated workspace)
    run_eval.score_run(...)   -> scorer (positive / negative / soft semantics, verbatim)
    --strict-exit thresholds  -> the routing_accuracy / negative_safety metrics below

Run it:

    # strict wording, 3 epochs, 3 concurrent sessions
    python evals/run_inspect.py --variant strict --epochs 3 --max-samples 3

    # or straight through the Inspect CLI (mockllm satisfies Inspect's model
    # requirement; the solver never actually calls a model):
    inspect eval evals/inspect_routing.py --model mockllm/model \
        -T variant=strict --epochs 3 --max-samples 3

    # a single case
    inspect eval evals/inspect_routing.py --model mockllm/model \
        --sample-id neg_security_review

Requires:  pip install inspect-ai   (and the `claude` CLI on PATH)
"""

from __future__ import annotations

import functools
import json
import sys
from pathlib import Path
from types import SimpleNamespace

# --------------------------------------------------------------------------- #
# Reuse the existing harness internals so scoring semantics stay identical.
# inspect_routing.py lives next to run_eval.py in evals/.
# --------------------------------------------------------------------------- #
EVALS_DIR = Path(__file__).resolve().parent
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))

import run_eval  # noqa: E402  (sibling module; defines run_trial, score_run, ...)

import anyio  # noqa: E402  (ships with inspect-ai)
from inspect_ai import Epochs, Task, task  # noqa: E402
from inspect_ai.dataset import MemoryDataset, Sample  # noqa: E402
from inspect_ai.model import ModelOutput  # noqa: E402
from inspect_ai.scorer import (  # noqa: E402
    CORRECT,
    INCORRECT,
    Metric,
    SampleScore,
    Score,
    Scorer,
    Target,
    metric,
    scorer,
    stderr,
    value_to_float,
)
from inspect_ai.solver import (  # noqa: E402
    Generate,
    Solver,
    TaskState,
    solver,
)

CASES_PATH = EVALS_DIR / "cases.json"
_RUN_KEY = "run_result"  # store key for the RunResult handed from solver -> scorer


# --------------------------------------------------------------------------- #
# Metrics
#
# The original harness pools rates over *non-soft* cases and treats negatives
# as a hard safety gate (must be 1.0). We mirror that here, ignoring soft edge
# cases so they are reported in the log but never move the headline numbers --
# exactly the "* = soft edge, never affects exit code" rule.
# --------------------------------------------------------------------------- #


def _score_of(item):
    """Accept either a SampleScore (newer Inspect) or a bare Score (older)."""
    return getattr(item, "score", item)


def _mean_over(scores: list[SampleScore], predicate) -> float:
    to_float = value_to_float()
    vals = []
    for item in scores:
        sc = _score_of(item)
        meta = sc.metadata or {}
        if predicate(meta):
            vals.append(to_float(sc.value))
    return sum(vals) / len(vals) if vals else 0.0


@metric
def routing_accuracy() -> Metric:
    """Pooled correctness over all non-soft cases (positives + negatives)."""

    def m(scores: list[SampleScore]) -> float:
        return _mean_over(scores, lambda meta: not meta.get("soft"))

    return m


@metric
def positive_rate() -> Metric:
    """Delegation rate over non-soft positive cases (the 'did it route?' number)."""

    def m(scores: list[SampleScore]) -> float:
        return _mean_over(
            scores,
            lambda meta: meta.get("category") == "positive" and not meta.get("soft"),
        )

    return m


@metric
def negative_safety() -> Metric:
    """Over-delegation gate: fraction of negatives that correctly declined.

    The original harness fails the suite if this is below 1.0 -- any negative
    that delegates even once is a regression. (On a filtered run with no
    negative cases this reads 0.0; it is only meaningful on the full suite.)
    """

    def m(scores: list[SampleScore]) -> float:
        return _mean_over(
            scores,
            lambda meta: meta.get("category") == "negative" and not meta.get("soft"),
        )

    return m


# --------------------------------------------------------------------------- #
# Solver: run Claude Code headless in an isolated workspace.
#
# We do not call an LLM through Inspect's model API -- the "system under test"
# is the `claude` CLI driven by the workspace CLAUDE.md. So this solver simply
# delegates to run_eval.run_trial (offloaded to a worker thread so Inspect can
# still run samples concurrently) and stashes the RunResult for the scorer.
# --------------------------------------------------------------------------- #


@solver
def claude_router(
    variant: str = "baseline",
    timeout: int = 300,
    model_cli: str | None = None,
) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        # state.metadata carries the full cases.json object (see task() below),
        # which is exactly the `case` dict run_eval expects.
        case = dict(state.metadata)
        case.setdefault("prompt", state.input_text)

        run_args = SimpleNamespace(
            keep_artifacts=False,  # Inspect's log captures everything we need
            model=model_cli,
            timeout=timeout,
        )
        run = await anyio.to_thread.run_sync(
            functools.partial(run_eval.run_trial, case, variant, 0, run_args, None)
        )

        state.store.set(_RUN_KEY, run)

        # Surface the routing decision in the transcript / viewer.
        summary = (
            f"delegated: {run.delegated or 'none'}\n"
            f"matched_command: {run.matched_command or '-'}\n"
            f"n_bash_commands: {len(run.all_commands)}\n"
            f"timed_out: {run.timed_out}  error: {run.error}"
        )
        state.output = ModelOutput.from_content(model="claude-code", content=summary)
        return state

    return solve


# --------------------------------------------------------------------------- #
# Scorer: reuse run_eval.score_run verbatim, then translate to an Inspect Score.
# --------------------------------------------------------------------------- #


@scorer(metrics=[routing_accuracy(), positive_rate(), negative_safety(), stderr()])
def routing_scorer() -> Scorer:
    async def score(state: TaskState, target: Target) -> Score:
        case = dict(state.metadata)
        run = state.store.get(_RUN_KEY)

        ok = run_eval.score_run(case, run) if run is not None else False
        delegated = run.delegated if run is not None else None

        if run is None:
            explanation = "no RunResult (solver did not run)"
        elif run.timed_out:
            explanation = "timed out before a routing decision"
        elif run.error:
            explanation = f"infra/error: {run.error}"
        else:
            expect = case.get("expect")
            explanation = (
                f"expected={expect!r} delegated={delegated!r} "
                f"args={case.get('expect_args', [])} matched={run.matched_command!r}"
            )

        return Score(
            value=CORRECT if ok else INCORRECT,
            answer=delegated or "none",
            explanation=explanation,
            metadata={
                "category": case.get("category"),
                "expect": case.get("expect"),
                "soft": bool(case.get("soft", False)),
                "informational": case.get("expect") is None,
                "timed_out": bool(getattr(run, "timed_out", False)),
                "error": getattr(run, "error", None),
            },
        )

    return score


# --------------------------------------------------------------------------- #
# Task
# --------------------------------------------------------------------------- #


def _load_samples(filter_substr: str | None = None) -> list[Sample]:
    cases = json.loads(CASES_PATH.read_text())
    if filter_substr:
        cases = [c for c in cases if filter_substr in c["id"]]
    return [
        Sample(input=c["prompt"], id=c["id"], metadata=c)
        for c in cases
    ]


@task
def coworker_routing(
    variant: str = "baseline",
    timeout: int = 300,
    model_cli: str | None = None,
    epochs: int = 3,
) -> Task:
    """coworker-tools delegation-routing eval.

    Args (override with ``-T name=value`` on the Inspect CLI):
        variant:    "baseline" (shipped CLAUDE.md.template) or "strict"
                    (evals/claude_strict.md upper bound).
        timeout:    per-session timeout in seconds (default 300).
        model_cli:  optional model passed through to `claude --model`.
        epochs:     trials per case; routing is stochastic (default 3).
                    The "mean" reducer makes each case's score its pass rate,
                    matching the original harness's correct/total.
    """
    return Task(
        dataset=MemoryDataset(_load_samples()),
        solver=claude_router(variant=variant, timeout=timeout, model_cli=model_cli),
        scorer=routing_scorer(),
        epochs=Epochs(epochs, "mean"),
    )
