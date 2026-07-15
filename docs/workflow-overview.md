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
either explicit paths or a config file that names them. In the private
workspace, the corresponding sync tool deploys by default through the Home
Assistant REST API: publish editable config, managed pull, managed backup,
local backup refresh, then generated views. `-SkipHaDeployment` is the explicit
repository-only opt-out. REST authentication uses `HASS_TOKEN`; `HASS_HOST`
defaults to `http://homeassistant.local:8123`.
