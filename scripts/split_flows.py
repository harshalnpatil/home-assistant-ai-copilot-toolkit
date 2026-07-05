#!/usr/bin/env python3
"""Split a Node-RED flows export into per-tab JSON files."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split a Node-RED flows export into per-tab JSON files.")
    parser.add_argument("--root", default=".", help="Workspace root used for relative defaults.")
    parser.add_argument("-i", "--input", default="", help="Input flows.json path. Default: <root>/flows.json")
    parser.add_argument(
        "-o",
        "--output",
        default="",
        help="Output directory for split files. Default: <root>/output/flows-split",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed output per file.")
    return parser.parse_args()


def slugify(label: str) -> str:
    slug = label.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug or "unnamed"


def unique_filename(used: set[str], stem: str) -> str:
    candidate = stem
    counter = 2
    while candidate in used:
        candidate = f"{stem}_{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def resolve_path(root: Path, raw: str, default_name: str) -> Path:
    if raw:
        return Path(raw).expanduser().resolve()
    return (root / default_name).resolve()


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()
    input_file = resolve_path(root, args.input, "flows.json")
    output_dir = resolve_path(root, args.output, "output/flows-split")

    if not input_file.exists():
        print(f"ERROR: {input_file} not found.", file=sys.stderr)
        return 1

    with input_file.open(encoding="utf-8") as handle:
        records = json.load(handle)
    if not isinstance(records, list):
        print(f"ERROR: {input_file} is not a JSON array.", file=sys.stderr)
        return 1

    tabs: dict[str, dict] = {}
    for record in records:
        if isinstance(record, dict) and record.get("type") == "tab" and record.get("id"):
            tabs[str(record["id"])] = record

    tab_nodes: dict[str, list[dict]] = defaultdict(list)
    globals_list: list[dict] = []
    for record in records:
        if not isinstance(record, dict) or record.get("type") == "tab":
            continue
        tab_id = record.get("z")
        if tab_id and tab_id in tabs:
            tab_nodes[str(tab_id)].append(record)
        else:
            globals_list.append(record)

    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("*.json"):
        old.unlink()

    used_names: set[str] = set()
    files_written = 0
    written_records = 0

    for tab_id, tab in sorted(tabs.items(), key=lambda item: (item[1].get("label") or item[0]).lower()):
        label = str(tab.get("label") or "unnamed")
        slug = unique_filename(used_names, slugify(label))
        nodes = tab_nodes.get(tab_id, [])
        out_path = output_dir / f"{slug}.json"
        with out_path.open("w", encoding="utf-8") as handle:
            json.dump([tab] + nodes, handle, indent=2, ensure_ascii=False)
        files_written += 1
        written_records += 1 + len(nodes)
        if args.verbose:
            print(f"{out_path.name:<40} tab + {len(nodes):>4} nodes")

    if globals_list:
        out_path = output_dir / f"{unique_filename(used_names, 'globals')}.json"
        with out_path.open("w", encoding="utf-8") as handle:
            json.dump(globals_list, handle, indent=2, ensure_ascii=False)
        files_written += 1
        written_records += len(globals_list)
        if args.verbose:
            print(f"{out_path.name:<40} globals = {len(globals_list):>4} records")

    print(f"Done: {files_written} files written to {output_dir}")
    if args.verbose:
        print(f"Original records: {len(records)}")
        print(f"Written records : {written_records}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
