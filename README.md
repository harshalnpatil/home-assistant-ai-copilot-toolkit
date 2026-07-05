# Home Assistant AI Copilot Toolkit

This repository is a small public toolkit for AI-assisted Home Assistant work.
It is not a full agent product and not a mirror of any private workspace.

It publishes:

- Generic utility scripts
- Public-safe wrappers
- Minimal documentation
- Redacted fixtures for smoke testing

It excludes:

- Live Home Assistant config
- Private `.env` files
- Generated indexes and outputs
- Backups and pulled dashboard data
- House-specific naming unless it is clearly marked as an example

## Contents

- `build_ha_index.py`, build a lightweight context index from explicit inputs
- `ha_incident_bundle.py`, build a REST-based incident bundle
- `verify_workspace.py`, run smoke tests against the toolkit and optional inputs
- `verify-workspace.ps1`, PowerShell wrapper for the verifier
- `sync-ha-config.ps1`, PowerShell wrapper for sibling repo syncs
- `scripts/split_flows.py`, split a Node-RED export by tab
- `scripts/spot_check_flows.py`, inspect a Node-RED export for obvious issues
- `docs/`, public usage notes
- `examples/sample_workspace/`, redacted fixtures

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

## Companion tools

This toolkit does not include the full Lovelace sync utility. The companion
`home-assistant-lovelace` repo owns that workflow.
