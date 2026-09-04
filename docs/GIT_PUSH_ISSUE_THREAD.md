# Git Push Completion Issue Thread

## Status

- **State:** Open — process mitigation documented; automation not yet enforced
- **First recorded:** 2026-09-04
- **Scope:** Tasks that require a local commit and push to the configured upstream branch
- **Symptom:** Work is implemented, validated, and committed, but the assistant stops before running or confirming `git push`

## Why this thread exists

The repository workflow requires every completed task to be committed and pushed unless the user explicitly says
not to push. This failure has been reported repeatedly: the implementation is complete, but task execution appears
to stall at the publication boundary.

This document is the durable thread for confirmed incidents, evidence, root-cause updates, and prevention measures.
Append new incidents here instead of relying on chat history.

## Impact

- The user cannot tell whether the remote branch contains the completed work.
- Local and remote state may diverge even though the task was described as complete.
- A later session must spend time inspecting commits, staging state, and the upstream branch.
- Automatic checkpoint activity can complicate recovery by staging or committing unrelated workspace data.
- Unrelated `private.nosync/` data could be published if recovery uses a broad `git add -A` without inspecting scope.

## Confirmed incident timeline

### 2026-09-03 to 2026-09-04 — yearless consultation-date fix

1. The task fixed requests such as `9月1號的咨詢紀錄` so they load the complete transcript for the current year.
2. During publication, an automatic checkpoint commit mixed the code change with unrelated `private.nosync/`
   workspace changes.
3. The mixed checkpoint was removed locally without discarding working-tree content. Only the seven intended code,
   test, and documentation files were staged.
4. Commit `4447318` (`fix: load full transcript for yearless consultation dates`) was created successfully.
5. The assistant did not execute and report an explicit `git push` verification before the turn ended.
6. At 2026-09-04 09:45 +08:00, inspection showed `HEAD`, `origin/main`, and `origin/HEAD` all at `4447318`.
   The remote was therefore synchronized by that time, but the original task still lacked an observable,
   assistant-verified push completion.

**Classification:** Publication sequencing and completion-verification failure. This incident does not demonstrate a
GitHub authentication or network failure.

### Earlier occurrences

The user reports that the same stop-before-push behavior has happened multiple times. Exact commit IDs and timestamps
have not yet been reconstructed. Add them below when repository or session evidence is available; do not guess.

## Current root-cause assessment

The fragile pattern is:

```text
validate -> commit -> wait for command result -> separate push call -> separate verification call
```

There are two interruption boundaries after the commit. A new user turn, tool interruption, or session completion can
leave publication unfinished or unverified. Long pre-commit hooks make this boundary more visible but are not the root
cause.

Automatic checkpoint activity is a separate risk multiplier: it can change staging or commit state while a task is in
progress. It does not excuse skipping push verification.

## Required prevention procedure

For every task that must be published:

1. Inspect `git status --short --branch` and the staged diff.
2. Stage only explicit task paths when unrelated files exist. Never use broad staging as a recovery shortcut.
3. Keep the final publication sequence in one shell invocation:

   ```bash
   git commit -m "<message>" \
     -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
     && git push \
     && git status --short --branch \
     && test "$(git rev-list --left-right --count '@{upstream}...HEAD')" = "0	0"
   ```

4. Do not mark the task complete or send the final response until the full command exits successfully.
5. Treat a successful commit with a failed or unverified push as **incomplete**, not as a completed task.
6. If push fails, record the exact command, exit code, stderr, branch, upstream, and ahead/behind count in this thread.
7. Preserve unrelated and private working-tree changes; never include them in the task commit.

## Completion evidence

A task is considered pushed only when all of the following are true:

- `git push` exits with status 0.
- The current branch has an upstream.
- `git rev-list --left-right --count '@{upstream}...HEAD'` returns `0 0`.
- `git status --short --branch` does not report the branch as ahead.
- The final response states the pushed commit hash.

`Everything up-to-date` is acceptable only when the upstream equality check also passes.

## Follow-up options

| Option | Benefit | Trade-off |
|---|---|---|
| Keep the documented single-command procedure | Simple, immediately reversible, no new moving parts | Still depends on the assistant following the procedure |
| Add a repository publication script | Centralizes commit/push/verification and can protect excluded paths | Adds a maintained script and requires careful argument handling |
| Add a post-commit hook that pushes automatically | Removes the interruption boundary | Surprising side effect, harder rollback, and unsafe on branches that should not publish automatically |

**Current recommendation:** Use the single-command procedure. It is the simplest and most reversible mitigation.
Only add a publication script if another confirmed incident occurs after this rule is in place. Do not add an
automatic post-commit push hook.

## Incident entry template

```markdown
### YYYY-MM-DD — short description

- Task:
- Branch:
- Commit:
- Expected upstream:
- Last successful command:
- Push command and result:
- Ahead/behind after failure:
- Root cause:
- Recovery:
- Prevention update:
```
