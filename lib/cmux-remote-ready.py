#!/usr/bin/env python3
"""Bring a remote to a usable state before a cmux pane connects to it.

    cmux-remote-ready.py <backend> <address>

Returns 0 only when the remote answers over the transport and the probe
command (CMUX_REMOTE_PROBE, default: the runner is readable) succeeds, so
callers never race a machine that is still booting. Exit 1 means it could not
be made ready; the reason is on stdout.

lima backend: many panes restore at once, so startup is serialized per
instance with a lock; the instance is started if it is not running.

Why the pre-boot cleanup exists: Lima leaves ha.pid/vz.pid behind when the
host reboots. If an unrelated process later owns one of those pids,
`limactl list` reports the instance as Broken and `limactl start` refuses with
"errors inspecting instance", which made every restored pane fail instantly
after a reboot. Files older than the host boot cannot belong to a live host
agent, so they are removed instead of calling `limactl stop -f` (which would
SIGKILL the unrelated pid).

ssh backend: nothing is started; the probe is retried until the host answers.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

backend, address = sys.argv[1:3]
probe = os.environ.get('CMUX_REMOTE_PROBE') or 'test -r %s' % shlex.quote(str(Path(__file__).resolve().parent/'cmux-remote-run.sh'))
probe_timeout = float(os.environ.get('CMUX_REMOTE_PROBE_TIMEOUT', '120'))
start_attempts = int(os.environ.get('CMUX_REMOTE_START_ATTEMPTS', '3'))
ssh_opts = shlex.split(os.environ.get('CMUX_SSH_OPTS', ''))
lima_home = Path(os.environ.get('LIMA_HOME') or Path.home()/'.lima')
inst_dir = lima_home/address
cache = Path(os.environ.get('CMUX_REMOTE_CACHE') or Path.home()/'.cache/cmux-remote')
cache.mkdir(parents=True, exist_ok=True)
key = hashlib.sha256((backend+':'+address).encode()).hexdigest()
lockfile = cache/('start-'+key+'.lock')
ready_marker = cache/('ready-'+key)


def say(message):
    print(message, flush=True)


def remote_command(script):
    if backend == 'lima':
        return ['limactl', 'shell', '--workdir', '/', address, '--', 'bash', '-c', script]
    return ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', *ssh_opts, address, '--', 'bash -c '+shlex.quote(script)]


def status():
    result = subprocess.run(['limactl', 'list', address, '--format', '{{.Status}}'],
                            text=True, capture_output=True)
    return result.stdout.strip()


def host_boot_time():
    override = os.environ.get('CMUX_REMOTE_HOST_BOOT_TIME')
    if override:
        return float(override)
    try:
        if sys.platform == 'darwin':
            out = subprocess.check_output(['sysctl', '-n', 'kern.boottime'], text=True)
            match = re.search(r'sec\s*=\s*(\d+)', out)
            return float(match.group(1)) if match else None
        with open('/proc/stat') as stat:
            for line in stat:
                if line.startswith('btime '):
                    return float(line.split()[1])
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return None


def clear_pre_boot_leftovers():
    """Remove Lima pid/socket files that predate the current host boot."""
    boot = host_boot_time()
    if boot is None or not inst_dir.is_dir():
        return []
    removed = []
    for path in sorted(inst_dir.iterdir()):
        if path.suffix not in ('.pid', '.sock'):
            continue
        try:
            if path.lstat().st_mtime < boot:
                path.unlink()
                removed.append(path.name)
        except OSError:
            pass
    return removed


def start_lima():
    for attempt in range(1, start_attempts+1):
        suffix = '' if attempt == 1 else ' (attempt %d of %d)' % (attempt, start_attempts)
        say("Starting Lima VM '%s'...%s" % (address, suffix))
        result = subprocess.run(['limactl', 'start', address])
        if result.returncode == 0 or status() == 'Running':
            return True
        time.sleep(3*attempt)
    return False


def identity():
    """Something that changes whenever the remote (re)starts, for the ready cache."""
    if backend == 'lima':
        try:
            pid_file = inst_dir/'ha.pid'
            return '%s:%d' % (pid_file.read_text().strip(), int(pid_file.stat().st_mtime))
        except OSError:
            return ''
    return ''


def remote_ready():
    ident = identity()
    try:
        if ident and ready_marker.read_text() == ident:
            return True
    except OSError:
        pass
    deadline = time.monotonic()+probe_timeout
    delay = 1.0
    announced = False
    while True:
        result = subprocess.run(remote_command(probe), stdin=subprocess.DEVNULL,
                                text=True, capture_output=True)
        if result.returncode == 0:
            if ident:
                ready_marker.write_text(ident)
            return True
        last = (result.stderr or result.stdout).strip().splitlines()[-1:] or ['']
        if time.monotonic() > deadline:
            say("Remote '%s' is not reachable: %s" % (address, last[0]))
            return False
        if not announced:
            say("Waiting for '%s' to become reachable..." % address)
            announced = True
        time.sleep(delay)
        delay = min(delay*2, 5.0)


if backend not in ('lima', 'ssh'):
    say('cmux-remote-ready: unknown backend %r' % backend)
    sys.exit(2)

with lockfile.open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    if backend == 'lima':
        current = status()
        if current != 'Running':
            removed = clear_pre_boot_leftovers()
            if removed:
                say("Cleared stale Lima files from before the last host boot: %s" % ', '.join(removed))
                current = status()
        if current != 'Running' and not start_lima():
            say("Could not start Lima VM '%s' (status: %s)." % (address, status() or 'unknown'))
            sys.exit(1)
    if not remote_ready():
        sys.exit(1)
