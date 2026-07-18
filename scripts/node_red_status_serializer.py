#!/usr/bin/env python3
"""Serialize Node-RED pull-stage status strings for Home Assistant input_text.

Node-RED `exec` nodes emit return objects like `{code: 0}`. Home Assistant's
`input_text.set_value` rejects non-string payloads, so the managed pull flow
must serialize the result into a pipe-delimited string:

    <run_id>|<stage>|<status>|<code>

This module is the Python counterpart of the `Serialize status string`
function node in `examples/node_red_pull_flow/template.json`. Keeping the
logic in Python lets the toolkit smoke-test the contract without running
Node-RED.

CLI:
    python node_red_status_serializer.py --run-id abc --stage node_red --status success --code 0
    # abc|node_red|success|0
"""
from __future__ import annotations

import argparse
from typing import Literal

Stage = Literal["pull", "backup", "node_red"]
Status = Literal["pending", "running", "success", "failure"]


def serialize_status(run_id: str, stage: Stage, status: Status, code: int) -> str:
    if not run_id:
        raise ValueError("run_id must not be empty")
    if stage not in ("pull", "backup", "node_red"):
        raise ValueError(f"invalid stage: {stage}")
    if status not in ("pending", "running", "success", "failure"):
        raise ValueError(f"invalid status: {status}")
    if not isinstance(code, int) or isinstance(code, bool):
        raise ValueError(f"code must be an int, got {type(code).__name__}")
    return f"{run_id}|{stage}|{status}|{code}"


def from_exec_payload(run_id: str, stage: Stage, payload: object) -> str:
    """Mirror the function-node logic that inspects an exec return object."""
    if isinstance(payload, dict) and "code" in payload:
        code = int(payload["code"])
    else:
        code = int(payload)
    status: Status = "success" if code == 0 else "failure"
    return serialize_status(run_id, stage, status, code)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serialize a Node-RED pull status string.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--stage", required=True, choices=["pull", "backup", "node_red"])
    parser.add_argument("--status", required=True, choices=["pending", "running", "success", "failure"])
    parser.add_argument("--code", required=True, type=int)
    args = parser.parse_args()
    print(serialize_status(args.run_id, args.stage, args.status, args.code))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
