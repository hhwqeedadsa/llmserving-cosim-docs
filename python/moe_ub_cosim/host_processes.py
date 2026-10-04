"""Process-scoped Linux descendant ownership, including setsid/orphaned children."""
import ctypes
import os
from pathlib import Path
import signal


def snapshot():
    result = {}
    page = os.sysconf('SC_PAGE_SIZE')
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal():
            continue
        try:
            fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            result[int(path.name)] = dict(parent=int(fields[1]), group=int(fields[2]),
                start=int(fields[19]), state=fields[0], rss_bytes=max(0, int(fields[21])) * page)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return result


class ProcessTree:
    """Owned descendants are keyed by PID and kernel start time, never PID alone.

    A subreaper adopts descendants whose intermediate parent exited. Its setting
    belongs to this monitor process and is restored; no host configuration or
    cgroup changes are made. Preexisting children are excluded. Call from a
    dedicated monitor (as host_probe CLI does), without concurrent child launches.
    """
    def __init__(self):
        self.pid = os.getpid()
        self.root = None
        self.owned = {}
        self.libc = ctypes.CDLL(None, use_errno=True)
        old = ctypes.c_int()
        self._prctl(37, ctypes.byref(old))  # PR_GET_CHILD_SUBREAPER
        self.previous_subreaper = old.value
        self.preexisting = {pid: row['start'] for pid, row in snapshot().items() if row['parent'] == self.pid}
        self._prctl(36, 1)  # PR_SET_CHILD_SUBREAPER

    def _prctl(self, command, value):
        if self.libc.prctl(command, value, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))

    def attach(self, pid):
        self.root = pid
        current = snapshot()
        if pid in current:
            self.owned[pid] = current[pid]['start']
        self.collect(current)

    def collect(self, current=None):
        if current is None:
            current = snapshot()
        owned = {pid for pid, start in self.owned.items() if pid in current and current[pid]['start'] == start}
        # Newly adopted orphans are now our direct children even if they changed
        # sessions before the first sample. Never adopt an existing child by PID.
        owned.update(pid for pid, row in current.items() if row['parent'] == self.pid and
                     self.preexisting.get(pid) != row['start'])
        children = {}
        for pid, row in current.items():
            children.setdefault(row['parent'], []).append(pid)
        pending = list(owned)
        while pending:
            parent = pending.pop()
            for pid in children.get(parent, ()):
                if pid not in owned:
                    owned.add(pid); pending.append(pid)
        # Forget exited identities. Live adopted descendants carry their own
        # identity; a reused PID never inherits ownership from an earlier child.
        self.owned = {pid: current[pid]['start'] for pid in owned}
        return [dict(pid=pid, rss_bytes=current[pid]['rss_bytes'])
                for pid in sorted(owned) if current[pid]['state'] != 'Z']

    def terminate(self, sig):
        self.collect()
        for pid, start in list(self.owned.items()):
            try:
                # Re-check identity immediately before signaling. pidfd binds
                # the signal to this process even if its numeric PID is reused.
                fd = os.pidfd_open(pid)
                try:
                    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                    if int(fields[19]) == start:
                        signal.pidfd_send_signal(fd, sig)
                finally:
                    os.close(fd)
            except (FileNotFoundError, ProcessLookupError):
                pass

    def reap(self):
        self.collect()
        for pid in list(self.owned):
            if pid == self.root:
                continue  # The Popen owner reaps the direct child.
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass

    def close(self):
        self.reap()
        self._prctl(36, self.previous_subreaper)
