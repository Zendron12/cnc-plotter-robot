"""TCP port helpers shared by launch files and web_server."""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import time
from pathlib import Path


def port_is_available(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(('0.0.0.0', port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def find_listener_pids(port: int) -> list[int]:
    """Return PIDs listening on a TCP port (best effort, no root required)."""
    pids: list[int] = []
    try:
        result = subprocess.run(
            ['lsof', '-t', f'-iTCP:{port}', '-sTCP:LISTEN'],
            capture_output=True,
            text=True,
            check=False,
            timeout=3.0,
        )
        for line in result.stdout.splitlines():
            token = line.strip()
            if token.isdigit():
                pids.append(int(token))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    if pids:
        return list(dict.fromkeys(pids))

    listen_inodes: set[str] = set()
    for table in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            with open(table, encoding='ascii') as handle:
                next(handle)
                for row in handle:
                    cols = row.split()
                    if len(cols) < 10 or cols[3] != '0A':
                        continue
                    local_port = int(cols[1].split(':')[1], 16)
                    if local_port == port:
                        listen_inodes.add(cols[9])
        except OSError:
            continue
    if not listen_inodes:
        return []

    proc_root = Path('/proc')
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        fd_dir = entry / 'fd'
        try:
            for fd in fd_dir.iterdir():
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if not target.startswith('socket:['):
                    continue
                inode = target.split('[', 1)[1].rstrip(']')
                if inode in listen_inodes:
                    pids.append(int(entry.name))
                    break
        except OSError:
            continue
    return list(dict.fromkeys(pids))


def free_tcp_port(port: int) -> None:
    """Best-effort kill of whatever is listening on a TCP port."""
    for pid in find_listener_pids(port):
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)
    try:
        subprocess.run(
            ['fuser', '-k', f'{port}/tcp'],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def bind_listening_socket(
    preferred: int,
    *,
    scan: int = 1,
    label: str = 'web UI',
) -> tuple[int, socket.socket]:
    """Bind ``preferred``; optionally scan forward if it is still held."""
    base = max(1024, int(preferred))
    last_error: OSError | None = None
    for offset in range(max(1, int(scan))):
        candidate = base + offset
        for _ in range(3):
            free_tcp_port(candidate)
            time.sleep(0.35)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('0.0.0.0', candidate))
                sock.listen(128)
                if offset:
                    print(
                        f'[wall_climber] {label} port {base} is busy; '
                        f'using http://localhost:{candidate} instead.'
                    )
                return candidate, sock
            except OSError as exc:
                last_error = exc
                sock.close()
        holders = find_listener_pids(candidate)
        if holders:
            print(f'[wall_climber] port {candidate} held by pids: {holders}')
    raise RuntimeError(
        f'Unable to bind {label} port {base}'
        f'{f"-{base + scan - 1}" if scan > 1 else ""}: {last_error}'
    )
