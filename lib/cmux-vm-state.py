#!/usr/bin/env python3
"""Atomic full-pane-ID session records, with compatibility for old VM shells."""
import fcntl
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import uuid


def atomic_write(path, value):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.session-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_record(path):
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('invalid pane mapping: ' + str(path))
    return value


def main():
    mode = sys.argv[1]
    surface = os.environ.get('CMUX_SURFACE_ID') or os.environ.get('CMUX_PANEL_ID')
    if not surface:
        if mode in ('read', 'read-env'):
            raise ValueError('missing CMUX_SURFACE_ID')
        return
    pane = str(uuid.UUID(surface))
    root = Path(os.environ.get('CMUX_VM_MAP_DIR') or
                str(Path(__file__).resolve().parent.parent/'state'/'sessions'))
    root.mkdir(parents=True, exist_ok=True)
    full = root / (pane + '.json')
    legacy = root / (surface[:8] + '.json')
    if not legacy.exists() and (root / (surface[:8].upper() + '.json')).exists():
        legacy = root / (surface[:8].upper() + '.json')
    with (root / (pane + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = read_record(full)
        try:
            old = read_record(legacy)
        except ValueError:
            if not current:
                raise
            old = {}  # An old shell may be midway through a non-atomic write.
        legacy_owner = str(old.get('surface_id', '')).lower()
        if old and legacy_owner in ('', pane):
            if not current or (not legacy_owner and legacy.stat().st_mtime_ns > full.stat().st_mtime_ns):
                current = old
        if current.get('session_id') and not current.get('agent'):
            current['agent'] = 'claude'
        if mode in ('read', 'read-env'):
            if current.get('agent') not in (None, 'claude', 'codex'):
                raise ValueError('unknown saved agent; mapping retained')
            if mode == 'read':
                print(json.dumps(current))
            else:
                for key, field in (('GSESSION', 'session_id'), ('GCWD', 'cwd'),
                                   ('GAGENT', 'agent'), ('GVM', 'vm_name'), ('GCODEX_HOME', 'codex_home')):
                    print(key + '=' + shlex.quote(str(current.get(field) or '')))
            return
        vm = os.environ.get('CMUX_VM_NAME') or current.get('vm_name') or 'ubuntu'
        if mode == 'hook':
            agent = sys.argv[2]
            payload = json.load(sys.stdin)
            event = payload.get('hook_event_name') or (sys.argv[3] if len(sys.argv) > 3 else '')
            event = {'session-start': 'SessionStart', 'stop': 'Stop',
                     'session-end': 'SessionEnd'}.get(event, event)
            session = payload.get('session_id')
            if not session or agent not in ('claude', 'codex'):
                return
            if event == 'SessionEnd':
                if not (agent == 'claude' and payload.get('reason') in ('prompt_input_exit', 'logout')
                        and current.get('session_id') == session and current.get('agent') == agent):
                    return
                current = {'cwd': payload.get('cwd') or current.get('cwd'), 'event': 'shell'}
            else:
                if event not in ('SessionStart', 'UserPromptSubmit', 'Stop'):
                    return
                if event == 'Stop' and current.get('session_id') != session:
                    return
                current = dict(agent=agent, session_id=session,
                               cwd=payload.get('cwd') or os.getcwd(), event=event)
                if agent == 'codex':
                    current['codex_home'] = os.environ.get('CODEX_HOME') or str(Path.home()/'.codex')
        elif mode == 'launch':
            current = {'cwd': os.environ.get('CMUX_VM_CD') or os.getcwd(), 'event': 'shell'}
            session = os.environ.get('CMUX_RESUME_SID')
            if session:
                current.update(agent=os.environ['CMUX_VM_AGENT'], session_id=session, event='resume')
                if current['agent'] == 'codex':
                    current['codex_home'] = os.environ.get('CODEX_HOME') or str(Path.home()/'.codex')
        elif mode == 'shell':
            if current.get('preserve_on_prompt'):
                return
            current = {'cwd': sys.argv[2], 'event': 'shell'}
        elif mode == 'failed':
            if not current.get('session_id'):
                return
            current['preserve_on_prompt'] = True
        else:
            raise ValueError('unknown operation: ' + mode)
        current.update(version=2, surface_id=pane, vm_name=vm, updated_at=time.time())
        atomic_write(legacy, current)
        atomic_write(full, current)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print('cmux session tracking: ' + str(error), file=sys.stderr)
        sys.exit(1)
