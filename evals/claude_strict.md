# coworker — Claude Code integration notes (STRICT variant)

This is an imperative-wording variant of the shipped `CLAUDE.md.template`, used
by the routing eval to measure the upper bound of delegation compliance. The
rules below say "delegate" rather than "consider delegating".

## Worker Delegation Rules

When asked to analyze, summarize, or search across multiple files: you MUST
delegate to `ask-coworker` with the relevant `--paths` and a `--question`. Do
not read the files yourself to answer.

When asked to generate boilerplate, tests, or documentation from a spec: you
MUST delegate to `coworker-write` with a `--spec` and, when an existing file is
named as a style reference, a `--style-ref`.

When asked to review, read, or extract turns from a Claude Code session
transcript (a `.jsonl` file): you MUST delegate to `coworker-extract`.

When asked which worker models are available or to switch the active worker
model: you MUST use `coworker-model`.

Do NOT delegate (handle these yourself):
- Architecture decisions and trade-offs
- Debugging subtle or complex behaviour
- Refactoring plans
- Security-critical code
- Anything requiring judgment beyond the files provided
