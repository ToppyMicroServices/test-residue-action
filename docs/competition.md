# Competition review

Reviewed 2026-10-11 JST. This is a bounded search and documentation review, not a
claim that competitors do not exist. Competitor software was not executed.

## Closest overlap: DSH Testkit

[DSH Testkit](https://github.com/iiwish/dsh-testkit) checks DeepSeek Harness plugin
lifecycles, including owned filesystem residue, processes, and ports. It has both
a CLI and a GitHub Action and treats unavailable required observations as
unsupported. Its documented prerequisites include Node.js 22+ and Docker.

That overlaps substantially with the residue-checking idea. The defensible
distinction here is a small, framework-independent wrapper around one Linux
command, with no Docker or model dependency. This prototype does not implement
DSH's packaging, installation, recovery, or host-compatibility checks.

Reviewed main commit: `c86f14babe59383c9a01bdc511f289c9f75e9ffe`.

## Adjacent tools

| Tool | Documented focus | Relationship to this prototype |
| --- | --- | --- |
| [process_triage](https://github.com/Dicklesworthstone/process_triage) | Bayesian classification of abandoned/zombie processes and cleanup advice, with agent-oriented usage | Substantial process-management overlap. This prototype uses one invocation's ancestry rather than abandonment classification. |
| [goleak](https://github.com/uber-go/goleak) | Go goroutine leak detection | Useful for leaks inside a Go process; our OS-process check does not replace it. |
| [batsman](https://github.com/rfxn/batsman) | BATS test infrastructure, process assertions, cleanup helpers, reusable CI | A framework-specific alternative to ad-hoc lifecycle scripts. |

Reviewed process_triage main commit:
`f97ba8f7f74c7bdb84d7d57ae416b23477abf6f7`.
Reviewed goleak master commit:
`b656bfda2fbf4f4964e1d9349840a72169224d88`.

Searches included `github action test residue`, `test process leak CI detector
language agnostic`, `github leftover processes action`, and `github testkit
residue`. No general one-command Action with exactly this proposed scope was
confirmed in that search. That is not proof of market exclusivity, product demand,
or legal freedom to operate. Compare again before publishing.

## Product recommendation

Validate the narrow use case first: "The tests are green, but a spawned server
still lives." JSON output lets a coding agent identify a remaining PID, port,
or scratch path without inventing another cleanup helper. Keep failure reasons
separate from observed residue; never advertise a clean machine or zero leaks.

The product is worth a small prototype. There is not enough evidence to call it
the first or only tool in this category.

## Marketplace and Verified

[GitHub's publishing documentation](https://docs.github.com/en/actions/how-tos/create-and-publish-actions/publish-in-github-marketplace)
requires a public repository and a unique Action name for Marketplace publication.
The verified creator badge is a separate partner-organization status, not a
code-quality certification or the organization's domain-verification badge.
Publishing this prototype would not itself confer Verified status.
