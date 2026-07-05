#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test the public toolkit repository.")
    parser.add_argument("--root", default=str(ROOT), help="Workspace root.")
    parser.add_argument("--ha-config-root", default="", help="Optional Home Assistant config export root.")
    parser.add_argument("--editable-root", default="", help="Optional editable Home Assistant repo root.")
    parser.add_argument("--node-red-root", default="", help="Optional Node-RED repo root.")
    parser.add_argument("--lovelace-root", default="", help="Optional Lovelace repo root.")
    parser.add_argument(
        "--index-output",
        default="",
        help="Index output directory. Default: <root>/output/ha-index",
    )
    parser.add_argument("--skip-help", action="store_true", help="Skip script --help checks.")
    return parser.parse_args()


def resolve_optional_path(root: Path, raw: str) -> Path | None:
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def run_help(script: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    ok = proc.returncode == 0
    text = (proc.stdout or proc.stderr).strip()
    return ok, text


def validate_ha_config(root: Path | None) -> list[str]:
    problems: list[str] = []
    if root is None or not root.exists():
        return problems
    storage = root / ".storage"
    for name in ("core.area_registry", "core.device_registry", "core.entity_registry", "core.floor_registry"):
        path = storage / name
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            problems.append(f"{path}: {exc}")
            continue
        if not isinstance(payload, dict):
            problems.append(f"{path}: expected JSON object")
    return problems


def validate_editable_repo(root: Path | None) -> list[str]:
    problems: list[str] = []
    if root is None or not root.exists():
        return problems

    checks = [
        ("automations", list),
        ("scripts", dict),
        ("input_booleans", dict),
        ("input_texts", dict),
    ]
    for folder, expected_type in checks:
        folder_path = root / folder
        if not folder_path.exists():
            continue
        for path in sorted(folder_path.glob("*.yaml")):
            try:
                payload = load_yaml(path)
            except Exception as exc:
                problems.append(f"{path}: {exc}")
                continue
            if not isinstance(payload, expected_type):
                problems.append(f"{path}: expected {expected_type.__name__}")
    return problems


def validate_node_red_repo(root: Path | None, toolkit_root: Path) -> list[str]:
    problems: list[str] = []
    if root is None or not root.exists():
        return problems

    flows = root / "flows.json"
    if not flows.exists():
        return problems

    split_script = toolkit_root / "scripts" / "split_flows.py"
    spot_script = toolkit_root / "scripts" / "spot_check_flows.py"
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir)
        split_proc = subprocess.run(
            [sys.executable, str(split_script), "--root", str(root), "--input", str(flows), "--output", str(output_dir)],
            capture_output=True,
            text=True,
            cwd=str(toolkit_root),
        )
        if split_proc.returncode != 0:
            problems.append(f"split_flows.py failed: {split_proc.stderr.strip() or split_proc.stdout.strip()}")
            return problems

        spot_proc = subprocess.run(
            [sys.executable, str(spot_script), "--root", str(root), "--input", str(output_dir)],
            capture_output=True,
            text=True,
            cwd=str(toolkit_root),
        )
        if spot_proc.returncode != 0:
            problems.append(f"spot_check_flows.py failed: {spot_proc.stderr.strip() or spot_proc.stdout.strip()}")
    return problems


def validate_lovelace_repo(root: Path | None) -> list[str]:
    problems: list[str] = []
    if root is None or not root.exists():
        return problems
    for path in sorted(root.glob("lovelace-live/*/dashboard.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            problems.append(f"{path}: {exc}")
            continue
        if not isinstance(payload, dict):
            problems.append(f"{path}: expected JSON object")
    return problems


def validate_index(
    root: Path,
    index_output: Path,
    ha_root: Path | None,
    editable_root: Path | None,
    node_red_root: Path | None,
    lovelace_root: Path | None,
) -> list[str]:
    problems: list[str] = []
    index_script = root / "build_ha_index.py"
    if not index_script.exists():
        return problems
    cmd = [sys.executable, str(index_script), "--root", str(root)]
    if ha_root is not None:
        cmd.extend(["--ha-config-root", str(ha_root)])
    if editable_root is not None:
        cmd.extend(["--editable-root", str(editable_root)])
    if node_red_root is not None:
        cmd.extend(["--node-red-root", str(node_red_root)])
    if lovelace_root is not None:
        cmd.extend(["--lovelace-root", str(lovelace_root)])

    if index_output.exists():
        cmd.extend(["--output", str(index_output), "--check"])
    else:
        with tempfile.TemporaryDirectory() as temp_dir:
            cmd.extend(["--output", temp_dir])
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(root))
            if proc.returncode != 0:
                problems.append(proc.stdout.strip() or proc.stderr.strip() or "build_ha_index.py failed")
            return problems

    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(root))
    if proc.returncode != 0:
        problems.append(proc.stdout.strip() or proc.stderr.strip() or "build_ha_index.py --check failed")
    return problems


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()
    ha_root = resolve_optional_path(root, args.ha_config_root)
    editable_root = resolve_optional_path(root, args.editable_root)
    node_red_root = resolve_optional_path(root, args.node_red_root)
    lovelace_root = resolve_optional_path(root, args.lovelace_root)
    index_output = resolve_optional_path(root, args.index_output) if args.index_output else root / "output" / "ha-index"

    problems: list[str] = []
    if not args.skip_help:
        for script in [
            root / "build_ha_index.py",
            root / "ha_incident_bundle.py",
            root / "verify_workspace.py",
            root / "scripts" / "split_flows.py",
            root / "scripts" / "spot_check_flows.py",
        ]:
            if not script.exists():
                continue
            ok, output = run_help(script)
            if not ok:
                problems.append(f"{script}: --help failed")
            elif not output:
                problems.append(f"{script}: --help produced no output")

    problems.extend(validate_ha_config(ha_root))
    problems.extend(validate_editable_repo(editable_root))
    problems.extend(validate_node_red_repo(node_red_root, root))
    problems.extend(validate_lovelace_repo(lovelace_root))
    problems.extend(validate_index(root, index_output, ha_root, editable_root, node_red_root, lovelace_root))

    if problems:
        print("FAILURES:")
        for item in problems:
            print(f"- {item}")
        return 1

    print("OK: toolkit smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
