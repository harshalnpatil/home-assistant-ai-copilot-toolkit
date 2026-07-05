#!/usr/bin/env python3
"""Spot-check a Node-RED flows export or split export for obvious problems."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


SKIP_TYPES = {"tab", "group", "junction"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Spot-check a Node-RED flows export or split export.")
    parser.add_argument("--root", default=".", help="Workspace root used for relative defaults.")
    parser.add_argument("-i", "--input", default="", help="Input file or folder. Default: <root>/output/flows-split")
    parser.add_argument("--max-items", type=int, default=20, help="Maximum items to print per section.")
    return parser.parse_args()


def resolve_input(root: Path, raw: str) -> Path:
    if raw:
        return Path(raw).expanduser().resolve()
    return (root / "output" / "flows-split").resolve()


def load_records(path: Path) -> list[dict[str, Any]]:
    if path.is_dir():
        records: list[dict[str, Any]] = []
        for child in sorted(path.glob("*.json")):
            payload = json.loads(child.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                records.extend(item for item in payload if isinstance(item, dict))
        return records
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()
    input_path = resolve_input(root, args.input)

    if not input_path.exists():
        print(f"ERROR: {input_path} not found.")
        return 1

    records = load_records(input_path)
    by_id = {record["id"]: record for record in records if record.get("id")}
    tabs = {record["id"]: record.get("label", record["id"]) for record in records if record.get("type") == "tab" and record.get("id")}

    broken_wires: list[tuple[str, str, str]] = []
    duplicate_ids: list[str] = []
    seen_ids: set[str] = set()
    for record in records:
        node_id = record.get("id")
        if node_id:
            if node_id in seen_ids:
                duplicate_ids.append(node_id)
            seen_ids.add(node_id)
        for port in record.get("wires") or []:
            for target in port or []:
                if target and target not in by_id:
                    broken_wires.append((str(node_id or "?"), record.get("name") or record.get("type") or "node", str(target)))

    stale_z = [record for record in records if record.get("z") and record.get("z") not in tabs and record.get("type") != "tab"]

    sources = defaultdict(list)
    for record in records:
        for port in record.get("wires") or []:
            for target in port or []:
                sources[target].append(record.get("id"))

    orphans = []
    for record in records:
        node_type = record.get("type")
        if node_type in SKIP_TYPES or node_type == "comment":
            continue
        has_out = bool(record.get("wires"))
        has_in = bool(record.get("id") and sources.get(record["id"]))
        if not has_out and not has_in:
            orphans.append(record)

    tab_counts = defaultdict(int)
    for record in records:
        if record.get("type") == "tab":
            continue
        tab_counts[str(record.get("z") or "global")] += 1

    print("=" * 60)
    print("NODE-RED FLOWS SPOT CHECK")
    print("=" * 60)
    print(f"Records: {len(records)}  Tabs: {len(tabs)}")
    print(f"[BROKEN WIRES] {len(broken_wires)}")
    for item in broken_wires[: args.max_items]:
        print(f"  {item}")
    print(f"[STALE Z] {len(stale_z)}")
    for item in stale_z[: args.max_items]:
        print(f"  {item.get('type')} {item.get('name', item.get('id'))} z={item.get('z')}")
    print(f"[ORPHANS] {len(orphans)}")
    for item in orphans[: args.max_items]:
        print(f"  {item.get('type')} {item.get('name', item.get('id'))}")
    print(f"[DUPLICATE IDS] {duplicate_ids[: args.max_items]}")
    print("[TAB COUNTS]")
    for label, count in sorted(tab_counts.items(), key=lambda item: (-item[1], item[0]))[: args.max_items]:
        print(f"  {label}: {count}")

    return 1 if broken_wires or stale_z or duplicate_ids else 0


if __name__ == "__main__":
    raise SystemExit(main())
