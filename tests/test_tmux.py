"""Integration coverage for title handling using an isolated tmux server."""
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


if __name__ == '__main__':
    unittest.main()
