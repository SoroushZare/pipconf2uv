# Idea validation

How this project was chosen. Research was done on 2026-11-15. Numbers are what the GitHub API returned that day and will drift.

**Evidence labels.** *Verified* means the maintainer of this repository queried the GitHub API or read the source directly. *Reported* means a research pass found it and it was not independently re-checked. Nothing here is an estimate of future adoption.

## Process

1. Five independent research passes ran in parallel: trends, recurring developer pain, competitors, distribution, and technical feasibility. One starting candidate, `wt-parity`, was given to each as something to assess, not to defend.
2. An adversarial review attacked the resulting shortlist. It rated all five ideas weak.
3. Because that shortlist leaned heavily on one area (Git worktrees for AI coding agents), one more pass searched without any candidate list: open, highly-reacted feature requests in widely used developer tools that maintainers had stalled or declined, where a small companion tool could deliver the feature from outside.
4. The strongest new candidate went back to the same adversarial reviewer, head-to-head against the previous best.
5. Load-bearing claims for the leading candidates were re-checked directly before scoring.

## Candidates considered

| # | Candidate | Outcome | Deciding evidence |
|---|---|---|---|
| 1 | `wt-parity`: verify a Git worktree is ready for an AI agent | Not selected | See comparison below. |
| 2 | Linter for AI-agent config files (CLAUDE.md, AGENTS.md, MCP) | Rejected | Reported: `agent-sh/agnix` (484 stars) already performs repository-grounded checks; many smaller linters exist. |
| 3 | `.env` drift checker | Rejected | Reported: `dotenv-linter` (2,118 stars) covers file checks; code-usage scanners exist only as tiny clones and are noisy by nature. |
| 4 | Docs-rot checker (commands and paths in README) | Rejected | Reported: `voonterr/realitylint` already ships these checks and has 7 stars after three months. |
| 5 | Linter for composite GitHub Actions (`action.yml`) | Rejected | Verified: `rhysd/actionlint#46` is open since 2021 with 169 reactions, but `jdx/jactionlint` (337 stars, created 2026-10-05) states in its README that it checks composite actions. |
| 6 | Bump `pyproject.toml` constraints for uv | Rejected | Verified: `astral-sh/uv#6794` has 699 reactions, but `astral-sh/uv#19738` ("Allow `uv upgrade` to update a single dependency constraint") merged on 2026-06-11, and six third-party tools exist (largest: `zundertj/uv-bump`, 48 stars). |
| 7 | CODEOWNERS offline validator | Rejected | Verified: `mszostok/codeowners-validator` (249 stars) last pushed 2024-05-01. Reported: GitHub has a native errors endpoint; `hmarr/codeowners` covers unowned files. |
| 8 | Stale-worktree disk reporter | Runner-up | Verified: `anthropics/claude-code#26725` open, 34 reactions, active through October 2026. Reported: worktree managers already show most of this. |
| 9 | Cross-worktree port and compose collision map | Rejected | Reported: managers prevent collisions outright; identical `PORT` values are the default, so the signal is low. |
| 10 | Explain which Claude Code permission rule matches | Rejected | Would have to replicate undocumented, changing matcher behaviour. |
| 11 | `uv.lock` freshness audit | Rejected | Reported: uv's `exclude-newer` already enforces this at resolve time. |
| 12 | Supabase row-level-security static check | Rejected | Demand link was an inference, not evidence. |
| 13 | **pip.conf → uv configuration translator** | **Selected** | See below. |
| 14 | `ansible-galaxy collection uninstall` | Rejected | Reported: an identical tool appeared three weeks earlier. |
| 15 | Ruff baseline file | Rejected | Reported: baseline support merged for `ty` in the same repository; Ruff may inherit it. |
| 16 | `~/.aws/config.d` assembler | Rejected | Risk of clobbering a file that `aws configure` also writes; alternatives exist. |
| 17 | Dependabot config pre-merge check | Rejected | Reported: crowded; only a thin slice would be new. |

## Final five, scored

Weights: demonstrated pain 25%, differentiation 20%, audience and shareability 20%, speed to a useful MVP 15%, demo clarity 10%, long-term usefulness 10%. Scores are 1–10 and are judgements, informed by the adversarial reviewer's own scores.

| Candidate | Pain | Diff. | Audience | Speed | Demo | Long-term | Weighted |
|---|---|---|---|---|---|---|---|
| pip.conf → uv translator | 7 | 8 | 5 | 6 | 7 | 5 | **6.45** |
| `wt-parity` | 4 | 5 | 5 | 7 | 7 | 5 | 5.25 |
| Stale-worktree reporter | 5 | 2 | 4 | 9 | 5 | 3 | 4.60 |
| Docs-rot checker | 3 | 1 | 4 | 5 | 6 | 5 | 3.60 |
| CODEOWNERS validator | 3 | 2 | 3 | 6 | 4 | 5 | 3.55 |

## Why the pip.conf translator

- **The pain is documented and unresolved.** Verified: [astral-sh/uv#1404](https://github.com/astral-sh/uv/issues/1404), "Use settings from the pip configuration file (`pip.conf`)", is open since 2024-02-16 with 191 reactions and 56 comments. Maintainers proposed opt-in support in March 2024; it has not shipped. Users were still asking in December 2025 and March 2026, citing enterprise package proxies.
- **uv's position is explicit.** Verified: uv's pip compatibility documentation says uv does not read configuration files or environment variables specific to pip.
- **No existing tool does it.** Verified: repository searches for a pip.conf-to-uv converter returned nothing. Reported: `stvnksslr/uv-migrator` (184 stars) reads only `extra-index-url` lines from one file; `osprey-oss/migrate-to-uv` (1,165 stars) converts project metadata and does not handle pip configuration.
- **The translation is not trivial.** Verified from uv's settings schema: with several indexes uv defaults to `first-index` while pip considers all of them; `trusted-host`, `cert`, `timeout`, `retries` and `constraint` have no same-named `uv.toml` key; `no-cache-dir` in pip disables the cache for any value, including `false`.

## Comparison with `wt-parity`

`wt-parity` proposed a read-only check that a worktree created for an AI agent has its untracked configuration, valid instruction files and no shared-resource collisions.

What held up:

- Verified: every adopted worktree tool sets up or copies (`max-sixty/worktrunk` 12,536 stars, `raine/workmux` 3,045, `coderabbitai/git-worktree-runner` 1,814); searches for a verifier found only repositories with 0–2 stars.
- Verified: silent partial-copy bugs in Claude Code's `.worktreeinclude` exist (`anthropics/claude-code#79424`, `#83098`, `#66615`), closed as not planned.

What did not:

- Verified: those bug reports have 0–1 reactions each. The request with traction, `anthropics/claude-code#27744` (29 reactions), asked for a setup hook and was closed as completed. People asked for setup, not for an audit.
- Reported: existing `--dry-run` modes (`wt step copy-ignored --dry-run`, `git-worktreeinclude apply --dry-run`) already list missing files, which leaves only thin checks uncovered.
- Without a `.worktreeinclude` manifest, "ignored file missing from the worktree" is usually intentional (caches, build output), so false positives are structural.

On the same rubric `wt-parity` scores 5.25 against 6.45. The decisive differences are demonstrated pain (requests for a verifier: none found; requests for pip.conf support: 191 reactions) and differentiation (partial coverage by existing tools versus none).

## Known weaknesses of the selected idea

Recorded so expectations stay honest.

- **Audience.** It skews to companies with private indexes, an audience that stars repositories less than hobbyists do. Its size is not known beyond the reactions above.
- **One-shot use.** Most people will run it once per machine. A `--check` mode for CI and onboarding is on the roadmap but its demand is unproven.
- **Upstream could close the gap.** Astral could ship opt-in pip.conf support at any time. Thirty-two months without it makes that less likely soon, not impossible.
- **Install path.** Machines that need this tool often cannot reach GitHub or PyPI directly. Publishing to PyPI (so the package is reachable through the same proxy pip already uses) matters more than usual for this project.
- **Sensitive output.** The tool handles index credentials and TLS settings. Redaction is treated as a tested security requirement.
