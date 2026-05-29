#!/usr/bin/env python3
"""Convenience runner for the Inspect AI port of the routing eval.

This wraps ``inspect_ai.eval`` so you don't have to remember the mockllm
incantation, and reproduces the original harness's ``--strict-exit`` behavior
(nonzero exit if negatives ever delegate or positives fall below a threshold).

    python evals/run_inspect.py --variant strict  --epochs 3 --max-samples 3
    python evals/run_inspect.py --variant baseline --epochs 3
    python evals/run_inspect.py --filter neg_security_review --epochs 1
    python evals/run_inspect.py --variant baseline --strict-exit --positive-threshold 0.5

Equivalent raw CLI (no exit-code gating):

    inspect eval evals/inspect_routing.py --model mockllm/model \
        -T variant=strict --epochs 3 --max-samples 3

Requires:  pip install inspect-ai   (and the `claude` CLI on PATH)
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Run the coworker routing eval under Inspect AI")
    p.add_argument("--variant", choices=["baseline", "strict"], default="baseline")
    p.add_argument("--epochs", type=int, default=3, help="trials per case (routing is stochastic)")
    p.add_argument("--max-samples", type=int, default=3, help="concurrent Claude sessions")
    p.add_argument("--filter", default=None, help="only run cases whose id contains this substring")
    p.add_argument("--model", default=None, help="model passed through to `claude --model`")
    p.add_argument("--timeout", type=int, default=300, help="per-session timeout in seconds")
    p.add_argument("--log-dir", default=None, help="Inspect log dir (default: evals/results/inspect-logs)")
    # Parity with run_eval.py --strict-exit:
    p.add_argument("--strict-exit", action="store_true",
                   help="exit nonzero if any negative delegates or any positive < threshold")
    p.add_argument("--positive-threshold", type=float, default=0.5)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if shutil.which("claude") is None:
        print("error: `claude` CLI not found on PATH.", file=sys.stderr)
        return 2

    try:
        from inspect_ai import eval as inspect_eval
    except ImportError:
        print("error: inspect-ai is not installed. Run: pip install inspect-ai", file=sys.stderr)
        return 2

    from inspect_routing import coworker_routing

    log_dir = args.log_dir or str(EVALS_DIR / "results" / "inspect-logs")

    task = coworker_routing(
        variant=args.variant,
        timeout=args.timeout,
        model_cli=args.model,
        epochs=args.epochs,
    )
    if args.filter:
        task.dataset = task.dataset.filter(lambda s: args.filter in str(s.id))
        if len(task.dataset) == 0:
            print(f"No cases match --filter {args.filter!r}", file=sys.stderr)
            return 2

    print("=" * 72)
    n = len(task.dataset)
    print(f"coworker routing eval (Inspect AI)  |  variant={args.variant}  epochs={args.epochs}")
    print(f"COST WARNING: ~{n * args.epochs} full Claude Code sessions "
          f"({n} cases x {args.epochs} epochs).")
    print(f"Logs: {log_dir}   (view with: inspect view --log-dir {log_dir})")
    print("=" * 72)

    # mockllm satisfies Inspect's model requirement; our solver never calls it.
    logs = inspect_eval(
        task,
        model="mockllm/model",
        max_samples=args.max_samples,
        log_dir=log_dir,
    )

    if not args.strict_exit:
        return 0

    # --strict-exit parity: inspect the per-sample reduced scores.
    log = logs[0] if isinstance(logs, list) else logs
    failures: list[str] = []
    samples = (log.samples or []) if log.samples is not None else []
    # With epochs, samples appear once per epoch; pool by sample id.
    from collections import defaultdict
    by_id: dict[str, list[float]] = defaultdict(list)
    meta_by_id: dict[str, dict] = {}
    from inspect_ai.scorer import value_to_float
    to_float = value_to_float()
    for s in samples:
        score = next(iter((s.scores or {}).values()), None)
        if score is None:
            continue
        meta = score.metadata or {}
        if meta.get("soft"):
            continue
        by_id[str(s.id)].append(to_float(score.value))
        meta_by_id[str(s.id)] = meta

    for sid, rates in by_id.items():
        meta = meta_by_id[sid]
        rate = sum(rates) / len(rates) if rates else 0.0
        if meta.get("expect") == "none":
            if rate < 1.0:
                failures.append(f"{sid}: negative delegated at least once (rate={rate:.2f})")
        elif meta.get("expect") is not None:
            if rate < args.positive_threshold:
                failures.append(
                    f"{sid}: positive rate {rate:.2f} < threshold {args.positive_threshold:.2f}"
                )

    if failures:
        print("\nStrict-exit failures:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nStrict-exit: all gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
