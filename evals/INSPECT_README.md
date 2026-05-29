# Routing eval — Inspect AI port

A prototype that re-expresses the existing routing eval (see `README.md`) in
[**Inspect AI**](https://inspect.aisi.org.uk/), the UK AI Safety Institute's
open-source eval framework. It runs **the same cases, the same Claude Code
sessions, and the same scoring logic** as `run_eval.py` — it is a thin adapter,
not a rewrite — so you can compare the two harnesses side by side and decide
whether to adopt a standard framework.

## Why bother

`run_eval.py` is a solid bespoke harness. Inspect adds what a bespoke runner
doesn't give you for free:

- a **recognized vocabulary** (`Dataset` / `Solver` / `Scorer` / `Metric`) and
  a documented framework reviewers already know;
- the **`inspect view` log viewer** — every run produces a `.eval` log with the
  full per-sample transcript, the routing decision, and the score;
- **epoch handling, concurrency, and timeouts** managed by the framework
  instead of hand-rolled `ThreadPoolExecutor` + `subprocess.timeout` code;
- a path to **result tracking over time** and to reusable scorers (incl.
  LLM-as-judge) if the eval grows beyond deterministic routing checks.

## What maps to what

| `run_eval.py` | Inspect AI |
|---|---|
| a `cases.json` case | `Sample(input=prompt, id, metadata=case)` |
| `--variant baseline\|strict` | task param `-T variant=...` |
| `--repeat N` | `--epochs N` (`"mean"` reducer ⇒ per-case pass rate) |
| `--jobs N` | `--max-samples N` |
| `run_trial(...)` (run Claude Code in a temp workspace) | the `claude_router` **solver** |
| `score_run(...)` (positive/negative/soft semantics) | the `routing_scorer` **scorer** (reused verbatim) |
| `--strict-exit` thresholds | `routing_accuracy` / `positive_rate` / `negative_safety` metrics + `run_inspect.py --strict-exit` |

The solver imports and calls `run_eval.run_trial` / `run_eval.score_run`
directly, so **scoring is identical by construction** — the same workspace
setup, stub PATH, stream parsing, both-invocation-form tool detection, and the
"a negative that times out is incorrect" rule all carry over unchanged.

## Install

```bash
pip install inspect-ai          # or: pip install -e '.[evals-inspect]'
```

You still need the `claude` CLI on `PATH` — the "system under test" is Claude
Code driven by the workspace `CLAUDE.md`, exactly as in the original harness.

## Run

```bash
# strict wording, 3 epochs, 3 concurrent sessions (the recommended runner)
python evals/run_inspect.py --variant strict  --epochs 3 --max-samples 3

# shipped wording (the regression truth — expect lower positive rates)
python evals/run_inspect.py --variant baseline --epochs 3

# a single case
python evals/run_inspect.py --filter neg_security_review --epochs 1

# CI-style gate (parity with run_eval.py --strict-exit)
python evals/run_inspect.py --variant baseline --strict-exit --positive-threshold 0.5
```

Or straight through the Inspect CLI (the runner just wraps this and adds the
exit-code gate). `mockllm/model` satisfies Inspect's model requirement; the
solver never actually calls a model:

```bash
inspect eval evals/inspect_routing.py --model mockllm/model \
    -T variant=strict --epochs 3 --max-samples 3
```

> 💸 **Same cost warning as the original.** Every epoch of every case is a full
> Claude Code session. 16 cases × 3 epochs = 48 sessions per variant.

## Reading the results

The summary table prints the three custom metrics:

- **`routing_accuracy`** — pooled correctness over all non-soft cases.
- **`positive_rate`** — delegation rate over non-soft positives ("did it route?").
- **`negative_safety`** — fraction of negatives that correctly declined; the
  original suite treats anything below `1.0` as a regression (over-delegation is
  the primary failure mode).

Soft edge cases (`soft: true` in `cases.json`) are scored and shown in the log
but **excluded from every metric**, matching the original "`*` = soft edge,
never affects exit code" rule.

Then open the interactive viewer for per-sample transcripts and the captured
routing decision:

```bash
inspect view --log-dir evals/results/inspect-logs
```

## Running it in CI

`tests/test_routing_eval.py` is a pytest wrapper around this eval. Because every
trial is a real, paid Claude Code session it is **opt-in** — it skips by default
(and also skips cleanly if `inspect-ai` or the `claude` CLI are missing), so the
normal `pytest` unit run stays fast and free.

```bash
# locally: run the whole suite under the strict wording
RUN_ROUTING_EVAL=1 ROUTING_EVAL_VARIANT=strict pytest tests/test_routing_eval.py -v

# a cheap smoke run of a single case
RUN_ROUTING_EVAL=1 ROUTING_EVAL_FILTER=neg_security_review \
  ROUTING_EVAL_EPOCHS=1 pytest tests/test_routing_eval.py -v
```

The headline gate is **`test_negatives_never_delegate`** (`negative_safety == 1.0`)
— over-delegation is the failure mode that matters for any wording. The
positive-rate gate is opt-in (set `ROUTING_EVAL_POSITIVE_THRESHOLD`) because the
shipped baseline wording is intentionally permissive.

Config env vars: `RUN_ROUTING_EVAL`, `ROUTING_EVAL_VARIANT`, `ROUTING_EVAL_EPOCHS`,
`ROUTING_EVAL_MAX_SAMPLES`, `ROUTING_EVAL_MODEL`, `ROUTING_EVAL_FILTER`,
`ROUTING_EVAL_POSITIVE_THRESHOLD`.

The `.github/workflows/routing-eval.yml` workflow runs this on demand
(`workflow_dispatch`, with variant/epochs/threshold inputs). It installs the
`claude` CLI and needs an `ANTHROPIC_API_KEY` repo secret for headless auth.
It's manual rather than on-push precisely because of the per-run session cost.

## Notes / known rough edges (it's a prototype)

- Per-category metrics read `0.0` on a **filtered** run that excludes a whole
  category (e.g. `--filter model_list` has no negatives). They are meaningful on
  the full suite, where all three categories are populated.
- The solver runs the blocking `claude` subprocess on a worker thread
  (`anyio.to_thread`) so Inspect can still schedule samples concurrently;
  bound concurrency with `--max-samples`.
- Logs land in `evals/results/inspect-logs/` (git-ignored).
