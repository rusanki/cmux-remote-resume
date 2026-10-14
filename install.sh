#!/usr/bin/env bash
# Host installer for cmux-lima-resume (macOS). Safe to re-run.
#
#   ./install.sh [--vm NAME] [--mode default|bypass] [--notify] [--no-guest]
#
# 1. writes ~/.config/cmux-lima/config (keeps an existing file)
# 2. adds a marked block to ~/.zshrc that sources zsh/cmux-lima.zsh
# 3. checks the repo is visible inside the guest and installs the guest side
set -eu
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
VM="" MODE="" NOTIFY="" GUEST=1
while [ $# -gt 0 ]; do
    case "$1" in
        --vm) VM="$2"; shift 2 ;;
        --mode) MODE="$2"; shift 2 ;;
        --notify) NOTIFY=1; shift ;;
        --no-guest) GUEST=0; shift ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "install.sh: unknown option $1" >&2; exit 2 ;;
    esac
done
command -v limactl >/dev/null || { echo 'install.sh: limactl not found (brew install lima)' >&2; exit 1; }

CONFIG="${CMUX_LIMA_CONFIG:-$HOME/.config/cmux-lima/config}"
if [ ! -f "$CONFIG" ]; then
    mkdir -p "$(dirname "$CONFIG")"
    {
        echo "# cmux-lima-resume configuration (see config.example in the repo)"
        echo "CMUX_VM_NAME=${VM:-ubuntu}"
        echo "CMUX_VM_GUEST_ROOT=$ROOT"
        echo "CMUX_VM_AGENT_MODE=${MODE:-default}"
    } > "$CONFIG"
    echo "wrote $CONFIG"
else
    [ -n "$VM" ] && sed -i '' "s|^CMUX_VM_NAME=.*|CMUX_VM_NAME=$VM|" "$CONFIG"
    [ -n "$MODE" ] && sed -i '' "s|^CMUX_VM_AGENT_MODE=.*|CMUX_VM_AGENT_MODE=$MODE|" "$CONFIG"
    echo "kept $CONFIG"
fi
CMUX_LIMA_ROOT="$ROOT"; . "$ROOT/lib/cmux-lima-env.sh"

ZSHRC="$HOME/.zshrc"
if ! grep -q '>>> CMUX-LIMA-RESUME >>>' "$ZSHRC" 2>/dev/null; then
    cp "$ZSHRC" "$ZSHRC.bak-cmux-lima-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
    {
        echo
        echo '# >>> CMUX-LIMA-RESUME >>>'
        echo "[[ -f '$ROOT/zsh/cmux-lima.zsh' ]] && source '$ROOT/zsh/cmux-lima.zsh'"
        echo '# <<< CMUX-LIMA-RESUME <<<'
    } >> "$ZSHRC"
    echo "added CMUX-LIMA-RESUME block to $ZSHRC"
fi
mkdir -p "$ROOT/state/sessions"

if [ "$GUEST" = 1 ]; then
    if [ "$(limactl list "$CMUX_VM_NAME" --format '{{.Status}}' 2>/dev/null)" != "Running" ]; then
        echo "Lima VM '$CMUX_VM_NAME' is not running; start it and re-run, or use --no-guest." >&2
        exit 1
    fi
    if ! limactl shell --workdir / "$CMUX_VM_NAME" -- test -w "$CMUX_VM_GUEST_ROOT/state" 2>/dev/null; then
        cat >&2 <<MSG
install.sh: the guest cannot write $CMUX_VM_GUEST_ROOT/state.
Mount this repository writable into the VM, for example in ~/.lima/$CMUX_VM_NAME/lima.yaml:

  mounts:
  - location: "$ROOT"
    writable: true

then 'limactl stop $CMUX_VM_NAME && limactl start $CMUX_VM_NAME' and re-run install.sh.
If the repo appears at a different path inside the guest, set CMUX_VM_GUEST_ROOT in $CONFIG.
MSG
        exit 1
    fi
    limactl shell --workdir / "$CMUX_VM_NAME" -- python3 "$CMUX_VM_GUEST_ROOT/guest/install-guest.py" ${NOTIFY:+--notify}
fi

cat <<MSG

Done. Open a new shell (or: source ~/.zshrc), then in a cmux pane run:
  vmc            # Claude or Codex in the VM
  vms            # VM shell
The first VM pane asks cmux to approve '$ROOT/bin/vm-restore' as Auto-Restore.
Approve it once; every VM pane then comes back by itself after a cmux restart or reboot.
MSG
