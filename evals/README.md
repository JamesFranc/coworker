# coworker-tools routing eval

A regression test on the **"Worker Delegation Rules"** in `CLAUDE.md.template`.
It measures whether Claude Code, given those rules, routes natural-language
requests to the correct coworker CLI (`ask-coworker`, `coworker-write`,
`coworker-extract`, `coworker-model`) — and, just as importantly, **declines to
delegate** architecture / debugging / security / refactoring work.

Over-delegation is the primary failure mode, so the negative controls are
first-class: a negative case is only correct if **no** coworker tool is invoked.

See `IMPLEMENTATION_BRIEF.md` for the full design rationale and the empirically
discovered gotchas the harness is built around.

## How it works

For each case the harness spins up an isolated temp workspace and runs Claude
Code headless:

```
claude -p "<prompt>" \
  --setting-sources project \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions
```

with `cwd` set to the workspace. Each workspace contains:

- a `CLAUDE.md` (the variant under test — see below),
- the whole `fixtures/` tree (so every file a prompt references actually exists),
- a `stubbin/` on `PATH` holding fast, harmless stubs of the four coworker CLIs
  that log their invocation to `$COWORKER_LOG` and print plausible output.

It then parses the `stream-json` stdout, collects every `Bash` `tool_use`
command in execution order, and the **first** command that matches a coworker
tool is the "delegated tool". Detection matches **both** the bare-name form
(`ask-coworker …`) and the module form (`python -m coworker.cli.ask_coworker …`),
so module invocations are not scored as false negatives. The stub log is a
secondary signal; any stub-vs-stream mismatch is surfaced, never hidden.

## The two arms

Routing depends entirely on the CLAUDE.md wording, so the harness runs two
variants and you should report **both**:

| `--variant`  | CLAUDE.md source          | wording                          | meaning |
|--------------|---------------------------|----------------------------------|---------|
| `baseline`   | `../CLAUDE.md.template`    | "**Consider** delegating …"      | the shipped regression truth |
| `strict`     | `claude_strict.md`        | "you **MUST** delegate …"        | the achievable upper bound |

> ⚠️ **Do not "fix" the baseline.** The shipped template is permissive on
> purpose. Expect baseline positive-delegation rates to be **low** — that is a
> true finding about the shipped wording, not a harness bug. Strengthening
> `CLAUDE.md.template` to make positives pass would test wording you don't ship.

## Running

```bash
# Upper bound: strict wording, 3 trials per case
python evals/run_eval.py --variant strict --repeat 3

# Shipped wording (the regression truth — likely lower positive rates)
python evals/run_eval.py --variant baseline --repeat 3

# A single case, keeping the raw stream for inspection
python evals/run_eval.py --filter neg_security_review --keep-artifacts
```

> 💸 **Cost warning.** Every trial is a full Claude Code session.
> `--variant X --repeat N` starts `cases × N` sessions (16 × 3 = 48 per arm).
> The harness prints the session count up front.

### Flags

| Flag | Default | Purpose |
|---|---|---|
| `--variant {baseline,strict}` | `baseline` | which CLAUDE.md wording to test |
| `--repeat N` | `3` | trials per case — routing is stochastic, never trust one run |
| `--jobs N` | `3` | concurrent sessions (`ThreadPoolExecutor`; runs are IO-bound) |
| `--filter SUBSTR` | — | only run cases whose id contains `SUBSTR` |
| `--model NAME` | — | passed through to `claude --model` |
| `--timeout SEC` | `300` | per-run timeout (enforced via `subprocess`, not a shell `timeout`) |
| `--keep-artifacts` | off | retain temp workspaces + raw `out.jsonl` per run under `results/artifacts-<variant>/` |
| `--out PATH` | `results/report-<variant>.json` | JSON report path |
| `--positive-threshold F` | `0.5` | min rate for a non-soft positive to pass under `--strict-exit` |
| `--strict-exit` | off | exit nonzero if any non-soft positive is below threshold or any negative ever delegates |

## Reading the results

The terminal table has one row per case:

```
case                  category  expect          rate  sample-delegated
ask_routes_status     positive  ask-coworker     2/3  ask-coworker
neg_security_review   negative  none             3/3  none
edge_single_small...  edge      (info)           0/3  none *
```

- **rate** = correct trials / total trials.
- **sample-delegated** = the most common tool observed across trials (`none` if
  it usually didn't delegate).
- A trailing `*` marks a **soft edge** case: reported but never affecting the
  exit code.

### Scoring semantics

- **positive** (`expect` is a tool name): correct iff `delegated == expect`
  **and** every `expect_args` substring appears in that tool's command.
- **negative** (`expect == "none"`): correct iff **no** coworker tool was
  invoked. A timeout or error counts as **incorrect** for a negative — it is
  never a silent pass.
- **soft edge** (`soft: true`): observed delegation is reported; if `expect` is
  set it is scored for the table, but it **never** affects the suite exit code.
  If `expect` is `null` the case is purely informational.

With `--strict-exit` the process returns nonzero when any non-soft positive
falls below `--positive-threshold` or any negative ever delegates (negatives
must be 1.0).

The JSON report (`results/report-<variant>.json`) carries per-run detail:
delegated tool, matched command, source (`stream`), the Bash command count,
stub-vs-stream mismatch flag, timeout/error status, and — with
`--keep-artifacts` — the path to each run's raw `out.jsonl`.

## Reproducibility notes (why the flags matter)

- **`cwd` is set on the subprocess.** `cd` doesn't persist between Bash calls
  and `--add-dir` only grants permission; if the working directory were wrong,
  Claude would load the *wrong* `CLAUDE.md` and the result would be meaningless.
- **`--setting-sources project`** isolates the host's `~/.claude` global config
  and memory so results don't drift with personal settings (verified: the
  global tab-title hook does not fire during a run).
- **`IS_SANDBOX=1`** is set in each run's environment so
  `--dangerously-skip-permissions` is honored even when the harness runs as root
  inside a container. Each workspace is a throwaway temp dir, so this is
  accurate, not a workaround.

## Adding a case

Append an object to `cases.json`
(`{id, category, prompt, expect, expect_args?, soft, note?}`) and make sure
**every file the prompt references exists in `fixtures/`** — otherwise Claude
diverges down a missing-file path instead of making the routing decision under
test.
