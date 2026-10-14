# cmux-remote-resume

Run Claude Code and Codex on a remote machine, a [Lima](https://lima-vm.io)
VM or any ssh host, from [cmux](https://github.com/manaflow-ai/cmux) panes,
and get every pane back, on the exact same conversation, after a cmux
restart, a VM restart, or a Mac reboot. No tmux.

```
vmc                        # Claude or Codex on the default target, in this pane
vmc ~/wd/app --codex
vmc --target build         # another configured target
vms /srv/project           # a remote shell that comes back in the same directory
vmpaste                    # drop a clipboard screenshot onto the remote for the agent
```

Quit cmux, reboot, reopen: each remote pane reconnects and runs
`claude --resume <its uuid>` or `codex resume <its uuid>` in the directory it
was in. Panes that were plain shells come back as shells in their last
directory. A conversation you ended on purpose with `/exit` stays ended.

## How it works, in one paragraph

cmux can re-run a per-pane "resume command" when it restores a layout. This
project registers one command, `remote-restore`, for every remote pane. On
the remote, Claude Code and Codex hooks write a small JSON record per pane
(agent, session UUID, cwd) under `~/.cache/cmux-remote/sessions/`. The host
keeps a one-line index of which target each pane belongs to. `remote-restore`
reads the index, fetches the record over the transport, and reopens exactly
that session. It never guesses a session from "the newest transcript in this
directory". Details in [docs/how-it-works.md](docs/how-it-works.md).

Transports: `lima` (through `limactl shell`, with VM start, post-reboot
repair and startup serialization) and `ssh` (any host you can reach with an
ssh alias). Nothing else differs between them.

## Requirements

- macOS with cmux installed at `/Applications/cmux.app` (or set `CMUX_CLI`)
- a target: a running Lima instance (`brew install lima`), or an ssh host
  with key-based login, ideally with `ControlMaster auto` in `~/.ssh/config`
- Claude Code and/or Codex installed on the target; bash and python3 there
- `python3` on the Mac (ships with macOS)

## Install

```bash
git clone https://github.com/rusanki/cmux-remote-resume ~/projects/cmux-remote-resume
cd ~/projects/cmux-remote-resume
./install.sh                                        # Lima instance "ubuntu"
./install.sh --target devbox=ssh:devbox --default devbox
./install.sh --target gpu=ssh:me@gpu.lan:/opt/cmux-remote-resume --notify
```

`install.sh` writes `~/.config/cmux-remote/config`, adds a marked block to
`~/.zshrc` that puts `bin/` on your PATH and loads completion, and installs
the remote side on each target: for ssh it rsyncs this repo to the remote
root, for Lima it relies on the default home mount making the checkout
visible at the same path. Then `remote/install-remote.py` runs there and adds
the Claude and Codex hooks and a marked block to the remote `~/.bashrc`. Both
installers are idempotent; `--sync` re-copies the repo after a `git pull`.

The first time a remote pane starts, cmux asks you to approve
`.../bin/remote-restore` as an auto-restore command. Approve it once. You can
review it later under cmux Settings > Terminal > Resume Commands.

## Configuration

`~/.config/cmux-remote/config`, plain `KEY=VALUE`. Environment variables of
the same name win. See [config.example](config.example).

| Key | Default | Meaning |
|---|---|---|
| `CMUX_TARGET_<name>` | `lima:<name>` | `lima:<instance>[:<root>]` or `ssh:<destination>[:<root>]`; root is where this repo lives on the remote |
| `CMUX_TARGET_DEFAULT` | `ubuntu` | Target used without `--target` |
| `CMUX_SSH_OPTS` | empty | Extra options for ssh/scp/rsync, e.g. `-F ~/.ssh/work` |
| `CMUX_AGENT_MODE` | `default` | `bypass` runs `claude --dangerously-skip-permissions` and `codex --dangerously-bypass-approvals-and-sandbox` |
| `CMUX_CLI` | cmux's bundled CLI | Path to the `cmux` CLI |

`bypass` is what the author uses: the remote is the sandbox, and an agent
that stops to ask about every file edit is not useful unattended. It is not
the default here. Decide for yourself.

## Commands

| Command | What it does |
|---|---|
| `vmc [dir] [--claude\|--codex] [--resume <uuid>] [--target NAME]` | Start an agent on the remote. Without a flag it shows a two-item menu. Registers the pane for restore. |
| `vms [dir] [--target NAME]` | Remote shell with directory tracking. Starting `claude` or `codex` inside it upgrades the pane to a resumable session. |
| `remote-restore` | What cmux runs on restore. Waits for the remote, retries the connection, offers Enter-to-retry on failure. |
| `vmpaste [--clip] [--surface <id>] [--target NAME]` | Copy the clipboard image to the remote and type its path into the focused pane. |
| `cmux-remote targets\|resolve\|exec\|home\|copy\|ready\|record` | Transport helpers, also handy for debugging. |
| `ct [dir]` | Local Claude Code shortcut; cmux's own wrapper handles its restore. |

Tab completion for `vmc`/`vms` lists remote directories live and remembers
the ones you used, per target.

## Behaviour you should know about

- **Clean exits are respected.** `/exit` or Ctrl-D in Claude drops the mapping. A kill, a cmux quit, or a reboot keeps it.
- **A failed resume keeps the mapping.** If the agent exits non-zero, the pane falls into a remote shell and the record is retained for the next restore.
- **A booting Lima VM is waited for, not raced.** Many panes restoring at once start the VM exactly once and wait until the guest answers over ssh. An ssh host is probed with backoff until it answers.
- **A Lima VM left "Broken" by a host reboot is repaired.** Leftover pid files can collide with a reused pid after a reboot, which makes `limactl start` refuse. The helper removes files older than the host boot. See [docs/troubleshooting.md](docs/troubleshooting.md).
- **An in-flight generation is lost on restart.** Resume restores the conversation to its last persisted message. Nothing keeps the agent process alive while cmux is closed.

## Tests

```bash
python3 -m unittest tests.test_resume
```

Twenty-four tests cover the record store, hook semantics, the launchers over
fake `limactl`/`ssh`/`scp`, target resolution, the Codex wrapper, the
readiness helper for both backends including the post-reboot repair, config
loading, and the remote installer. They run on macOS and Linux and touch
nothing outside a temp directory.

## Layout

```
bin/          host launchers: vmc, vms, remote-restore, vmpaste, cmux-remote
lib/          config loader, transport, helpers; the runner and state store run on the remote
remote/       remote installer, bashrc block, hook entrypoints, notify scripts
completion/   zsh completion
zsh/          host shell integration (ct, codex wrapper, PATH)
tests/        unittest suite
docs/         design notes and troubleshooting
lima/         example instance config
```

## License

MIT.
