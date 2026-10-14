#!/usr/bin/env python3
"""Bring a Lima VM to a usable state before a cmux pane connects to it.

Many panes restore at once, so startup is serialized per VM with a lock. The
helper returns 0 only when `limactl shell` can reach the guest and the shared
launcher mount is visible there, so callers never race a VM that is still
booting. Exit 1 means the VM could not be made ready; the message is on stdout.

Why the pre-boot cleanup exists: Lima leaves ha.pid/vz.pid behind when the host
reboots. If an unrelated process later owns one of those pids, `limactl list`
reports the instance as Broken and `limactl start` refuses with "errors
inspecting instance", which made every restored pane fail instantly after a
reboot. Files older than the host boot cannot belong to a live host agent, so
they are removed instead of calling `limactl stop -f` (which would SIGKILL the
unrelated pid).
"""
import fcntl
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import time

vm = sys.argv[1]
# Guest path of the runner; the launchers export it from the config. The fallback
# assumes the checkout is mounted at the same path inside the guest.
probe_path = os.environ.get('CMUX_VM_PROBE_PATH') or str(Path(__file__).resolve().parent/'cmux-vm-run.sh')
probe_timeout = float(os.environ.get('CMUX_VM_PROBE_TIMEOUT', '120'))
start_attempts = int(os.environ.get('CMUX_VM_START_ATTEMPTS', '3'))
lima_home = Path(os.environ.get('LIMA_HOME') or Path.home()/'.lima')
inst_dir = lima_home/vm
cache = Path.home()/'.cache/cmux-vmc'
cache.mkdir(parents=True, exist_ok=True)
key = hashlib.sha256(vm.encode()).hexdigest()
lockfile = cache/('start-'+key+'.lock')
ready_marker = cache/('ready-'+key)


def say(message):
    print(message, flush=True)


def status():
    result = subprocess.run(['limactl', 'list', vm, '--format', '{{.Status}}'],
                            text=True, capture_output=True)
    return result.stdout.strip()


def host_boot_time():
    override = os.environ.get('CMUX_VM_HOST_BOOT_TIME')
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
    """Remove pid/socket files that predate the current host boot."""
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


def start_vm():
    for attempt in range(1, start_attempts+1):
        suffix = '' if attempt == 1 else ' (attempt %d of %d)' % (attempt, start_attempts)
        say("Starting Lima VM '%s'...%s" % (vm, suffix))
        result = subprocess.run(['limactl', 'start', vm])
        if result.returncode == 0 or status() == 'Running':
            return True
        time.sleep(3*attempt)
    return False


def host_agent_identity():
    """pid plus pid-file mtime, so a reused pid on a later boot never matches."""
    try:
        pid_file = inst_dir/'ha.pid'
        return '%s:%d' % (pid_file.read_text().strip(), int(pid_file.stat().st_mtime))
    except OSError:
        return ''


def guest_ready():
    """Wait until the guest answers over ssh and the launcher mount is visible."""
    identity = host_agent_identity()
    try:
        if identity and ready_marker.read_text() == identity:
            return True
    except OSError:
        pass
    deadline = time.monotonic()+probe_timeout
    delay = 1.0
    last = ''
    announced = False
    while True:
        result = subprocess.run(
            ['limactl', 'shell', '--workdir', '/', vm, '--', 'test', '-r', probe_path],
            stdin=subprocess.DEVNULL, text=True, capture_output=True)
        if result.returncode == 0:
            if identity:
                ready_marker.write_text(identity)
            return True
        last = (result.stderr or result.stdout).strip().splitlines()[-1:] or ['']
        if time.monotonic() > deadline:
            say("Lima VM '%s' is running but not reachable: %s" % (vm, last[0]))
            return False
        if not announced:
            say("Waiting for Lima VM '%s' to become reachable..." % vm)
            announced = True
        time.sleep(delay)
        delay = min(delay*2, 5.0)


with lockfile.open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    current = status()
    if current != 'Running':
        removed = clear_pre_boot_leftovers()
        if removed:
            say("Cleared stale Lima files from before the last host boot: %s" % ', '.join(removed))
            current = status()
    if current != 'Running' and not start_vm():
        say("Could not start Lima VM '%s' (status: %s)." % (vm, status() or 'unknown'))
        sys.exit(1)
    if not guest_ready():
        sys.exit(1)
