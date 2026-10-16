import json
import os
from pathlib import Path
import subprocess
import select
import pty
import shutil
import sys
import tempfile
import unittest
import importlib.machinery
import importlib.util

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT/'lib'
BIN = ROOT/'bin'
REMOTE = ROOT/'remote'
PANE = 'aaaaaaaa-1111-4111-8111-111111111111'
OTHER = 'aaaaaaaa-2222-4222-8222-222222222222'

# Fake limactl/ssh/scp: every non-"list" call is appended to LAUNCH_LOG as a JSON argv.
# "shell"/ssh invocations that carry the state helper are executed for real so
# record reads work end to end (the remote is this machine).
FAKE_TRANSPORT = '''#!/usr/bin/env python3
import json,os,subprocess,sys
argv=sys.argv[1:]
log=os.environ['LAUNCH_LOG']
tool=os.path.basename(sys.argv[0])
if tool=='limactl' and argv[:1]==['list']: print('Running'); sys.exit()
with open(log,'a') as f: f.write(json.dumps([tool]+argv)+'\\n')
# find the remote script: limactl: after '--' comes bash -c SCRIPT ; ssh: last arg is "bash -c <quoted>"
script=None
if tool=='limactl' and 'bash' in argv and argv[argv.index('bash')+1]=='-c': script=argv[argv.index('bash')+2]
# ssh hands the remote login shell one string; emulate that shell with bash -c.
if tool=='ssh' and argv and argv[-1].startswith('bash -c '): script=argv[-1]; runner=['bash','-c',script]
elif script is not None: runner=['bash','-c',script]
if script is not None and ('cmux-remote-state.py' in script or 'cmux_sync_guard' in script):
    r=subprocess.run(runner,text=True,capture_output=True)
    sys.stdout.write(r.stdout); sys.stderr.write(r.stderr); sys.exit(r.returncode)
if script is not None and 'test -r' in script: sys.exit(0)
if script is not None and 'printf %s "$HOME"' in script: print(os.environ['HOME'],end=''); sys.exit(0)
'''


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.maps = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('CMUX_')}
        self.env.update(CMUX_REMOTE_STATE_DIR=str(self.maps/'sessions'), CMUX_SURFACE_ID=PANE,
                        CMUX_TARGET='ubuntu', CMUX_REMOTE_CONFIG=str(self.maps/'no-config'),
                        CMUX_REMOTE_CACHE=str(self.maps/'cache'))

    # ---- state store -------------------------------------------------------
    def state(self, *args, pane=PANE, payload=None):
        result = subprocess.run(['python3', str(LIB/'cmux-remote-state.py'), *args],
                                input=json.dumps(payload or {}), text=True, capture_output=True,
                                env=dict(self.env, CMUX_SURFACE_ID=pane))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def hook(self, sid, pane=PANE, event='SessionStart', agent='codex', **extra):
        self.state('hook', agent, pane=pane, payload=dict(
            session_id=sid, cwd='/work/shared', hook_event_name=event, **extra))

    def test_same_directory_and_same_short_prefix_keep_exact_sessions(self):
        self.hook('session-one')
        self.hook('session-two', pane=OTHER)
        self.assertEqual(self.state('read')['session_id'], 'session-one')
        self.assertEqual(self.state('read', pane=OTHER)['session_id'], 'session-two')
        self.assertEqual(self.state('read')['surface_id'], PANE)
        self.assertEqual(self.state('read')['target'], 'ubuntu')

    def test_stale_stop_and_end_do_not_replace_new_session(self):
        self.hook('old', agent='claude')
        self.hook('new')
        self.hook('old', event='Stop', agent='claude')
        self.hook('old', event='SessionEnd', agent='claude', reason='prompt_input_exit')
        self.assertEqual(self.state('read')['session_id'], 'new')

    def test_codex_end_preserves_session_until_shell_prompt(self):
        self.hook('exact')
        self.hook('exact', event='SessionEnd', reason='other')
        self.assertEqual(self.state('read')['session_id'], 'exact')
        self.state('shell', '/work/a "quoted" directory')
        self.assertNotIn('session_id', self.state('read'))
        self.assertEqual(self.state('read')['cwd'], '/work/a "quoted" directory')

    def test_failed_resume_survives_shell_prompt(self):
        self.hook('exact')
        self.state('failed')
        self.state('shell', '/work/shared')
        self.assertEqual(self.state('read')['session_id'], 'exact')
        self.hook('replacement')
        self.state('shell', '/work/shared')
        self.assertNotIn('session_id', self.state('read'))

    def test_record_is_written_only_under_the_full_pane_id(self):
        self.hook('exact')
        names = sorted(p.name for p in (self.maps/'sessions').iterdir())
        self.assertEqual(names, [PANE+'.json', PANE+'.lock'])

    def test_claude_clean_exit_clears_only_its_session(self):
        self.hook('claude-one', agent='claude')
        self.hook('claude-one', agent='claude', event='SessionEnd', reason='other')
        self.assertEqual(self.state('read')['session_id'], 'claude-one')
        self.hook('claude-one', agent='claude', event='SessionEnd', reason='prompt_input_exit')
        self.assertNotIn('session_id', self.state('read'))

    def test_explicit_launch_replaces_failed_previous_session(self):
        self.hook('previous')
        self.state('failed')
        self.env.update(CMUX_REMOTE_AGENT='codex', CMUX_REMOTE_CD='/new', CMUX_RESUME_SID='chosen')
        self.state('launch')
        self.assertEqual(self.state('read')['session_id'], 'chosen')
        self.assertNotIn('preserve_on_prompt', self.state('read'))
        self.env['CMUX_RESUME_SID'] = ''
        self.state('launch')
        self.assertNotIn('session_id', self.state('read'))

    # ---- launchers over a fake transport ----------------------------------
    def launcher_environment(self, config=''):
        bin_dir = self.maps/'bin'
        bin_dir.mkdir(exist_ok=True)
        for tool in ('limactl', 'ssh', 'scp'):
            (bin_dir/tool).write_text(FAKE_TRANSPORT)
            (bin_dir/tool).chmod(0o755)
        cli = bin_dir/'cmux'
        cli.write_text('#!/bin/sh\nexit 0\n')
        cli.chmod(0o755)
        (self.maps/'config').write_text(config)
        return dict(self.env, HOME=str(self.maps), PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],
                    CMUX_CLI=str(cli), LAUNCH_LOG=str(self.maps/'launch.log'),
                    CMUX_REMOTE_CONFIG=str(self.maps/'config'))

    def calls(self):
        return [json.loads(s) for s in (self.maps/'launch.log').read_text().splitlines()]

    def test_vmc_explicit_codex_supported_without_prompt(self):
        result = subprocess.run(['bash', str(BIN/'vmc'), '--codex', '--help'],
                                text=True, capture_output=True, env=self.launcher_environment())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_restore_uses_pane_index_and_saved_record_without_menu(self):
        self.hook('01a0ae52-8939-7f61-b83e-2acb625b76cb')
        env = self.launcher_environment('CMUX_TARGET_DEFAULT=wrong-default\nCMUX_TARGET_box=lima:another-vm\n')
        (self.maps/'cache/panes').mkdir(parents=True)
        (self.maps/'cache/panes'/PANE).write_text('box\n')
        result = subprocess.run(['bash', str(BIN/'remote-restore')], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        last = self.calls()[-1]
        self.assertEqual(last[:3], ['limactl', 'shell', 'another-vm'])
        self.assertIn('CMUX_RESUME_SID=01a0ae52-8939-7f61-b83e-2acb625b76cb', last)
        self.assertIn('CMUX_REMOTE_AGENT=codex', last)
        self.assertIn('CMUX_TARGET=box', last)
        self.assertIn('CMUX_REMOTE_CD=/work/shared', last)
        self.assertIn("source %s/lib/cmux-remote-run.sh" % ROOT, last[-1])
        self.assertNotIn('Choose an agent', result.stdout)

    def test_ssh_target_runs_through_ssh_with_options_and_remote_root(self):
        self.hook('01a0ae52-8939-7f61-b83e-2acb625b76cb', agent='claude')
        env = self.launcher_environment('CMUX_TARGET_devbox=ssh:me@devbox:%s\nCMUX_SSH_OPTS=-F /tmp/sshcfg\n' % ROOT)
        (self.maps/'cache/panes').mkdir(parents=True)
        (self.maps/'cache/panes'/PANE).write_text('devbox\n')
        result = subprocess.run(['bash', str(BIN/'remote-restore')], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertTrue(all(c[0] == 'ssh' for c in calls), calls)
        probe = calls[0]
        self.assertIn('-F', probe); self.assertIn('/tmp/sshcfg', probe); self.assertIn('me@devbox', probe)
        self.assertTrue(probe[-1].startswith('bash -c '), probe)
        self.assertIn('%s/lib/cmux-remote-run.sh' % ROOT, probe[-1])
        last = calls[-1]
        self.assertEqual(last[:1], ['ssh']); self.assertIn('-t', last)
        remote = last[-1]
        self.assertTrue(remote.startswith('env '), remote)
        self.assertIn('CMUX_RESUME_SID=01a0ae52-8939-7f61-b83e-2acb625b76cb', remote)
        self.assertIn('CMUX_REMOTE_AGENT=claude', remote)
        self.assertIn('bash -ic source\\ %s/lib/cmux-remote-run.sh' % ROOT, remote)

    def test_vms_ssh_target_passes_env_through_ssh(self):
        env = self.launcher_environment('CMUX_TARGET_devbox=ssh:devbox:/srv/cmux\n')
        result = subprocess.run(['bash', str(BIN/'vms'), '--target', 'devbox', '/srv/app'], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.maps/'cache/panes'/PANE).read_text().strip(), 'devbox')
        self.assertEqual((self.maps/'cache/history-devbox').read_text(), '/srv/app\n')
        last = self.calls()[-1]
        self.assertIn('CMUX_REMOTE_CD=/srv/app', last[-1])
        self.assertIn('/srv/cmux/lib/cmux-remote-run.sh', last[-1])

    def test_launchers_quote_remote_root_and_directory(self):
        env = self.launcher_environment('CMUX_TARGET_box=lima:vm:/opt/my root\n')
        result = subprocess.run(['bash', str(BIN/'vms'), '--target', 'box', '~/my dir'], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        scripts = [c[-1] for c in self.calls()]
        self.assertIn('test -d "$HOME"/my\\ dir', scripts)
        self.assertIn('source /opt/my\\ root/lib/cmux-remote-run.sh', scripts)
        self.assertIn('CMUX_REMOTE_CD=~/my dir', self.calls()[-1])

    def test_tilde_directory_expands_on_the_remote(self):
        home = self.maps/'remote-home'
        (home/'proj').mkdir(parents=True)
        env = dict(self.env, HOME=str(home), CMUX_REMOTE_CD='~/proj', CMUX_REMOTE_AGENT='', CMUX_RESUME_SID='')
        result = subprocess.run(['bash', '-c', 'source "$1"', 'run', str(LIB/'cmux-remote-run.sh')],
                                env=env, text=True, capture_output=True, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('Directory unavailable', result.stderr)
        self.assertEqual(self.state('read')['cwd'], str(home/'proj'))

    def test_restore_evaluates_only_record_lines_from_the_remote(self):
        self.hook('e87ccf85-d6b8-4477-9c7d-ff7dcdeca2ce', agent='claude')
        env = self.launcher_environment()
        marker = "if script is not None and ('cmux-remote-state.py' in script"
        self.assertIn(marker, FAKE_TRANSPORT)
        (self.maps/'bin/limactl').write_text(FAKE_TRANSPORT.replace(marker,
            "if script is not None and 'read-env' in script: print('touch '+os.environ['HOME']+'/pwned'); print('echo Welcome to Ubuntu')\n"+marker))
        result = subprocess.run(['bash', str(BIN/'remote-restore')], env=env, text=True, capture_output=True, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.maps/'pwned').exists())
        self.assertIn('CMUX_RESUME_SID=e87ccf85-d6b8-4477-9c7d-ff7dcdeca2ce', self.calls()[-1])

    def test_restore_treats_output_without_a_record_as_a_failed_read(self):
        env = self.launcher_environment()
        marker = "if script is not None and ('cmux-remote-state.py' in script"
        (self.maps/'bin/limactl').write_text(FAKE_TRANSPORT.replace(marker,
            "if script is not None and 'read-env' in script: print('echo Welcome to Ubuntu'); sys.exit(0)\n"+marker))
        env['CMUX_REMOTE_RESTORE_ATTEMPTS'] = '1'
        result = subprocess.run(['bash', str(BIN/'remote-restore')], env=env, text=True, capture_output=True, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(any('-ic' in c for c in self.calls()), self.calls())

    def test_interactive_menu_selects_codex(self):
        env = self.launcher_environment()
        master, slave = pty.openpty()
        process = subprocess.Popen(['bash', str(BIN/'vmc')], env=env, stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        try:
            output = b''
            for _ in range(10):
                if select.select([master], [], [], 1)[0]: output += os.read(master, 4096)
                if b'Agent [1/2' in output: break
            self.assertIn(b'1) Claude', output)
            self.assertIn(b'2) Codex', output)
            os.write(master, b'2\n')
            self.assertEqual(process.wait(timeout=5), 0)
            self.assertIn('CMUX_REMOTE_AGENT=codex', self.calls()[-1])
        finally:
            if process.poll() is None: process.kill(); process.wait()
            os.close(master)

    def test_cmux_remote_cli_resolves_targets(self):
        env = self.launcher_environment('CMUX_TARGET_DEFAULT=a\nCMUX_TARGET_a=lima:vm-a\nCMUX_TARGET_b=ssh:host-b:/opt/x\nCMUX_TARGET_bad=ftp:x\n')
        out = subprocess.run([str(BIN/'cmux-remote'), 'targets'], env=env, text=True, capture_output=True).stdout
        self.assertIn('a (default)', out); self.assertIn('b ssh:host-b:/opt/x', out)
        for name, expected in (('a', 'backend=lima address=vm-a root=%s' % ROOT),
                               ('b', 'backend=ssh address=host-b root=/opt/x'),
                               ('zzz', 'backend=lima address=zzz root=%s' % ROOT)):
            r = subprocess.run([str(BIN/'cmux-remote'), 'resolve', name], env=env, text=True, capture_output=True)
            self.assertEqual(r.stdout.strip(), expected, r.stderr)
        r = subprocess.run([str(BIN/'cmux-remote'), 'resolve', 'bad'], env=env, text=True, capture_output=True)
        self.assertNotEqual(r.returncode, 0); self.assertIn('unknown backend', r.stderr)

    def test_config_file_fills_only_unset_variables(self):
        config = self.maps/'config'
        config.write_text('# comment\nCMUX_TARGET_DEFAULT=from-file\nCMUX_AGENT_MODE="bypass"\nNOT_OURS=x\nCMUX_not a key=1\n')
        script = 'source "%s"; echo "$CMUX_TARGET_DEFAULT $CMUX_AGENT_MODE ${NOT_OURS:-unset}"' % (LIB/'cmux-remote-env.sh')
        env = {k: v for k, v in os.environ.items() if not k.startswith('CMUX_')}
        env.update(HOME=str(self.maps), CMUX_REMOTE_CONFIG=str(config))
        result = subprocess.run(['bash', '-c', script], env=env, text=True, capture_output=True)
        self.assertEqual(result.stdout.split(), ['from-file', 'bypass', 'unset'], result.stderr)
        self.assertEqual(result.stderr, '')
        result = subprocess.run(['bash', '-c', script], env=dict(env, CMUX_TARGET_DEFAULT='from-env'), text=True, capture_output=True)
        self.assertEqual(result.stdout.split()[0], 'from-env')
        if shutil.which('zsh'):
            result = subprocess.run(['zsh', '-c', script], env=env, text=True, capture_output=True)
            self.assertEqual(result.stdout.split()[:2], ['from-file', 'bypass'], result.stderr)
            self.assertEqual(result.stderr, '')

    # ---- codex wrapper and remote shell block -----------------------------
    def test_codex_wrapper_identifies_only_explicit_interactive_uuid(self):
        loader = importlib.machinery.SourceFileLoader('codex_wrapper', str(LIB/'cmux-codex'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        sid = '01a0ae52-8939-7f61-b83e-2acb625b76cb'
        self.assertEqual(module.resume_target(['--model', 'x', 'resume', '--cd', '/work', sid]), (sid, '/work'))
        self.assertEqual(module.resume_target(['resume', sid]), (sid, None))
        for args in (['exec', 'resume', sid], ['resume', '--last'], ['--', 'resume', sid], ['review'], ['resume', 'named']):
            self.assertEqual(module.resume_target(args)[0], None, args)
        self.assertEqual(module.capture_args(['resume', sid, '--cd', 'relative', '--model', 'x']), ['resume', sid, '--model', 'x'])

    def test_remote_shell_existing_alias_and_failure_status(self):
        command = '''alias codex='/home/someone/.local/bin/codex --dangerously-bypass-approvals-and-sandbox'
CMUX_REMOTE_ROOT=/remote/root
source "$1"
alias codex
python3() { printf '%s\\n' "$*"; }
false
_cmux_track
'''
        result = subprocess.run(['bash', '-O', 'expand_aliases', '-c', command, 'test', str(REMOTE/'bashrc-cmux-block.sh')],
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("alias codex='/remote/root/lib/cmux-codex --dangerously-bypass-approvals-and-sandbox'", result.stdout)
        self.assertIn('cmux-remote-state.py failed', result.stdout)
        self.assertIn('cmux-remote-state.py shell', result.stdout)

    def test_codex_wrapper_runs_codex_even_if_tracking_fails(self):
        bin_dir = self.maps/'.local/bin'
        bin_dir.mkdir(parents=True)
        fake = bin_dir/'codex'
        fake.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
        fake.chmod(0o755)
        blocker = self.maps/'not-a-directory'
        blocker.write_text('')
        env = dict(self.env, PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],
                   CMUX_REMOTE_STATE_DIR=str(blocker/'sessions'), CMUX_CLI=str(blocker/'missing-cmux'))
        sid = '01a0ae52-8939-7f61-b83e-2acb625b76cb'
        result = subprocess.run([str(LIB/'cmux-codex'), 'resume', sid], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ['resume', sid])

    def test_trust_cwd_marks_the_directory_and_keeps_other_settings(self):
        home = self.maps/'remote-home'
        home.mkdir()
        (home/'.claude.json').write_text(json.dumps({'theme': 'dark', 'projects': {'/other': {'hasTrustDialogAccepted': True}}}))
        project = self.maps/'proj'
        project.mkdir()
        result = subprocess.run([str(LIB/'cmux-trust-cwd')], cwd=project, env=dict(self.env, HOME=str(home)), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((home/'.claude.json').read_text())
        self.assertTrue(data['projects'][str(project)]['hasTrustDialogAccepted'])
        self.assertTrue(data['projects']['/other']['hasTrustDialogAccepted'])
        self.assertEqual(data['theme'], 'dark')
        self.assertEqual([p.name for p in home.iterdir()], ['.claude.json'])

    def test_notification_hook_does_not_need_jq(self):
        home = self.maps/'remote-home'
        (home/'.claude').mkdir(parents=True)
        (home/'.claude/cmux-osc-emit.sh').write_text('#!/usr/bin/env bash\nprintf "%s" "$1"\n')
        tools = self.maps/'tools'
        tools.mkdir()
        (tools/'python3').symlink_to(sys.executable)
        (tools/'bash').symlink_to(shutil.which('bash'))
        env = dict(self.env, HOME=str(home), PATH=str(tools))
        result = subprocess.run(['bash', str(REMOTE/'notify/cmux-osc-notify.sh')], input='{"message": "Allow Bash?\\u0007"}',
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'cmux: Allow Bash?')

    def test_agent_mode_controls_bypass_flags(self):
        bin_dir = self.maps/'.local/bin'
        bin_dir.mkdir(parents=True)
        for executable in ('claude', 'codex'):
            path = bin_dir/executable
            path.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            path.chmod(0o755)
        env = dict(os.environ, HOME=str(self.maps), PATH=str(bin_dir)+os.pathsep+os.environ['PATH'])
        for key in list(env):
            if key.startswith('CMUX_'): del env[key]
        sid = '01a0ae52-8939-7f61-b83e-2acb625b76cb'
        cases = [('bypass', 'claude', [], ['--dangerously-skip-permissions']),
                 ('bypass', 'codex', ['resume', sid], ['--dangerously-bypass-approvals-and-sandbox', 'resume', sid]),
                 ('default', 'claude', ['--resume', sid], ['--resume', sid]),
                 (None, 'codex', [], [])]
        for mode, agent, args, expected in cases:
            with self.subTest(mode=mode, agent=agent):
                run_env = dict(env)
                if mode: run_env['CMUX_AGENT_MODE'] = mode
                result = subprocess.run(['bash', str(LIB/'cmux-remote-agent'), agent, *args], env=run_env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), expected)

    # ---- readiness helper --------------------------------------------------
    def ready_env(self, shim_body, **extra):
        bin_dir = self.maps/'bin'
        bin_dir.mkdir(exist_ok=True)
        for tool in ('limactl', 'ssh'):
            (bin_dir/tool).write_text(shim_body)
            (bin_dir/tool).chmod(0o755)
        env = dict(self.env, HOME=str(self.maps), PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],
                   LAUNCH_LOG=str(self.maps/'launch.log'), LIMA_HOME=str(self.maps/'.lima'),
                   CMUX_REMOTE_HOST_BOOT_TIME='1000000', CMUX_REMOTE_START_ATTEMPTS='1', CMUX_REMOTE_PROBE='test -r /x')
        env.update(extra)
        return env

    STALE_SHIM = '''#!/usr/bin/env python3
import os,pathlib,sys
p=pathlib.Path(os.environ['LAUNCH_LOG']); inst=pathlib.Path(os.environ['LIMA_HOME'])/'ubuntu'
stale=(inst/'ha.pid').exists()
if sys.argv[1]=='list': print('Broken' if stale else ('Running' if p.exists() else 'Stopped'))
elif sys.argv[1]=='start':
 if stale: print('errors inspecting instance',file=sys.stderr); sys.exit(1)
 (inst/'ha.pid').write_text('4242'); p.write_text('started\\n')
'''

    def test_concurrent_restores_start_vm_once(self):
        env = self.ready_env('''#!/usr/bin/env python3
import os,pathlib,sys,time
p=pathlib.Path(os.environ['LAUNCH_LOG'])
if sys.argv[1]=='list': print('Running' if p.exists() else 'Stopped')
elif sys.argv[1]=='start':
 time.sleep(0.1)
 with p.open('a') as f:f.write('started\\n')
''')
        processes = [subprocess.Popen(['python3', str(LIB/'cmux-remote-ready.py'), 'lima', 'ubuntu'], env=env) for _ in range(4)]
        for process in processes: self.assertEqual(process.wait(timeout=10), 0)
        self.assertEqual((self.maps/'launch.log').read_text(), 'started\n')

    def test_ready_refuses_to_create_an_unknown_lima_instance(self):
        env = self.ready_env('''#!/usr/bin/env python3
import os,pathlib,sys
if sys.argv[1]=='list': sys.exit(0)
pathlib.Path(os.environ['LAUNCH_LOG']).write_text(' '.join(sys.argv[1:])+'\\n')
''')
        result = subprocess.run(['python3', str(LIB/'cmux-remote-ready.py'), 'lima', 'devbx'], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
        self.assertIn("'devbx' does not exist", result.stdout)
        self.assertFalse((self.maps/'launch.log').exists())

    def test_ready_clears_pid_files_left_from_before_host_boot(self):
        env = self.ready_env(self.STALE_SHIM)
        inst = self.maps/'.lima/ubuntu'; inst.mkdir(parents=True)
        for name in ('ha.pid', 'vz.pid', 'ha.sock'):
            (inst/name).write_text('2471'); os.utime(inst/name, (999_000, 999_000))
        result = subprocess.run(['python3', str(LIB/'cmux-remote-ready.py'), 'lima', 'ubuntu'], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('stale Lima files', result.stdout)
        self.assertEqual((inst/'ha.pid').read_text(), '4242')
        self.assertFalse((inst/'vz.pid').exists())

    def test_ready_keeps_pid_files_written_after_host_boot(self):
        env = self.ready_env(self.STALE_SHIM)
        inst = self.maps/'.lima/ubuntu'; inst.mkdir(parents=True)
        (inst/'ha.pid').write_text('2471'); os.utime(inst/'ha.pid', (1_000_500, 1_000_500))
        result = subprocess.run(['python3', str(LIB/'cmux-remote-ready.py'), 'lima', 'ubuntu'], env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((inst/'ha.pid').read_text(), '2471')
        self.assertFalse((self.maps/'launch.log').exists())

    def test_ready_waits_until_remote_answers_over_ssh(self):
        env = self.ready_env('''#!/usr/bin/env python3
import os,pathlib,sys
p=pathlib.Path(os.environ['LAUNCH_LOG'])
n=len(p.read_text().splitlines()) if p.exists() else 0
with p.open('a') as f:f.write(' '.join(sys.argv[1:])+'\\n')
if n<2: print('ssh: connect to host devbox port 22: Connection refused',file=sys.stderr); sys.exit(255)
''', CMUX_REMOTE_PROBE_TIMEOUT='20', CMUX_SSH_OPTS='-F /tmp/cfg')
        result = subprocess.run(['python3', str(LIB/'cmux-remote-ready.py'), 'ssh', 'devbox'], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        lines = (self.maps/'launch.log').read_text().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn('-F /tmp/cfg devbox -- bash -c', lines[0])
        self.assertIn('become reachable', result.stdout)
        env['CMUX_REMOTE_PROBE_TIMEOUT'] = '0'; (self.maps/'launch.log').unlink()
        result = subprocess.run(['python3', str(LIB/'cmux-remote-ready.py'), 'ssh', 'devbox'], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Connection refused', result.stdout)

    def test_restore_retries_connection_that_fails_before_remote_shell(self):
        self.hook('e87ccf85-d6b8-4477-9c7d-ff7dcdeca2ce', agent='claude')
        env = self.launcher_environment()
        shim = self.maps/'bin/limactl'
        marker = "if script is not None and 'test -r' in script: sys.exit(0)"
        self.assertIn(marker, FAKE_TRANSPORT)
        shim.write_text(FAKE_TRANSPORT.replace(marker,
            marker+"\n"
            "if 'bash' in argv and argv[argv.index('bash')+1]=='-ic':\n"
            " n=sum(1 for l in open(log) if '-ic' in l)\n"
            " if n<2: sys.exit(255)\n"
            " print('CONNECTED')\n"))
        env['CMUX_REMOTE_RESTORE_ATTEMPTS'] = '3'
        result = subprocess.run(['bash', str(BIN/'remote-restore')], env=env, text=True, capture_output=True, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('CONNECTED', result.stdout)
        self.assertEqual(sum(1 for c in self.calls() if '-ic' in c), 2)
        self.assertIn('attempt 1 of 3', result.stderr)
        env['CMUX_REMOTE_RESTORE_ATTEMPTS'] = '1'; (self.maps/'launch.log').unlink()
        result = subprocess.run(['bash', str(BIN/'remote-restore')], env=env, text=True, capture_output=True, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 1)
        self.assertIn('not reachable', result.stderr)

    # ---- host installer ----------------------------------------------------
    def install_environment(self):
        env = self.launcher_environment()
        rsync = self.maps/'bin/rsync'
        rsync.write_text('#!/bin/sh\nprintf \'rsync %s\\n\' "$*" >> "$LAUNCH_LOG"\n')
        rsync.chmod(0o755)
        env['CMUX_REMOTE_CONFIG'] = str(self.maps/'install-config')
        return env

    def install_log(self):
        log = self.maps/'launch.log'
        return log.read_text() if log.exists() else ''

    def test_install_refuses_to_sync_into_a_directory_that_is_not_this_repo(self):
        env = self.install_environment()
        foreign = self.maps/'remote-root'
        foreign.mkdir()
        (foreign/'keep.txt').write_text('mine')
        result = subprocess.run(['bash', str(ROOT/'install.sh'), '--sync', '--target', 'devbox=ssh:devbox:%s' % foreign],
                                env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('refusing to sync', result.stderr)
        self.assertNotIn('rsync', self.install_log())
        self.assertEqual((foreign/'keep.txt').read_text(), 'mine')

    def test_install_syncs_into_a_missing_or_repo_directory(self):
        env = self.install_environment()
        fresh = self.maps/'fresh-root'
        result = subprocess.run(['bash', str(ROOT/'install.sh'), '--sync', '--target', 'devbox=ssh:devbox:%s' % fresh],
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('rsync -a --delete', self.install_log())
        self.assertIn('devbox:%s/' % fresh, self.install_log())
        self.assertIn('CMUX_TARGET_devbox=ssh:devbox:%s' % fresh, (self.maps/'install-config').read_text())
        self.assertIn('>>> CMUX-REMOTE-RESUME >>>', (self.maps/'.zshrc').read_text())

    # ---- remote installer --------------------------------------------------
    def test_remote_installer_is_idempotent_and_renders_root(self):
        home = self.maps/'remote-home'
        (home/'.claude').mkdir(parents=True)
        (home/'.claude/settings.json').write_text(json.dumps({'permissions': {'defaultMode': 'auto'},
            'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'bash ~/.claude/other.sh'}]}]}}))
        (home/'.bashrc').write_text('export FOO=1\n')
        (home/'.codex').mkdir()
        (home/'.codex/config.toml').write_text('model = "x"\n# cmux VM session hook trust begin\n'
                                               '[hooks.state."old"]\ntrusted_hash = "sha256:0"\n# cmux VM session hook trust end\n')
        env = dict(os.environ, HOME=str(home))
        for _ in range(2):
            result = subprocess.run(['python3', str(REMOTE/'install-remote.py'), '--notify'], env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        bashrc = (home/'.bashrc').read_text()
        self.assertEqual(bashrc.count('# >>> CMUX-REMOTE-SHELL >>>'), 1)
        self.assertIn('export FOO=1', bashrc)
        self.assertIn(str(ROOT), bashrc)
        self.assertNotIn('@ROOT@', bashrc)
        settings = json.loads((home/'.claude/settings.json').read_text())
        self.assertEqual(settings['permissions'], {'defaultMode': 'auto'})
        stop = [h['command'] for g in settings['hooks']['Stop'] for h in g['hooks']]
        self.assertEqual(stop, ['bash ~/.claude/other.sh', 'bash ~/.claude/cmux-remote-session.sh stop', 'bash ~/.claude/cmux-osc-stop.sh'])
        self.assertEqual(len(settings['hooks']['SessionStart']), 1)
        self.assertIn('Notification', settings['hooks'])
        self.assertIn(str(LIB/'cmux-remote-state.py'), (home/'.claude/cmux-remote-session.sh').read_text())
        codex = json.loads((home/'.codex/hooks.json').read_text())
        self.assertEqual(len(codex['hooks']['SessionStart']), 1)
        toml = (home/'.codex/config.toml').read_text()
        self.assertEqual(toml.count('hook trust begin'), 1)
        self.assertIn('model = "x"', toml)
        self.assertNotIn('cmux VM session', toml)
        self.assertIn('# cmux-remote-resume hook trust begin', toml)
        self.assertTrue((home/'.cache/cmux-remote/sessions').is_dir())


if __name__ == '__main__':
    unittest.main()
