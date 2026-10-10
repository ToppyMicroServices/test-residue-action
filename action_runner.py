#!/usr/bin/env python3
"""GitHub-specific report/output adapter; no network or third-party packages."""

import os
from pathlib import Path
import sys
import tempfile


def main() -> int:
    directory = Path(tempfile.mkdtemp(prefix="test-residue-report-",
                                      dir=os.environ.get("RUNNER_TEMP")))
    report_path = directory / "report.json"
    arguments = [sys.executable, str(Path(__file__).with_name("residue.py")),
                 "--report", str(report_path), "--command-json", os.environ.get("TR_COMMAND_JSON", ""),
                 "--timeout-seconds", os.environ.get("TR_TIMEOUT", "300"),
                 "--grace-seconds", os.environ.get("TR_GRACE", "0.5")]
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        arguments.extend(["--github-output", output_path])
    # Replace the adapter so cancellation reaches the supervisor directly.
    os.execv(sys.executable, arguments)
    return 2  # execv does not return on success.


if __name__ == "__main__":
    sys.exit(main())
