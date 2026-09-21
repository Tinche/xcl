#!/bin/sh
# xcl installer.
#
#   curl -fsSL https://raw.githubusercontent.com/Tinche/xcl/main/install.sh | sh
#
# Or, from a clone, ./install.sh — it then installs the files next to itself
# instead of downloading.
#
# Environment:
#   XCL_BIN_DIR    where the executables go   (default ~/.local/bin)
#   XCL_CONF_DIR   where tmux.conf goes       (default ~/.config/xcl)
#   XCL_REPO       owner/name                 (default Tinche/xcl)
#   XCL_REF        branch or tag              (default main)
set -eu

REPO="${XCL_REPO:-Tinche/xcl}"
REF="${XCL_REF:-main}"
BIN_DIR="${XCL_BIN_DIR:-$HOME/.local/bin}"
CONF_DIR="${XCL_CONF_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/xcl}"
BASE_URL="https://raw.githubusercontent.com/$REPO/$REF"

say() { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Run from a clone? $0 has no slash under `curl | sh`, so this stays empty.
SRC_DIR=""
case "$0" in
  */*) d=$(unset CDPATH; cd -- "$(dirname -- "$0")" && pwd)
       [ -f "$d/bin/xcl" ] && SRC_DIR="$d" ;;
esac

fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- "$1"
  else
    die "need curl or wget to download; or run install.sh from a clone"
  fi
}

tmp=$(mktemp -d) || die "cannot create a temp directory"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

get() { # get <repo-relative-path>
  if [ -n "$SRC_DIR" ]; then
    cat "$SRC_DIR/$1"
  else
    fetch "$BASE_URL/$1"
  fi
}

for f in bin/xcl bin/xcl-tab bin/xcl-open tmux.conf; do
  mkdir -p "$tmp/$(dirname "$f")"
  get "$f" > "$tmp/$f" || die "could not obtain $f"
  [ -s "$tmp/$f" ] || die "$f came back empty (bad ref '$REF'?)"
done

# A 404 from raw.githubusercontent.com is an HTML page, not a script. Catch
# that before installing something unrunnable.
for f in bin/xcl bin/xcl-tab bin/xcl-open; do
  head -n 1 "$tmp/$f" | grep -q '^#!' || die "$f does not look like a script"
done
head -n 1 "$tmp/tmux.conf" | grep -q '^#' || die "tmux.conf does not look like a config"

mkdir -p "$BIN_DIR" "$CONF_DIR"
for f in xcl xcl-tab xcl-open; do
  cp "$tmp/bin/$f" "$BIN_DIR/$f"
  chmod 755 "$BIN_DIR/$f"
done
cp "$tmp/tmux.conf" "$CONF_DIR/tmux.conf"

say "installed:"
say "  $BIN_DIR/xcl"
say "  $BIN_DIR/xcl-tab"
say "  $BIN_DIR/xcl-open"
say "  $CONF_DIR/tmux.conf"

# ---- post-install checks -------------------------------------------------
command -v tmux >/dev/null 2>&1 || warn "tmux is not installed — xcl needs it"

case ":${PATH}:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH; add it, or the keybinds will not work" ;;
esac

missing=""
for t in claude codex lazygit; do
  command -v "$t" >/dev/null 2>&1 || missing="$missing $t"
done
[ -n "$missing" ] && say "optional tools not found:$missing (their keybinds will do nothing)"

say ""
say "run:  xcl            (from inside a repo)"
say "then: prefix-c new Claude tab, prefix-s switch repo, prefix-M-r reload config"
