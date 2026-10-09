"""Bounded remote output must preserve overflow flags and the next cell's state.

Drive the generated remote runner as a native child using its real file protocol;
this is not a live SSH/container transport test or a peak-RSS benchmark.
"""

import json
import os
import subprocess
import sys
import time

import pytest

from tools.code_kernel import RUNNER_CELL_SOURCE
from tools.code_kernel_remote import REMOTE_KERNEL_RUNNER_SOURCE


def _cell(cells, process, sequence, code):
    request = cells / f"cell_req_{sequence:06d}.json"
    staging = request.with_suffix(".tmp")
    staging.write_text(json.dumps({"id": str(sequence), "code": code}), encoding="utf-8")
    staging.replace(request)
    result = cells / f"cell_res_{sequence:06d}.json"
    deadline = time.monotonic() + 10
    while not result.exists():
        assert process.poll() is None, "runner exited before answering"
        assert time.monotonic() < deadline, "runner did not answer within ten seconds"
        time.sleep(0.01)
    return json.loads(result.read_text(encoding="utf-8"))


@pytest.mark.parametrize("channel,other", [("stdout", "stderr"), ("stderr", "stdout")])
def test_remote_overflow_is_bounded_and_next_cell_keeps_state(tmp_path, channel, other):
    cells = tmp_path / "cells"
    cells.mkdir()
    runner = tmp_path / "runner.py"
    capture_limit = 50_000
    runner.write_text(REMOTE_KERNEL_RUNNER_SOURCE.format(
        capture_limit=capture_limit, idle_exit=10, cell_source=RUNNER_CELL_SOURCE), encoding="utf-8")
    env = {**os.environ, "HERMES_KERNEL_DIR": str(tmp_path)}
    process = subprocess.Popen([sys.executable, str(runner)], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        code = (f"import sys\nanswer = 42\n"
                f"for _ in range(100): sys.{channel}.write('x' * 20_000)\n"
                f"sys.{other}.write('retained=%d' % len(sys.{channel}.getvalue()))")
        overflow = _cell(cells, process, 1, code)
        assert overflow["status"] == "ok"
        assert overflow[f"{channel}_clipped"] is True
        assert overflow[channel] == "x" * capture_limit
        assert overflow[other] == f"retained={capture_limit + 1}"
        next_cell = _cell(cells, process, 2, "print(answer + 1)")
        assert next_cell["status"] == "ok"
        assert next_cell["stdout"] == "43\n"
        assert next_cell["stdout_clipped"] is False
        assert next_cell["stderr_clipped"] is False
        assert next_cell["execution_count"] == 2
        assert _cell(cells, process, 3, "raise SystemExit(0)")["status"] == "exit"
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0 and not stdout and not stderr
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
