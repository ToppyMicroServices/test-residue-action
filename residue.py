#!/usr/bin/env python3
"""Observe one trusted command's Linux process and scratch-file residue."""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass

VERSION = "0.1.0"
MAX_PROCESSES = 32768
MAX_ENTRIES = 2000
MAX_FDS = 4096
MAX_LOG_BYTES = 1024 * 1024


class ObservationError(Exception):
    """An observer could not establish its result; never interpret as clean."""


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    state: str
    start_ticks: int


def parse_stat(text: str) -> Process:
    # comm can contain spaces and parentheses; it is intentionally not reported.
    opening, closing = text.find("("), text.rfind(")")
    if opening < 1 or closing <= opening:
        raise ObservationError("invalid_process_stat")
    fields = text[closing + 1:].split()
    try:
        return Process(int(text[:opening].strip()), int(fields[1]),
                       fields[0], int(fields[19]))
    except (ValueError, IndexError) as exc:
        raise ObservationError("invalid_process_stat") from exc


def read_process(pid: int, proc: Path = Path("/proc")) -> Process:
    return parse_stat((proc / str(pid) / "stat").read_text(encoding="utf-8", errors="replace"))


def snapshot(proc: Path = Path("/proc")) -> dict[int, Process]:
    result: dict[int, Process] = {}
    try:
        with os.scandir(proc) as entries:
            for entry in entries:
                if not entry.name.isdecimal():
                    continue
                if len(result) >= MAX_PROCESSES:
                    raise ObservationError("process_scan_limit")
                try:
                    item = read_process(int(entry.name), proc)
                    result[item.pid] = item
                except (FileNotFoundError, ProcessLookupError):
                    continue
    except OSError as exc:
        raise ObservationError("process_scan_unavailable") from exc
    return result


def descendants(table: dict[int, Process], root: int) -> list[Process]:
    children: dict[int, list[Process]] = {}
    for item in table.values():
        children.setdefault(item.ppid, []).append(item)
    pending, visited, found = [root], {root}, []
    while pending:
        for item in children.get(pending.pop(), []):
            if item.pid not in visited:
                visited.add(item.pid)
                found.append(item)
                pending.append(item.pid)
    return sorted(found, key=lambda item: item.pid)


def enable_subreaper() -> None:
    if sys.platform != "linux":
        raise ObservationError("linux_required")
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise ObservationError("pidfd_support_required")
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                     ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    if prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise ObservationError("subreaper_unavailable")
    try:
        fd = os.pidfd_open(os.getpid())
        os.close(fd)
    except OSError as exc:
        raise ObservationError("pidfd_unavailable") from exc


def signal_owned(item: Process, number: int) -> None:
    """Use a pidfd and start time so a recycled PID is never signalled."""
    try:
        fd = os.pidfd_open(item.pid)
        try:
            current = read_process(item.pid)
            if current.start_ticks != item.start_ticks:
                return
            signal.pidfd_send_signal(fd, number)
        finally:
            os.close(fd)
    except (FileNotFoundError, ProcessLookupError):
        return
    except OSError as exc:
        raise ObservationError("owned_signal_unavailable") from exc


def reap_adopted(items: list[Process], command_pid: int) -> None:
    for item in items:
        if item.pid != command_pid and item.ppid == os.getpid() and item.state == "Z":
            try:
                os.waitpid(item.pid, os.WNOHANG)
            except ChildProcessError:
                pass


def parse_tcp(text: str, inodes: set[str], family: str) -> list[dict]:
    result = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10:
            raise ObservationError("invalid_tcp_table")
        if fields[3] == "0A" and fields[9] in inodes:
            try:
                port = int(fields[1].rsplit(":", 1)[1], 16)
            except (ValueError, IndexError) as exc:
                raise ObservationError("invalid_tcp_table") from exc
            result.append({"family": family, "port": port})
    return result


def listening_ports(item: Process, proc: Path = Path("/proc")) -> list[dict]:
    directory = proc / str(item.pid)
    try:
        if read_process(item.pid, proc).start_ticks != item.start_ticks:
            return []
        inodes: set[str] = set()
        with os.scandir(directory / "fd") as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_FDS:
                    raise ObservationError("fd_scan_limit")
                try:
                    target = os.readlink(entry.path)
                except FileNotFoundError:
                    continue
                if target.startswith("socket:[") and target.endswith("]"):
                    inodes.add(target[8:-1])
        ports = []
        if inodes:
            for family in ("tcp", "tcp6"):
                table = directory / "net" / family
                try:
                    ports.extend(parse_tcp(table.read_text(), inodes, family))
                except FileNotFoundError:
                    if family != "tcp6":
                        raise
        if read_process(item.pid, proc).start_ticks != item.start_ticks:
            return []
        return [dict(pid=item.pid, start_ticks=item.start_ticks, **port) for port in ports]
    except (FileNotFoundError, ProcessLookupError):
        # A process that exited during inspection has no observable listener.
        if (directory / "stat").exists():
            raise ObservationError("tcp_scan_unavailable")
        return []
    except OSError as exc:
        raise ObservationError("tcp_scan_unavailable") from exc


def scratch_entries(root: Path, identity: tuple[int, int]) -> list[dict]:
    """List only the dedicated scratch root; never read contents or follow links."""
    result = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        fd = os.open(root, flags)
    except FileNotFoundError:
        return []  # Workload removed its empty scratch root.
    except OSError as exc:
        raise ObservationError("scratch_root_unavailable") from exc
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != identity:
            raise ObservationError("scratch_root_replaced")

        def walk(directory: int, prefix: str, depth: int) -> None:
            if depth > 32:
                raise ObservationError("scratch_depth_limit")
            with os.scandir(directory) as entries:
                for entry in entries:
                    if len(result) >= MAX_ENTRIES:
                        raise ObservationError("scratch_entry_limit")
                    relative = prefix + entry.name
                    info = entry.stat(follow_symlinks=False)
                    kind = ("directory" if stat.S_ISDIR(info.st_mode) else
                            "symlink" if stat.S_ISLNK(info.st_mode) else "file")
                    result.append({"path": relative, "kind": kind})
                    if kind == "directory":
                        child = os.open(entry.name, flags, dir_fd=directory)
                        try:
                            current = os.fstat(child)
                            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                                raise ObservationError("scratch_changed_during_scan")
                            if current.st_dev != identity[0]:
                                raise ObservationError("scratch_mount_unsupported")
                            walk(child, relative + "/", depth + 1)
                        finally:
                            os.close(child)
        walk(fd, "", 0)
    except OSError as exc:
        raise ObservationError("scratch_scan_unavailable") from exc
    finally:
        os.close(fd)
    return sorted(result, key=lambda entry: entry["path"])


def make_report() -> dict:
    return {
        "schema_version": 1, "tool_version": VERSION,
        "command": {"status": "not_started", "returncode": None},
        "observation": "not_started", "residue": "unknown",
        "processes": [], "tcp_listeners": [], "scratch_entries": [],
        "errors": [], "scratch_path": None,
        "logs": {"forwarded_bytes": 0, "truncated": False},
        "scope": {"processes": "descendants of this supervisor in its PID namespace",
                  "files": "TEST_RESIDUE_TMPDIR only", "ports": "owned TCP listeners only"},
    }


def forward_logs(stream, state: dict) -> bool:
    """Drain a bounded amount without waiting for inherited pipes to reach EOF."""
    if stream is None:
        return False
    active = False
    try:
        for _ in range(16):
            try:
                chunk = os.read(stream.fileno(), 65536)
            except BlockingIOError:
                return active
            if not chunk:
                return active
            active = True
            remaining = max(0, MAX_LOG_BYTES - state["forwarded_bytes"])
            visible = chunk[:remaining]
            if visible:
                sys.stderr.buffer.write(visible)
                sys.stderr.buffer.flush()
                state["forwarded_bytes"] += len(visible)
            if len(chunk) > remaining:
                state["truncated"] = True
    except OSError as exc:
        raise ObservationError("log_forwarding_unavailable") from exc
    return active


def run(command: list[str], timeout: float, grace: float, report: dict) -> int:
    enable_subreaper()
    if descendants(snapshot(), os.getpid()):
        raise ObservationError("supervisor_already_has_children")
    scratch = Path(tempfile.mkdtemp(prefix="test-residue-"))
    info = scratch.stat()
    identity = (info.st_dev, info.st_ino)
    report["scratch_path"] = str(scratch)
    env = os.environ.copy()
    env["TEST_RESIDUE_TMPDIR"] = str(scratch)
    interrupted = [0]

    def interrupt(number: int, _frame: object) -> None:
        interrupted[0] = number

    saved_handlers = {number: signal.signal(number, interrupt)
                      for number in (signal.SIGINT, signal.SIGTERM)}
    child = None
    errors: set[str] = set()
    stop_at = None
    completed_at = None
    started = time.monotonic()
    last: list[Process] = []
    sent: set[tuple[int, int, int]] = set()
    try:
        # No shell interpretation. Child logs go to stderr; stdout is the JSON result.
        try:
            child = subprocess.Popen(command, env=env, start_new_session=True,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, close_fds=True)
        except OSError as exc:
            report["command"]["status"] = "could_not_start"
            raise ObservationError("command_start_failed") from exc
        if child.stdout is not None:
            try:
                os.set_blocking(child.stdout.fileno(), False)
            except OSError:
                errors.add("log_pipe_unavailable")
                child.stdout.close()
                child.stdout = None
        report["command"]["status"] = "running"
        while True:
            now = time.monotonic()
            code = child.poll()
            if code is not None and completed_at is None:
                completed_at = now
            logs_active = False
            try:
                logs_active = forward_logs(child.stdout, report["logs"])
            except ObservationError as exc:
                errors.add(str(exc))
            try:
                last = descendants(snapshot(), os.getpid())
                reap_adopted(last, child.pid)
                last = descendants(snapshot(), os.getpid())
            except ObservationError as exc:
                errors.add(str(exc))
            timed_out = code is None and now - started >= timeout
            if (interrupted[0] or timed_out or errors) and stop_at is None:
                stop_at = now
                report["command"]["status"] = (
                    "interrupted" if interrupted[0] else "timed_out" if timed_out else "observer_error")
            if stop_at is not None:
                # Only bounded shutdown of the command we launched on timeout/cancel.
                # Normal completion never kills residue.
                number = signal.SIGKILL if now - stop_at >= 1 else signal.SIGTERM
                # The direct child is still ours to wait for, even if /proc fails.
                # Popen checks its exit status before signalling; no other thread
                # or signal handler in this supervisor reaps this child.
                if code is None and (child.pid, -1, number) not in sent:
                    try:
                        child.send_signal(number)
                        sent.add((child.pid, -1, number))
                    except OSError:
                        errors.add("command_signal_unavailable")
                for item in last:
                    key = (item.pid, item.start_ticks, number)
                    if item.pid == child.pid or key in sent:
                        continue
                    try:
                        signal_owned(item, number)
                        sent.add(key)
                    except ObservationError as exc:
                        errors.add(str(exc))
                if (code is not None and not last) or now - stop_at >= 2:
                    if code is None or last:
                        errors.add("shutdown_incomplete")
                    break
            elif completed_at is not None and now - completed_at >= grace:
                break
            time.sleep(0.005 if logs_active else 0.05)
        try:
            forward_logs(child.stdout, report["logs"])
        except ObservationError as exc:
            errors.add(str(exc))
        report["command"]["returncode"] = child.poll()
        if report["command"]["status"] == "running":
            report["command"]["status"] = "passed" if child.returncode == 0 else "failed"
        try:
            current = descendants(snapshot(), os.getpid())
            reap_adopted(current, child.pid)
            current = descendants(snapshot(), os.getpid())
            report["processes"] = [asdict(item) for item in current]
            for item in current:
                try:
                    report["tcp_listeners"].extend(listening_ports(item))
                except ObservationError as exc:
                    errors.add(str(exc))
        except ObservationError as exc:
            errors.add(str(exc))
        try:
            report["scratch_entries"] = scratch_entries(scratch, identity)
        except ObservationError as exc:
            errors.add(str(exc))
        report["errors"] = sorted(errors)
        report["observation"] = "incomplete" if errors else "complete"
        found = bool(report["processes"] or report["scratch_entries"] or report["tcp_listeners"])
        report["residue"] = "found" if found else "unknown" if errors else "none_observed"
        if not found and not errors:
            try:
                # Never recursively delete. Preserve anything a surviving writer creates.
                check = scratch.lstat()
                if (check.st_dev, check.st_ino) == identity:
                    scratch.rmdir()
            except FileNotFoundError:
                pass
            except OSError:
                report["observation"] = "incomplete"
                report["residue"] = "unknown"
                report["errors"].append("scratch_cleanup_raced")
        if report["observation"] != "complete":
            return 2
        if report["command"]["status"] != "passed" or found:
            return 1
        return 0
    finally:
        if child is not None and child.stdout is not None:
            # Never wait for EOF: a remaining child may hold the write end forever.
            child.stdout.close()
        for number, handler in saved_handlers.items():
            signal.signal(number, handler)
        if child is None:
            try:
                scratch.rmdir()
            except OSError:
                pass


def bounded_seconds(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(number) or not 0.05 <= number <= 86400:
        raise argparse.ArgumentTypeError("must be between 0.05 and 86400 seconds")
    return number


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--report", help="New JSON report path; existing files are never overwritten")
    parser.add_argument("--github-output", help="Append step outputs to this GitHub runner file")
    parser.add_argument("--timeout-seconds", type=bounded_seconds, default=300)
    parser.add_argument("--grace-seconds", type=bounded_seconds, default=0.5)
    parser.add_argument("--command-json", help="Command as a JSON array of strings, no shell")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.command_json is not None:
        if command:
            parser.error("choose either --command-json or trailing command")
        try:
            command = json.loads(args.command_json)
        except json.JSONDecodeError:
            parser.error("invalid command JSON")
    if not isinstance(command, list) or not command or not all(
            isinstance(part, str) and "\0" not in part for part in command) or not command[0]:
        parser.error("a nonempty command array is required")
    args.command = command
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = make_report()
    report_file = None
    try:
        if args.report:
            # Reserve before starting any workload. O_EXCL also rejects final symlinks.
            fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            report_file = os.fdopen(fd, "w", encoding="utf-8")
        code = run(args.command, args.timeout_seconds, args.grace_seconds, report)
    except ObservationError as exc:
        report["observation"] = "incomplete"
        report["errors"].append(str(exc))
        code = 2
    except OSError:
        report["observation"] = "incomplete"
        report["errors"].append("report_unavailable")
        code = 2
    report["exit_code"] = code
    rendered = json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    try:
        if report_file:
            with report_file:
                report_file.write(rendered)
        if args.github_output:
            values = {"report-path": str(Path(args.report).absolute()) if args.report else "",
                      "residue": report["residue"], "observation": report["observation"]}
            if any("\n" in value or "\r" in value for value in values.values()):
                raise OSError("unsafe output value")
            with open(args.github_output, "a", encoding="utf-8") as output:
                for key, value in values.items():
                    output.write(f"{key}={value}\n")
        sys.stdout.write(rendered)
    except OSError:
        return 2
    return code


if __name__ == "__main__":
    sys.exit(main())
