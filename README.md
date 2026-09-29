# xcl

A tabbed terminal workspace for coding agents, built on tmux.

`xcl` gives each git repo its own tmux session, and binds keys that open
Claude Code or Codex tabs inside it. One repo, one workspace, as many agent
tabs as you want — and a different repo gets a clean slate.

It runs on a dedicated tmux socket with its own config, so your personal
tmux setup is untouched: your own `tmux` keeps stock defaults and never sees
these sessions, options, or keybinds.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/Tinche/xcl/main/install.sh | sh
```

Installs `xcl`, `xcl-tab`, `xcl-open` and `xcl-theme` to `~/.local/bin`,
and `tmux.conf` and the bundled themes to `~/.config/xcl/`. Re-run it to upgrade. From a clone, `./install.sh` installs
the local files instead of downloading.

## Use

```sh
cd ~/code/my-repo
xcl
```

That creates the workspace for `my-repo` with one Claude tab (or tries Codex
if Claude is not available) and attaches to it. The availability check respects
`XCL_CLAUDE_BIN`. Run `xcl` again — from anywhere in the repo, including subdirectories —
to come back to it. `xcl <dir>` targets another repo.

Use `xcl -r` (or `xcl -r <dir>`) to start a new workspace with the agent's
resume picker instead of a fresh conversation. This uses Claude when
available, otherwise Codex. If the workspace already exists, xcl attaches
to it as usual. `--resume` also works.

## Keys

`prefix` is tmux's, `Ctrl+b` unless you change it.

| key | action |
| --- | --- |
| `prefix c` | new Claude tab |
| `prefix r` | new Claude tab, resume a past session |
| `prefix C` | new Codex tab |
| `prefix R` | new Codex tab, resume a past session |
| `prefix S` | new shell tab |
| `prefix g` | lazygit — one per repo, at the repo root |
| `prefix z` | open the repo in your editor |
| `prefix s` | switch repo |
| `prefix T` | switch theme |
| `prefix M-r` | reload the config |
| `Alt-1`…`Alt-9` | jump to tab |
| `Alt-←` / `Alt-→` | previous / next tab |
| `Alt-t` / `Alt-w` | new tab / close tab |

Tabs are named after what they run: `claude`, `claude 2`, `claude↻` for a
resumed one, `cx` for Codex, `sh`, `git`. A tab opened outside the repo root
is tagged with its subdirectory, like `claude:staging`.

Agent tabs keep that label until the conversation has a title, then follow
it. Long titles are shortened to 40 characters plus an ellipsis. This also
applies to resumed conversations, once one is picked.

Claude Code publishes its title by default: the name given with `/rename` or
`--name`, otherwise a generated summary. xcl strips the `✳` glyph Claude puts
in front of it. Set `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` to keep the plain
labels instead.

Codex tabs keep the label while the title is empty or a session UUID. This
requires a Codex version that supports `tui.terminal_title`. Codex terminal
animations are disabled in xcl to reduce flickering in tmux.

The `Alt` keys need your terminal to send Option/Alt as Alt. In Ghostty on
macOS that is `macos-option-as-alt = true`. The `prefix` binds always work.

## Themes

The tab bar's colors come from a theme. Three are bundled: `mocha` (the
default), `latte` for light terminals, and `gruvbox`. `prefix T` pops up a
picker; the pick applies to every workspace at once and is remembered in
`~/.config/xcl/theme`. From a shell, `xcl-theme` lists the themes and
`xcl-theme set <name>` switches. `XCL_THEME=<name> xcl` uses a theme for
that run without remembering it.

A theme is a file in `~/.config/xcl/themes/` that sets six palette options:

```tmux
set -g @xcl_bar_bg    "#181825"   # tab bar background
set -g @xcl_bar_fg    "#a6adc8"   # inactive tabs
set -g @xcl_muted     "#6c7086"   # the counts on the right
set -g @xcl_accent_bg "#585b70"   # repo name
set -g @xcl_accent_fg "#cdd6f4"   # repo name and current tab text
set -g @xcl_tab_bg    "#313244"   # current tab
```

To make your own, copy a bundled theme under a new name. Upgrades
overwrite the bundled files but leave others alone. An option a theme leaves
out keeps its Mocha default.

## Configure

Set these before launching `xcl`; the tmux server inherits them.

| variable | default | what |
| --- | --- | --- |
| `XCL_CLAUDE_ARGS` | `--dangerously-skip-permissions` | args for Claude tabs |
| `XCL_CODEX_ARGS` | `--dangerously-bypass-approvals-and-sandbox` | args for Codex tabs |
| `XCL_LAZYGIT_ARGS` | `branch` | args for lazygit |
| `XCL_EDITOR` | `zed` | what `prefix z` runs |
| `XCL_THEME` | the last pick, else `mocha` | tab bar theme |
| `XCL_CLAUDE_BIN` `XCL_CODEX_BIN` `XCL_LAZYGIT_BIN` | the tool's name | binary paths |
| `XCL_SOCKET` | `xcl` | tmux socket name |
| `XCL_CONF` | `~/.config/xcl/tmux.conf` | config path; `themes/` sits beside it |

The defaults assume a workspace you have already decided to trust. Codex
runs without approval prompts or sandbox restrictions. Set any
of the `_ARGS` variables to the empty string to get the tool's own defaults
back, including its permission prompts.

## Requirements

- `tmux` 3.2 or newer
- `git`, for repo detection
- `claude`, `codex`, `lazygit`, an editor — each optional; without one, its
  key just does nothing

On Linux, Zed's binary is sometimes `zeditor`: set `XCL_EDITOR=zeditor`.
If tmux complains about missing `tmux-256color` terminfo, install
`ncurses-term` or change `default-terminal` in the config.

## Tests

Run the suite with Python 3.9 or newer; no Python packages are required:

```sh
python3 -m unittest discover -s tests -v
```

The script tests use isolated temporary directories and mocked tools to
cover startup, agent fallback, resume mode, tab commands and labels,
editor selection, and theme selection. They never launch real agents or
require credentials. The tmux integration tests check conversation title
updates, UUID fallback, truncation, and live theme switching on a separate
server; they are skipped if tmux is not installed.

GitHub Actions runs the full suite, including the tmux test and shell syntax
checks, on Linux and macOS, on pushes to `main` and on pull requests.

## Uninstall

```sh
rm -f ~/.local/bin/xcl ~/.local/bin/xcl-tab ~/.local/bin/xcl-open ~/.local/bin/xcl-theme
rm -rf ~/.config/xcl
```
