# Managed Node-RED pull flow (template)

This folder ships a **redacted, configurable** Node-RED flow that performs an
unattended `git pull --ff-only` of a Node-RED Project repository from inside
the Node-RED add-on, using a repository-scoped **read-only GitHub deploy key**
that is generated and persisted under `/data` inside the add-on. It targets
**Home Assistant Supervisor installations running the Node-RED add-on with
Projects enabled**. It is opt-in and is not universal Node-RED support.

## Files

- `template.json` — redacted Node-RED flow with `{{PLACEHOLDER}}` tokens. Do
  not import this directly; the placeholders must be filled first.
- `generate_flow.py` — fills the template from CLI flags or a `nodeRedPullFlow`
  block in `config.json`, producing an importable `pull_flow.json`.

## Why this design

The Node-RED Projects UI can pull and push while an automated `exec` node child
process returns Git exit code 128. **UI credentials are not inherited by `exec`
child processes.** The HTTPS token the Node-RED UI uses is not visible to a
shell started by an `exec` node, so an unattended `git pull` over HTTPS fails
with `could not read Username for 'https://github.com'`. A dedicated SSH deploy
key stored under the add-on's persistent `/data` directory is the durable fix.

## Two-phase bootstrap

**Phase 1 — Install the flow and generate the key.**

1. Fill the template:
   ```powershell
   python .\examples\node_red_pull_flow\generate_flow.py `
     --template .\examples\node_red_pull_flow\template.json `
     --output .\examples\node_red_pull_flow\pull_flow.json `
     --repo-ssh-url git@github.com:owner/example-node-red-flows.git `
     --repo-branch main `
     --ha-server-id <your-nodered-ha-server-config-id> `
     --pull-script-entity-id script.agent_node_red_pull `
     --status-entity-id input_text.agent_config_sync_status `
     --diagnostic-entity-id input_text.agent_node_red_deploy_detail
   ```
   Or via `config.json`:
   ```powershell
   python .\examples\node_red_pull_flow\generate_flow.py --config .\config.json
   ```
2. Import `pull_flow.json` into Node-RED (Menu → Import) and Deploy.
3. Trigger the pull once (call the pull script from HA, or inject the
   `server-state-changed` node manually). The first run will:
   - generate `/data/.ssh/node_red_deploy_ed25519` and `/data/.ssh/known_hosts`,
   - emit `DEPLOY_KEY=ssh-ed25519 AAAA...` into the bounded stderr helper,
   - fail with `Permission denied (publickey)` because the public key is not
     yet registered with GitHub.

**Phase 2 — Register the public half as a read-only deploy key.**

1. Read the `DEPLOY_KEY=ssh-ed25519 AAAA...` value from the diagnostic helper
   (`input_text.agent_node_red_deploy_detail` by default).
2. On GitHub, add it as a repository deploy key with **read-only** access
   (Repository settings → Deploy keys → Add deploy key).
3. Trigger the pull again. It should now succeed and write
   `<run_id>|node_red|success|0` to the status helper.

The private key never leaves the add-on. No GitHub token is used by the `exec`
child process.

## What the flow does

1. `server-state-changed` fires when the pull-script entity flips to `on`.
2. `Extract run_id and restart` reads `run_id` and `restart` from the trigger
   attributes.
3. `git pull --ff-only via deploy key` runs a bounded POSIX shell command that:
   - ensures `/data/.ssh/node_red_deploy_ed25519` exists (generating it on first
     run and printing the public half to stderr),
   - pins GitHub's published Ed25519 host key in `/data/.ssh/known_hosts`,
   - discovers the Node-RED Project worktree under `/config`, `/data`, or
     `/opt` by looking for a directory containing both `flows.json` and
     `package.json` inside a Git worktree,
   - runs `timeout 45 git -C <worktree> -c core.sshCommand="ssh ... batch + 15s
     connect timeout" pull --ff-only <ssh-url> <branch>`.
4. `Serialize status string` converts the `exec` return object (`{code: 0}`) to
   the string `<run_id>|node_red|success|0` on success, or
   `<run_id>|node_red|failure|<code>` on failure. This is required because HA's
   `input_text.set_value` rejects non-string payloads.
5. `Record pull success` / `Record pull failure` write the status string to the
   status helper. `Record bounded stderr` writes a 220-character truncation of
   stderr to the diagnostic helper on failure.
6. If `restartOnSuccess` is enabled and a `restartAddonSlug` is supplied, the
   `restart_branch` and `restart_addon` nodes call `hassio.addon_restart` after
   a successful pull. **Pulling Project files and activating them are separate
   concerns**: a pull updates files on disk; a Deploy or add-on restart is
   required for the pulled flows to become active.

## Security notes

- The deploy key is **repository-scoped and read-only**. Do not reuse a key
  across repositories.
- The key file is created with `chmod 600` and lives under the add-on's
  persistent `/data` directory, which survives add-on restarts.
- `GIT_TERMINAL_PROMPT=0` is implied by `BatchMode=yes`; the child will never
  prompt interactively.
- The 45-second `timeout` and 15-second SSH connect timeout guarantee a failure
  code on remote stalls instead of an indefinite hang.
- GitHub's published Ed25519 host key is pinned in `known_hosts` to prevent
  MITM on first connection.
