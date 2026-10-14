# Host shell integration for cmux-remote-resume. Sourced from ~/.zshrc by install.sh.
#
#   claude            local Claude Code; cmux's own wrapper handles restore
#   ct [dir]          cd to a host dir (optional) and run claude
#   vmc [remote-dir]  Claude or Codex on the default target, restorable by cmux
#   vms [remote-dir]  a remote shell, restorable to the same directory
#   vmpaste           clipboard image -> remote path
#   codex             local Codex routed through the resume-aware wrapper
#   cmux-remote       targets / exec / home / record helpers
#
# One-time: approve `remote-restore` as Auto-Restore in cmux Settings > Terminal >
# Resume Commands the first time a remote pane asks. Clean exits (/exit) are not
# resurrected; kills, cmux restarts and reboots are.
CMUX_REMOTE_HOME="${CMUX_REMOTE_HOME:-${${(%):-%x}:A:h:h}}"
source "$CMUX_REMOTE_HOME/lib/cmux-remote-env.sh"
case ":$PATH:" in *":$CMUX_REMOTE_HOME/bin:"*) ;; *) export PATH="$CMUX_REMOTE_HOME/bin:$PATH" ;; esac

ct() {
  if [[ -n "${1:-}" ]]; then cd "$1" || return; fi
  claude
}

codex() { "$CMUX_REMOTE_HOME/lib/cmux-codex" "$@"; }

source "$CMUX_REMOTE_HOME/completion/vmc-completion.zsh"
