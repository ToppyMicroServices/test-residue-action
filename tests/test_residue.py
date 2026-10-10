import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import residue

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "residue.py"
FIXTURE = ROOT / "tests" / "fixture.py"
LINUX = sys.platform == "linux"


def stat_line(pid=123, parent=12, ticks=500):
    return f"{pid} (odd ) name) S {parent} " + "0 " * 17 + str(ticks)


class UnitTests(unittest.TestCase):
    def test_stat_with_spaces_and_parentheses(self):
        self.assertEqual(residue.parse_stat(stat_line()), residue.Process(123, 12, "S", 500))

    def test_invalid_stat_is_unknown(self):
        for text in ("", "123", "123 (name) S 1"):
            with self.assertRaises(residue.ObservationError):
                residue.parse_stat(text)

    def test_descendants_exclude_unrelated_and_handle_cycles(self):
        table = {pid: residue.Process(pid, parent, "S", 1)
                 for pid, parent in ((2, 1), (3, 2), (4, 90), (1, 3))}
        self.assertEqual([p.pid for p in residue.descendants(table, 1)], [2, 3])

    def test_tcp_uses_socket_inode_not_just_port(self):
        table = "header\n0: 0100007F:1F90 00000000:0000 0A 0:0 0:0 0 0 0 321\n"
        self.assertEqual(residue.parse_tcp(table, {"321"}, "tcp"), [{"family": "tcp", "port": 8080}])
        self.assertEqual(residue.parse_tcp(table, {"123"}, "tcp"), [])
        self.assertEqual(residue.parse_tcp(table.replace(" 0A ", " 01 "), {"321"}, "tcp"), [])

    def test_invalid_tcp_is_unknown(self):
        with self.assertRaises(residue.ObservationError):
            residue.parse_tcp("header\nbroken", set(), "tcp")

    def test_scratch_does_not_follow_links(self):
        with tempfile.TemporaryDirectory() as container:
            base = Path(container)
            outside = base / "outside"
            outside.mkdir()
            (outside / "secret").write_text("not in the report")
            root = base / "scratch"
            root.mkdir()
            (root / "link").symlink_to(outside, target_is_directory=True)
            (root / "sub").mkdir()
            (root / "sub" / "x").touch()
            info = root.stat()
            found = residue.scratch_entries(root, (info.st_dev, info.st_ino))
            self.assertEqual(found, [{"path": "link", "kind": "symlink"},
                                     {"path": "sub", "kind": "directory"},
                                     {"path": "sub/x", "kind": "file"}])
            self.assertEqual((outside / "secret").read_text(), "not in the report")

    def test_replaced_scratch_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            info = root.stat()
            with self.assertRaisesRegex(residue.ObservationError, "replaced"):
                residue.scratch_entries(root, (info.st_dev, info.st_ino + 1))

    def test_missing_scratch_is_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(residue.scratch_entries(Path(temp) / "missing", (0, 0)), [])

    def test_scratch_cap_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "x").touch()
            info = root.stat()
            with patch.object(residue, "MAX_ENTRIES", 0):
                with self.assertRaisesRegex(residue.ObservationError, "entry_limit"):
                    residue.scratch_entries(root, (info.st_dev, info.st_ino))

    def test_proc_scan_failure_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(residue.ObservationError, "unavailable"):
                residue.snapshot(Path(temp) / "absent")

    def test_time_limits_reject_nan_infinity_zero(self):
        for value in ("nan", "inf", "0", "-1", "90000"):
            with self.assertRaises(Exception):
                residue.bounded_seconds(value)

    def test_command_json_preserves_shell_characters(self):
        command = ["printf", "$(touch nope); abc *", ""]
        self.assertEqual(residue.parse_args(["--command-json", json.dumps(command)]).command, command)

    def test_invalid_command_arrays(self):
        for value in ('{}', '[]', '[12]', '["", "arg"]', '["a\\u0000b"]'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                residue.parse_args(["--command-json", value])

    def test_unsupported_system_never_starts_work(self):
        with patch.object(residue.sys, "platform", "darwin"), patch.object(residue.subprocess, "Popen") as spawn:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = residue.main(["--", "unused"])
            self.assertEqual(result, 2)
            self.assertEqual(json.loads(output.getvalue())["residue"], "unknown")
            spawn.assert_not_called()

    def test_report_collision_never_starts_work(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "existing.json"
            path.write_text("original")
            with patch.object(residue, "run") as run:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(residue.main(["--report", str(path), "--", "unused"]), 2)
                run.assert_not_called()
            self.assertEqual(path.read_text(), "original")

    def test_report_symlink_never_overwrites_target(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "target"
            target.write_text("original")
            link = Path(temp) / "report"
            link.symlink_to(target)
            with patch.object(residue, "run") as run, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(residue.main(["--report", str(link), "--", "unused"]), 2)
                run.assert_not_called()
            self.assertEqual(target.read_text(), "original")

    def test_observer_error_is_not_clean(self):
        with patch.object(residue, "enable_subreaper", side_effect=residue.ObservationError("denied")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(residue.main(["--", "unused"]), 2)
        report = json.loads(output.getvalue())
        self.assertEqual(report["observation"], "incomplete")
        self.assertEqual(report["residue"], "unknown")

    def test_scratch_root_symlink_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            root.symlink_to(Path(temp), target_is_directory=True)
            with self.assertRaisesRegex(residue.ObservationError, "unavailable"):
                residue.scratch_entries(root, (0, 0))

    def test_scratch_depth_limit_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root
            for _ in range(34):
                current = current / "d"
                current.mkdir()
            info = root.stat()
            with self.assertRaisesRegex(residue.ObservationError, "depth_limit"):
                residue.scratch_entries(root, (info.st_dev, info.st_ino))

    def test_github_output_is_written_on_observation_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            output_path = Path(temp) / "outputs"
            report_path = Path(temp) / "report.json"
            with patch.object(residue.sys, "platform", "darwin"), contextlib.redirect_stdout(io.StringIO()):
                code = residue.main(["--report", str(report_path), "--github-output", str(output_path), "--", "unused"])
            self.assertEqual(code, 2)
            self.assertIn("residue=unknown\n", output_path.read_text())
            self.assertIn("observation=incomplete\n", output_path.read_text())

    def test_observer_loss_still_stops_direct_child(self):
        child = unittest.mock.Mock()
        child.pid = 999999
        child.stdout = None
        child.poll.return_value = None
        with patch.object(residue, "enable_subreaper"), \
             patch.object(residue, "snapshot", side_effect=[{}] + [residue.ObservationError("denied")] * 30), \
             patch.object(residue.subprocess, "Popen", return_value=child), \
             patch.object(residue.time, "sleep"), \
             patch.object(residue.time, "monotonic", side_effect=[0, 0.1, 1.2, 2.2]):
            report = residue.make_report()
            self.assertEqual(residue.run(["unused"], 10, 0.1, report), 2)
        child.send_signal.assert_any_call(signal.SIGTERM)
        child.send_signal.assert_any_call(signal.SIGKILL)
        self.assertEqual(report["residue"], "unknown")
        self.assertIn("shutdown_incomplete", report["errors"])
        Path(report["scratch_path"]).rmdir()

    @unittest.skipUnless(LINUX, "Linux pidfd support")
    def test_pid_reuse_never_signals_replacement(self):
        item = residue.Process(100, 1, "S", 20)
        replacement = residue.Process(100, 1, "S", 21)
        with patch.object(os, "pidfd_open", return_value=99), patch.object(os, "close"), \
             patch.object(residue, "read_process", return_value=replacement), \
             patch.object(signal, "pidfd_send_signal") as send:
            residue.signal_owned(item, signal.SIGTERM)
            send.assert_not_called()


@unittest.skipUnless(LINUX, "Linux-only integration suite")
class LinuxIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.controls = []
        self.reports = []

    def tearDown(self):
        # Only fixture identities created by this test can be signalled.
        for control in self.controls:
            if control.exists():
                item = json.loads(control.read_text())
                residue.signal_owned(residue.Process(item["pid"], 0, "S", item["start_ticks"]), signal.SIGKILL)
        for report in self.reports:
            scratch = report.get("scratch_path")
            if scratch:
                path = Path(scratch)
                if path.exists():
                    for entry in path.iterdir():
                        if entry.is_file() or entry.is_symlink():
                            entry.unlink()
                    path.rmdir()
        self.directory.cleanup()

    def command(self, mode):
        control = self.base / f"pid-{len(self.controls)}.json"
        self.controls.append(control)
        return [sys.executable, str(FIXTURE), mode, str(control)], control

    def invoke(self, command, *options):
        result = subprocess.run([sys.executable, str(CLI), "--grace-seconds", "0.1",
                                 *options, "--", *command], capture_output=True, text=True, timeout=10)
        self.assertTrue(result.stdout, result.stderr)
        report = json.loads(result.stdout)
        self.reports.append(report)
        self.assertEqual(result.returncode, report["exit_code"], result.stderr)
        return result.returncode, report

    def test_clean_command(self):
        command, _ = self.command("clean")
        code, report = self.invoke(command)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["residue"], "none_observed")
        self.assertEqual(report["observation"], "complete")

    def test_original_command_failure_is_preserved(self):
        code, report = self.invoke([sys.executable, "-c", "raise SystemExit(7)"])
        self.assertEqual(code, 1)
        self.assertEqual(report["command"], {"status": "failed", "returncode": 7})

    def test_shell_workload_needs_no_language_adapter(self):
        code, report = self.invoke(["/bin/sh", "-c", "printf shell-workload; exit 0"])
        self.assertEqual(code, 0, report)
        self.assertEqual(report["logs"]["forwarded_bytes"], len("shell-workload"))

    def test_scratch_file_is_reported_without_content(self):
        command, _ = self.command("file")
        code, report = self.invoke(command)
        self.assertEqual(code, 1, report)
        self.assertEqual(report["scratch_entries"], [{"path": "remaining.txt", "kind": "file"}])
        self.assertNotIn("never read this content", json.dumps(report))

    def test_process_left_alive_and_unrelated_process_excluded(self):
        sentinel_cmd, sentinel_path = self.command("listener")
        sentinel = subprocess.Popen(sentinel_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            command, control = self.command("process")
            code, report = self.invoke(command)
            item = json.loads(control.read_text())
            self.assertEqual(code, 1, report)
            self.assertEqual([p["pid"] for p in report["processes"]], [item["pid"]])
            self.assertIsNone(sentinel.poll())
            self.assertEqual(residue.read_process(item["pid"]).start_ticks, item["start_ticks"])
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=5)

    def test_owned_listener_is_reported(self):
        command, control = self.command("port")
        code, report = self.invoke(command)
        item = json.loads(control.read_text())
        self.assertEqual(code, 1, report)
        self.assertIn({"pid": item["pid"], "start_ticks": item["start_ticks"],
                       "port": item["port"], "family": "tcp"}, report["tcp_listeners"])

    def test_double_fork_with_cleared_environment_is_owned(self):
        command, control = self.command("double-fork")
        code, report = self.invoke(command)
        self.assertEqual(code, 1, report)
        self.assertEqual([p["pid"] for p in report["processes"]], [json.loads(control.read_text())["pid"]])

    def test_inherited_log_pipe_does_not_hold_ci_open(self):
        command, control = self.command("inherited-pipes")
        code, report = self.invoke(command)
        self.assertEqual(code, 1, report)
        self.assertEqual([p["pid"] for p in report["processes"]], [json.loads(control.read_text())["pid"]])

    def test_large_logs_are_bounded_without_failing_the_command(self):
        code, report = self.invoke([sys.executable, "-c", "import sys; sys.stdout.write('x' * 2097152)"])
        self.assertEqual(code, 0, report)
        self.assertEqual(report["logs"]["forwarded_bytes"], residue.MAX_LOG_BYTES)
        self.assertTrue(report["logs"]["truncated"])

    def test_adopted_exited_children_are_reaped(self):
        command, _ = self.command("zombie")
        code, report = self.invoke(command)
        self.assertEqual(code, 0, report)

    def test_timeout_stops_command_and_stubborn_descendant(self):
        command, control = self.command("timeout-tree")
        code, report = self.invoke(command, "--timeout-seconds", "0.5")
        self.assertEqual(code, 1, report)
        self.assertEqual(report["command"]["status"], "timed_out")
        self.assertEqual(report["processes"], [])
        self.assertEqual(report["observation"], "complete")
        self.assertTrue(control.exists())

    def test_sigterm_is_bounded_and_fails(self):
        command, control = self.command("timeout-tree")
        process = subprocess.Popen([sys.executable, str(CLI), "--", *command],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not control.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(control.exists())
            process.terminate()
            output, error = process.communicate(timeout=5)
            report = json.loads(output)
            self.reports.append(report)
            self.assertEqual(process.returncode, 1, error)
            self.assertEqual(report["command"]["status"], "interrupted")
            self.assertEqual(report["processes"], [])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    def test_missing_executable_is_unknown(self):
        code, report = self.invoke([str(self.base / "missing-program")])
        self.assertEqual(code, 2, report)
        self.assertEqual(report["observation"], "incomplete")
        self.assertIn("command_start_failed", report["errors"])

    def test_repeated_short_lived_forks_do_not_report_leaks(self):
        for _ in range(8):
            command, _ = self.command("zombie")
            code, report = self.invoke(command)
            self.assertEqual(code, 0, report)

    def test_json_arguments_are_not_shell_evaluated(self):
        target = self.base / "should-not-exist"
        code, report = self.invoke([sys.executable, "-c", "import sys; assert len(sys.argv) == 2",
                                    f"$(touch {target}); *"])
        self.assertEqual(code, 0, report)
        self.assertFalse(target.exists())

    def test_json_report_file_matches_stdout(self):
        target = self.base / "report.json"
        code, report = self.invoke([sys.executable, "-c", "pass"], "--report", str(target))
        self.assertEqual(code, 0, report)
        self.assertEqual(json.loads(target.read_text()), report)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_action_adapter_writes_outputs_for_residue(self):
        outputs = self.base / "outputs"
        env = dict(os.environ, TR_COMMAND_JSON=json.dumps([sys.executable, str(FIXTURE), "file"]),
                   TR_GRACE="0.1", RUNNER_TEMP=str(self.base), GITHUB_OUTPUT=str(outputs))
        result = subprocess.run([sys.executable, str(ROOT / "action_runner.py")],
                                env=env, capture_output=True, text=True, timeout=10)
        report = json.loads(result.stdout)
        self.reports.append(report)
        self.assertEqual(result.returncode, 1, result.stderr)
        values = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
        self.assertEqual(values["residue"], "found")
        self.assertEqual(values["observation"], "complete")
        self.assertEqual(json.loads(Path(values["report-path"]).read_text()), report)


if __name__ == "__main__":
    unittest.main()
