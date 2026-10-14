import json
import os
from pathlib import Path
import subprocess
import select
import pty
import tempfile
import unittest
import importlib.machinery
import importlib.util

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT/'lib'
BIN = ROOT/'bin'
GUEST = ROOT/'guest'
PANE = 'aaaaaaaa-1111-4111-8111-111111111111'
OTHER = 'aaaaaaaa-2222-4222-8222-222222222222'


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.maps = Path(self.tmp.name)
        self.env = dict(os.environ, CMUX_VM_MAP_DIR=str(self.maps),
                        CMUX_SURFACE_ID=PANE, CMUX_VM_NAME='ubuntu')

    def state(self, *args, pane=PANE, payload=None):
        script = LIB / 'cmux-vm-state.py'
        self.assertTrue(script.exists(), 'VM state tracker is not implemented')
        result = subprocess.run(['python3', str(script), *args],
                                input=json.dumps(payload or {}), text=True,
                                capture_output=True,
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
        self.assertEqual(self.state('read')['vm_name'], 'ubuntu')

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

    def test_legacy_claude_mapping_still_restores(self):
        (self.maps / 'aaaaaaaa.json').write_text(json.dumps(
            dict(session_id='legacy', cwd='/old/project')))
        record = self.state('read')
        self.assertEqual(record['agent'], 'claude')
        self.assertEqual(record['session_id'], 'legacy')

    def test_old_shell_can_retire_new_mapping(self):
        self.hook('exact')
        (self.maps / 'aaaaaaaa.json').write_text('{"cwd":"/work", "event":"shell"}')
        self.assertNotIn('session_id', self.state('read'))

    def test_claude_clean_exit_clears_only_its_session(self):
        self.hook('claude-one', agent='claude')
        self.hook('claude-one', agent='claude', event='SessionEnd', reason='other')
        self.assertEqual(self.state('read')['session_id'], 'claude-one')
        self.hook('claude-one', agent='claude', event='SessionEnd', reason='prompt_input_exit')
        self.assertNotIn('session_id', self.state('read'))

    def test_vmc_explicit_codex_supported_without_prompt(self):
        result = subprocess.run(['bash', str(BIN/'vmc'), '--codex', '--help'],
                                text=True, capture_output=True, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_explicit_launch_replaces_failed_previous_session(self):
        self.hook('previous')
        self.state('failed')
        self.env.update(CMUX_VM_AGENT='codex', CMUX_VM_CD='/new', CMUX_RESUME_SID='chosen')
        self.state('launch')
        self.assertEqual(self.state('read')['session_id'], 'chosen')
        self.assertNotIn('preserve_on_prompt', self.state('read'))
        self.env['CMUX_RESUME_SID'] = ''
        self.state('launch')
        self.assertNotIn('session_id', self.state('read'))

    def test_partial_legacy_write_does_not_hide_full_record(self):
        self.hook('exact')
        (self.maps/'aaaaaaaa.json').write_text('{"cwd":')
        self.assertEqual(self.state('read')['session_id'], 'exact')

    def launcher_environment(self):
        bin_dir = self.maps/'bin'
        bin_dir.mkdir()
        shim = bin_dir/'limactl'
        shim.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                        'if sys.argv[1] == "list": print("Running")\n'
                        'else:\n'
                        ' with open(os.environ["LAUNCH_LOG"],"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n')
        shim.chmod(0o755)
        cli = bin_dir/'cmux'
        cli.write_text('#!/bin/sh\nexit 0\n')
        cli.chmod(0o755)
        return dict(self.env, HOME=str(self.maps),
                    PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],
                    CMUX_BUNDLED_CLI_PATH=str(cli), LAUNCH_LOG=str(self.maps/'launch.log'))

    def test_restore_passes_exact_id_and_saved_vm_without_menu(self):
        self.env['CMUX_VM_NAME'] = 'another-vm'
        self.hook('01a0ae52-8939-7f61-b83e-2acb625b76cb')
        env = self.launcher_environment()
        env['CMUX_VM_NAME'] = 'wrong-default'
        result = subprocess.run(['bash', str(BIN/'vm-restore')],env=env,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        calls = [json.loads(s) for s in (self.maps/'launch.log').read_text().splitlines()]
        self.assertEqual(calls[-1][:2],['shell','another-vm'])
        self.assertIn('CMUX_RESUME_SID=01a0ae52-8939-7f61-b83e-2acb625b76cb', calls[-1])
        self.assertIn('CMUX_VM_AGENT=codex', calls[-1])
        self.assertNotIn('Choose an agent',result.stdout)

    def test_interactive_menu_selects_codex(self):
        env = self.launcher_environment()
        master, slave = pty.openpty()
        process = subprocess.Popen(['bash',str(BIN/'vmc')],env=env,stdin=slave,stdout=slave,stderr=slave)
        os.close(slave)
        try:
            output = b''
            for _ in range(10):
                if select.select([master],[],[],1)[0]: output += os.read(master,4096)
                if b'Agent [1/2' in output:break
            self.assertIn(b'1) Claude',output)
            self.assertIn(b'2) Codex',output)
            os.write(master,b'2\n')
            self.assertEqual(process.wait(timeout=5),0)
            calls = [json.loads(s) for s in (self.maps/'launch.log').read_text().splitlines()]
            self.assertIn('CMUX_VM_AGENT=codex',calls[-1])
        finally:
            if process.poll() is None: process.kill(); process.wait()
            os.close(master)

    def test_codex_wrapper_identifies_only_explicit_interactive_uuid(self):
        path = LIB/'cmux-codex'
        self.assertTrue(path.exists(), 'Explicit Codex resume registration is missing')
        loader = importlib.machinery.SourceFileLoader('codex_wrapper',str(path))
        spec = importlib.util.spec_from_loader(loader.name,loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        sid = '01a0ae52-8939-7f61-b83e-2acb625b76cb'
        self.assertEqual(module.resume_target(['--model','x','resume','--cd','/work',sid]), (sid,'/work'))
        self.assertEqual(module.resume_target(['resume',sid]), (sid,None))
        for args in (['exec','resume',sid], ['resume','--last'], ['--','resume',sid], ['review'], ['resume','named']):
            self.assertEqual(module.resume_target(args)[0], None, args)
        self.assertEqual(module.capture_args(['resume',sid,'--cd','relative','--model','x']),
                         ['resume',sid,'--model','x'])

    def test_guest_shell_existing_alias_and_failure_status(self):
        command = '''alias codex='/home/someone/.local/bin/codex --dangerously-bypass-approvals-and-sandbox'
CMUX_VM_GUEST_ROOT=/guest/root
source "$1"
alias codex
python3() { printf '%s\\n' "$*"; }
false
_cmux_track
'''
        result = subprocess.run(['bash','-O','expand_aliases','-c',command,'test',
                                 str(GUEST/'bashrc-cmux-block.sh')],env=self.env,
                                text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("alias codex='/guest/root/lib/cmux-codex --dangerously-bypass-approvals-and-sandbox'",result.stdout)
        self.assertIn('cmux-vm-state.py failed',result.stdout)
        self.assertIn('cmux-vm-state.py shell',result.stdout)

    def test_concurrent_restores_start_vm_once(self):
        helper=LIB/'cmux-vm-ready.py'
        self.assertTrue(helper.exists(), 'VM startup is not coordinated across panes')
        env=self.launcher_environment()
        shim=self.maps/'bin/limactl'
        shim.write_text('''#!/usr/bin/env python3
import os,pathlib,sys,time
p=pathlib.Path(os.environ['LAUNCH_LOG'])
if sys.argv[1]=='list': print('Running' if p.exists() else 'Stopped')
elif sys.argv[1]=='start':
 time.sleep(0.1)
 with p.open('a') as f:f.write('started\\n')
''')
        processes=[subprocess.Popen(['python3',str(helper),'ubuntu'],env=env) for _ in range(4)]
        for process in processes:self.assertEqual(process.wait(timeout=10),0)
        self.assertEqual((self.maps/'launch.log').read_text(),'started\n')

    def lima_shim_with_stale_pids(self, env):
        """limactl that mimics Lima after a host reboot: leftover pid files make
        the instance Broken and `start` refuse with 'errors inspecting instance'."""
        inst=self.maps/'.lima/ubuntu'
        inst.mkdir(parents=True)
        shim=self.maps/'bin/limactl'
        shim.write_text('''#!/usr/bin/env python3
import os,pathlib,sys
p=pathlib.Path(os.environ['LAUNCH_LOG']); inst=pathlib.Path(os.environ['LIMA_HOME'])/'ubuntu'
stale=(inst/'ha.pid').exists()
if sys.argv[1]=='list': print('Broken' if stale else ('Running' if p.exists() else 'Stopped'))
elif sys.argv[1]=='start':
 if stale: print('errors inspecting instance',file=sys.stderr); sys.exit(1)
 (inst/'ha.pid').write_text('4242'); p.write_text('started\\n')
elif sys.argv[1]=='shell':
 with (inst/'shell.log').open('a') as f:f.write(' '.join(sys.argv[2:])+'\\n')
''')
        env.update(LIMA_HOME=str(self.maps/'.lima'), CMUX_VM_HOST_BOOT_TIME='1000000',
                   CMUX_VM_START_ATTEMPTS='1')
        return inst

    def test_ready_clears_pid_files_left_from_before_host_boot(self):
        helper=LIB/'cmux-vm-ready.py'
        env=self.launcher_environment()
        inst=self.lima_shim_with_stale_pids(env)
        for name in ('ha.pid','vz.pid','ha.sock'):
            (inst/name).write_text('2471')
            os.utime(inst/name,(999_000,999_000))
        result=subprocess.run(['python3',str(helper),'ubuntu'],env=env,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('stale Lima files',result.stdout)
        self.assertEqual((self.maps/'launch.log').read_text(),'started\n')
        self.assertEqual((inst/'ha.pid').read_text(),'4242')
        self.assertFalse((inst/'vz.pid').exists())

    def test_ready_keeps_pid_files_written_after_host_boot(self):
        helper=LIB/'cmux-vm-ready.py'
        env=self.launcher_environment()
        inst=self.lima_shim_with_stale_pids(env)
        (inst/'ha.pid').write_text('2471')
        os.utime(inst/'ha.pid',(1_000_500,1_000_500))
        result=subprocess.run(['python3',str(helper),'ubuntu'],env=env,text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual((inst/'ha.pid').read_text(),'2471')
        self.assertFalse((self.maps/'launch.log').exists())

    def test_ready_waits_until_guest_answers(self):
        helper=LIB/'cmux-vm-ready.py'
        env=self.launcher_environment()
        shim=self.maps/'bin/limactl'
        shim.write_text('''#!/usr/bin/env python3
import os,pathlib,sys
p=pathlib.Path(os.environ['LAUNCH_LOG'])
if sys.argv[1]=='list': print('Running')
elif sys.argv[1]=='shell':
 n=len(p.read_text().splitlines()) if p.exists() else 0
 with p.open('a') as f:f.write('probe\\n')
 if n<2: print('ssh: connect to host 127.0.0.1 port 1: Connection refused',file=sys.stderr); sys.exit(255)
''')
        env.update(CMUX_VM_PROBE_TIMEOUT='20')
        result=subprocess.run(['python3',str(helper),'ubuntu'],env=env,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((self.maps/'launch.log').read_text(),'probe\n'*3)
        self.assertIn('become reachable',result.stdout)
        env.update(CMUX_VM_PROBE_TIMEOUT='0')
        (self.maps/'launch.log').unlink()
        result=subprocess.run(['python3',str(helper),'ubuntu'],env=env,text=True,capture_output=True)
        self.assertEqual(result.returncode,1)
        self.assertIn('Connection refused',result.stdout)

    def test_restore_retries_connection_that_fails_before_guest_shell(self):
        env=self.launcher_environment()
        self.hook('e87ccf85-d6b8-4477-9c7d-ff7dcdeca2ce',agent='claude')
        shim=self.maps/'bin/limactl'
        shim.write_text('''#!/usr/bin/env python3
import json,os,pathlib,sys
p=pathlib.Path(os.environ['LAUNCH_LOG'])
if sys.argv[1]=='list': print('Running')
elif sys.argv[1]=='shell' and 'env' in sys.argv:
 n=len(p.read_text().splitlines()) if p.exists() else 0
 with p.open('a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
 if n<1: sys.exit(255)
 print('CONNECTED',json.dumps(sys.argv[1:]))
''')
        env.update(CMUX_VM_RESTORE_ATTEMPTS='3')
        result=subprocess.run(['bash',str(BIN/'vm-restore')],env=env,text=True,capture_output=True,stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('CONNECTED',result.stdout)
        self.assertIn('CMUX_RESUME_SID=e87ccf85-d6b8-4477-9c7d-ff7dcdeca2ce',result.stdout)
        self.assertEqual(len((self.maps/'launch.log').read_text().splitlines()),2)
        self.assertIn('attempt 1 of 3',result.stderr)
        env.update(CMUX_VM_RESTORE_ATTEMPTS='1')
        (self.maps/'launch.log').unlink()
        result=subprocess.run(['bash',str(BIN/'vm-restore')],env=env,text=True,capture_output=True,stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode,1)
        self.assertIn('not reachable',result.stderr)

    def test_vm_agent_mode_controls_bypass_flags(self):
        launcher = LIB/'cmux-vm-agent'
        bin_dir = self.maps/'.local/bin'
        bin_dir.mkdir(parents=True)
        for executable in ('claude','codex'):
            path = bin_dir/executable
            path.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            path.chmod(0o755)
        env = dict(os.environ,HOME=str(self.maps),PATH=str(bin_dir)+os.pathsep+os.environ['PATH'])
        for key in list(env):
            if key.startswith('CMUX_'):del env[key]
        sid = '01a0ae52-8939-7f61-b83e-2acb625b76cb'
        cases = [
            ('bypass','claude',[],['--dangerously-skip-permissions']),
            ('bypass','claude',['--resume',sid],['--dangerously-skip-permissions','--resume',sid]),
            ('bypass','codex',[],['--dangerously-bypass-approvals-and-sandbox']),
            ('bypass','codex',['resume',sid],['--dangerously-bypass-approvals-and-sandbox','resume',sid]),
            ('default','claude',['--resume',sid],['--resume',sid]),
            ('default','codex',['resume',sid],['resume',sid]),
            (None,'claude',[],[]),
        ]
        for mode,agent,args,expected in cases:
            with self.subTest(mode=mode,agent=agent,args=args):
                run_env = dict(env)
                if mode: run_env['CMUX_VM_AGENT_MODE'] = mode
                result = subprocess.run(['bash',str(launcher),agent,*args],env=run_env,
                                        text=True,capture_output=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout),expected)

    def test_config_file_fills_only_unset_variables(self):
        config = self.maps/'config'
        config.write_text('# comment\nCMUX_VM_NAME=from-file\nCMUX_VM_AGENT_MODE="bypass"\nNOT_OURS=x\n')
        script = 'source "%s"; echo "$CMUX_VM_NAME $CMUX_VM_AGENT_MODE $CMUX_VM_GUEST_ROOT ${NOT_OURS:-unset}"' % (LIB/'cmux-lima-env.sh')
        env = {k:v for k,v in os.environ.items() if not k.startswith('CMUX_')}
        env.update(HOME=str(self.maps),CMUX_LIMA_CONFIG=str(config))
        result = subprocess.run(['bash','-c',script],env=env,text=True,capture_output=True)
        self.assertEqual(result.stdout.split(),['from-file','bypass',str(ROOT),'unset'],result.stderr)
        result = subprocess.run(['bash','-c',script],env=dict(env,CMUX_VM_NAME='from-env'),text=True,capture_output=True)
        self.assertEqual(result.stdout.split()[0],'from-env')
        result = subprocess.run(['zsh','-c',script],env=env,text=True,capture_output=True)
        self.assertEqual(result.stdout.split()[:2],['from-file','bypass'],result.stderr)

    def test_guest_installer_is_idempotent_and_renders_root(self):
        home = self.maps/'guest-home'
        (home/'.claude').mkdir(parents=True)
        (home/'.claude/settings.json').write_text(json.dumps({'permissions':{'defaultMode':'auto'},
            'hooks':{'Stop':[{'hooks':[{'type':'command','command':'bash ~/.claude/other.sh'}]}]}}))
        (home/'.bashrc').write_text('export FOO=1\n')
        env = dict(os.environ,HOME=str(home))
        for _ in range(2):
            result = subprocess.run(['python3',str(GUEST/'install-guest.py'),'--notify'],env=env,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
        bashrc = (home/'.bashrc').read_text()
        self.assertEqual(bashrc.count('# >>> CMUX-VM-SHELL >>>'),1)
        self.assertIn('export FOO=1',bashrc)
        self.assertIn(str(ROOT),bashrc)
        self.assertNotIn('@ROOT@',bashrc)
        settings = json.loads((home/'.claude/settings.json').read_text())
        self.assertEqual(settings['permissions'],{'defaultMode':'auto'})
        stop = [h['command'] for g in settings['hooks']['Stop'] for h in g['hooks']]
        self.assertEqual(stop,['bash ~/.claude/other.sh','bash ~/.claude/cmux-vm-session.sh stop','bash ~/.claude/cmux-osc-stop.sh'])
        self.assertEqual(len(settings['hooks']['SessionStart']),1)
        self.assertIn('Notification',settings['hooks'])
        entry = (home/'.claude/cmux-vm-session.sh').read_text()
        self.assertIn(str(LIB/'cmux-vm-state.py'),entry)
        codex = json.loads((home/'.codex/hooks.json').read_text())
        self.assertEqual(len(codex['hooks']['SessionStart']),1)
        self.assertEqual((home/'.codex/config.toml').read_text().count('hook trust begin'),1)
        self.assertTrue((ROOT/'state/sessions').is_dir())


if __name__ == '__main__':
    unittest.main()
