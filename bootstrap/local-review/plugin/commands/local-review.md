---
allowed-tools: Bash(git diff:*), Bash(git status:*), Bash(git log:*), Bash(git blame:*), Bash(git show:*), Bash(ls:*), Bash(cat:*), Bash(find:*), Read, Task
description: Multi-persona code review of uncommitted local changes (architecture, code-cleanliness, code-quality, security, tests, UX) against the worktree diff
argument: Optional focus area or context (e.g., "focus on the payment flow", "I refactored the auth module")
---

Provide a code review for uncommitted local changes (both staged and unstaged) by fanning out to a panel of reviewer specialists.

**Optional Focus**: If the user provided an argument, weight every reviewer's attention toward that area:
- A specific area (e.g., "focus on the payment flow")
- Context about what changed (e.g., "I refactored the auth module")
- A specific concern (e.g., "make sure the error handling is correct")
- A method or file to prioritize (e.g., "check the handleSubmit function")

Where a focus is provided, every reviewer should still surface critical issues outside the focus area — but weight focus-area findings higher and provide more detail there.

## Pipeline

Make a todo list first, then follow these steps precisely:

1. **Capture the diff.** Use a Haiku agent to gather working-tree state:
   - Run `git status` to see what changed.
   - Run `git diff` (unstaged) and `git diff --staged` (staged).
   - If there are no changes, do not proceed and inform the user.
   - Return: a summary of what changed + the raw diff content + which files are most relevant to the focus argument (if provided).

2. **Find CLAUDE.md guidance.** Use another Haiku agent to find:
   - The repo root `CLAUDE.md` (if one exists).
   - Any `CLAUDE.md` in directories containing modified files.
   - Return the relevant CLAUDE.md content. Note: these files are guidance for Claude when *writing* code, so not every instruction is applicable during review.

3. **List the reviewer personas.** Run `ls /opt/devbox/local-review/personas/*.md` to enumerate available reviewer files. Each file is a self-contained persona prompt with Knowledge Base, What to Look For, Red Flags (Must Fix), Yellow Flags (Should Fix), and Examples sections.

4. **Spawn the reviewer panel.** For each persona file from step 3, launch a parallel Sonnet `Task` agent. Each agent gets:
   - **System prompt**: the FULL content of the persona file (read via the `Read` tool).
   - **User prompt**: "Review the following uncommitted changes against your persona's criteria.
     - CLAUDE.md context: <content from step 2>
     - Focus argument: <focus or 'none'>
     - Diff: <diffs from step 1>
     Return a list of findings. For each finding, include:
       - File path and line number
       - Severity: 'Critical' (matches a Red Flag in your persona) or 'Warning' (matches a Yellow Flag)
       - Brief description of the problem
       - Why it matters (1-2 sentences)
       - Suggested fix (code snippet if helpful)
     If no findings, return an empty list — do not invent issues."

5. **Confidence-score each finding.** For every finding returned in step 4, spawn a parallel Haiku agent. Pass the finding + the CLAUDE.md content + the relevant diff hunk. Score 0-100 on this scale:
   - 0 — false positive that doesn't survive light scrutiny, or a pre-existing issue not introduced in the diff
   - 25 — might be real, but plausibly a false positive; if stylistic, not explicitly called out in CLAUDE.md
   - 50 — moderately confident; real issue but possibly minor or rare in practice
   - 75 — highly confident; will likely hit in practice, directly impacts functionality or correctness
   - 100 — certain; will definitely happen and matter

6. **Filter.** Drop any finding with score < 80. If nothing survives, report no significant issues.

7. **Emit consolidated output** in the format below. Group by severity. Within each severity, group by reviewer persona so the user can see which specialist flagged what.

## False positives to filter out at steps 4 and 5

- Pre-existing issues not introduced in the current changes
- Pedantic nitpicks a senior engineer wouldn't call out
- Issues a linter, typechecker, or compiler would catch automatically
- General code quality issues not explicitly required by CLAUDE.md
- Issues silenced by lint-ignore comments
- Changes in functionality that are likely intentional

## Notes

- Do NOT attempt to build or typecheck the application.
- Reviewers read full file context (not just the diff hunk) when needed to judge a finding.
- Run reviewer agents (step 4) in parallel; run confidence-scoring agents (step 5) in parallel.

## Output format

If issues found:

```
---

### Local Code Review

**Focus**: <user's focus argument, if provided, otherwise omit this line>
**Reviewers**: <comma-separated list of persona files that contributed surviving findings>

Found N issues in uncommitted changes:

**Critical** (N issues — must fix before committing)

1. **file.ts:42** — <brief description> _(reviewer: security)_

   <why it matters and suggested fix>

**Warning** (N issues — should consider fixing)

1. **file.ts:15** — <brief description> _(reviewer: code-cleanliness)_

   <why it matters and suggested fix>

---
```

If no issues survived filtering:

```
---

### Local Code Review

**Focus**: <user's focus argument, if provided, otherwise omit this line>
**Reviewers**: architecture, code-cleanliness, code-quality, security, test, ux

No significant issues found. Code looks good for commit.

Checked for: architectural integrity, code cleanliness, production-grade quality, security, test coverage, developer UX.

---
```
