# Test Residue Check

Your tests passed. Did they leave a server running?

Run one command and check what it leaves behind: descendant processes, their TCP
listeners, and files in an opt-in scratch directory. Get a JSON result that a CI
job or coding agent can act on without writing another process-cleanup script.

**Experimental; no Marketplace release yet.** Linux only, Python 3.11+, with `/proc`, subreaper
and pidfd support. No Docker, root, model API key, or third-party Python packages
are required by the detector. See [limitations](docs/limitations.md) before use.

## Try the CLI

From this checkout on Linux:

```sh
python3 residue.py --report report.json -- npm test
python3 residue.py --timeout-seconds 120 -- python3 -m unittest discover
```

The noninteractive command runs in the current working directory. Up to 1 MiB of
its merged logs go to stderr (truncation is reported); the
report goes to stdout and, when requested, a **new** report file. Existing report
files are never overwritten.

## GitHub Action

After checking out your project and installing its test dependencies, add this
step. This example pins the prototype to an immutable commit; there is no `v1`
release tag yet.

```yaml
- name: Run tests and check residue
  id: residue
  uses: ToppyMicroServices/test-residue-action@29eba833c158c3c7aa53a50324dec9da01d44a71
  with:
    command-json: '["npm", "test"]'
    timeout-seconds: '300'
    grace-seconds: '0.5'
```

Use a Linux runner with Python 3.11 or newer. Inputs are passed through environment
variables, not interpolated into shell commands. The command is an argument array;
shell expansion happens only if you explicitly run a shell yourself.

Outputs are `report-path`, `residue`, and `observation`. A failed step still writes
outputs when it can produce a report. Reports are not automatically uploaded.
No GitHub token permissions are needed by the detector itself.

## What fails the check?

| Exit | Meaning |
| --- | --- |
| `0` | Command succeeded and no residue was observed in the documented scope. |
| `1` | Residue was found, the command failed, or it was interrupted/timed out. |
| `2` | Invalid input, unsupported environment, or incomplete observation. |

The original command's exit code remains in `command.returncode`. Missing
observations never become a pass.

Temporary-file detection is explicit: tests must write their disposable files
under `TEST_RESIDUE_TMPDIR` and remove them before returning. We do not change
`TMPDIR` or scan arbitrary project/host files.

Normal completion is **report-only**: leftover processes and files stay available
for investigation. Timeout or SIGINT/SIGTERM triggers a short shutdown attempt
against this invocation's process tree. Use disposable CI runners, especially
while evaluating the prototype.

[Design and limits](docs/limitations.md) · [Competition research](docs/competition.md)
· [Validation status](docs/validation.md)
