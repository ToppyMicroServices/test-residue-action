"""Controlled, short-lived integration workloads. Not part of the Action API."""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import residue


def linger(control: Path, listening: bool = False, ignore_term: bool = False) -> None:
    os.environ.clear()  # Ownership must not depend on inherited environment tags.
    if ignore_term:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    server = None
    port = None
    if listening:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
    item = residue.read_process(os.getpid())
    control.write_text(json.dumps({"pid": item.pid, "start_ticks": item.start_ticks,
                                   "port": port}))
    time.sleep(30)  # Safety ceiling even if the test harness fails.


def main() -> None:
    mode = sys.argv[1]
    control = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    if mode in ("linger", "listener", "stubborn"):
        linger(control, mode == "listener", mode == "stubborn")
    elif mode == "clean":
        path = Path(os.environ["TEST_RESIDUE_TMPDIR"]) / "removed.txt"
        path.write_text("fixture")
        path.unlink()
        subprocess.run([sys.executable, "-c", "pass"], check=True)
    elif mode == "file":
        path = Path(os.environ["TEST_RESIDUE_TMPDIR"]) / "remaining.txt"
        path.write_text("never read this content")
    elif mode in ("process", "port", "timeout-tree", "inherited-pipes"):
        child_mode = "listener" if mode == "port" else "stubborn" if mode == "timeout-tree" else "linger"
        output = None if mode == "inherited-pipes" else subprocess.DEVNULL
        subprocess.Popen([sys.executable, __file__, child_mode, str(control)],
                         stdin=subprocess.DEVNULL, stdout=output,
                         stderr=output, start_new_session=True)
        deadline = time.monotonic() + 5
        while not control.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("fixture did not become ready")
            time.sleep(0.01)
        if mode == "timeout-tree":
            time.sleep(30)
    elif mode == "double-fork":
        first = os.fork()
        if first == 0:
            os.setsid()
            if os.fork() != 0:
                os._exit(0)
            for descriptor in (0, 1, 2):
                fd = os.open(os.devnull, os.O_RDWR)
                os.dup2(fd, descriptor)
                if fd > 2:
                    os.close(fd)
            linger(control)
            os._exit(0)
        os.waitpid(first, 0)
        deadline = time.monotonic() + 5
        while not control.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("double-fork fixture did not become ready")
            time.sleep(0.01)
    elif mode == "zombie":
        if os.fork() == 0:
            os._exit(0)
        time.sleep(0.05)
    else:
        raise ValueError("unknown fixture mode")


if __name__ == "__main__":
    main()
