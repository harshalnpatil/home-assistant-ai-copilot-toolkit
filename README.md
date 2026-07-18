# Home Assistant AI Copilot Toolkit

## Problem

Home Assistant configuration often spans several inputs, which makes it hard to give an AI assistant useful context without exposing a live household setup.

## What this toolkit does

This small toolkit builds a lightweight context index from explicit inputs, creates REST-based incident bundles, verifies a workspace, and includes helpers for inspecting Node-RED exports.

## Public-safe scope

This is a release artifact, not a backup or a full agent product. It includes generic scripts, documentation, and redacted fixtures only. It excludes live Home Assistant exports, private `.env` files, generated outputs, backups, pulled dashboard data, and house-specific names. See [the privacy boundary](docs/privacy-boundary.md).

## How it works

1. Pass the relevant configuration roots explicitly.
2. Build an index or incident bundle into `output/`.
3. Run the verifier against either the included redacted fixture set or your own local paths.
4. Use the optional PowerShell wrappers when working with sibling repositories.

See [the workflow overview](docs/workflow-overview.md) for the detailed flow.

## Visual evidence

The image below is a synthetic demo. It contains fictional labels and no live system, credential, URL, or infrastructure information.

![Synthetic Home Assistant verification demo](docs/synthetic-demo-verification.png)

*Synthetic verification result shown with example dashboard data only.*

## Contents

- `build_ha_index.py`, build a lightweight context index from explicit inputs
- `ha_incident_bundle.py`, build a REST-based incident bundle
- `verify_workspace.py`, run smoke tests against the toolkit and optional inputs
- `verify-workspace.ps1`, PowerShell wrapper for the verifier
- `sync-ha-config.ps1`, PowerShell wrapper for sibling repo syncs, with opt-in
  managed Home Assistant deployment (config pull, backup, Node-RED Project pull)
- `scripts/split_flows.py`, split a Node-RED export by tab
- `scripts/spot_check_flows.py`, inspect a Node-RED export for obvious issues
- `scripts/node_red_status_serializer.py`, serialize Node-RED pull-stage status
  strings for Home Assistant `input_text` helpers
- `docs/`, public usage notes
- `examples/sample_workspace/`, redacted fixtures
- `examples/node_red_pull_flow/`, redacted Node-RED flow template plus a
  generator for unattended Project pulls via a read-only deploy key

## Quick Start

Run the help for any script first:

```powershell
python .\build_ha_index.py --help
python .\ha_incident_bundle.py --help
python .\verify_workspace.py --help
python .\scripts\split_flows.py --help
python .\scripts\spot_check_flows.py --help
```

Build the example index:

```powershell
python .\build_ha_index.py `
  --root . `
  --ha-config-root .\examples\sample_workspace\home-assistant-config `
  --editable-root .\examples\sample_workspace\home-assistant-config-editable `
  --node-red-root .\examples\sample_workspace\home-assistant-node-red `
  --lovelace-root .\examples\sample_workspace\home-assistant-lovelace `
  --output .\output\ha-index
```

Run the smoke tests on the redacted fixture set:

```powershell
python .\verify_workspace.py `
  --root . `
  --ha-config-root .\examples\sample_workspace\home-assistant-config `
  --editable-root .\examples\sample_workspace\home-assistant-config-editable `
  --node-red-root .\examples\sample_workspace\home-assistant-node-red `
  --lovelace-root .\examples\sample_workspace\home-assistant-lovelace
```

## Environment

`ha_incident_bundle.py` reads:

- `HASS_HOST`
- `HASS_TOKEN`
- `HASS_WS_URL` or `HASS_SOCKET_URL`

Use `.env.example` as the template for a repo-local `.env`.

The workspace-owned sync deployment uses Home Assistant REST with `HASS_TOKEN`
and optional `HASS_HOST`. It resolves each setting from the process environment,
then the Windows User environment, then a gitignored toolkit `.env`. The
workspace tool defaults to deployment, in this order: publish editable config,
run the managed pull, run the managed backup, refresh the local backup, then
build local views. Use `-SkipHaDeployment` only for a repository-only run.

In the public toolkit, managed mode is **opt-in**: it activates only when
managed flags are passed or `config.json` supplies managed entity IDs. Without
those, `sync-ha-config.ps1` runs the repository-only explicit-path flow that
publishes sibling repos and regenerates local views.

## Companion tools

This toolkit does not include the full Lovelace sync utility. The companion
`home-assistant-lovelace` repo owns that workflow.

## Managed Home Assistant deployment (opt-in)

`sync-ha-config.ps1` has an opt-in **managed mode** that drives Home Assistant
REST to run the managed config pull, backup, and Node-RED Project pull scripts
by run ID, with bounded polling and bounded stderr capture. It activates when
any managed flag is passed or when `config.json` supplies managed entity IDs.
It targets **Home Assistant Supervisor installations running the Node-RED
add-on with Projects**; it is not universal Node-RED support.

See `docs/workflow-overview.md` for the staged protocol and
`examples/node_red_pull_flow/README.md` for the two-phase bootstrap that
installs the unattended Node-RED pull flow and registers its read-only GitHub
deploy key.

Key constraints captured by this design:

- **UI credentials are not inherited by Node-RED `exec` child processes.** The
  HTTPS token the Node-RED Projects UI uses is not visible to a shell started
  by an `exec` node, so unattended `git pull` over HTTPS fails with
  `could not read Username for 'https://github.com'`. A dedicated SSH deploy
  key stored under the add-on's persistent `/data` directory is the durable
  fix.
- **Project file pulling is separate from runtime activation.** A successful
  pull updates files on disk; a Deploy or add-on restart is required for the
  pulled flows to become active. `-RestartNodeRed` requests a restart but the
  flow owns the activation.
