#!/bin/bash
# End-to-end: the module against a real Kilix engine on a private X display.
# ENGINE=<kitty checkout with embedded windows, built>. Nothing from the
# caller's desktop session is passed in.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
MOD=$(cd "$HERE/../.." && pwd)
: "${ENGINE:?set ENGINE to a built kitty checkout with embedded windows}"
R=$(mktemp -d "${TMPDIR:-/tmp}/kep-engine.XXXXXX")
mkdir -p "$R/config" "$R/runtime" "$R/cache"; chmod 700 "$R/runtime"
DISP=
for n in $(seq 90 140); do [ -e /tmp/.X11-unix/X$n ] || [ -e /tmp/.X$n-lock ] || { DISP=:$n; break; }; done
[ -n "$DISP" ] || { echo "no free X display" >&2; exit 2; }
Xvfb "$DISP" -screen 0 1600x1000x24 -nolisten tcp >"$R/xvfb.log" 2>&1 &
XPID=$!
for _ in $(seq 100); do [ -S "/tmp/.X11-unix/X${DISP#:}" ] && break; sleep 0.1; done
SOCK=unix:@kep-engine-$$
env -i HOME="$HOME" PATH=/usr/bin:/bin DISPLAY="$DISP" TERM=xterm-256color \
    KITTY_CONFIG_DIRECTORY="$R/config" KITTY_RUNTIME_DIRECTORY="$R/runtime" KITTY_CACHE_DIRECTORY="$R/cache" \
    LIBGL_ALWAYS_SOFTWARE=1 \
    "$ENGINE/kitty/launcher/kitty" --config NONE -o allow_remote_control=yes -o enable_audio_bell=no \
    -o shell_integration=disabled --listen-on "$SOCK" bash --norc >"$R/engine.log" 2>&1 &
KPID=$!
for _ in $(seq 150); do ss -xl 2>/dev/null | /usr/bin/grep -q "@kep-engine-$$" && break; sleep 0.1; done
make -s -C "$MOD" BUILD="$R/build" "$R/build/libkilix-embed-pane.so" || { kill $KPID $XPID; exit 2; }
KEP_SOCK=$SOCK KEP_KITTEN=$ENGINE/kitty/launcher/kitten KEP_LIB=$R/build/libkilix-embed-pane.so \
    KEP_RUNTIME=$R/runtime python3 -I "$HERE/test_engine.py" ${KEP_TESTS:-}; rc=$?
kill $KPID 2>/dev/null; wait $KPID 2>/dev/null
kill $XPID 2>/dev/null; wait $XPID 2>/dev/null
if [ $rc -ne 0 ]; then echo "--- engine log"; tail -20 "$R/engine.log"; echo "(kept $R)"; else rm -rf "$R"; fi
exit $rc
