# Workflow Overview

## 1. Inspect

Start by running `--help` on the tool you want to use. The public interface is
explicit on purpose.

## 2. Point at inputs

Pass the relevant root path or paths. Do not rely on hidden sibling folders.

## 3. Generate

- `build_ha_index.py` writes a lightweight index into `output/ha-index/`
- `ha_incident_bundle.py` writes incident bundles into `output/incident-bundles/`
- `split_flows.py` writes split Node-RED files into `output/flows-split/`

## 4. Verify

Use `verify_workspace.py` against either the redacted examples or your own
paths. It will skip missing inputs instead of failing on absent private data.

## 5. Sync when needed

`sync-ha-config.ps1` is a convenience wrapper for sibling repos. It expects
either explicit paths or a config file that names them. It has two modes:

- **Explicit-path mode (default):** publish sibling repos by layout and
  regenerate local views. No Home Assistant REST calls.
- **Managed mode (opt-in):** publish repos, then drive HA REST to run the
  managed pull, backup, and Node-RED Project pull scripts by run ID, with
  bounded polling and bounded stderr capture. Activates when any managed flag
  is passed or when `config.json` supplies managed entity IDs.

REST authentication uses `HASS_TOKEN`; `HASS_HOST` defaults to
`http://homeassistant.local:8123`. `HASS_TOKEN` and `HASS_HOST` precedence in
managed mode: explicit flag, process environment, Windows User environment,
then a gitignored toolkit-local `.env`.

Managed mode targets Home Assistant Supervisor installations running the
Node-RED add-on with Projects. It is opt-in and is not universal Node-RED
support.

### Managed-mode staged protocol

Each managed stage writes a run-ID-prefixed status string to a single HA
`input_text` helper:

    <run_id>|<stage>|<status>|<code>

Stages: `pull`, `backup`, `node_red`. Statuses: `pending`, `running`,
`success`, `failure`. The toolkit polls the helper until it sees
`<run_id>|<stage>|success|0` or a failure, with a per-stage timeout (default
180s). On Node-RED stage failure, an optional diagnostic `input_text` helper
provides a 220-character truncation of the Git/SSH stderr.

The Node-RED pull flow serializes the `exec` return object (`{code: 0}`) into
the string `<run_id>|node_red|success|0` because HA's `input_text.set_value`
rejects non-string payloads. The Python counterpart in
`scripts/node_red_status_serializer.py` is smoke-tested by
`verify_workspace.py`.

### Node-RED pull two-phase bootstrap

See `examples/node_red_pull_flow/README.md` for the full bootstrap. In short:

1. **Install the flow and generate the key.** Fill
   `examples/node_red_pull_flow/template.json` via `generate_flow.py`, import
   into Node-RED, Deploy, and trigger the pull once. The first run generates a
   dedicated Ed25519 deploy key under `/data/.ssh/`, pins GitHub's published
   host key, and emits `DEPLOY_KEY=ssh-ed25519 AAAA...` into the diagnostic
   helper. It fails with `Permission denied (publickey)` until the public half
   is registered.
2. **Register the public half as a read-only deploy key** on GitHub. The next
   pull succeeds and writes `<run_id>|node_red|success|0`.

### Important constraints

- **UI credentials are not inherited by `exec` child processes.** The HTTPS
  token the Node-RED Projects UI uses is not visible to a shell started by an
  `exec` node, so unattended `git pull` over HTTPS fails. A repository-scoped
  read-only SSH deploy key under `/data` is the durable fix.
- **Project file pulling is separate from runtime activation.** A successful
  pull updates files on disk; a Deploy or add-on restart is required for the
  pulled flows to become active. `-RestartNodeRed` requests a restart but the
  flow owns the activation.
