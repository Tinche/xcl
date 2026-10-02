"""Integration coverage using an isolated tmux server."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]
TMUX = shutil.which('tmux')


@unittest.skipUnless(TMUX, 'tmux is required for integration tests')
class TmuxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='xcl-tmux-')
        self.addCleanup(self.tmp.cleanup)
        # Resolved, as in test_scripts: the scripts report physical paths.
        self.root = Path(self.tmp.name).resolve()
        self.socket = self.root.name
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith('XCL_') and key not in ('TMUX', 'TMUX_TMPDIR')}
        self.env.update(HOME=str(self.root), XDG_CONFIG_HOME=str(self.root),
                        TERM='xterm-256color', SHELL=shutil.which('bash'),
                        XCL_SOCKET=self.socket)
        self.addCleanup(self.stop_server)
        self.tm('-f', str(REPO / 'tmux.conf'), 'new-session', '-d', '-s', 'test', '-n', 'original')
        self.tm('set-option', '-t', '=test:', '@xcl_root', str(self.root))
        self.original = self.tm('display-message', '-p', '-t', '=test:', '#{window_id}')
        # Agent stand-in: each input line becomes a terminal title via OSC 0.
        fake = self.root / 'fake-agent'
        fake.write_text('#!/bin/sh\nwhile IFS= read -r title; do\n'
                        '  printf "\\033]0;%s\\007" "$title"\ndone\n')
        fake.chmod(0o755)
        self.env['XCL_CLAUDE_BIN'] = str(fake)
        self.env['XCL_CODEX_BIN'] = str(fake)
        self.env['XCL_LAZYGIT_BIN'] = str(fake)

    def open_tab(self, agent):
        result = subprocess.run([str(REPO / 'bin/xcl-tab'), agent, str(self.root), 'test'],
                                env=self.env, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.tm('display-message', '-p', '-t', '=test:', '#{window_id}')

    def windows(self):
        return self.tm('list-windows', '-t', '=test', '-F', '#{window_index}:#{window_name}')

    def wait_windows(self, expected):
        deadline = time.monotonic() + 5
        while self.windows() != expected:
            self.assertLess(time.monotonic(), deadline, 'Window numbering was not restored')
            time.sleep(0.05)

    def test_git_stays_leftmost_without_consuming_number_keys(self):
        shell = self.open_tab('shell')
        git = self.open_tab('lazygit')
        self.assertEqual(self.windows(), '0:lazygit\n1:original\n2:sh')
        self.assertEqual(self.tm('display-message', '-p', '-t', git,
                                 '#{E:window-status-format}'), 'lazygit')
        current = self.tm('display-message', '-p', '-t', git,
                          '#{E:window-status-current-format}')
        self.assertTrue(current.endswith(' lazygit'), current)
        self.tm('select-window', '-t', '=test:1')
        self.assertEqual(self.open_tab('lazygit'), git)
        self.assertEqual(self.windows(), '0:lazygit\n1:original\n2:sh')
        self.tm('kill-window', '-t', self.original)
        self.assertEqual(self.windows(), '0:lazygit\n1:sh')
        self.tm('source-file', str(REPO / 'tmux.conf'))
        self.open_tab('shell')
        self.assertEqual(self.windows(), '0:lazygit\n1:sh\n2:sh 2')
        self.assertEqual(self.tm('display-message', '-p', '-t', shell,
                                 '#{E:window-status-format}'), '1 sh')
        self.tm('kill-window', '-t', git)
        self.wait_windows('1:sh\n2:sh 2')
        self.open_tab('lazygit')
        self.assertEqual(self.windows(), '0:lazygit\n1:sh\n2:sh 2')

    def test_alt_g_binding_replaces_prefix_g_on_reload(self):
        self.tm('bind-key', 'g', 'display-message', 'old binding')
        self.tm('source-file', str(REPO / 'tmux.conf'))
        root = self.tm('list-keys', '-T', 'root').splitlines()
        binding = next(line for line in root if line.split()[3] == 'M-g')
        self.assertIn('xcl-tab lazygit', binding)
        prefix = self.tm('list-keys', '-T', 'prefix').splitlines()
        self.assertFalse(any(line.split()[3] in ('g', '0') for line in prefix))

    def test_existing_git_tab_is_moved_and_other_sessions_are_unchanged(self):
        self.tm('new-window', '-t', '=test:', '-n', 'git')
        git = self.tm('display-message', '-p', '-t', '=test:', '#{window_id}')
        self.open_tab('shell')
        self.tm('new-session', '-d', '-s', 'other', '-n', 'other')
        self.assertEqual(self.open_tab('lazygit'), git)
        self.assertEqual(self.windows(), '0:lazygit\n1:original\n2:sh')
        self.assertEqual(self.tm('show-options', '-Av', '-t', '=other:', 'base-index'), '1')
        self.tm('select-window', '-t', '=other:1')
        self.tm('kill-window', '-t', git)
        self.wait_windows('1:original\n2:sh')
        self.assertEqual(self.tm('list-windows', '-t', '=other', '-F', '#{window_index}'), '1')

    def test_git_exiting_restores_numbering_and_git_can_be_the_only_tab(self):
        git = self.open_tab('lazygit')
        self.tm('kill-window', '-t', self.original)
        self.assertEqual(self.windows(), '0:lazygit')
        self.open_tab('shell')
        self.assertEqual(self.windows(), '0:lazygit\n1:sh')
        # Exit the stand-in process naturally, as when quitting lazygit.
        self.tm('send-keys', '-t', git, 'C-d')
        self.wait_windows('1:sh')

    def tm(self, *args):
        result = subprocess.run([TMUX, '-L', self.socket, *args], env=self.env,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def stop_server(self):
        subprocess.run([TMUX, '-L', self.socket, 'kill-server'], env=self.env,
                       capture_output=True, timeout=10)

    def wait_name(self, target, expected):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            actual = self.tm('display-message', '-p', '-t', target, '#{window_name}')
            if actual == expected:
                return
            time.sleep(0.05)
        self.fail(f'Expected window name {expected!r}, got {actual!r}')

    # Titles as each agent publishes them, and the tab name they should
    # produce. FALLBACK means the tab keeps its original label.
    FALLBACK = object()
    CLAUDE_TITLES = (
        ('✳ Claude Code', FALLBACK),              # fresh session, nothing asked yet
        ('claude · resume', FALLBACK),            # the resume picker
        ('✳ Fix startup fallback', 'Fix startup fallback'),
        ('◐ Fix startup fallback', 'Fix startup fallback'),  # spinner glyphs
        ('◑ Fix startup fallback', 'Fix startup fallback'),
        ('✳ ' + 'x' * 60, 'x' * 40 + '…'),
        ('', FALLBACK),
        ('◑ Claude Code', FALLBACK),
        ('New conversation title', 'New conversation title'),  # no glyph
    )
    CODEX_TITLES = (
        ('01995eca-1234-4567-89ab-0123456789ab', FALLBACK),
        ('Fix startup fallback', 'Fix startup fallback'),
        ('ABCDEF01-2345-6789-ABCD-0123456789AB', FALLBACK),
        ('x' * 60, 'x' * 40 + '…'),
        ('', FALLBACK),
        ('New conversation title', 'New conversation title'),
    )

    def test_titles_follow_conversation_with_placeholder_and_empty_fallback(self):
        for agent, fallback, titles in (
            ('claude', 'claude', self.CLAUDE_TITLES),
            ('claude-resume', 'claude↻', self.CLAUDE_TITLES),
            ('codex', 'cx', self.CODEX_TITLES),
            ('codex-resume', 'cx↻', self.CODEX_TITLES),
        ):
            with self.subTest(agent=agent):
                result = subprocess.run([str(REPO / 'bin/xcl-tab'), agent, str(self.root), 'test'],
                                        env=self.env, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                pane = self.tm('display-message', '-p', '-t', '=test:', '#{pane_id}')
                self.wait_name(pane, fallback)
                for title, expected in titles:
                    if expected is self.FALLBACK:
                        expected = fallback
                    if title:
                        self.tm('send-keys', '-t', pane, '-l', title)
                    self.tm('send-keys', '-t', pane, 'Enter')
                    # Wait for the OSC to be received, even if the expected
                    # window name is unchanged (e.g. placeholder -> fallback).
                    deadline = time.monotonic() + 5
                    while self.tm('display-message', '-p', '-t', pane, '#{pane_title}') != title:
                        self.assertLess(time.monotonic(), deadline, 'OSC title was not received')
                        time.sleep(0.05)
                    self.wait_name(pane, expected)
                self.assertEqual(self.tm('display-message', '-p', '-t', self.original,
                                         '#{window_name}'), 'original')
                self.assertEqual(self.tm('show-options', '-wv', '-t', self.original,
                                         'automatic-rename'), 'off')

    def test_theme_switch_recolors_running_server(self):
        # XDG_CONFIG_HOME is the temp dir, so this is xcl-theme's default config.
        conf_dir = self.root / 'xcl'
        conf_dir.mkdir()
        shutil.copy(REPO / 'tmux.conf', conf_dir)
        shutil.copytree(REPO / 'themes', conf_dir / 'themes')

        def theme(name):
            result = subprocess.run([str(REPO / 'bin/xcl-theme'), 'set', name],
                                    env=self.env, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

        def bar():
            return (self.tm('show-options', '-gv', 'status-style'),
                    self.tm('display-message', '-p', '#{E:status-left}'))

        # With no theme file set, the config loads cleanly with its defaults.
        self.tm('source-file', str(conf_dir / 'tmux.conf'))
        mocha = ('bg=#181825,fg=#a6adc8', '#[bg=#585b70,fg=#cdd6f4,bold] test #[default]')
        self.assertEqual(bar(), mocha)
        theme('latte')
        self.assertEqual(bar(), ('bg=#e6e9ef,fg=#6c6f85',
                                 '#[bg=#acb0be,fg=#4c4f69,bold] test #[default]'))
        theme('mocha')
        self.assertEqual(bar(), mocha)
        # An option a theme leaves out reverts to the default, rather than
        # keeping the previous theme's value.
        (conf_dir / 'themes/partial.conf').write_text('set -g @xcl_bar_bg "#000000"\n')
        theme('latte')
        theme('partial')
        self.assertEqual(bar(), ('bg=#000000,fg=#a6adc8', mocha[1]))


if __name__ == '__main__':
    unittest.main()
