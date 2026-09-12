## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues on heikkileivo/circuitpython-ha-blinds (via `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Uses the default five-role vocabulary (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Git workflow

Do each task on its own branch off `main`, named `<type>/<slug>` like the existing branches (`feat/`, `fix/`, `ci/`, `research/`). This holds even when a skill says to commit to the current branch. Once the task is committed, push the branch and open a PR against `main` with `gh pr create`, with `Closes #<issue>` in the body. Heikki reviews and merges.
