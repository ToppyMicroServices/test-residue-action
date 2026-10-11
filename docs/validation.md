# Validation status

Prototype under development. No Marketplace release or verified creator badge is
claimed. Checked on 2026-10-11 JST.

Runtime checks:

- Alpine 3.24.2, Linux 6.18.52-0-virt, aarch64, Python 3.14.8: 39 tests passed,
  none skipped, as the unprivileged `runner` user in an ephemeral QEMU VM.
- macOS, Python 3.14.6: 39 tests discovered, 21 passed, 18 Linux-specific cases
  skipped. These skips do not establish Linux runtime correctness.
- `actionlint .github/workflows/ci.yml`: passed.
- `python3 -m py_compile residue.py action_runner.py tests/fixture.py tests/test_residue.py`: passed.

The final 39-case Linux suite also passed three consecutive repeat runs
(9.435s, 8.464s, 8.584s), with no skipped cases.

Tested source SHA-256:

```text
d97f24223c894d4074e80cec8bcde77d02cf832d6a6937c08c0ce15ff4c9b10e  residue.py
b083c91ade944cc31c2749e813d79a98524ab462cdfcda9f68c3e8e91d1d26ce  action_runner.py
156082cd5bcd09d2fbab9d65d643d3ad41d0b1e073f31524c7a9bbfaa7827c89  tests/test_residue.py
059e910157275b839d144483e19f2bf4ca30e4068961c75faccf2d61f8b7a11d  tests/fixture.py
```

The Linux suite covers actual descendants, double-fork/setsid with cleared
environment, an unrelated process, owned TCP listeners, scratch residue, normal
command failure, timeout/SIGTERM shutdown, inherited log pipes, and large logs.
Unit tests cover PID-reuse guards, observer failure, no-follow scratch scanning,
report-file collisions, and invalid input.

Initial network-fixture failures in the minimal Linux boot were traced to its
unconfigured loopback device (bind returned EADDRNOTAVAIL). After enabling
loopback in the disposable guest, the listener and unrelated-process tests passed.
No detector check was disabled to obtain the passing result.

A dedicated Colima VM failed to start because the host ran out of disk space.
The newly created failed VM and its newly downloaded image were removed; existing
VM profiles were unchanged. This is an environment failure, not a runtime test.

## GitHub-hosted CI

The public repository is
[ToppyMicroServices/test-residue-action](https://github.com/ToppyMicroServices/test-residue-action).
[CI run 38099470804](https://github.com/ToppyMicroServices/test-residue-action/actions/runs/38099470804)
passed on Ubuntu 24.04 for commit
`c7d8a06a05792de2eb00e82c5805fb746ff066a9`:

- Python 3.11: all 39 tests passed, none skipped.
- Python 3.14: all 39 tests passed, none skipped.
- On both versions, the composite Action passed a clean workload. A workload
  leaving a scratch file failed as expected, with `residue=found` and
  `observation=complete`. A separate step asserted that failure and both outputs.

[CodeQL run 38099474838](https://github.com/ToppyMicroServices/test-residue-action/actions/runs/38099474838)
also completed successfully. These results do not confer a Marketplace verified
creator badge. No Marketplace release has been published.
