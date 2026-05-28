# Implementation brief: coworker-tools routing eval

Hand this whole file to whoever (human or agent) implements the eval. It encodes
gotchas that were discovered empirically — skipping any of them produces a
harness that looks like it works but measures the wrong thing.

## Goal

Measure whether Claude Code, given the shipped delegation rules, routes natural-
language requests to the correct coworker CLI (`ask-coworker`, `coworker-write`,
`coworker-extract`, `coworker-model`) — and, just as importantly, *declines to
delegate* architecture / debugging / security / refactoring requests. Over-
delegation is the primary failure mode, so negative controls are first-class.

The source of truth for routing rules is the **"Worker Delegation Rules"**
section of `CLAUDE.md.template`. The eval is a regression test on *that wording*.

## What already exists in the repo (do not recreate)

- `evals/fixtures/` — the file tree staged into every test workspace
  (`app/routes/{users,orders,health}.py`, `config_{a,b,c}.toml`, `safety.py`,
  `tests/test_sample.py`, `spec.md`, `transcript.jsonl`). Every prompt references
  files that exist here on purpose (see gotcha #4).
- `evals/cases.json` — the ~16 cases: positives, negatives, soft edges. Schema:
  `{id, category, prompt, expect, expect_args?, soft, note?}`. `expect` is a tool
  name, `"none"`, or `null` (info-only). `expect_args` is a list of substrings
  that MUST appear in the delegated command.
- `evals/claude_strict.md` — imperative-wording variant for the second arm.
- `evals/results/` — output dir.

## What to build

1. `evals/run_eval.py` — the runner (spec below).
2. `evals/README.md` — how to run, flags, cost warning, how to read results.

## The mechanism (validated — use exactly this)

For each case, spin up an isolated workspace and run Claude Code headless:

```
claude -p "<prompt>" \
  --setting-sources project \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions
```

Parse stdout (one JSON object per line). Collect every `tool_use` block where
`name == "Bash"` and read its `input.command`. A coworker tool counts as invoked
if a Bash command matches it (see gotcha #2 for the match rule). The *first*
matching command in execution order is the "delegated tool" for scoring.

## Non-negotiable gotchas (each cost debugging time to find)

1. **cwd MUST be set on the subprocess, not via `cd`.** `cd` does not persist in
   the shell between calls, and `--add-dir` only grants *permission* — neither
   sets Claude's working directory. If cwd is wrong, Claude loads the *wrong*
   `CLAUDE.md` (in testing it loaded the developer's home-repo + global config
   and even fired a global tab-title hook) and the whole result is meaningless.
   Pass `cwd=workspace` to `subprocess.run`.

2. **Capture from the Bash command string, not just a stub log.** A PATH stub
   named `ask-coworker` only catches the bare-name form. Claude may instead run
   `python -m coworker.cli.ask_coworker` or `uv run ask-coworker` — those bypass
   the stub and would score as false negatives. Match against the command string
   with a regex covering BOTH forms, e.g.
   `(ask-coworker|coworker-write|coworker-extract|coworker-model|coworker\.cli\.(ask_coworker|coworker_write|coworker_extract|coworker_model))`
   and normalize the module form back to the dashed tool name. Keep the PATH
   stubs too (so the commands are fast/harmless and produce a confirmation log),
   but treat the stream as the primary signal and log any stub-vs-stream mismatch.

3. **Isolate global config with `--setting-sources project`.** Without it, the
   developer's `~/.claude/CLAUDE.md`, settings, and memory load into every run,
   making results non-reproducible as personal config drifts. Verified: with
   this flag the global tab-title hook no longer fires. (The global config never
   mentions coworker tools, so this is about reproducibility, not bias — but make
   it explicit, don't silently depend on the host's config.)

4. **Every prompt's referenced files must exist in the workspace — including
   negatives.** `neg_security_review` names `safety.py`; if it's absent Claude
   diverges down a missing-file path instead of making the routing decision under
   test. Copy the whole `evals/fixtures/` tree into each workspace. If you add a
   case, add its fixture.

5. **Two arms, and DO NOT "fix" the baseline.** The shipped template says
   "**Consider** delegating" (permissive, three times). Expect baseline positive-
   delegation rates to be *low* — that is a true finding about the shipped
   wording, NOT a harness bug. Run two variants and report both:
   - `baseline` → uses `CLAUDE.md.template` **verbatim** (the regression truth).
   - `strict` → uses `evals/claude_strict.md` (imperative; the achievable upper
     bound).
   Never strengthen the baseline CLAUDE.md to make positives pass — that would
   test wording you don't ship.

6. **Routing is stochastic — repeat and report rates, never a single pass/fail.**
   Default `--repeat 3` (allow 5). Report `k/N delegated correctly` per case and
   an aggregate per category. One run is noise.

7. **macOS has no `timeout` binary.** Enforce per-run timeouts via
   `subprocess.run(..., timeout=...)`, not a shell `timeout`/`gtimeout`. A timed-
   out or errored run scores as incorrect (and is surfaced separately), never as
   a silent pass — especially for negatives.

8. **Validate the full pipeline on TWO cases before scaling to all 16.** Run one
   positive and one negative end-to-end through scoring first. The capture step
   is already proven; the pass/fail logic against real output is not.

## Scoring semantics

- **positive** (`expect` = a tool name): a run is correct iff `delegated == expect`
  AND every `expect_args` substring appears in that tool's invocation command.
- **negative** (`expect == "none"`): correct iff NO coworker tool was invoked.
- **soft edge** (`soft: true`): report the observed delegation; if `expect` is set
  score it for the table, but NEVER let it affect the suite exit code. If
  `expect` is `null`, it is purely informational.
- Aggregate to a rate per case over `--repeat`. With `--strict-exit`, return
  nonzero when any non-soft positive falls below `--positive-threshold`
  (default 0.5) or any negative ever delegates (negatives must be 1.0).

## Runner CLI (`run_eval.py`)

Flags: `--variant {baseline,strict}` (default baseline), `--repeat N` (default 3),
`--jobs N` (default 3, ThreadPoolExecutor — runs are IO-bound on Claude),
`--filter SUBSTR` (case-id contains), `--model` (passed to `claude`),
`--timeout SEC` (default 300, per run), `--keep-artifacts` (retain temp
workspaces + raw `out.jsonl` per run for debugging), `--out PATH`
(JSON report, default `evals/results/report-<variant>.json`), `--strict-exit`.

Print a cost warning up front: `cases × repeat` full Claude sessions.
Per-workspace setup: temp dir; write chosen CLAUDE.md; copy `evals/fixtures/`;
create a `stubbin/` with the four stub executables (each appends
`"$(basename "$0") $@"` to `$COWORKER_LOG` and prints plausible output —
`coworker-model` should print a fake two-model list); run with
`PATH=stubbin:$PATH`, `COWORKER_LOG` set, and `cwd=workspace`.

Output a table (case | category | expect | rate | sample-delegated) plus a JSON
report with per-run detail (delegated tool, matched command, source, raw stdout
path if `--keep-artifacts`).

## Tool interfaces (for writing `expect_args` and reading results)

- `ask-coworker --question Q --paths P [P...]` (also: `--backend --model --dry-run`)
- `coworker-write --spec S --out PATH [--style-ref FILE]`
- `coworker-extract -i FILE [-o FILE] [-f text|json] [-r human|assistant|all]`
- `coworker-model` (mutually-exclusive group to list available / set active)

## Acceptance criteria

- `python evals/run_eval.py --variant strict --repeat 3` runs clean and produces
  a report; strict positives should be high, negatives 1.0.
- `--variant baseline` runs and reports the (likely lower) shipped-wording rates
  without modification to `CLAUDE.md.template`.
- `--filter <id>` runs a single case; `--keep-artifacts` leaves inspectable
  `out.jsonl` per run.
- Capture detects the `python -m coworker.cli.*` form, not only the bare name.
- No dependence on the host's `~/.claude` config (verify a global hook does not
  fire during a run).
