#!/usr/bin/env bash
# Host installer for cmux-remote-resume (macOS). Safe to re-run.
#
#   ./install.sh [--target NAME=BACKEND:ADDRESS[:ROOT]] [--default NAME]
#                [--mode default|bypass] [--notify] [--no-remote] [--sync]
#
# 1. writes ~/.config/cmux-remote/config (keeps an existing file, adds targets)
# 2. adds a marked block to ~/.zshrc that sources zsh/cmux-remote.zsh
# 3. for each target: makes sure the repo is present on the remote (ssh:
#    rsync; lima: the home mount) and runs remote/install-remote.py there
#
# Examples:
#   ./install.sh                                   # lima:ubuntu
#   ./install.sh --target devbox=ssh:devbox --default devbox
#   ./install.sh --target build=ssh:me@build.example.com:/opt/cmux-remote-resume
set -eu
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
MODE="" NOTIFY="" REMOTE=1 SYNC="" DEFAULT=""
TARGETS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --target) TARGETS+=("$2"); shift 2 ;;
        --default) DEFAULT="$2"; shift 2 ;;
        --mode) MODE="$2"; shift 2 ;;
        --notify) NOTIFY=1; shift ;;
        --no-remote) REMOTE=0; shift ;;
        --sync) SYNC=1; shift ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "install.sh: unknown option $1" >&2; exit 2 ;;
    esac
done

CONFIG="${CMUX_REMOTE_CONFIG:-$HOME/.config/cmux-remote/config}"
mkdir -p "$(dirname "$CONFIG")"
if [ "${#TARGETS[@]}" -gt 0 ]; then first="${TARGETS[0]%%=*}"; else first="ubuntu"; fi
if [ ! -f "$CONFIG" ]; then
    {
        echo "# cmux-remote-resume configuration (see config.example in the repo)"
        [ "${#TARGETS[@]}" -gt 0 ] || echo "CMUX_TARGET_ubuntu=lima:ubuntu"
        echo "CMUX_TARGET_DEFAULT=${DEFAULT:-$first}"
        echo "CMUX_AGENT_MODE=${MODE:-default}"
    } > "$CONFIG"
    echo "wrote $CONFIG"
else
    echo "kept $CONFIG"
fi
for spec in ${TARGETS[@]+"${TARGETS[@]}"}; do
    name=${spec%%=*}; value=${spec#*=}
    key="CMUX_TARGET_$(printf '%s' "$name" | tr -- '-' '_')"
    if grep -q "^$key=" "$CONFIG"; then
        sed -i '' "s|^$key=.*|$key=$value|" "$CONFIG"
    else
        printf '%s=%s\n' "$key" "$value" >> "$CONFIG"
    fi
done
[ -n "$DEFAULT" ] && sed -i '' "s|^CMUX_TARGET_DEFAULT=.*|CMUX_TARGET_DEFAULT=$DEFAULT|" "$CONFIG"
[ -n "$MODE" ] && sed -i '' "s|^CMUX_AGENT_MODE=.*|CMUX_AGENT_MODE=$MODE|" "$CONFIG"
CMUX_REMOTE_HOME="$ROOT"; . "$ROOT/lib/cmux-remote-env.sh"; . "$ROOT/lib/cmux-remote-transport.sh"

ZSHRC="$HOME/.zshrc"
if ! grep -q '>>> CMUX-REMOTE-RESUME >>>' "$ZSHRC" 2>/dev/null; then
    cp "$ZSHRC" "$ZSHRC.bak-cmux-remote-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
    {
        echo
        echo '# >>> CMUX-REMOTE-RESUME >>>'
        echo "[[ -f '$ROOT/zsh/cmux-remote.zsh' ]] && source '$ROOT/zsh/cmux-remote.zsh'"
        echo '# <<< CMUX-REMOTE-RESUME <<<'
    } >> "$ZSHRC"
    echo "added CMUX-REMOTE-RESUME block to $ZSHRC"
fi
mkdir -p "$CMUX_REMOTE_CACHE/panes"

install_remote() {
    local name="$1"
    cmux_target_resolve "$name"
    echo "== $name ($T_BACKEND:$T_ADDR, root $T_ROOT)"
    case "$T_BACKEND" in
        lima)
            command -v limactl >/dev/null || { echo 'limactl not found (brew install lima)' >&2; return 1; }
            if [ "$(limactl list "$T_ADDR" --format '{{.Status}}' 2>/dev/null)" != "Running" ]; then
                echo "Lima VM '$T_ADDR' is not running; start it and re-run, or use --no-remote." >&2; return 1
            fi
            if ! cmux_remote_exec "$name" "test -r $T_ROOT/lib/cmux-remote-run.sh" 2>/dev/null; then
                if [ -n "$SYNC" ] || [ "$T_ROOT" != "$ROOT" ]; then
                    sync_repo "$name"
                else
                    cat >&2 <<MSG
The VM cannot read $T_ROOT. Either keep Lima's default home mount (location "~")
so the checkout is visible at the same path, or give the target an explicit root
and re-run with --sync to copy the repo there:
  ./install.sh --target $name=lima:$T_ADDR:/opt/cmux-remote-resume --sync
MSG
                    return 1
                fi
            fi ;;
        ssh)
            if [ -n "$SYNC" ] || ! cmux_remote_exec "$name" "test -r $T_ROOT/lib/cmux-remote-run.sh" 2>/dev/null; then
                sync_repo "$name"
            fi ;;
    esac
    cmux_remote_exec "$name" "python3 $T_ROOT/remote/install-remote.py ${NOTIFY:+--notify}"
}

sync_repo() {
    local name="$1"
    cmux_target_resolve "$name"
    local home; home=$(cmux_remote_home "$name") || { echo "cannot reach $name" >&2; return 1; }
    local dest="${T_ROOT/#\~/$home}"
    echo "syncing repo to $name:$dest"
    cmux_remote_exec "$name" "mkdir -p $(printf '%q' "$dest")"
    case "$T_BACKEND" in
        ssh)
            # shellcheck disable=SC2086
            rsync -a --delete --exclude .git --exclude __pycache__ -e "ssh $CMUX_SSH_OPTS" "$ROOT/" "$T_ADDR:$dest/" ;;
        lima)
            local tar; tar=$(mktemp -t cmux-remote); tar -C "$ROOT" --exclude .git --exclude __pycache__ -cf "$tar" .
            cmux_remote_copy "$name" "$tar" "$dest/.sync.tar"
            cmux_remote_exec "$name" "cd $(printf '%q' "$dest") && tar -xf .sync.tar && rm -f .sync.tar"
            rm -f "$tar" ;;
    esac
}

if [ "$REMOTE" = 1 ]; then
    if [ "${#TARGETS[@]}" -gt 0 ]; then
        for spec in "${TARGETS[@]}"; do install_remote "${spec%%=*}"; done
    else
        install_remote "$CMUX_TARGET_DEFAULT"
    fi
fi

cat <<MSG

Done. Open a new shell (or: source ~/.zshrc), then in a cmux pane run:
  vmc            # Claude or Codex on '$CMUX_TARGET_DEFAULT'
  vms            # remote shell
  vmc --target NAME ...   # another configured target
The first remote pane asks cmux to approve '$ROOT/bin/remote-restore' as Auto-Restore.
Approve it once; every remote pane then comes back by itself after a cmux restart or reboot.
MSG
