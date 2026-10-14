#!/usr/bin/env python3
"""Install the guest side of cmux-lima-resume without replacing unrelated settings.

Run inside the VM:  python3 <guest-root>/guest/install-guest.py [--notify]

- ~/.bashrc gets the CMUX-VM-SHELL block (cwd tracking, codex wrapper alias)
- ~/.claude/settings.json gets SessionStart/Stop/SessionEnd hooks
- ~/.codex/hooks.json gets SessionStart/UserPromptSubmit/Stop hooks, trusted in config.toml
- --notify also installs the OSC 9 notification hooks (Stop, Notification)
Everything is idempotent and keyed on the installed command strings.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

root = Path(__file__).resolve().parent.parent
home = Path.home()
notify = '--notify' in sys.argv[1:]


def render(template):
    return (root/'guest'/template).read_text().replace('@ROOT@', str(root))


def install_claude_hooks():
    claude = home/'.claude'
    claude.mkdir(exist_ok=True)
    entry = claude/'cmux-vm-session.sh'
    entry.write_text(render('cmux-vm-session.guest.sh'))
    entry.chmod(0o755)
    settings_path = claude/'settings.json'
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    hooks = settings.setdefault('hooks', {})
    wanted = {'SessionStart': 'bash ~/.claude/cmux-vm-session.sh session-start',
              'Stop': 'bash ~/.claude/cmux-vm-session.sh stop',
              'SessionEnd': 'bash ~/.claude/cmux-vm-session.sh session-end'}
    if notify:
        for name in ('cmux-osc-emit.sh', 'cmux-osc-stop.sh', 'cmux-osc-notify.sh'):
            shutil.copy2(root/'guest/notify'/name, claude/name)
            (claude/name).chmod(0o755)
        wanted['Notification'] = 'bash ~/.claude/cmux-osc-notify.sh'
        wanted['Stop'] = [wanted['Stop'], 'bash ~/.claude/cmux-osc-stop.sh']
    for event, commands in wanted.items():
        groups = hooks.setdefault(event, [])
        present = {h.get('command') for g in groups for h in g.get('hooks', [])}
        for command in ([commands] if isinstance(commands, str) else commands):
            if command not in present:
                groups.append({'hooks': [{'type': 'command', 'command': command}]})
    settings_path.write_text(json.dumps(settings, indent=2)+'\n')


def install_codex_hooks():
    config = home/'.codex'
    config.mkdir(exist_ok=True)
    p = config/'hooks.json'
    data = json.loads(p.read_text()) if p.exists() else {}
    hooks = data.setdefault('hooks', {})
    command = 'python3 %s/lib/cmux-vm-state.py hook codex || true' % root
    trust = []
    for event, label in [('SessionStart', 'session_start'), ('UserPromptSubmit', 'user_prompt_submit'), ('Stop', 'stop')]:
        groups = hooks.setdefault(event, [])
        for group in groups:
            group['hooks'] = [h for h in group.get('hooks', []) if 'cmux-vm-state.py hook codex' not in h.get('command', '')]
        groups[:] = [g for g in groups if g.get('hooks')]
        handler = dict(type='command', command=command, timeout=5)
        groups.append({'hooks': [handler]})
        identity = dict(event_name=label, hooks=[dict(handler, **{'async': False})])
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        trust.append((str(p)+':'+label+':'+str(len(groups)-1)+':0', 'sha256:'+digest))
    p.write_text(json.dumps(data, indent=2)+'\n')
    p = config/'config.toml'
    s = p.read_text() if p.exists() else ''
    s = re.sub(r'(?ms)^# cmux VM session hook trust begin\n.*?^# cmux VM session hook trust end\n?', '', s)
    s += '\n# cmux VM session hook trust begin\n'
    for key, digest in trust:
        s += '[hooks.state.'+json.dumps(key)+']\ntrusted_hash = '+json.dumps(digest)+'\n'
    s += '# cmux VM session hook trust end\n'
    p.write_text(s)


def install_bashrc_block():
    p = home/'.bashrc'
    s = p.read_text() if p.exists() else ''
    block = render('bashrc-cmux-block.sh').rstrip()
    s, n = re.subn(r'(?ms)^# >>> CMUX-VM-SHELL >>>.*?^# <<< CMUX-VM-SHELL <<<', lambda m: block, s)
    if not n:
        s += '\n'+block+'\n'
    p.write_text(s)


(root/'state'/'sessions').mkdir(parents=True, exist_ok=True)
install_bashrc_block()
install_claude_hooks()
install_codex_hooks()
print('Installed guest shell tracking, Claude hooks, and Codex hooks' + (' with notifications.' if notify else '.'))
