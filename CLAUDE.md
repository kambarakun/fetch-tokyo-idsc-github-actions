# CLAUDE.md

@AGENTS.md

The canonical project instructions are in `AGENTS.md` (imported above). Read it first, even if the
import above did not load. Edit rules there, not here; this file holds Claude-specific notes only.

## Claude-specific notes

- The CI workflows `.github/workflows/claude-code-review.yml` and `.github/workflows/claude.yml`
  run Claude Code, which reads this file and, through it, `AGENTS.md`.
- `.claude/` is gitignored local state (settings, worktrees, session data); never commit it.
