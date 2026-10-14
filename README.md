# cmux-lima-resume

Run Claude Code and Codex inside a [Lima](https://lima-vm.io) VM from
[cmux](https://github.com/manaflow-ai/cmux) panes, and get every pane back,
on the exact same conversation, after a cmux restart, a VM restart, or a Mac
reboot. No tmux.

```
vmc                 # Claude or Codex in the VM, in this pane
vmc ~/wd/app --codex
vms /srv/project    # a VM shell that comes back in the same directory
vmpaste             # drop a clipboard screenshot into the VM for the agent to read
```

Quit cmux, reboot, reopen: each VM pane reconnects and runs
`claude --resume <its uuid>` or `codex resume <its uuid>` in the directory it
was in. Panes that were plain shells come back as shells in their last
directory. A conversation you ended on purpose with `/exit` stays ended.

## How it works, in one paragraph

cmux can re-run a per-pane "resume command" when it restores a layout. This
project registers one command, `vm-restore`, for every VM pane. Inside the
guest, Claude Code and Codex hooks write a small JSON record per pane
(agent, session UUID, cwd, VM name) to `state/sessions/` in this repository,
which is mounted into the VM, so host and guest see the same file.
`vm-restore` reads that record and reopens exactly that session. It never
guesses a session from "the newest transcript in this directory". Details in
[docs/how-it-works.md](docs/how-it-works.md).

## Requirements

- macOS with cmux installed at `/Applications/cmux.app` (or set `CMUX_CLI`)
- Lima (`brew install lima`) with a running Linux instance
- Claude Code and/or Codex installed inside the VM
- `python3` on both sides (macOS ships one; Ubuntu images ship one)

## Install

```bash
git clone https://github.com/rusanki/cmux-lima-resume ~/projects/cmux-lima-resume
```

Mount the checkout **writable** into the VM. With Lima's default home mount
the path is the same on both sides but read-only, so add an explicit mount in
`~/.lima/<vm>/lima.yaml` (see [lima/ubuntu.example.yaml](lima/ubuntu.example.yaml)):

```yaml
mounts:
- location: "~/projects/cmux-lima-resume"
  writable: true
```

Restart the instance, then:

```bash
cd ~/projects/cmux-lima-resume
./install.sh --vm ubuntu            # add --notify for OSC 9 turn-complete notifications
```

`install.sh` writes `~/.config/cmux-lima/config`, adds a marked block to
`~/.zshrc` that puts `bin/` on your PATH and loads completion, and runs
`guest/install-guest.py` inside the VM, which adds the Claude and Codex hooks
and a marked block to the guest `~/.bashrc`. Both installers are idempotent.

The first time a VM pane starts, cmux asks you to approve
`.../bin/vm-restore` as an auto-restore command. Approve it once. You can
review it later under cmux Settings > Terminal > Resume Commands.

## Configuration

`~/.config/cmux-lima/config`, plain `KEY=VALUE`. Environment variables of the
same name win.

| Key | Default | Meaning |
|---|---|---|
| `CMUX_VM_NAME` | `ubuntu` | Lima instance |
| `CMUX_VM_GUEST_ROOT` | checkout path | Where this repo appears inside the guest |
| `CMUX_VM_AGENT_MODE` | `default` | `bypass` runs `claude --dangerously-skip-permissions` and `codex --dangerously-bypass-approvals-and-sandbox` |
| `CMUX_CLI` | cmux's bundled CLI | Path to the `cmux` CLI |

`bypass` is what the author uses: the VM is the sandbox, and an agent that
stops to ask about every file edit is not useful unattended. It is not the
default here. Decide for yourself.

## Commands

| Command | What it does |
|---|---|
| `vmc [guest-dir] [--claude\|--codex] [--resume <uuid>]` | Start an agent in the VM. Without a flag it shows a two-item menu. Registers the pane for restore. |
| `vms [guest-dir]` | VM shell with directory tracking. Starting `claude` or `codex` inside it upgrades the pane to a resumable session. |
| `vm-restore` | What cmux runs on restore. Waits for the VM, retries the connection, offers Enter-to-retry on failure. Never run it by hand for a different pane. |
| `vmpaste [--clip] [--surface <id>]` | Save the clipboard image under `state/clip/` and type its guest path into the focused pane. |
| `ct [dir]` | Local Claude Code shortcut; cmux's own wrapper handles its restore. |

Tab completion for `vmc`/`vms` lists guest directories live and remembers the
ones you used.

## Behaviour you should know about

- **Clean exits are respected.** `/exit` or Ctrl-D in Claude drops the mapping. A kill, a cmux quit, or a reboot keeps it.
- **A failed resume keeps the mapping.** If the agent exits non-zero, the pane falls into a VM shell and the record is retained for the next restore.
- **A booting VM is waited for, not raced.** Many panes restoring at once start the VM exactly once and wait until the guest answers over ssh.
- **A VM left "Broken" by a host reboot is repaired.** Lima's leftover pid files can collide with a reused pid after a reboot, which makes `limactl start` refuse. The helper removes files older than the host boot. See [docs/troubleshooting.md](docs/troubleshooting.md).
- **An in-flight generation is lost on restart.** Resume restores the conversation to its last persisted message. Nothing keeps the agent process alive while cmux is closed.

## Tests

```bash
python3 -m unittest tests.test_resume
```

Twenty-two tests cover the mapping store, hook semantics, the launchers (with
a fake `limactl`), the Codex wrapper, the readiness helper including the
post-reboot repair, config loading, and the guest installer. They run on macOS
and Linux and touch nothing outside a temp directory.

## Layout

```
bin/          host launchers: vmc, vms, vm-restore, vmpaste
lib/          shared helpers, run on host and guest via the mount
guest/        guest installer, bashrc block, hook entrypoints, notify scripts
completion/   zsh completion
zsh/          host shell integration (ct, codex wrapper, PATH)
state/        runtime data, git-ignored: sessions/ and clip/
tests/        unittest suite
docs/         design notes and troubleshooting
lima/         example instance config
```

## License

MIT.
