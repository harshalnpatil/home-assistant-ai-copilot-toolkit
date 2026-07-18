#!/usr/bin/env python3
"""Fill the redacted Node-RED pull-flow template from a config block.

Produces an importable Node-RED flow JSON with all placeholders substituted.
The private deploy key is generated at runtime inside the Node-RED add-on; no
key, token, host address, or HA server ID is embedded by this script beyond
the explicit values you provide.

Usage:
  python generate_flow.py \
    --template template.json \
    --output pull_flow.json \
    --repo-ssh-url git@github.com:owner/example-node-red-flows.git \
    --repo-branch main \
    --ha-server-id <nodered-ha-server-config-id> \
    --pull-script-entity-id script.agent_node_red_pull \
    --status-entity-id input_text.agent_config_sync_status \
    --diagnostic-entity-id input_text.agent_node_red_deploy_detail \
    [--restart-addon-slug a0d7b954_nodered]

Alternatively, pass --config <config.json> to read a "nodeRedPullFlow" block
with keys: repoSshUrl, repoBranch, haServerId, pullScriptEntityId,
statusEntityId, diagnosticEntityId, restartAddonSlug (optional),
restartOnSuccess (optional bool; if true and restartAddonSlug is set, the
restart branch stays enabled; otherwise the restart branch is removed).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PLACEHOLDERS = {
    "REPO_SSH_URL": None,
    "REPO_BRANCH": None,
    "HA_SERVER_ID": None,
    "PULL_SCRIPT_ENTITY_ID": None,
    "STATUS_ENTITY_ID": None,
    "DIAGNOSTIC_ENTITY_ID": None,
}
RESTART_NODE_IDS = {"restart_branch", "restart_addon"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--template", default=str(Path(__file__).parent / "template.json"))
    parser.add_argument("--output", default=str(Path(__file__).parent / "pull_flow.json"))
    parser.add_argument("--config", default="", help="config.json with a 'nodeRedPullFlow' block")
    parser.add_argument("--repo-ssh-url", default="")
    parser.add_argument("--repo-branch", default="")
    parser.add_argument("--ha-server-id", default="")
    parser.add_argument("--pull-script-entity-id", default="")
    parser.add_argument("--status-entity-id", default="")
    parser.add_argument("--diagnostic-entity-id", default="")
    parser.add_argument("--restart-addon-slug", default="")
    parser.add_argument("--restart-on-success", action="store_true")
    return parser.parse_args()


def load_config_block(path: str) -> dict:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("nodeRedPullFlow", {}) or {}


def resolve_values(args: argparse.Namespace) -> dict:
    cfg = load_config_block(args.config)
    get = lambda cli, key: cli or cfg.get(key) or ""
    values = {
        "REPO_SSH_URL": get(args.repo_ssh_url, "repoSshUrl"),
        "REPO_BRANCH": get(args.repo_branch, "repoBranch"),
        "HA_SERVER_ID": get(args.ha_server_id, "haServerId"),
        "PULL_SCRIPT_ENTITY_ID": get(args.pull_script_entity_id, "pullScriptEntityId"),
        "STATUS_ENTITY_ID": get(args.status_entity_id, "statusEntityId"),
        "DIAGNOSTIC_ENTITY_ID": get(args.diagnostic_entity_id, "diagnosticEntityId"),
    }
    missing = [k for k, v in values.items() if not v]
    if missing:
        raise SystemExit(f"Missing required values: {', '.join(missing)}. Pass them as flags or via --config.")
    restart_slug = get(args.restart_addon_slug, "restartAddonSlug")
    restart_on = args.restart_on_success or bool(cfg.get("restartOnSuccess", False))
    return values, restart_slug, restart_on


def fill_template(template_text: str, values: dict) -> str:
    filled = template_text
    for key, value in values.items():
        filled = filled.replace("{{" + key + "}}", value)
    leftover = [p for p in PLACEHOLDERS if "{{" + p + "}}" in filled]
    if leftover:
        raise SystemExit(f"Unfilled placeholders remain: {', '.join(leftover)}")
    return filled


def apply_restart_branch(flow: list, restart_slug: str, restart_on: bool) -> list:
    if restart_on and restart_slug:
        for node in flow:
            if node.get("id") == "restart_addon":
                data = node.get("data", "")
                if "addon" in data:
                    node["data"] = '{ "addon": "' + restart_slug + '" }'
        return flow
    return [node for node in flow if node.get("id") not in RESTART_NODE_IDS]


def main() -> int:
    args = parse_args()
    values, restart_slug, restart_on = resolve_values(args)
    template_text = Path(args.template).read_text(encoding="utf-8")
    filled_text = fill_template(template_text, values)
    flow = json.loads(filled_text)
    flow = apply_restart_branch(flow, restart_slug, restart_on)
    # Re-serialize so the final file has no leftover placeholders.
    final_text = json.dumps(flow, indent=2, ensure_ascii=False)
    for placeholder in PLACEHOLDERS:
        token = "{{" + placeholder + "}}"
        if token in final_text:
            raise SystemExit(f"Placeholder leaked into output: {token}")
    Path(args.output).write_text(final_text + "\n", encoding="utf-8")
    print(f"Wrote {args.output} ({len(flow)} nodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
