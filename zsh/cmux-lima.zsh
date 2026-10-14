# Host shell integration for cmux-lima-resume. Sourced from ~/.zshrc by install.sh.
#
#   claude          local Claude Code; cmux's own wrapper handles restore
#   ct [dir]        cd to a host dir (optional) and run claude
#   vmc [guest-dir] Claude or Codex inside the Lima VM, restorable by cmux
#   vms [guest-dir] a VM shell, restorable to the same guest directory
#   codex           local Codex routed through the resume-aware wrapper
#
# One-time: approve `vm-restore` as Auto-Restore in cmux Settings > Terminal >
# Resume Commands the first time a VM pane asks. Clean exits (/exit) are not
# resurrected; kills, cmux restarts and reboots are.
CMUX_LIMA_ROOT="${CMUX_LIMA_ROOT:-${${(%):-%x}:A:h:h}}"
source "$CMUX_LIMA_ROOT/lib/cmux-lima-env.sh"
case ":$PATH:" in *":$CMUX_LIMA_ROOT/bin:"*) ;; *) export PATH="$CMUX_LIMA_ROOT/bin:$PATH" ;; esac

ct() {
  if [[ -n "${1:-}" ]]; then cd "$1" || return; fi
  claude
}

codex() { "$CMUX_LIMA_ROOT/lib/cmux-codex" "$@"; }

source "$CMUX_LIMA_ROOT/completion/vmc-completion.zsh"
