"""Bounded cosim/1 pipe reads shared by service and frozen-graph drivers."""
import os
import subprocess
import time

from .plan import require


def read_line(child, selector, buffer, timeout):
    """Bound waits even when a child writes only a partial JSON line."""
    deadline = time.monotonic() + timeout
    while b'\n' not in buffer:
        require(len(buffer) < 1048576, 'Backend protocol line exceeds 1 MiB')
        remaining = deadline - time.monotonic()
        require(remaining > 0 and selector.select(remaining), 'Timed out waiting for backend DECIDE')
        chunk = os.read(child.stdout.fileno(), 65536)
        if not chunk:
            try:
                code = child.wait(timeout=min(remaining, 1.0))
            except subprocess.TimeoutExpired:
                code = None
            raise ValueError(f'Backend EOF before service finished (exit={code}); see backend.stderr.log')
        buffer.extend(chunk)
    end = buffer.index(b'\n') + 1
    line = bytes(buffer[:end]).decode('utf-8')
    del buffer[:end]
    return line
