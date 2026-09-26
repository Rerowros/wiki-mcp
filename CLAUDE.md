# CLAUDE.md

The operating contract for this repository is in [AGENTS.md](AGENTS.md) — read
it before writing anything. It is the same contract for every agent, whichever
client it runs in, so it is deliberately kept in one file rather than duplicated
here.

Short version:

- Markdown under `wiki/` is the source of truth; indexes and databases are derived.
- Never edit a file marked `<!-- generated: -->` (`wiki/index.md`, `wiki/log.md`, `wiki/state.md`).
- Never append to a shared file — one log entry is one new file under `wiki/log/`.
- When a fact changes, write a new dated page and set `supersedes`; do not edit the old numbers away.
- Run `python tools/build.py && python tools/lint.py` before committing.
