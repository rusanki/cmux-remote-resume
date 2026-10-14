#!/usr/bin/env python3
"""Resolve only an explicitly supplied session UUID, never latest-by-directory."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

agent, raw = sys.argv[1:]
try:
    sid = str(uuid.UUID(raw))
except ValueError:
    sys.exit('vmc: --resume requires an exact session UUID')
if agent == 'codex':
    root = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))
    for db in sorted(root.glob('state_*.sqlite'), reverse=True):
        try:
            with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as con:
                row = con.execute('select cwd from threads where id = ?', (sid,)).fetchone()
            if row and row[0]:
                print(row[0]); sys.exit(0)
        except sqlite3.Error:
            continue
    files = (root/'sessions').glob('**/*' + sid + '.jsonl')
else:
    files = (Path.home()/'.claude/projects').glob('*/' + sid + '.jsonl')
for p in files:
    with p.open() as stream:
        for line in stream:
            try: d = json.loads(line)
            except ValueError: continue
            if agent == 'codex':
                if d.get('type') != 'session_meta': continue
                d = d.get('payload', {})
                if d.get('id') != sid: continue
            if d.get('cwd'):
                print(d['cwd']); sys.exit(0)
sys.exit('vmc: no saved ' + agent + ' session found with UUID ' + sid)
