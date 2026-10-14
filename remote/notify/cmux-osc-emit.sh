#!/usr/bin/env bash
# usage: cmux-osc-emit.sh <text>  -> emit an OSC 9 desktop notification on the
# pane's pty. cmux turns it into a badge/notification on the host.
msg="$1"
if [ -n "${TMUX:-}" ]; then
  seq=$(printf '\033Ptmux;\033\033]9;%s\007\033\\' "$msg")
else
  seq=$(printf '\033]9;%s\007' "$msg")
fi
if { printf '%s' "$seq" >/dev/tty; } 2>/dev/null; then exit 0; fi
# No controlling tty (hooks run detached): walk up to the nearest pts.
pid=$PPID
for _ in 1 2 3 4 5 6; do
  [ -n "$pid" ] && [ "$pid" != "1" ] || break
  for fd in 1 0 2; do
    link=$(readlink "/proc/$pid/fd/$fd" 2>/dev/null) || continue
    case "$link" in
      /dev/pts/*) printf '%s' "$seq" > "$link" 2>/dev/null && exit 0 ;;
    esac
  done
  pid=$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null)
done
exit 0
