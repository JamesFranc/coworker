#!/usr/bin/env python3
"""Routing eval harness for coworker-tools.

Measures whether Claude Code, given the shipped delegation rules, routes
natural-language requests to the correct coworker CLI -- and, just as
importantly, declines to delegate architecture/debugging/security/refactoring
work. See evals/IMPLEMENTATION_BRIEF.md for the full rationale and the
non-negotiable gotchas this harness is built around.

Usage:
    python evals/run_eval.py --variant strict  --repeat 3
    python evals/run_eval.py --variant baseline --repeat 3
    python evals/run_eval.py --filter neg_ --keep-artifacts
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

EVALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVALS_DIR.parent
FIXTURES_DIR = EVALS_DIR / "fixtures"
CASES_PATH = EVALS_DIR / "cases.json"
RESULTS_DIR = EVALS_DIR / "results"
BASELINE_CLAUDE_MD = REPO_ROOT / "CLAUDE.md.template"
STRICT_CLAUDE_MD = EVALS_DIR / "claude_strict.md"

# --------------------------------------------------------------------------- #
# Tool detection (gotcha #2: match the Bash command string, both invocation
# forms -- bare name AND `python -m coworker.cli.<module>`).
# --------------------------------------------------------------------------- #

# Maps every recognized token back to the canonical dashed tool name.
_MODULE_TO_TOOL = {
    "ask_coworker": "ask-coworker",
    "coworker_write": "coworker-write",
    "coworker_extract": "coworker-extract",
    "coworker_model": "coworker-model",
}
TOOL_NAMES = ["ask-coworker", "coworker-write", "coworker-extract", "coworker-model"]

# The module form (`python -m coworker.cli.<module>`) is unambiguous wherever it
# appears, so a plain substring match is correct for it.
_MODULE_RE = re.compile(
    r"coworker\.cli\.(ask_coworker|coworker_write|coworker_extract|coworker_model)"
)

# Splits a Bash command string into pipeline/sequence segments so we can inspect
# each segment's *command word* (the executable actually being run).
_SEGMENT_RE = re.compile(r"\|\||&&|[;|&\n]")

# Command wrappers that precede the real executable; skip them to find it.
_WRAPPERS = {"env", "command", "exec", "time", "nohup", "nice", "sudo",
             "stdbuf", "setsid", "builtin", "then", "do", "!"}
# Runner front-ends: the real tool is the first non-flag token after `run`/etc.
_RUNNERS = {"uv", "uvx", "npx", "poetry", "pdm", "hatch", "rye", "pipx"}


def _effective_exec(tokens: list[str]) -> str | None:
    """Resolve the executable basename a command segment actually runs.

    Skips leading `VAR=val` env assignments and wrappers like `env`/`sudo`, and
    unwraps `uv run <tool>` style front-ends. Returns the basename, or None.
    """
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):  # env assignment prefix
            i += 1
            continue
        base = tok.rsplit("/", 1)[-1]
        if base in _WRAPPERS:
            i += 1
            continue
        if base in _RUNNERS:
            j = i + 1
            while j < n and (tokens[j] in ("run", "tool", "exec") or tokens[j].startswith("-")):
                j += 1
            return tokens[j].rsplit("/", 1)[-1] if j < n else None
        return base
    return None


def detect_tool(command: str) -> str | None:
    """Return the canonical dashed tool name a command *invokes*, or None.

    Detects both the module form (`python -m coworker.cli.ask_coworker`) and the
    dashed form (`ask-coworker`, `./stubbin/ask-coworker`, `uv run ask-coworker`)
    -- but only when the tool is the command word being executed, so that merely
    *reading* a stub (`cat .../stubbin/coworker-model`) or naming a tool inside an
    argument/string is NOT scored as a delegation.
    """
    m = _MODULE_RE.search(command)
    if m:
        return _MODULE_TO_TOOL[m.group(1)]
    for segment in _SEGMENT_RE.split(command):
        tokens = segment.strip().split()
        if not tokens:
            continue
        base = _effective_exec(tokens)
        if base in TOOL_NAMES:
            return base
    return None


# --------------------------------------------------------------------------- #
# PATH stubs (gotcha #2: keep them so coworker commands are fast + harmless and
# leave a confirmation log; the stream is still the primary signal).
# --------------------------------------------------------------------------- #

_STUB_OUTPUTS = {
    "ask-coworker": 'echo "[stub ask-coworker] (local model answer would appear here)"',
    "coworker-write": 'echo "[stub coworker-write] wrote target file"',
    "coworker-extract": 'printf "[human] example question\\n---\\n[assistant] example answer\\n---\\n"',
    "coworker-model": (
        'printf "Available worker models:\\n"\n'
        'printf "* Gemma 4 E4B          gemma-4-E4B-it-UD-Q4_K_XL\\n"\n'
        'printf "  Qwopus3.5-9B-Coder    qwopus3.5-9b-coder\\n"'
    ),
}


def _stub_script(tool: str) -> str:
    return (
        "#!/bin/sh\n"
        '# Stub for %s -- records the invocation and prints plausible output.\n'
        'echo "$(basename "$0") $@" >> "$COWORKER_LOG"\n'
        "%s\n" % (tool, _STUB_OUTPUTS[tool])
    )


def write_stubs(stubbin: Path) -> None:
    stubbin.mkdir(parents=True, exist_ok=True)
    for tool in TOOL_NAMES:
        path = stubbin / tool
        path.write_text(_stub_script(tool))
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# --------------------------------------------------------------------------- #
# Workspace setup (gotchas #1, #3, #4)
# --------------------------------------------------------------------------- #


def setup_workspace(workspace: Path, variant: str) -> None:
    """Stage CLAUDE.md, the full fixtures tree, and the stub PATH dir."""
    workspace.mkdir(parents=True, exist_ok=True)

    src_md = BASELINE_CLAUDE_MD if variant == "baseline" else STRICT_CLAUDE_MD
    # The workspace CLAUDE.md must be named CLAUDE.md so it loads as project memory.
    shutil.copyfile(src_md, workspace / "CLAUDE.md")

    # Gotcha #4: copy the WHOLE fixtures tree so every referenced file exists,
    # even for negatives -- otherwise Claude diverges down a missing-file path.
    for item in FIXTURES_DIR.iterdir():
        dest = workspace / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copyfile(item, dest)

    write_stubs(workspace / "stubbin")


# --------------------------------------------------------------------------- #
# Stream parsing (the capture step)
# --------------------------------------------------------------------------- #


def _iter_tool_uses(obj):
    """Recursively yield every tool_use block in a parsed stream-json line."""
    if isinstance(obj, dict):
        if obj.get("type") == "tool_use":
            yield obj
        for value in obj.values():
            yield from _iter_tool_uses(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_tool_uses(value)


def extract_bash_commands(stdout: str) -> list[str]:
    """Return every Bash tool_use command, in execution (stream) order."""
    commands: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        for tu in _iter_tool_uses(obj):
            if tu.get("name") == "Bash":
                cmd = (tu.get("input") or {}).get("command")
                if isinstance(cmd, str):
                    commands.append(cmd)
    return commands


def first_delegated(commands: list[str]) -> tuple[str | None, str | None]:
    """Return (tool, command) for the first coworker invocation, or (None, None)."""
    for cmd in commands:
        tool = detect_tool(cmd)
        if tool:
            return tool, cmd
    return None, None


def parse_stub_log(log_path: Path) -> list[str]:
    """Tools recorded by the PATH stubs (secondary signal, for mismatch checks)."""
    if not log_path.exists():
        return []
    tools = []
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        first = line.split()[0]
        if first in TOOL_NAMES:
            tools.append(first)
    return tools


# --------------------------------------------------------------------------- #
# Running one (case, repeat) trial
# --------------------------------------------------------------------------- #


@dataclass
class RunResult:
    delegated: str | None = None
    matched_command: str | None = None
    source: str | None = None  # "stream", or None
    all_commands: list[str] = field(default_factory=list)
    stub_tools: list[str] = field(default_factory=list)
    stub_stream_mismatch: bool = False
    timed_out: bool = False
    error: str | None = None
    returncode: int | None = None
    stdout_path: str | None = None


def invoke_claude(prompt: str, workspace: Path, log_path: Path, args) -> tuple[str, str, int | None, bool]:
    """Run Claude Code headless in ``workspace``. Returns (stdout, stderr, rc, timed_out)."""
    cmd = [
        "claude",
        "-p",
        prompt,
        "--setting-sources",
        "project",  # gotcha #3: isolate the host's ~/.claude config
        "--output-format",
        "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ]
    if args.model:
        cmd += ["--model", args.model]

    env = os.environ.copy()
    env["PATH"] = f"{workspace / 'stubbin'}{os.pathsep}{env['PATH']}"
    env["COWORKER_LOG"] = str(log_path)
    # Each workspace is an isolated, throwaway temp dir, so we are genuinely a
    # sandbox. The CLI honors --dangerously-skip-permissions under root only when
    # IS_SANDBOX is exactly "1" (some hosts pre-set it to "yes", which it rejects),
    # so set it explicitly. Harmless when not running as root.
    env["IS_SANDBOX"] = "1"

    try:
        # gotcha #1: cwd MUST be set here, not via `cd`.
        # gotcha #7: enforce the timeout via subprocess, not a shell `timeout` binary.
        proc = subprocess.run(
            cmd,
            cwd=str(workspace),
            env=env,
            capture_output=True,
            text=True,
            timeout=args.timeout,
        )
        return proc.stdout, proc.stderr, proc.returncode, False
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return out, err, None, True


def run_trial(case: dict, variant: str, repeat_idx: int, args, artifacts_root: Path | None) -> RunResult:
    if args.keep_artifacts and artifacts_root is not None:
        workspace = artifacts_root / case["id"] / f"run{repeat_idx}"
        if workspace.exists():
            shutil.rmtree(workspace)
        owns_workspace = False  # kept for inspection
    else:
        workspace = Path(tempfile.mkdtemp(prefix=f"coworker-eval-{case['id']}-"))
        owns_workspace = True

    result = RunResult()
    try:
        setup_workspace(workspace, variant)
        log_path = workspace / "coworker.log"
        log_path.write_text("")  # ensure $COWORKER_LOG exists even if never appended to

        stdout, stderr, rc, timed_out = invoke_claude(case["prompt"], workspace, log_path, args)
        result.returncode = rc
        result.timed_out = timed_out

        if args.keep_artifacts and artifacts_root is not None:
            out_file = workspace / "out.jsonl"
            out_file.write_text(stdout)
            result.stdout_path = str(out_file)

        if timed_out:
            result.error = "timeout"
        elif rc not in (0, None):
            # Non-zero exit: surface stderr tail but still try to parse what we have.
            tail = (stderr or "").strip().splitlines()[-1:] if stderr else []
            result.error = f"exit {rc}" + (f": {tail[0]}" if tail else "")

        commands = extract_bash_commands(stdout)
        result.all_commands = commands
        tool, matched_cmd = first_delegated(commands)
        if tool:
            result.delegated = tool
            result.matched_command = matched_cmd
            result.source = "stream"

        result.stub_tools = parse_stub_log(log_path)
        stream_tools = {detect_tool(c) for c in commands}
        stream_tools.discard(None)
        result.stub_stream_mismatch = set(result.stub_tools) != stream_tools
        return result
    except Exception as exc:  # noqa: BLE001 -- never let one trial kill the suite
        result.error = f"harness error: {exc!r}"
        return result
    finally:
        if owns_workspace and not args.keep_artifacts:
            shutil.rmtree(workspace, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Scoring (see brief: scoring semantics)
# --------------------------------------------------------------------------- #


def score_run(case: dict, run: RunResult) -> bool:
    """Was this single run correct? Timeouts/errors are never silently correct."""
    expect = case.get("expect")
    delegated = run.delegated

    if expect == "none":
        # Negative: correct iff NO coworker tool was invoked. A timeout/error is
        # incorrect for a negative (we could not confirm it declined).
        if run.timed_out or run.error:
            return False
        return delegated is None

    if expect is None:
        # Purely informational (soft edge with no expectation). Not pass/fail.
        return False

    # Positive (or soft edge with an expected tool).
    if run.timed_out or run.error:
        return False
    if delegated != expect:
        return False
    for needle in case.get("expect_args", []):
        if needle not in (run.matched_command or ""):
            return False
    return True


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def load_cases(filter_substr: str | None) -> list[dict]:
    cases = json.loads(CASES_PATH.read_text())
    if filter_substr:
        cases = [c for c in cases if filter_substr in c["id"]]
    return cases


def most_common_delegation(runs: list[RunResult]) -> str:
    counts: dict[str, int] = {}
    for r in runs:
        key = r.delegated or "none"
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return "-"
    return max(counts, key=counts.get)


def run_suite(args) -> dict:
    cases = load_cases(args.filter)
    if not cases:
        print(f"No cases match --filter {args.filter!r}", file=sys.stderr)
        sys.exit(2)

    total_sessions = len(cases) * args.repeat
    print("=" * 72)
    print(f"coworker-tools routing eval  |  variant={args.variant}  repeat={args.repeat}")
    print(f"COST WARNING: this will start {total_sessions} full Claude Code "
          f"sessions ({len(cases)} cases x {args.repeat} repeats).")
    if args.model:
        print(f"Model: {args.model}")
    print("=" * 72)

    artifacts_root = None
    if args.keep_artifacts:
        artifacts_root = RESULTS_DIR / f"artifacts-{args.variant}"
        artifacts_root.mkdir(parents=True, exist_ok=True)
        print(f"Keeping artifacts under: {artifacts_root}")

    # Build the flat list of trials so the thread pool can interleave them.
    trials = [(case, idx) for case in cases for idx in range(args.repeat)]
    results: dict[str, list[RunResult]] = {c["id"]: [None] * args.repeat for c in cases}

    started = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {
            pool.submit(run_trial, case, args.variant, idx, args, artifacts_root): (case["id"], idx)
            for case, idx in trials
        }
        for fut in as_completed(futures):
            case_id, idx = futures[fut]
            results[case_id][idx] = fut.result()
            done += 1
            print(f"  [{done}/{total_sessions}] {case_id} run{idx} done", flush=True)
    elapsed = time.time() - started

    # Assemble report.
    by_id = {c["id"]: c for c in cases}
    case_reports = []
    for case in cases:
        runs = results[case["id"]]
        per_run = []
        correct = 0
        for r in runs:
            ok = score_run(case, r)
            correct += int(ok)
            per_run.append({
                "delegated": r.delegated,
                "matched_command": r.matched_command,
                "source": r.source,
                "correct": ok,
                "timed_out": r.timed_out,
                "error": r.error,
                "returncode": r.returncode,
                "stub_tools": r.stub_tools,
                "stub_stream_mismatch": r.stub_stream_mismatch,
                "n_bash_commands": len(r.all_commands),
                "stdout_path": r.stdout_path,
            })
        total = len(runs)
        rate = correct / total if total else 0.0
        case_reports.append({
            "id": case["id"],
            "category": case["category"],
            "expect": case.get("expect"),
            "expect_args": case.get("expect_args", []),
            "soft": case.get("soft", False),
            "note": case.get("note"),
            "rate": rate,
            "correct": correct,
            "total": total,
            "sample_delegated": most_common_delegation(runs),
            "runs": per_run,
        })

    # Per-category aggregate (pooled over non-soft cases).
    by_category: dict[str, dict] = {}
    for cr in case_reports:
        cat = cr["category"]
        bucket = by_category.setdefault(cat, {"correct": 0, "total": 0, "soft_cases": 0})
        if cr["soft"]:
            bucket["soft_cases"] += 1
            continue
        bucket["correct"] += cr["correct"]
        bucket["total"] += cr["total"]
    for cat, b in by_category.items():
        b["rate"] = (b["correct"] / b["total"]) if b["total"] else None

    # Exit-status determination (only meaningful with --strict-exit).
    failures = []
    for cr in case_reports:
        if cr["soft"]:
            continue
        if cr["expect"] == "none":
            if cr["rate"] < 1.0:
                failures.append(f"{cr['id']}: negative delegated at least once (rate={cr['rate']:.2f})")
        elif cr["expect"] is not None:
            if cr["rate"] < args.positive_threshold:
                failures.append(
                    f"{cr['id']}: positive rate {cr['rate']:.2f} < threshold {args.positive_threshold:.2f}"
                )

    report = {
        "variant": args.variant,
        "repeat": args.repeat,
        "jobs": args.jobs,
        "model": args.model,
        "timeout": args.timeout,
        "positive_threshold": args.positive_threshold,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "n_cases": len(cases),
        "n_sessions": total_sessions,
        "cases": case_reports,
        "by_category": by_category,
        "failures": failures,
        "passed": len(failures) == 0,
    }
    return report


# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #


def print_table(report: dict) -> None:
    print()
    print(f"Results  (variant={report['variant']}, repeat={report['repeat']}, "
          f"{report['elapsed_seconds']}s)")
    header = f"{'case':<30} {'category':<9} {'expect':<16} {'rate':>7}  sample-delegated"
    print(header)
    print("-" * len(header))
    for cr in report["cases"]:
        expect = cr["expect"] if cr["expect"] is not None else "(info)"
        soft_mark = " *" if cr["soft"] else ""
        rate = f"{cr['correct']}/{cr['total']}"
        print(f"{cr['id']:<30} {cr['category']:<9} {str(expect):<16} {rate:>7}  "
              f"{cr['sample_delegated']}{soft_mark}")
    print("-" * len(header))
    print("(* = soft edge: reported, never affects exit code)")

    print("\nBy category (non-soft, pooled):")
    for cat, b in sorted(report["by_category"].items()):
        if b["total"]:
            print(f"  {cat:<10} {b['correct']}/{b['total']} = {b['rate']:.0%}"
                  + (f"   (+{b['soft_cases']} soft, informational)" if b["soft_cases"] else ""))
        else:
            print(f"  {cat:<10} (no non-soft cases; {b['soft_cases']} soft)")

    # Surface infra issues prominently (never silent).
    infra = []
    for cr in report["cases"]:
        for i, r in enumerate(cr["runs"]):
            if r["timed_out"] or r["error"]:
                infra.append(f"  {cr['id']} run{i}: {r['error'] or 'timeout'}")
            if r["stub_stream_mismatch"]:
                infra.append(f"  {cr['id']} run{i}: stub/stream mismatch "
                             f"(stubs={r['stub_tools']}, stream-detected commands={r['n_bash_commands']})")
    if infra:
        print("\nInfrastructure notes (timeouts / errors / stub-vs-stream mismatches):")
        print("\n".join(infra))

    if report["failures"]:
        print("\nStrict-exit failures:")
        for f in report["failures"]:
            print(f"  - {f}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="coworker-tools routing eval harness")
    p.add_argument("--variant", choices=["baseline", "strict"], default="baseline",
                   help="baseline uses CLAUDE.md.template verbatim; strict uses evals/claude_strict.md")
    p.add_argument("--repeat", type=int, default=3, help="trials per case (routing is stochastic)")
    p.add_argument("--jobs", type=int, default=3, help="concurrent Claude sessions (IO-bound)")
    p.add_argument("--filter", default=None, help="only run cases whose id contains this substring")
    p.add_argument("--model", default=None, help="model passed to `claude --model`")
    p.add_argument("--timeout", type=int, default=300, help="per-run timeout in seconds")
    p.add_argument("--keep-artifacts", action="store_true",
                   help="retain temp workspaces + raw out.jsonl per run for debugging")
    p.add_argument("--out", default=None, help="JSON report path (default: evals/results/report-<variant>.json)")
    p.add_argument("--positive-threshold", type=float, default=0.5,
                   help="min rate for a non-soft positive to pass under --strict-exit")
    p.add_argument("--strict-exit", action="store_true",
                   help="exit nonzero if any non-soft positive is below threshold or any negative ever delegates")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if shutil.which("claude") is None:
        print("error: `claude` CLI not found on PATH.", file=sys.stderr)
        return 2
    if not CASES_PATH.exists():
        print(f"error: {CASES_PATH} not found.", file=sys.stderr)
        return 2
    if not FIXTURES_DIR.is_dir():
        print(f"error: {FIXTURES_DIR} not found.", file=sys.stderr)
        return 2
    src_md = BASELINE_CLAUDE_MD if args.variant == "baseline" else STRICT_CLAUDE_MD
    if not src_md.exists():
        print(f"error: CLAUDE.md source for variant {args.variant!r} not found: {src_md}", file=sys.stderr)
        return 2

    report = run_suite(args)
    print_table(report)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"report-{args.variant}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nJSON report written to {out_path}")

    if args.strict_exit and not report["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
