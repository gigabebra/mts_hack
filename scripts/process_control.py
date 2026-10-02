"""Bounded cleanup for a subprocess started with start_new_session=True."""
import os
import signal
import subprocess


def terminate_tree(process, grace=15):
    """Reap the owned process group, including descendants of an exited parent."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)
