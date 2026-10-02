"""Black-box tests of the shell scripts; no agents or credentials required."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
BASH = shutil.which('bash')

# This executable records argv rather than executing agents, editors, or tmux.
STUB = '''import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['TEST_LOG'], 'a') as log:
    log.write(json.dumps([name, args]) + '\\n')
if name == 'tmux':
    while args and args[0] in ('-L', '-f'):
        args = args[2:]
    command = args[0]
    if command == 'has-session':
        sessions = json.loads(os.environ.get('TEST_SESSIONS', '{}'))
        sys.exit(0 if args[-1].lstrip('=') in sessions else 1)
    if command == 'show-options':
        if '-t' not in args:
            print(os.environ.get('TEST_GLOBAL', ''))
            sys.exit(0)
        sessions = json.loads(os.environ.get('TEST_SESSIONS', '{}'))
        target = args[args.index('-t') + 1].lstrip('=').rstrip(':')
        print(sessions.get(target, os.environ.get('TEST_ROOT', '')))
    if command == 'list-windows':
        print(os.environ.get('TEST_INDEX' if '-f' in args else 'TEST_WINDOWS', ''))
    if command == 'display-message':
        print(os.environ.get('TEST_SESSION', ''))
'''


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='xcl-tests-')
        self.addCleanup(self.tmp.cleanup)
        # Resolved: on macOS the temp dir is under /var, a symlink to
        # /private/var, and the scripts report physical paths.
        self.root = Path(self.tmp.name).resolve()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.project = self.root / 'my project'
        self.project.mkdir()
        self.log = self.root / 'calls.jsonl'
        self.conf = self.root / 'tmux.conf'
        self.conf.touch()
        self.themes = self.root / 'themes'
        shutil.copytree(REPO / 'themes', self.themes)
        self.saved = self.root / 'theme'
        # Restrict PATH so installed agents/editors cannot affect the tests.
        for tool in ('bash', 'basename', 'tr', 'cut', 'sha256sum', 'shasum',
                     'cksum', 'grep', 'head', 'sed', 'git'):
            binary = shutil.which(tool)
            if binary:
                (self.bin / tool).symlink_to(binary)
        self.env = {
            'PATH': str(self.bin), 'HOME': str(self.root), 'SHELL': BASH,
            'LC_ALL': 'C', 'XCL_CONF': str(self.conf), 'XCL_SOCKET': 'test-xcl',
            'TEST_LOG': str(self.log), 'TEST_ROOT': str(self.project),
            'GIT_CONFIG_NOSYSTEM': '1',
        }
        self.stub('tmux')
        self.stub('xcl-tab')
        # The real one: xcl asks it which theme file to use.
        (self.bin / 'xcl-theme').symlink_to(REPO / 'bin' / 'xcl-theme')

    def stub(self, name):
        path = self.bin / name
        path.write_text(f'#!{sys.executable}\n' + STUB)
        path.chmod(0o755)
        return path

    def run_script(self, script, *args, code=0, **env):
        self.log.unlink(missing_ok=True)
        result = subprocess.run(
            [BASH, str(REPO / 'bin' / script), *map(str, args)],
            cwd=self.project, env=self.env | env, text=True,
            capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, code, result.stderr)
        return result

    def calls(self, name):
        records = self.log.read_text().splitlines() if self.log.exists() else []
        return [args for tool, args in map(json.loads, records) if tool == name]

    def tm_calls(self, command):
        return [args[args.index(command):] for args in self.calls('tmux')
                if command in args]

    def test_startup_prefers_claude_and_falls_back_to_codex(self):
        for available in (False, True):
            if available:
                self.stub('claude')
            for flags, suffix in (([], ''), (['-r'], '-resume'), (['--resume'], '-resume')):
                with self.subTest(available=available, flags=flags):
                    self.run_script('xcl', *flags, self.project)
                    agent = ('claude' if available else 'codex') + suffix
                    self.assertEqual(self.calls('xcl-tab'), [[agent, str(self.project), 'my project']])
                    self.assertTrue(self.tm_calls('attach-session'))
                    self.assertTrue(self.tm_calls('kill-window'))

    def test_startup_respects_binary_override(self):
        self.stub('claude')
        self.run_script('xcl', '-r', XCL_CLAUDE_BIN='/nonexistent/claude')
        self.assertEqual(self.calls('xcl-tab')[0][0], 'codex-resume')
        custom = self.stub('custom-claude')
        self.run_script('xcl', XCL_CLAUDE_BIN=str(custom))
        self.assertEqual(self.calls('xcl-tab')[0][0], 'claude')

    def test_existing_workspace_is_reused_even_with_resume(self):
        self.run_script('xcl', '-r', TEST_SESSIONS=json.dumps({'my project': str(self.project)}))
        self.assertFalse(self.calls('xcl-tab'))
        self.assertFalse(self.tm_calls('new-session'))
        self.assertTrue(self.tm_calls('attach-session'))

    def test_repo_subdirectory_uses_repo_session(self):
        subprocess.run([str(self.bin / 'git'), 'init', '-q', str(self.project)],
                       env=self.env, check=True)
        child = self.project / 'nested'
        child.mkdir()
        self.run_script('xcl', '-r', child)
        self.assertEqual(self.calls('xcl-tab')[0][1:], [str(child), 'my project'])
        self.assertEqual(self.tm_calls('new-session')[0][-2], str(self.project))

    def test_same_basename_is_disambiguated(self):
        self.run_script('xcl', TEST_SESSIONS=json.dumps({'my project': '/elsewhere'}))
        digest = hashlib.sha256(str(self.project).encode()).hexdigest()[:6]
        self.assertEqual(self.calls('xcl-tab')[0][2], 'my project-' + digest)

    def test_inside_tmux_switches_only_on_same_server(self):
        self.run_script('xcl', TMUX='/tmp/test-xcl,123,0')
        self.assertTrue(self.tm_calls('switch-client'))
        self.assertFalse(self.tm_calls('attach-session'))
        result = self.run_script('xcl', TMUX='/tmp/other,123,0')
        self.assertFalse(self.tm_calls('switch-client'))
        self.assertFalse(self.tm_calls('attach-session'))
        self.assertIn('already inside another tmux', result.stderr)

    def test_cli_validation_and_help(self):
        for args in (['-x'], ['one', 'two']):
            with self.subTest(args=args):
                self.run_script('xcl', *args, code=2)
                self.assertFalse(self.calls('tmux'))
        self.assertIn('resume picker', self.run_script('xcl', '--help').stdout)
        self.run_script('xcl', '-r', '--', self.project)
        self.assertEqual(self.calls('xcl-tab')[0][0], 'codex-resume')
        self.run_script('xcl', '/nonexistent/directory', code=1)

    def test_tab_commands_and_resume_argument_order(self):
        for agent in ('claude', 'claude-resume', 'codex', 'codex-resume'):
            with self.subTest(agent=agent):
                self.run_script('xcl-tab', agent, self.project, 'workspace')
                call = self.tm_calls('new-window')[0]
                argv = shlex.split(call[call.index('-n') + 2])
                if agent.startswith('claude'):
                    expected = ['exec', 'claude', '--dangerously-skip-permissions']
                    if agent.endswith('resume'):
                        expected += ['--resume']
                else:
                    expected = ['exec', 'codex']
                    if agent.endswith('resume'):
                        expected += ['resume']
                    expected += ['--dangerously-bypass-approvals-and-sandbox',
                                 '-c', 'tui.terminal_title=["thread"]', '-c', 'tui.animations=false']
                self.assertEqual(argv, expected)

    def test_agent_tabs_track_titles_and_other_tabs_do_not(self):
        placeholders = {
            'claude': '^(✳ |◐ |◑ )?(Claude Code|claude · resume)$',
            'codex': '^[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$',
        }
        for agent in ('claude', 'claude-resume', 'codex', 'codex-resume'):
            with self.subTest(agent=agent):
                self.run_script('xcl-tab', agent, self.project, 'workspace')
                call = self.tm_calls('new-window')[0]
                # One tmux invocation: new-window, then the window options,
                # separated by ';' so they target the new window.
                label = call[call.index('-n') + 1]
                self.assertEqual(call[call.index('@xcl_label') + 1], label)
                self.assertEqual(call[call.index('@xcl_placeholder') + 1],
                                 placeholders[agent.split('-')[0]])
                self.assertEqual(call[call.index('automatic-rename') + 1], 'on')
                fmt = call[call.index('automatic-rename-format') + 1]
                self.assertIn('@xcl_placeholder', fmt)
                self.assertIn('@xcl_label', fmt)
        for agent in ('shell', 'lazygit'):
            with self.subTest(agent=agent):
                self.run_script('xcl-tab', agent, self.project, 'workspace')
                call = self.tm_calls('new-window')[0]
                self.assertNotIn('automatic-rename', call)

    def test_empty_args_override_and_custom_binary(self):
        self.run_script('xcl-tab', 'codex', self.project, 'workspace',
                        XCL_CODEX_BIN='custom-codex', XCL_CODEX_ARGS='')
        call = self.tm_calls('new-window')[0]
        argv = shlex.split(call[call.index('-n') + 2])
        self.assertEqual(argv[:2], ['exec', 'custom-codex'])
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', argv)

    def test_labels_include_subdirectory_and_avoid_duplicates(self):
        child = self.project / 'staging'
        child.mkdir()
        self.run_script('xcl-tab', 'claude', child, 'workspace',
                        TEST_WINDOWS='claude:staging\nclaude:staging 2')
        call = self.tm_calls('new-window')[0]
        self.assertEqual(call[call.index('-n') + 1], 'claude:staging 3')

    def test_symlinked_repo_root_is_not_tagged_as_subdirectory(self):
        link = self.root / 'link'
        link.symlink_to(self.project)
        self.run_script('xcl-tab', 'claude', link, 'workspace')
        call = self.tm_calls('new-window')[0]
        self.assertEqual(call[call.index('-n') + 1], 'claude')
        self.assertEqual(call[call.index('-c') + 1], str(self.project))
        self.run_script('xcl', link)
        self.assertEqual(self.calls('xcl-tab')[0][1:], [str(self.project), 'my project'])

    def test_shell_tab_uses_tmux_default_shell(self):
        self.run_script('xcl-tab', 'shell', self.project, 'workspace')
        self.assertEqual(self.tm_calls('new-window'),
                         [['new-window', '-t', '=workspace:', '-c', str(self.project), '-n', 'sh']])

    def test_lazygit_reuses_existing_window_or_opens_at_repo_root(self):
        child = self.project / 'nested'
        child.mkdir()
        self.run_script('xcl-tab', 'lazygit', child, 'workspace', TEST_INDEX='3')
        self.assertEqual(self.tm_calls('select-window'), [['select-window', '-t', '=workspace:0']])
        self.assertEqual(self.tm_calls('move-window')[0],
                         ['move-window', '-s', '=workspace:3', '-t', '=workspace:0'])
        self.assertFalse(self.tm_calls('new-window'))
        self.run_script('xcl-tab', 'lazygit', child, 'workspace')
        call = self.tm_calls('new-window')[0]
        self.assertEqual(call[call.index('-c') + 1], str(self.project))
        self.assertEqual(call[call.index('-n') + 1], 'lazygit')
        self.assertEqual(call[call.index('-t') + 1], '=workspace:0')

    def test_tab_rejects_unknown_agent_and_missing_session(self):
        self.run_script('xcl-tab', 'unknown', self.project, 'workspace', code=2)
        self.assertFalse(self.tm_calls('new-window'))
        self.run_script('xcl-tab', 'shell', self.project, code=1)
        self.assertFalse(self.tm_calls('new-window'))

    def applied_theme(self):
        """The theme file xcl or xcl-theme pointed the server at."""
        calls = self.tm_calls('set-option')
        call = next(c for c in calls if '@xcl_theme_file' in c)
        # One invocation, so the reload sees the new option.
        self.assertEqual(call[call.index('@xcl_theme_file') + 2:],
                         [';', 'source-file', str(self.conf)])
        return Path(call[call.index('@xcl_theme_file') + 1]).stem

    def test_bundled_themes_set_the_whole_palette(self):
        conf = (REPO / 'tmux.conf').read_text()
        palette = set(re.findall(r'^set -g (@xcl_\w+) ', conf, re.M))
        self.assertIn('@xcl_bar_bg', palette)
        for theme in sorted((REPO / 'themes').glob('*.conf')):
            with self.subTest(theme=theme.stem):
                defined = re.findall(r'^set -g (@xcl_\w+) +"#[0-9a-f]{6}"$',
                                     theme.read_text(), re.M)
                self.assertEqual(sorted(defined), sorted(palette))
        installed = re.search(r'^THEMES="(.*)"$', (REPO / 'install.sh').read_text(), re.M)
        self.assertEqual(sorted(installed[1].split()),
                         sorted(t.stem for t in (REPO / 'themes').glob('*.conf')))

    def test_startup_applies_theme_for_new_and_existing_sessions(self):
        for sessions in ({}, {'my project': str(self.project)}):
            with self.subTest(existing=bool(sessions)):
                self.run_script('xcl', TEST_SESSIONS=json.dumps(sessions))
                self.assertEqual(self.applied_theme(), 'mocha')

    def test_theme_precedence(self):
        self.run_script('xcl', XCL_THEME='latte')
        self.assertEqual(self.applied_theme(), 'latte')
        self.saved.write_text('gruvbox\n')
        self.run_script('xcl')
        self.assertEqual(self.applied_theme(), 'gruvbox')
        self.run_script('xcl', XCL_THEME='latte')
        self.assertEqual(self.applied_theme(), 'latte')
        # A saved theme that has since been deleted falls back, with a warning.
        self.saved.write_text('deleted\n')
        result = self.run_script('xcl')
        self.assertEqual(self.applied_theme(), 'mocha')
        self.assertIn("saved theme 'deleted' not found", result.stderr)

    def test_unknown_theme_fails_before_any_session(self):
        for name in ('nope', '../themes/mocha'):
            with self.subTest(name=name):
                result = self.run_script('xcl', code=1, XCL_THEME=name)
                self.assertIn('no theme', result.stderr)
                self.assertFalse(self.calls('tmux'))

    def test_theme_set_switches_server_and_remembers(self):
        result = self.run_script('xcl-theme', 'set', 'latte')
        self.assertEqual(self.applied_theme(), 'latte')
        self.assertEqual(self.saved.read_text(), 'latte\n')
        self.assertNotIn('applies the next time', result.stdout)
        self.assertEqual(self.run_script('xcl-theme').stdout,
                         '  gruvbox\n* latte\n  mocha\n')
        self.assertEqual(self.run_script('xcl-theme', 'path').stdout.strip(),
                         str(self.themes / 'latte.conf'))
        for args in (['set', 'nope'], ['set', '../x']):
            with self.subTest(args=args):
                self.run_script('xcl-theme', *args, code=1)
                self.assertFalse(self.calls('tmux'))
                self.assertEqual(self.saved.read_text(), 'latte\n')
        self.run_script('xcl-theme', 'set', code=2)
        self.run_script('xcl-theme', 'bogus', code=2)

    def test_theme_set_without_server_still_remembers(self):
        (self.bin / 'tmux').unlink()
        (self.bin / 'tmux').symlink_to(shutil.which('false'))
        result = self.run_script('xcl-theme', 'set', 'gruvbox')
        self.assertIn('applies the next time', result.stdout)
        self.assertEqual(self.saved.read_text(), 'gruvbox\n')

    def test_theme_menu_lists_themes_and_marks_the_shown_one(self):
        (self.themes / 'bad name.conf').touch()
        self.run_script('xcl-theme', 'menu', '/dev/ttys001',
                        TEST_GLOBAL=str(self.themes / 'latte.conf'))
        call = self.tm_calls('display-menu')[0]
        self.assertEqual(call[call.index('-c') + 1], '/dev/ttys001')
        items = call[call.index('-c') + 2:]
        self.assertEqual([items[i:i + 3] for i in range(0, len(items), 3)], [
            ['  gruvbox', '1', "run-shell -b 'xcl-theme set gruvbox'"],
            ['* latte', '2', "run-shell -b 'xcl-theme set latte'"],
            ['  mocha', '3', "run-shell -b 'xcl-theme set mocha'"],
        ])

    def test_editor_override_preserves_arguments_and_directory(self):
        self.stub('my-editor')
        self.run_script('xcl-open', self.project, XCL_EDITOR='my-editor --wait')
        self.assertEqual(self.calls('my-editor'), [['--wait', str(self.project)]])

    def test_editor_preference_and_fallback(self):
        self.stub('my-editor')
        self.run_script('xcl-open', VISUAL='missing-editor', EDITOR='my-editor --wait')
        self.assertEqual(self.calls('my-editor'), [['--wait', str(self.project)]])
        self.stub('zeditor')
        self.stub('zed')
        self.run_script('xcl-open', EDITOR='my-editor')
        self.assertEqual(self.calls('zed'), [[str(self.project)]])
        self.assertFalse(self.calls('my-editor'))

    def test_missing_editor_reports_error(self):
        result = self.run_script('xcl-open', code=1)
        self.assertIn('no editor found', result.stderr)


if __name__ == '__main__':
    unittest.main()
