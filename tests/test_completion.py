import os
from pathlib import Path
import shutil
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
# first TAB press. The real _description calls _setup, which puts
# compstate[list] back to the value saved by _main_complete whenever no list-*
# style is set; the stub does the same so a forced listing written too early
# does not look like it worked.
DRIVER = '''
emulate -L zsh
typeset -A compstate
compstate[insert]=${INSERT_BEFORE}
compstate[list]=ambiguous
_saved_list=ambiguous
_menu_style=(${=MENU_STYLE})
compdef() { : }
_description() { eval "$3=(-X \\"$4\\")"; compstate[list]="$_saved_list" }
compadd() { print -r -- "compadd $*" }
source ${COMPLETION}
words=(vmc ${=PREV} "$CUR"); CURRENT=${#words}
_vmc || exit 1
print -r -- "insert=<${compstate[insert]}> list=<${compstate[list]}>"
'''


@unittest.skipUnless(shutil.which('zsh'), 'zsh is not installed')
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

    def complete(self, cur, insert_before='automenu-unambiguous', prev='', menu=''):
        env = dict(self.env, CUR=cur, PREV=prev, MENU_STYLE=menu, INSERT_BEFORE=insert_before)
        result = subprocess.run(['zsh', '-f', '-c', DRIVER], text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        return result.stdout

    def write_history(self, *dirs):
        hist = Path(self.env['CMUX_REMOTE_CACHE'])/'history-ubuntu'
        hist.parent.mkdir(parents=True, exist_ok=True)
        hist.write_text(''.join(d+'\n' for d in dirs))

    def test_empty_word_lists_instead_of_inserting_common_prefix(self):
        self.write_history('/remote/home/a', '/tmp')
        out = self.complete('')
        self.assertIn('compadd -X recent dirs on ubuntu -S / -- /tmp /remote/home/a', out)
        self.assertIn('compadd -X directories on ubuntu -S / -- /remote/home/a /remote/home/b', out)
        # The listing is forced after the last _description (whose _setup
        # resets compstate[list]) and the prefix insertion is dropped because
        # it would collapse to "/".
        self.assertIn('insert=<> list=<list force>', out)

    def test_menu_style_keeps_insert_untouched(self):
        self.write_history('/remote/home/a', '/tmp')
        out = self.complete('', menu='yes')
        self.assertIn('insert=<automenu-unambiguous> list=<list force>', out)

    def test_without_recents_the_home_prefix_is_still_inserted(self):
        out = self.complete('')
        self.assertIn('compadd -X directories on ubuntu -S / -- /remote/home/a /remote/home/b', out)
        self.assertIn('insert=<automenu-unambiguous> list=<ambiguous>', out)

    def test_partial_path_keeps_default_insertion(self):
        out = self.complete('/remote/home/a')
        self.assertIn('insert=<automenu-unambiguous>', out)
        self.assertIn('-- /remote/home/a /remote/home/b', out)

    def test_root_listing_has_single_slash(self):
        out = self.complete('/')
        self.assertIn('ls -1d /*/ 2>/dev/null', self.log.read_text())
        self.assertIn('-- /bin /usr', out)

    def test_tilde_base_lists_the_remote_home(self):
        out = self.complete('~/')
        self.assertIn('ls -1d /remote/home/*/ 2>/dev/null', self.log.read_text())
        self.assertIn('compadd -X directories on ubuntu -Q -S / -- ~/a ~/b', out)

    def test_resume_takes_a_uuid_not_a_directory(self):
        out = self.complete('', prev='--resume')
        self.assertNotIn('compadd', out)

    def test_target_with_equals_completes_target_names(self):
        out = self.complete('--target=')
        self.assertIn('compadd -P --target= -- ubuntu', out)


if __name__ == '__main__':
    unittest.main()
