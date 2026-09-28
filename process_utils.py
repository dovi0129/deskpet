"""Bounded subprocess text and cleanup of descendants owned by a child."""
from __future__ import annotations
import subprocess


def bounded_lines(stream, limit=131072):
    """Yield whole lines up to limit characters; discard oversized records.

    A line split by the length limit must never be parsed as a complete JSON
    message. Reading in bounded pieces avoids first allocating a giant line.
    """
    while True:
        line = stream.readline(limit + 1)
        if not line:
            return
        if len(line) > limit:
            while line and not line.endswith("\n"):
                line = stream.readline(limit + 1)
            continue
        yield line


def terminate_tree(proc, timeout=1.0):
    """Capture descendants before terminating the parent; never target unrelated PIDs."""
    import psutil
    if proc is None:
        return
    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
    except psutil.Error:
        children = []
        parent = None
    targets = children + ([parent] if parent is not None else [])
    for child in reversed(targets):
        try:
            child.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(targets, timeout=timeout)
    for child in alive:
        try:
            child.kill()
        except psutil.Error:
            pass
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        pass
