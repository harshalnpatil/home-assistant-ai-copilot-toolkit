#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a lightweight Home Assistant context index from explicit local inputs."
    )
    parser.add_argument("--root", default=".", help="Workspace root used for relative defaults.")
    parser.add_argument("--ha-config-root", default="", help="Path to a Home Assistant config export root.")
    parser.add_argument("--editable-root", default="", help="Path to an editable Home Assistant repo.")
    parser.add_argument("--node-red-root", default="", help="Path to a Node-RED repo or export folder.")
    parser.add_argument("--lovelace-root", default="", help="Path to a Lovelace repo or export folder.")
    parser.add_argument(
        "--output",
        default="",
        help="Output directory. Default: <root>/output/ha-index",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate into a temporary directory and compare with the current output.",
    )
    return parser.parse_args()


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_yaml(path: Path, data: Any) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=False), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: format_csv_value(row.get(key)) for key in fieldnames})


def format_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    return str(value)


def slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = "".join(ch if ch.isalnum() else "-" for ch in slug)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "item"


def resolve_optional_path(root: Path, raw: str) -> Path | None:
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def read_if_exists(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return read_json(path)
    except Exception:
        return None


def collect_ha_index(ha_root: Path | None) -> dict[str, Any]:
    if ha_root is None or not ha_root.exists():
        return {"root": None, "areas": [], "entities": [], "floors": [], "devices": []}

    storage = ha_root / ".storage"
    area_data = read_if_exists(storage / "core.area_registry") or {}
    device_data = read_if_exists(storage / "core.device_registry") or {}
    entity_data = read_if_exists(storage / "core.entity_registry") or {}
    floor_data = read_if_exists(storage / "core.floor_registry") or {}

    areas = {
        item["id"]: item
        for item in area_data.get("data", {}).get("areas", [])
        if isinstance(item, dict) and item.get("id")
    }
    devices = {
        item["id"]: item
        for item in device_data.get("data", {}).get("devices", [])
        if isinstance(item, dict) and item.get("id")
    }
    floors = {
        item["id"]: item.get("name") or item["id"]
        for item in floor_data.get("data", {}).get("floors", [])
        if isinstance(item, dict) and item.get("id")
    }
    entities = [
        item
        for item in entity_data.get("data", {}).get("entities", [])
        if isinstance(item, dict) and item.get("entity_id")
    ]

    area_rows: list[dict[str, Any]] = []
    for area_id, area in sorted(areas.items(), key=lambda item: (item[1].get("name") or item[0]).lower()):
        area_rows.append(
            {
                "area_id": area_id,
                "name": area.get("name") or area_id,
                "floor_id": area.get("floor_id"),
                "floor_name": floors.get(area.get("floor_id"), ""),
                "picture": area.get("picture"),
            }
        )

    entity_rows: list[dict[str, Any]] = []
    for entity in sorted(entities, key=lambda item: item["entity_id"]):
        area = areas.get(entity.get("area_id"), {})
        device = devices.get(entity.get("device_id"), {})
        area_name = area.get("name") or ""
        floor_name = floors.get(area.get("floor_id") or device.get("floor_id"), "")
        entity_rows.append(
            {
                "entity_id": entity.get("entity_id"),
                "domain": entity.get("entity_id", "").split(".", 1)[0],
                "name": entity.get("name") or entity.get("original_name") or entity.get("entity_id"),
                "area_id": entity.get("area_id"),
                "area_name": area_name,
                "floor_name": floor_name,
                "device_id": entity.get("device_id"),
                "platform": entity.get("platform"),
                "source": "home-assistant-config/.storage/core.entity_registry",
            }
        )

    device_rows: list[dict[str, Any]] = []
    for device_id, device in sorted(devices.items(), key=lambda item: (item[1].get("name") or item[0]).lower()):
        area = areas.get(device.get("area_id"), {})
        device_rows.append(
            {
                "device_id": device_id,
                "name": device.get("name_by_user") or device.get("name") or device_id,
                "area_name": area.get("name") or "",
                "manufacturer": device.get("manufacturer"),
                "model": device.get("model"),
            }
        )

    return {
        "root": str(ha_root),
        "areas": area_rows,
        "entities": entity_rows,
        "floors": [{"floor_id": floor_id, "name": name} for floor_id, name in sorted(floors.items(), key=lambda item: item[1].lower())],
        "devices": device_rows,
    }


def collect_yaml_items(root: Path | None, folder: str, kind: str) -> list[dict[str, Any]]:
    if root is None or not root.exists():
        return []
    folder_path = root / folder
    if not folder_path.exists():
        return []

    items: list[dict[str, Any]] = []
    for path in sorted(folder_path.glob("*.yaml")):
        try:
            data = read_yaml(path)
        except Exception:
            continue
        rel_path = str(path.relative_to(root))
        if kind == "automation" and isinstance(data, list):
            for index, entry in enumerate(data):
                if isinstance(entry, dict):
                    items.append(
                        {
                            "kind": kind,
                            "file": rel_path,
                            "item_index": index,
                            "name": entry.get("alias") or entry.get("name") or entry.get("id") or path.stem,
                            "entity_id": entry.get("id"),
                        }
                    )
        elif kind == "script" and isinstance(data, dict):
            for script_name, script in data.items():
                if isinstance(script, dict):
                    items.append(
                        {
                            "kind": kind,
                            "file": rel_path,
                            "name": script.get("alias") or script_name,
                            "entity_id": script_name,
                        }
                    )
        elif kind in {"helper", "input_boolean", "input_text"} and isinstance(data, dict):
            for entity_id, helper in data.items():
                if isinstance(helper, dict):
                    items.append(
                        {
                            "kind": kind,
                            "file": rel_path,
                            "name": helper.get("name") or entity_id,
                            "entity_id": entity_id,
                        }
                    )
    return items


def collect_editable_index(editable_root: Path | None) -> dict[str, Any]:
    if editable_root is None or not editable_root.exists():
        return {"root": None, "items": []}

    items: list[dict[str, Any]] = []
    items.extend(collect_yaml_items(editable_root, "automations", "automation"))
    items.extend(collect_yaml_items(editable_root, "scripts", "script"))
    items.extend(collect_yaml_items(editable_root, "input_booleans", "input_boolean"))
    items.extend(collect_yaml_items(editable_root, "input_texts", "input_text"))
    items.sort(key=lambda item: (item["kind"], item["name"].lower(), item["file"]))
    return {"root": str(editable_root), "items": items}


def summarize_flow_records(records: list[dict[str, Any]], source: str) -> dict[str, Any]:
    tabs = [item for item in records if item.get("type") == "tab"]
    tab_map = {str(tab.get("id")): tab for tab in tabs if tab.get("id")}
    nodes_by_tab: dict[str, list[dict[str, Any]]] = defaultdict(list)
    global_nodes: list[dict[str, Any]] = []
    for record in records:
        if record.get("type") == "tab":
            continue
        tab_id = record.get("z")
        if tab_id and tab_id in tab_map:
            nodes_by_tab[str(tab_id)].append(record)
        else:
            global_nodes.append(record)

    tab_summaries: list[dict[str, Any]] = []
    for tab_id, tab in sorted(tab_map.items(), key=lambda item: (item[1].get("label") or item[0]).lower()):
        nodes = nodes_by_tab.get(tab_id, [])
        type_counts = Counter(node.get("type") or "unknown" for node in nodes)
        tab_summaries.append(
            {
                "tab_id": tab_id,
                "label": tab.get("label") or tab_id,
                "node_count": len(nodes),
                "type_counts": dict(sorted(type_counts.items())),
                "source": source,
            }
        )

    return {
        "source": source,
        "tab_count": len(tab_summaries),
        "global_node_count": len(global_nodes),
        "tabs": tab_summaries,
    }


def collect_node_red_index(node_red_root: Path | None) -> dict[str, Any]:
    if node_red_root is None or not node_red_root.exists():
        return {"root": None, "flows": []}

    flows: list[dict[str, Any]] = []
    flows_json = node_red_root / "flows.json"
    if flows_json.exists():
        try:
            records = read_json(flows_json)
        except Exception:
            records = None
        if isinstance(records, list):
            flows.append(summarize_flow_records([item for item in records if isinstance(item, dict)], "flows.json"))
    split_root = node_red_root / "flows-split"
    if split_root.exists():
        for path in sorted(split_root.glob("*.json")):
            try:
                records = read_json(path)
            except Exception:
                continue
            if isinstance(records, list) and records:
                flows.append(summarize_flow_records([item for item in records if isinstance(item, dict)], str(path.relative_to(node_red_root))))
    flows.sort(key=lambda item: item["source"])
    return {"root": str(node_red_root), "flows": flows}


def collect_lovelace_index(lovelace_root: Path | None) -> dict[str, Any]:
    if lovelace_root is None or not lovelace_root.exists():
        return {"root": None, "dashboards": []}

    dashboards: list[dict[str, Any]] = []
    dashboard_paths = list(sorted(lovelace_root.glob("lovelace-live/*/dashboard.json")))
    dashboard_paths.extend(sorted(lovelace_root.glob("view*.json")))
    for path in dashboard_paths:
        try:
            data = read_json(path)
        except Exception:
            continue
        if isinstance(data, dict):
            view_count = len(data.get("views", [])) if isinstance(data.get("views"), list) else 0
            dashboards.append(
                {
                    "path": str(path.relative_to(lovelace_root)),
                    "title": data.get("title") or data.get("name") or path.stem,
                    "view_count": view_count,
                    "has_strategy": isinstance(data.get("strategy"), dict),
                }
            )
    dashboards.sort(key=lambda item: item["path"])
    return {"root": str(lovelace_root), "dashboards": dashboards}


def latest_source_timestamp(paths: list[Path | None]) -> str:
    mtimes: list[float] = []
    for path in paths:
        if path is None:
            continue
        if path.is_file():
            mtimes.append(path.stat().st_mtime)
        elif path.is_dir():
            for candidate in path.rglob("*"):
                if candidate.is_file():
                    mtimes.append(candidate.stat().st_mtime)
    if not mtimes:
        return iso_z(now_utc())
    return datetime.fromtimestamp(max(mtimes), tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_index(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).expanduser().resolve()
    ha_root = resolve_optional_path(root, args.ha_config_root)
    editable_root = resolve_optional_path(root, args.editable_root)
    node_red_root = resolve_optional_path(root, args.node_red_root)
    lovelace_root = resolve_optional_path(root, args.lovelace_root)

    index = {
        "generated_at": iso_z(now_utc()),
        "source_root": str(root),
        "inputs": {
            "ha_config_root": str(ha_root) if ha_root else None,
            "editable_root": str(editable_root) if editable_root else None,
            "node_red_root": str(node_red_root) if node_red_root else None,
            "lovelace_root": str(lovelace_root) if lovelace_root else None,
        },
        "ha": collect_ha_index(ha_root),
        "editable": collect_editable_index(editable_root),
        "node_red": collect_node_red_index(node_red_root),
        "lovelace": collect_lovelace_index(lovelace_root),
    }

    index["summary"] = {
        "areas": len(index["ha"]["areas"]),
        "entities": len(index["ha"]["entities"]),
        "editable_items": len(index["editable"]["items"]),
        "node_red_tabs": sum(flow["tab_count"] for flow in index["node_red"]["flows"]),
        "lovelace_dashboards": len(index["lovelace"]["dashboards"]),
    }
    index["generated_from"] = latest_source_timestamp([ha_root, editable_root, node_red_root, lovelace_root])
    return index


def build_outputs(index: dict[str, Any], output_root: Path) -> dict[str, Path]:
    output_root.mkdir(parents=True, exist_ok=True)

    write_yaml(
        output_root / "index.yaml",
        {
            "generated_at": index["generated_at"],
            "generated_from": index["generated_from"],
            "source_root": index["source_root"],
            "inputs": index["inputs"],
            "summary": index["summary"],
        },
    )
    write_yaml(output_root / "ha.yaml", index["ha"])
    write_yaml(output_root / "editable.yaml", index["editable"])
    write_yaml(output_root / "node-red.yaml", index["node_red"])
    write_yaml(output_root / "lovelace.yaml", index["lovelace"])
    write_csv(
        output_root / "entities.csv",
        index["ha"]["entities"],
        ["entity_id", "domain", "name", "area_id", "area_name", "floor_name", "device_id", "platform", "source"],
    )
    write_text_readme(output_root)

    return {
        "index.yaml": output_root / "index.yaml",
        "ha.yaml": output_root / "ha.yaml",
        "editable.yaml": output_root / "editable.yaml",
        "node-red.yaml": output_root / "node-red.yaml",
        "lovelace.yaml": output_root / "lovelace.yaml",
        "entities.csv": output_root / "entities.csv",
        "README.md": output_root / "README.md",
    }


def write_text_readme(output_root: Path) -> None:
    readme = """# HA index

Generated context for AI lookup and diffing.

Files:

- `index.yaml`, generation metadata and counts
- `ha.yaml`, areas, floors, devices, and entities from an HA export
- `editable.yaml`, automations, scripts, and helpers from an editable repo
- `node-red.yaml`, Node-RED tab summaries
- `lovelace.yaml`, dashboard summaries
- `entities.csv`, flat entity lookup table

Regenerate with:

```powershell
python build_ha_index.py --root . --output output/ha-index
```
"""
    (output_root / "README.md").write_text(readme, encoding="utf-8")


def compare_outputs(expected: dict[str, Path], actual_root: Path) -> list[str]:
    problems: list[str] = []
    for name, generated_path in expected.items():
        actual_path = actual_root / name
        if not actual_path.exists():
            problems.append(f"missing output: {name}")
            continue
        if name == "index.yaml":
            generated_payload = yaml.safe_load(generated_path.read_text(encoding="utf-8")) or {}
            actual_payload = yaml.safe_load(actual_path.read_text(encoding="utf-8")) or {}
            for payload in (generated_payload, actual_payload):
                if isinstance(payload, dict):
                    payload.pop("generated_at", None)
                    payload.pop("generated_from", None)
            if generated_payload != actual_payload:
                problems.append("content differs: index.yaml")
            continue
        generated_text = generated_path.read_text(encoding="utf-8")
        actual_text = actual_path.read_text(encoding="utf-8")
        if generated_text != actual_text:
            problems.append(f"content differs: {name}")
    return problems


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()
    output_root = Path(args.output).expanduser().resolve() if args.output else (root / "output" / "ha-index")

    if args.check:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            generated = build_outputs(build_index(args), temp_root)
            problems = compare_outputs(generated, output_root)
            if problems:
                for problem in problems:
                    print(f"ERROR: {problem}")
                return 1
            print(f"OK: {output_root} matches regenerated output")
            return 0

    index = build_index(args)
    outputs = build_outputs(index, output_root)
    print(f"Built {len(outputs)} files in {output_root}")
    print(f"Summary: {index['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
