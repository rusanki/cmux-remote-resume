import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
COMPLETION = ROOT/'completion'/'vmc-completion.zsh'

# Fake cmux-remote: "home" prints a fixed remote home, "exec" logs the remote
# command and prints a listing for it.
FAKE_CMUX_REMOTE = '''#!/bin/sh
case "$1" in
  home) printf '%s' /remote/home ;;
  exec) printf '%s\\n' "$3" >> "$EXEC_LOG"; printf '%s\\n' "$3" | grep -q '/remote/home' && printf '%s\\n' /remote/home/a/ /remote/home/b/ || printf '%s\\n' /bin/ /usr/ ;;
  targets) printf 'ubuntu\\n' ;;
esac
'''

# Drive _vmc outside a real completion widget: stub compadd/_description and
# declare compstate as a plain associative array seeded the way zsh does for a
# first TAB press.
DRIVER = '''
emulate -L zsh
typeset -A compstate
compstate[insert]=${INSERT_BEFORE}
compdef() { : }
_description() { eval "$3=(-X \\"$4\\")" }
compadd() { print -r -- "compadd $*" }
source ${COMPLETION}
words=(vmc ${CUR}); CURRENT=2
_vmc
print -r -- "insert=<${compstate[insert]}> list=<${compstate[list]}>"
'''


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)/'remote-home'
        (home/'bin').mkdir(parents=True)
        fake = home/'bin'/'cmux-remote'
        fake.write_text(FAKE_CMUX_REMOTE)
        fake.chmod(0o755)
        self.log = Path(self.tmp.name)/'exec.log'
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('CMUX_')}
        self.env.update(CMUX_REMOTE_HOME=str(home), CMUX_REMOTE_CACHE=str(Path(self.tmp.name)/'cache'),
                        EXEC_LOG=str(self.log), COMPLETION=str(COMPLETION))

    def complete(self, cur, insert_before='automenu-unambiguous'):
        env = dict(self.env, CUR=cur, INSERT_BEFORE=insert_before)
        result = subprocess.run(['zsh', '-f', '-c', DRIVER], text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_empty_word_lists_instead_of_inserting_common_prefix(self):
        hist = Path(self.env['CMUX_REMOTE_CACHE'])/'history-ubuntu'
        hist.parent.mkdir(parents=True)
        hist.write_text('/remote/home/a\n/tmp\n')
        out = self.complete('')
        self.assertIn('compadd -X recent dirs on ubuntu -Q -S / -- /tmp /remote/home/a', out)
        self.assertIn('compadd -X directories on ubuntu -Q -S / -- /remote/home/a /remote/home/b', out)
        self.assertIn('insert=<> list=<list force>', out)

    def test_partial_path_keeps_default_insertion(self):
        out = self.complete('/remote/home/a')
        self.assertIn('insert=<automenu-unambiguous>', out)
        self.assertIn('-- /remote/home/a /remote/home/b', out)

    def test_root_listing_has_single_slash(self):
        out = self.complete('/')
        self.assertIn('ls -1d /*/ 2>/dev/null', self.log.read_text())
        self.assertIn('-- /bin /usr', out)


if __name__ == '__main__':
    unittest.main()
