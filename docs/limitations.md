# Scope and limits

This is a test aid for trusted workloads, not an isolation boundary or a malware
sandbox. The workload has the same privileges as the supervisor and can interfere
with it. Never run untrusted PR code with credentials or on a sensitive host.

## Process ownership

The CLI becomes a Linux child subreaper before launching a command. Descendants
orphaned by a double fork are adopted by it, even after `setsid` or environment
clearing. `/proc` parent relationships identify its current descendants. The
supervisor must start without existing children.

PID and process start ticks are recorded. Signals to discovered descendants use
pidfds plus a start-time check. The direct child remains owned by `Popen`, which
is the only component that waits for it. The detector does not classify processes
by name, command-line substring, age, or memory use.

This is a sampled, end-of-run check, not a complete process history. It cannot
attribute a service launched on your behalf by an already-running Docker daemon,
systemd, a remote host, or another external broker. Privilege changes, restricted
`/proc`, and namespaces can prevent inspection. Known scan failures produce an
incomplete result, but the tool cannot detect every way a hostile workload might
escape observation. It does not detect in-process memory, thread, or goroutine
leaks. A listener inherited from elsewhere is attributed to the remaining
process holding it, not proven to have been created by this command.

## Files and ports

Each invocation creates a fresh private scratch directory. Only its entry names
and kinds are inspected; file contents and symlink targets are not included.
Directory traversal uses no-follow opens and identity checks. Removed scratch
roots count as empty; replaced roots and scan limits produce incomplete results.
The root is removed only when empty. Nonempty directories are retained.

Only TCP LISTEN sockets referenced by an owned process's file descriptors are
reported, by socket inode. UDP, UNIX sockets, TIME_WAIT, files outside the scratch
root, and remote resources are out of scope. No machine-wide port before/after
comparison is used. A process can exit during inspection; the report is a sample,
not a transactionally consistent snapshot.

## Failure and shutdown

Default command timeout: 300 seconds. Default post-exit grace: 0.5 seconds.
Ordinary command completion never kills residue. Timeout, SIGINT/SIGTERM, or a
detected observer failure initiates SIGTERM, then SIGKILL after one second, to
owned processes. The shutdown loop stops after two seconds and marks remaining
or unobservable work incomplete. Uninterruptible kernel tasks may survive.
SIGKILL or abrupt runner termination cannot be caught and may prevent any report.

Scans are limited to 32,768 processes, 4,096 descriptors per remaining process,
and 2,000 scratch entries with directory depth at most 32. Exceeding these limits
is not a pass. A normal result means only that no residue was observed in this
scope after the grace interval.

## Data and permissions

The detector makes no network requests and does not read process environments or
command lines. It reads basic process metadata, owned descriptor links, socket
tables, and scratch entry metadata. Reports include PIDs, start ticks, ports,
scratch paths, and filenames; those can still reveal project information.

Workload stdout and stderr are merged and forwarded, up to 1 MiB, and are **not
redacted**. Excess output is drained and discarded, with `logs.truncated` set in
the report. Standard input is closed: this is for noninteractive commands.
The log pipe is closed when observation ends, even if a descendant still holds
its write end. A later write by that descendant may receive SIGPIPE/EPIPE. This
prevents inherited log handles from holding an outer CI capture open forever.

The workload retains normal
network and filesystem access. Review reports and logs before publishing them.
No report is automatically uploaded by the Action.

## Development

```sh
python3 -m unittest discover -s tests -v
actionlint .github/workflows/ci.yml
```

Linux integration tests deliberately create short-lived leftover processes. The
test harness kills only its own recorded fixture identities during teardown.
On other systems, Linux cases are skipped and are not evidence of Linux support.

References:

- [Linux child subreapers](https://manpages.debian.org/bookworm/manpages-dev/prctl.2.en.html)
- [Python pidfd_open](https://docs.python.org/3.11/library/os.html#os.pidfd_open)
- [Python pidfd_send_signal](https://docs.python.org/3.11/library/signal.html#signal.pidfd_send_signal)
