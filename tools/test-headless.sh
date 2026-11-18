#!/usr/bin/env bash
# Run the test suite against a private, headless GNOME Shell so the GTK windows don't appear on
# (or grab focus from) your desktop. Extra arguments go to pytest, e.g.
#   tools/test-headless.sh tests/test_z_control_gtk.py -x
set -euo pipefail
cd "$(dirname "$0")/.."

socket="floorshow-test-$$"
log="${TMPDIR:-/tmp}/floorshow-test-shell-$$.log"

# The private session's D-Bus services are chatty on stderr; their output goes to the log, pytest's to fd 3.
exec 3>&1
exec dbus-run-session -- bash -c '
  socket=$1 log=$2; shift 2
  gnome-shell --headless --no-x11 --wayland-display="$socket" --virtual-monitor 1920x1080 >"$log" 2>&1 &
  shell=$!
  trap "kill $shell 2>/dev/null; wait $shell 2>/dev/null; rm -f \"$log\"" EXIT
  for _ in $(seq 100); do [ -S "$XDG_RUNTIME_DIR/$socket" ] && break; sleep 0.1; done
  [ -S "$XDG_RUNTIME_DIR/$socket" ] || { echo "headless gnome-shell did not start, see $log" >&3; trap - EXIT; exit 1; }
  WAYLAND_DISPLAY="$socket" GDK_BACKEND=wayland DISPLAY= uv run --extra render --extra control --extra audio pytest "$@" >&3 2>&3
' _ "$socket" "$log" "$@" 2>>"$log"
