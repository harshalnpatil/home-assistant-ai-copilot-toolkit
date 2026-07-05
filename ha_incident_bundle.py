#!/usr/bin/env python3
"""Build a Home Assistant incident debug bundle from REST evidence.

This tool is API-first. It uses the Home Assistant REST API for current
states, history, and logbook evidence, and does a single hop of graph
expansion from the best matching automation, script, or Node-RED flow.

Outputs:
- incident_bundle.json
- incident_summary.md

The supported path in v1 is direct REST. MCP can be added later if its runtime
is reliably available.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

import yaml


ROOT = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = ROOT / ".env"
DEFAULT_OUTPUT_ROOT = ROOT / "output" / "incident-bundles"
HA_EDITABLE_ROOT: Path | None = None
HA_EXPORT_ROOT: Path | None = None
NR_ROOT: Path | None = None
NR_SPLIT_ROOT: Path | None = None

ENTITY_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]+\.[a-zA-Z0-9_]+\b")
TOKEN_RE = re.compile(r"[a-z0-9]+")
COMMON_STATE_ATTRS = {
    "friendly_name",
    "icon",
    "entity_id",
    "device_class",
    "state_class",
    "last_triggered",
    "current",
    "temperature",
    "brightness",
    "percentage",
    "volume_level",
    "hvac_mode",
    "preset_mode",
    "latched",
    "source",
    "mode",
}
SECTION_NAMES = {"trigger", "condition", "action", "sequence"}
ALLOWED_ENTITY_DOMAINS = {
    "alarm_control_panel",
    "automation",
    "binary_sensor",
    "button",
    "calendar",
    "camera",
    "climate",
    "cloud",
    "cover",
    "counter",
    "date",
    "datetime",
    "device_tracker",
    "event",
    "fan",
    "group",
    "humidifier",
    "input_boolean",
    "input_button",
    "input_datetime",
    "input_number",
    "input_select",
    "input_text",
    "light",
    "lock",
    "media_player",
    "number",
    "notify",
    "person",
    "scene",
    "script",
    "select",
    "sensor",
    "sun",
    "switch",
    "text",
    "timer",
    "todo",
    "tts",
    "vacuum",
    "valve",
    "weather",
    "zone",
}
BLOCKED_ENTITY_SUFFIXES = {
    "includes",
    "payload",
    "status",
    "state",
    "toggle",
    "turn_off",
    "turn_on",
}
STOPWORDS = {
    "a",
    "an",
    "and",
    "did",
    "do",
    "does",
    "for",
    "in",
    "is",
    "it",
    "light",
    "lights",
    "not",
    "of",
    "off",
    "on",
    "room",
    "the",
    "to",
    "turn",
    "was",
}

ROOM_ALIASES = {
    "living room": {"living room", "family room", "lounge"},
    "bedroom": {"bedroom", "guest bedroom", "kids bedroom", "master bedroom"},
    "kitchen": {"kitchen"},
    "bathroom": {"bathroom", "ensuite"},
    "hallway": {"hallway", "corridor", "landing"},
    "office": {"office", "study"},
}


@dataclass
class Candidate:
    kind: str
    source_id: str
    name: str
    source: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    entity_refs: set[str] = field(default_factory=set)
    trigger_entities: set[str] = field(default_factory=set)
    condition_entities: set[str] = field(default_factory=set)
    action_entities: set[str] = field(default_factory=set)
    helper_entities: set[str] = field(default_factory=set)
    script_hops: set[str] = field(default_factory=set)
    node_ids: set[str] = field(default_factory=set)
    matched_nodes: list[dict[str, Any]] = field(default_factory=list)


def slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_") or "incident"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_dt(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_env(env_file: Path | None = None) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_file is not None:
        values.update(parse_env_file(env_file))
    for key in ("HASS_HOST", "HASS_TOKEN", "HASS_WS_URL", "HASS_SOCKET_URL"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def infer_hass_host(env: dict[str, str]) -> str:
    host = env.get("HASS_HOST")
    if host:
        return host.rstrip("/")
    ws_url = env.get("HASS_WS_URL") or env.get("HASS_SOCKET_URL")
    if ws_url:
        parsed = urlparse(ws_url)
        if parsed.scheme in {"ws", "wss"} and parsed.netloc:
            scheme = "https" if parsed.scheme == "wss" else "http"
            return urlunparse((scheme, parsed.netloc, "", "", "", "")).rstrip("/")
    return "http://homeassistant.local:8123"


def http_json(
    method: str,
    url: str,
    token: str,
    body: Any | None = None,
    timeout: int = 30,
) -> Any:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url, data=payload, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"{method} {url} failed: {exc.code} {exc.reason} {detail}".strip()) from exc
    except URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc.reason}") from exc
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def get_states(base_url: str, token: str) -> list[dict[str, Any]]:
    return http_json("GET", f"{base_url}/api/states", token)


def get_state(base_url: str, token: str, entity_id: str) -> dict[str, Any] | None:
    try:
        return http_json("GET", f"{base_url}/api/states/{entity_id}", token)
    except RuntimeError as exc:
        if "404" in str(exc):
            return None
        raise


def get_history(
    base_url: str,
    token: str,
    start: datetime,
    end: datetime,
    entity_ids: list[str],
    minimal_response: bool = True,
    significant_changes_only: bool = True,
    chunk_size: int = 25,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    if not entity_ids:
        return results
    start_iso = iso_z(start)
    start_path = quote(start_iso, safe="")
    for index in range(0, len(entity_ids), chunk_size):
        chunk = entity_ids[index : index + chunk_size]
        query = urlencode(
            {
                "filter_entity_id": ",".join(chunk),
                "end_time": iso_z(end),
                "minimal_response": str(bool(minimal_response)).lower(),
                "significant_changes_only": str(bool(significant_changes_only)).lower(),
            }
        )
        url = f"{base_url}/api/history/period/{start_path}?{query}"
        payload = http_json("GET", url, token)
        if not isinstance(payload, list):
            continue
        if len(chunk) == 1 and payload and isinstance(payload[0], list):
            results[chunk[0]] = payload[0]
            continue
        if len(payload) == len(chunk):
            for entity_id, item in zip(chunk, payload, strict=False):
                results[entity_id] = item
        else:
            for item in payload:
                if isinstance(item, list) and item:
                    entity_id = item[0].get("entity_id") if isinstance(item[0], dict) else None
                    if entity_id:
                        results[entity_id] = item
    return results


def get_logbook(
    base_url: str,
    token: str,
    start: datetime,
    end: datetime,
    entity_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    results: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[Any, ...]] = set()
    start_path = quote(iso_z(start), safe="")
    query_base = urlencode({"end_time": iso_z(end)})
    for entity_id in entity_ids:
        url = f"{base_url}/api/logbook/{start_path}?entity={quote(entity_id, safe='')}&{query_base}"
        payload = http_json("GET", url, token)
        if not isinstance(payload, list):
            continue
        filtered: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            key = (
                item.get("when"),
                item.get("entity_id"),
                item.get("state"),
                item.get("message"),
                item.get("name"),
            )
            if key in seen:
                continue
            seen.add(key)
            filtered.append(item)
        if filtered:
            results[entity_id] = filtered
    return results


def token_list(text: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(text.lower()) if token}


def meaningful_tokens(text: str) -> set[str]:
    return {token for token in token_list(text) if token not in STOPWORDS and len(token) > 2}


def entity_tokens(entity_id: str) -> set[str]:
    parts = set()
    parts.update(token_list(entity_id.replace(".", " ")))
    parts.update(token_list(entity_id.replace("_", " ")))
    return parts


def text_matches(text: str, needles: Iterable[str]) -> bool:
    haystack = text.lower()
    return any(needle in haystack for needle in needles)


def extract_entities(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        for match in ENTITY_RE.findall(value):
            entity_id = match.lower()
            domain, _, object_id = entity_id.partition(".")
            if domain not in ALLOWED_ENTITY_DOMAINS:
                continue
            if object_id in BLOCKED_ENTITY_SUFFIXES:
                continue
            found.add(entity_id)
        return found
    if isinstance(value, list):
        for item in value:
            found.update(extract_entities(item))
        return found
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"device_id", "area_id", "label_id"}:
                continue
            found.update(extract_entities(child))
        return found
    return found


def selected_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for key in COMMON_STATE_ATTRS:
        if key in attributes:
            selected[key] = attributes[key]
    for key, value in attributes.items():
        if key.startswith("last_") and key not in selected:
            selected[key] = value
    return selected


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def iter_yaml_files() -> list[Path]:
    paths: list[Path] = []
    for root in (HA_EDITABLE_ROOT, HA_EXPORT_ROOT):
        if root is not None and root.exists():
            paths.extend(sorted(root.rglob("*.yaml")))
            paths.extend(sorted(root.rglob("*.yml")))
    return paths


def score_text(name: str, hint_tokens: set[str], room_aliases: set[str]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    text = name.lower()
    if hint_tokens:
        overlap = [token for token in hint_tokens if token in text]
        if overlap:
            score += 4.0 * len(overlap)
            reasons.append(f"hint tokens in name: {', '.join(sorted(set(overlap)))}")
    if room_aliases and any(alias in text for alias in room_aliases):
        score += 4.0
        reasons.append("room alias matched name")
    return score, reasons


def classify_section(path: list[str]) -> str:
    for part in path:
        if part in SECTION_NAMES:
            return part
    return "action"


def build_automation_candidate(path: Path, index: int, item: dict[str, Any], hint_tokens: set[str], room_aliases: set[str], target_entities: set[str]) -> Candidate:
    alias = str(item.get("alias") or item.get("name") or f"automation_{index}")
    source_id = f"{path}#{index}"
    candidate = Candidate(kind="ha_automation", source_id=source_id, name=alias, source=str(path))
    candidate.entity_refs.update(extract_entities(item))
    for section_name in ("trigger", "condition", "action", "sequence"):
        section = item.get(section_name)
        if section is None:
            continue
        refs = extract_entities(section)
        candidate.entity_refs.update(refs)
        if section_name == "trigger":
            candidate.trigger_entities.update(refs)
        elif section_name == "condition":
            candidate.condition_entities.update(refs)
        else:
            candidate.action_entities.update(refs)
        candidate.script_hops.update(ref for ref in refs if ref.startswith("script."))
    if item.get("id"):
        candidate.reasons.append(f"yaml id: {item['id']}")
    score, reasons = score_text(alias, hint_tokens, room_aliases)
    candidate.score += score
    candidate.reasons.extend(reasons)
    for ref in sorted(candidate.entity_refs):
        if ref in target_entities:
            candidate.score += 40
            candidate.reasons.append(f"exact entity match: {ref}")
    if candidate.script_hops:
        candidate.score += 3
        candidate.reasons.append("calls script entities")
    return candidate


def build_script_candidate(path: Path, script_name: str, script: dict[str, Any], hint_tokens: set[str], room_aliases: set[str], target_entities: set[str]) -> Candidate:
    alias = str(script.get("alias") or script_name)
    source_id = f"{path}#{script_name}"
    candidate = Candidate(kind="ha_script", source_id=source_id, name=alias, source=str(path))
    candidate.entity_refs.update(extract_entities(script))
    sequence = script.get("sequence", [])
    candidate.action_entities.update(extract_entities(sequence))
    candidate.helper_entities.update(ref for ref in candidate.entity_refs if ref.startswith(("input_boolean.", "input_text.", "timer.", "scene.", "sensor.", "switch.")))
    candidate.script_hops.update(ref for ref in candidate.entity_refs if ref.startswith("script."))
    score, reasons = score_text(alias, hint_tokens, room_aliases)
    candidate.score += score
    candidate.reasons.extend(reasons)
    for ref in sorted(candidate.entity_refs):
        if ref in target_entities:
            candidate.score += 40
            candidate.reasons.append(f"exact entity match: {ref}")
    if candidate.script_hops:
        candidate.score += 2
        candidate.reasons.append("references script entities")
    return candidate


def build_helper_candidate(path: Path, entity_id: str, helper: dict[str, Any], hint_tokens: set[str], room_aliases: set[str], target_entities: set[str]) -> Candidate:
    name = str(helper.get("name") or entity_id)
    candidate = Candidate(kind="helper", source_id=f"{path}#{entity_id}", name=name, source=str(path))
    candidate.entity_refs.add(entity_id)
    score, reasons = score_text(name, hint_tokens, room_aliases)
    candidate.score += score
    candidate.reasons.extend(reasons)
    if entity_id in target_entities:
        candidate.score += 35
        candidate.reasons.append(f"exact entity match: {entity_id}")
    return candidate


def build_yaml_candidates(hint_tokens: set[str], room_aliases: set[str], target_entities: set[str]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for path in iter_yaml_files():
        if not path.exists():
            continue
        try:
            data = load_yaml(path)
        except Exception:
            continue
        if isinstance(data, list):
            if "automations" in path.parts or path.name == "automations.yaml":
                for index, item in enumerate(data):
                    if isinstance(item, dict):
                        candidates.append(build_automation_candidate(path, index, item, hint_tokens, room_aliases, target_entities))
            else:
                for index, item in enumerate(data):
                    if isinstance(item, dict) and any(k in item for k in ("alias", "sequence", "platform")):
                        candidate = Candidate(kind="yaml_list", source_id=f"{path}#{index}", name=str(item.get("alias") or item.get("name") or path.stem), source=str(path))
                        candidate.entity_refs.update(extract_entities(item))
                        candidates.append(candidate)
        elif isinstance(data, dict):
            if "scripts" in path.parts or path.name == "scripts.yaml":
                for script_name, script in data.items():
                    if isinstance(script, dict):
                        candidates.append(build_script_candidate(path, script_name, script, hint_tokens, room_aliases, target_entities))
            elif "input_boolean" in path.parts or "input_booleans" in path.parts or "input_text" in path.parts or "input_texts" in path.parts:
                for entity_id, helper in data.items():
                    if isinstance(helper, dict):
                        candidates.append(build_helper_candidate(path, str(entity_id), helper, hint_tokens, room_aliases, target_entities))
            else:
                candidate = Candidate(kind="yaml_mapping", source_id=str(path), name=path.stem, source=str(path))
                candidate.entity_refs.update(extract_entities(data))
                score, reasons = score_text(path.stem, hint_tokens, room_aliases)
                candidate.score += score
                candidate.reasons.extend(reasons)
                candidates.append(candidate)
    return candidates


def load_node_red_split_candidates(hint_tokens: set[str], room_aliases: set[str], target_entities: set[str]) -> tuple[list[Candidate], dict[str, Any]]:
    candidates: list[Candidate] = []
    split_index: dict[str, Any] = {"tabs": {}, "nodes": {}, "path_by_tab": {}}
    if NR_SPLIT_ROOT is None or not NR_SPLIT_ROOT.exists():
        return candidates, split_index
    for path in sorted(NR_SPLIT_ROOT.glob("*.json")):
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(records, list) or not records:
            continue
        tab = records[0] if isinstance(records[0], dict) else {}
        if not tab:
            continue
        tab_id = str(tab.get("id") or path.stem)
        tab_label = str(tab.get("label") or path.stem)
        nodes = [node for node in records[1:] if isinstance(node, dict)]
        split_index["tabs"][tab_id] = tab
        split_index["path_by_tab"][tab_id] = path
        for node in nodes:
            node_id = node.get("id")
            if node_id:
                split_index["nodes"][str(node_id)] = node
        candidate = Candidate(kind="node_red_flow", source_id=f"{path}#{tab_id}", name=tab_label, source=str(path))
        candidate.node_ids.update(str(node.get("id")) for node in nodes if node.get("id"))
        for node in nodes:
            refs = extract_entities(node)
            if not refs and node.get("type") == "comment":
                continue
            candidate.entity_refs.update(refs)
            node_name = str(node.get("name") or "")
            node_type = str(node.get("type") or "")
            node_score, node_reasons = score_text(node_name or node_type, hint_tokens, room_aliases)
            if node_score:
                candidate.score += node_score / 2
                candidate.reasons.extend([f"node match ({node_name or node_type}): {reason}" for reason in node_reasons])
                candidate.matched_nodes.append(node)
            if refs & target_entities:
                candidate.score += 40
                candidate.reasons.append(f"exact entity match in node {node.get('id')}")
                candidate.matched_nodes.append(node)
            if node_type in {"server-state-changed", "api-current-state", "ha-wait-until"}:
                candidate.trigger_entities.update(refs)
            elif node_type == "api-call-service":
                candidate.action_entities.update(refs)
            else:
                candidate.helper_entities.update(refs)
            if any(ref.startswith("script.") for ref in refs):
                candidate.script_hops.update(ref for ref in refs if ref.startswith("script."))
        score, reasons = score_text(tab_label, hint_tokens, room_aliases)
        candidate.score += score
        candidate.reasons.extend(reasons)
        if candidate.entity_refs & target_entities:
            candidate.score += 20
        if not candidate.matched_nodes:
            candidate.matched_nodes.extend(nodes)
        candidates.append(candidate)
    return candidates, split_index


def verify_flows_split(split_index: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if NR_ROOT is None:
        return problems
    flows_path = NR_ROOT / "flows.json"
    if not flows_path.exists() or not split_index["tabs"]:
        return problems
    try:
        flows = json.loads(flows_path.read_text(encoding="utf-8"))
    except Exception as exc:
        problems.append(f"failed to parse flows.json: {exc}")
        return problems
    if not isinstance(flows, list):
        problems.append("flows.json is not a list")
        return problems
    flow_ids = {str(item.get("id")) for item in flows if isinstance(item, dict) and item.get("id")}
    for tab_id in split_index["tabs"]:
        if tab_id not in flow_ids:
            problems.append(f"tab id {tab_id} missing from flows.json")
    for node_id in split_index["nodes"]:
        if node_id not in flow_ids:
            problems.append(f"node id {node_id} missing from flows.json")
    return problems


def categorize_refs(candidate: Candidate, primary_kind: str | None = None) -> dict[str, list[str]]:
    categories = {
        "target_entities": [],
        "trigger_entities": sorted(candidate.trigger_entities),
        "condition_entities": sorted(candidate.condition_entities),
        "action_entities": sorted(candidate.action_entities),
        "helper_entities": sorted(candidate.helper_entities),
        "script_hops": sorted(candidate.script_hops),
    }
    if candidate.kind == "node_red_flow":
        categories["target_entities"] = sorted(candidate.entity_refs)
    else:
        categories["target_entities"] = sorted(candidate.entity_refs)
    return categories


def score_candidates(candidates: list[Candidate]) -> list[Candidate]:
    candidates = [candidate for candidate in candidates if candidate.entity_refs or candidate.score]
    candidates.sort(key=lambda item: (item.score, len(item.entity_refs), item.name.lower()), reverse=True)
    return candidates


def choose_primary(candidates: list[Candidate]) -> tuple[Candidate | None, float, list[str]]:
    if not candidates:
        return None, 0.0, []
    best = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None
    if best.score >= 40:
        confidence = 0.95
    elif best.score >= 20:
        confidence = 0.8
    elif best.score >= 8:
        confidence = 0.6
    else:
        confidence = 0.4
    if second and abs(best.score - second.score) < 5:
        confidence = max(0.35, confidence - 0.15)
    return best, confidence, list(best.reasons[:8])


def expand_one_hop(
    primary: Candidate,
    split_index: dict[str, Any],
    target_entities: set[str],
) -> dict[str, set[str]]:
    graph = {
        "target_entities": set(),
        "trigger_entities": set(primary.trigger_entities),
        "condition_entities": set(primary.condition_entities),
        "action_entities": set(primary.action_entities),
        "helper_entities": set(primary.helper_entities),
        "script_hops": set(primary.script_hops),
    }
    if primary.kind == "node_red_flow":
        matched_nodes: set[str] = set()
        for node in primary.matched_nodes:
            node_id = node.get("id")
            if node_id:
                matched_nodes.add(str(node_id))
        if not matched_nodes and primary.node_ids:
            matched_nodes = set(primary.node_ids)
        reverse: dict[str, set[str]] = defaultdict(set)
        forward: dict[str, set[str]] = defaultdict(set)
        for node in split_index.get("nodes", {}).values():
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id") or "")
            if not node_id:
                continue
            for output in node.get("wires", []) or []:
                for target in output or []:
                    if target:
                        forward[node_id].add(str(target))
                        reverse[str(target)].add(node_id)
        frontier = set(matched_nodes)
        for node_id in matched_nodes:
            frontier.update(forward.get(node_id, set()))
            frontier.update(reverse.get(node_id, set()))
        for node_id in frontier:
            node = split_index.get("nodes", {}).get(node_id)
            if not isinstance(node, dict):
                continue
            refs = extract_entities(node)
            graph["target_entities"].update(refs & target_entities)
            node_type = str(node.get("type") or "")
            if node_type in {"server-state-changed", "api-current-state", "ha-wait-until"}:
                graph["trigger_entities"].update(refs)
            elif node_type == "api-call-service":
                graph["action_entities"].update(refs)
            elif node_type in {"comment", "delay", "function", "status"}:
                graph["helper_entities"].update(refs)
            else:
                graph["helper_entities"].update(refs)
            graph["script_hops"].update(ref for ref in refs if ref.startswith("script."))
    else:
        graph["target_entities"].update(primary.entity_refs & target_entities)
        graph["helper_entities"].update(
            ref
            for ref in primary.entity_refs
            if ref.startswith(("input_boolean.", "input_text.", "timer.", "scene.", "sensor.", "switch."))
        )
    return graph


def timeline_from_history(history: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for entity_id, rows in history.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            timestamp = row.get("last_changed") or row.get("last_updated") or row.get("when")
            if not timestamp:
                continue
            events.append(
                {
                    "timestamp": timestamp,
                    "source": "history",
                    "entity_id": row.get("entity_id") or entity_id,
                    "state": row.get("state"),
                    "message": row.get("attributes", {}).get("friendly_name") if isinstance(row.get("attributes"), dict) else None,
                    "raw": row,
                }
            )
    return events


def timeline_from_logbook(logbook: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for entity_id, rows in logbook.items():
        for row in rows:
            timestamp = row.get("when") or row.get("timestamp")
            if not timestamp:
                continue
            message = row.get("message") or row.get("state") or row.get("name")
            events.append(
                {
                    "timestamp": timestamp,
                    "source": "logbook",
                    "entity_id": row.get("entity_id") or entity_id,
                    "state": row.get("state"),
                    "message": message,
                    "raw": row,
                }
            )
    return events


def sort_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in events:
        key = (
            str(item.get("timestamp") or ""),
            str(item.get("entity_id") or ""),
            str(item.get("state") or ""),
            str(item.get("message") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    def sort_key(item: dict[str, Any]) -> tuple[datetime, str, str]:
        timestamp = item.get("timestamp")
        try:
            dt = parse_dt(str(timestamp))
        except Exception:
            dt = datetime.min.replace(tzinfo=timezone.utc)
        return dt, str(item.get("source") or ""), str(item.get("entity_id") or "")

    return sorted(deduped, key=sort_key)


def summarize_window(start: datetime, end: datetime) -> str:
    duration = end - start
    minutes = int(duration.total_seconds() // 60)
    return f"{iso_z(start)} to {iso_z(end)} ({minutes} minutes)"


def build_summary(
    incident: dict[str, Any],
    primary: dict[str, Any],
    graph: dict[str, list[str]],
    current_states: dict[str, Any],
    history: dict[str, Any],
    logbook: dict[str, Any],
    timeline: list[dict[str, Any]],
    gaps: dict[str, Any],
) -> str:
    lines: list[str] = []
    lines.append("# Incident Summary")
    lines.append("")
    lines.append(f"Incident: {incident.get('hint') or incident.get('entity') or 'unspecified incident'}")
    lines.append(f"Window: {incident['start']} to {incident['end']}")
    lines.append("")
    lines.append("## Primary Match")
    lines.append(f"- Kind: {primary.get('kind', 'unknown')}")
    lines.append(f"- ID: {primary.get('id', 'unknown')}")
    lines.append(f"- Confidence: {primary.get('confidence', 0):.2f}")
    if primary.get("reasons"):
        lines.append(f"- Reasons: {', '.join(primary['reasons'])}")
    lines.append("")
    lines.append("## Relevant Entities")
    for label in ("target_entities", "trigger_entities", "condition_entities", "action_entities", "helper_entities", "script_hops"):
        values = graph.get(label, [])
        if values:
            lines.append(f"- {label.replace('_', ' ').title()}: {', '.join(values)}")
    lines.append("")
    lines.append("## Timeline")
    if timeline:
        for event in timeline[:40]:
            lines.append(
                f"- {event.get('timestamp')} [{event.get('source')}] {event.get('entity_id')} "
                f"{event.get('state') or ''} {event.get('message') or ''}".strip()
            )
    else:
        lines.append("- No timeline events were returned for the selected window.")
    lines.append("")
    lines.append("## Likely Failure Point")
    if primary.get("kind") == "unknown":
        lines.append("- No strong owner match was found. The symptom may not map cleanly to the local config.")
    elif not timeline:
        lines.append("- No recorder evidence was returned for the candidate entities in the window.")
    elif graph.get("target_entities") and not any(
        entity in current_states and current_states[entity].get("state") not in {None, "unknown", "unavailable"}
        for entity in graph.get("target_entities", [])
    ):
        lines.append("- The target entity or its related helper entities did not produce useful current-state evidence.")
    else:
        lines.append("- The bundle shows related activity, but the exact skipped branch is not visible from REST alone.")
    lines.append("")
    lines.append("## Unknowns / Next Checks")
    if gaps.get("missing_current_states"):
        lines.append(f"- Missing current states: {', '.join(gaps['missing_current_states'])}")
    if gaps.get("missing_history"):
        lines.append(f"- No history returned for: {', '.join(gaps['missing_history'])}")
    if gaps.get("missing_logbook"):
        lines.append(f"- No logbook returned for: {', '.join(gaps['missing_logbook'])}")
    if gaps.get("ambiguous_matches"):
        lines.append(f"- Ambiguous matches: {', '.join(gaps['ambiguous_matches'])}")
    if not any(gaps.values()):
        lines.append("- No major gaps were detected, but the bundle is still limited to REST-visible state changes.")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a Home Assistant incident debug bundle.")
    parser.add_argument("--root", default=str(ROOT), help="Workspace root used for relative defaults.")
    parser.add_argument(
        "--env-file",
        default="",
        help="Path to a repo-local .env file. Default: <root>/.env if present.",
    )
    parser.add_argument("--editable-root", default="", help="Optional editable HA repo root.")
    parser.add_argument("--export-root", default="", help="Optional HA export root.")
    parser.add_argument("--node-red-root", default="", help="Optional Node-RED repo root.")
    parser.add_argument("--time", help="Anchor time for the incident window, ISO 8601")
    parser.add_argument("--start", help="Start of the incident window, ISO 8601")
    parser.add_argument("--end", help="End of the incident window, ISO 8601")
    parser.add_argument("--window-before-min", type=int, default=30, help="Minutes before --time to include")
    parser.add_argument("--window-after-min", type=int, default=30, help="Minutes after --time to include")
    parser.add_argument("--hint", default="", help="Free-text incident hint")
    parser.add_argument("--entity", action="append", default=[], help="Likely target entity_id, repeatable")
    parser.add_argument("--room", default="", help="Optional room hint")
    parser.add_argument("--output-dir", default="", help="Output directory for the bundle")
    parser.add_argument("--format", choices=("both", "json", "md"), default="both", help="Output format")
    parser.add_argument(
        "--include-all-history-changes",
        action="store_true",
        help="Disable significant_changes_only for history queries",
    )
    parser.add_argument("--history-chunk-size", type=int, default=25, help="Entity chunk size for history calls")
    return parser.parse_args()


def normalize_window(args: argparse.Namespace) -> tuple[datetime, datetime]:
    if args.start and args.end:
        return parse_dt(args.start), parse_dt(args.end)
    anchor = parse_dt(args.time) if args.time else utc_now()
    before = timedelta(minutes=args.window_before_min)
    after = timedelta(minutes=args.window_after_min)
    return anchor - before, anchor + after


def make_room_aliases(room: str, hint: str) -> set[str]:
    aliases = set()
    for value in (room, hint):
        text = (value or "").lower()
        for key, values in ROOM_ALIASES.items():
            if key in text or any(alias in text for alias in values):
                aliases.update(values)
    return aliases


def choose_primary_entity(
    all_candidates: list[Candidate],
    target_entities: set[str],
    hint_tokens: set[str],
    room_aliases: set[str],
) -> tuple[Candidate | None, float, list[str]]:
    allowed_kinds = {"ha_automation", "ha_script", "node_red_flow"}
    scored = score_candidates([candidate for candidate in all_candidates if candidate.kind in allowed_kinds])
    if not scored:
        scored = score_candidates(all_candidates)
    if target_entities:
        direct_hits = [candidate for candidate in scored if candidate.entity_refs & target_entities]
        if direct_hits:
            scored = sorted(direct_hits, key=lambda item: (item.score, len(item.entity_refs)), reverse=True) + [
                candidate for candidate in scored if candidate not in direct_hits
            ]
    best, confidence, reasons = choose_primary(scored)
    if best is None:
        fallback = Candidate(kind="unknown", source_id="unknown", name="unknown", source="unknown")
        fallback.reasons = ["no candidate matched"]
        return fallback, 0.2, fallback.reasons
    if best.kind not in {"ha_automation", "ha_script", "node_red_flow"}:
        fallback = Candidate(
            kind="unknown",
            source_id=best.source_id,
            name=best.name,
            source=best.source,
            score=best.score,
            reasons=list(best.reasons),
        )
        return fallback, max(0.35, confidence - 0.1), reasons or fallback.reasons
    if not reasons:
        name_score, name_reasons = score_text(best.name, hint_tokens, room_aliases)
        if name_score and name_reasons:
            reasons = name_reasons
            confidence = max(confidence, 0.6)
    return best, confidence, reasons


def resolve_optional_path(base: Path, raw: str) -> Path | None:
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def configure_runtime(args: argparse.Namespace) -> tuple[Path, Path]:
    global HA_EDITABLE_ROOT, HA_EXPORT_ROOT, NR_ROOT, NR_SPLIT_ROOT

    root = Path(args.root).expanduser().resolve()
    HA_EDITABLE_ROOT = resolve_optional_path(root, args.editable_root)
    HA_EXPORT_ROOT = resolve_optional_path(root, args.export_root)
    NR_ROOT = resolve_optional_path(root, args.node_red_root)
    NR_SPLIT_ROOT = NR_ROOT / "flows-split" if NR_ROOT is not None else None

    if args.env_file:
        env_file = resolve_optional_path(root, args.env_file)
        if env_file is None:
            env_file = root / ".env"
    else:
        env_file = root / ".env"
    return root, env_file


def build_bundle(args: argparse.Namespace, env_file: Path | None) -> tuple[dict[str, Any], str, dict[str, Any]]:
    env = load_env(env_file)
    token = env.get("HASS_TOKEN")
    if not token:
        raise RuntimeError("HASS_TOKEN not found in environment or .env file")
    base_url = infer_hass_host(env)
    start, end = normalize_window(args)
    target_entities = {entity.strip().lower() for entity in args.entity if entity.strip()}
    hint_tokens = meaningful_tokens(args.hint)
    room_aliases = make_room_aliases(args.room, args.hint)

    live_states = get_states(base_url, token)
    live_state_index = {state.get("entity_id"): state for state in live_states if isinstance(state, dict) and state.get("entity_id")}
    for entity in target_entities:
        target_name = None
        live = live_state_index.get(entity)
        if live and isinstance(live.get("attributes"), dict):
            target_name = str(live["attributes"].get("friendly_name") or entity)
        if target_name:
            hint_tokens.update(meaningful_tokens(target_name))

    yaml_candidates = build_yaml_candidates(hint_tokens, room_aliases, target_entities)
    node_red_candidates, split_index = load_node_red_split_candidates(hint_tokens, room_aliases, target_entities)
    split_problems = verify_flows_split(split_index)
    all_candidates = yaml_candidates + node_red_candidates
    primary_candidate, confidence, reasons = choose_primary_entity(all_candidates, target_entities, hint_tokens, room_aliases)

    if primary_candidate is None:
        primary_candidate = Candidate(kind="unknown", source_id="unknown", name="unknown", source="unknown")
        confidence = 0.2
        reasons = ["no candidate matched"]

    graph = expand_one_hop(primary_candidate, split_index, target_entities)
    graph["target_entities"].update(target_entities)

    entity_set = sorted({
        *graph["target_entities"],
        *graph["trigger_entities"],
        *graph["condition_entities"],
        *graph["action_entities"],
        *graph["helper_entities"],
        *graph["script_hops"],
    })
    current_states: dict[str, Any] = {}
    missing_current_states: list[str] = []
    for entity_id in entity_set:
        state = get_state(base_url, token, entity_id)
        if state is None:
            missing_current_states.append(entity_id)
            continue
        attributes = state.get("attributes", {}) if isinstance(state.get("attributes"), dict) else {}
        current_states[entity_id] = {
            "state": state.get("state"),
            "last_changed": state.get("last_changed"),
            "last_updated": state.get("last_updated"),
            "attributes": selected_attributes(attributes),
        }

    history = get_history(
        base_url,
        token,
        start,
        end,
        entity_set,
        minimal_response=True,
        significant_changes_only=not args.include_all_history_changes,
        chunk_size=max(1, args.history_chunk_size),
    )
    logbook_entities = list(dict.fromkeys(
        [primary_candidate.source_id] + entity_set
    ))
    logbook = get_logbook(base_url, token, start, end, [entity for entity in entity_set if "." in entity])

    history_timeline = timeline_from_history(history)
    logbook_timeline = timeline_from_logbook(logbook)
    timeline = sort_timeline(history_timeline + logbook_timeline)

    missing_history = [entity_id for entity_id in entity_set if entity_id not in history or not history.get(entity_id)]
    missing_logbook = [entity_id for entity_id in entity_set if entity_id not in logbook or not logbook.get(entity_id)]
    ambiguous_matches = []
    if len(score_candidates(all_candidates)) > 1:
        sorted_candidates = score_candidates(all_candidates)
        if sorted_candidates and len(sorted_candidates) > 1 and abs(sorted_candidates[0].score - sorted_candidates[1].score) < 5:
            ambiguous_matches.append(f"{sorted_candidates[0].source_id} tied with {sorted_candidates[1].source_id}")
    if split_problems:
        ambiguous_matches.extend(split_problems)

    primary = {
        "kind": primary_candidate.kind,
        "id": primary_candidate.source_id,
        "name": primary_candidate.name,
        "source": primary_candidate.source,
        "confidence": round(confidence, 2),
        "reasons": reasons,
    }
    entity_graph = categorize_refs(primary_candidate)
    entity_graph["target_entities"] = sorted(graph["target_entities"])
    entity_graph["trigger_entities"] = sorted(graph["trigger_entities"])
    entity_graph["condition_entities"] = sorted(graph["condition_entities"])
    entity_graph["action_entities"] = sorted(graph["action_entities"])
    entity_graph["helper_entities"] = sorted(graph["helper_entities"])
    entity_graph["script_hops"] = sorted(graph["script_hops"])

    gaps = {
        "missing_current_states": missing_current_states,
        "missing_history": missing_history,
        "missing_logbook": missing_logbook,
        "ambiguous_matches": ambiguous_matches,
    }

    incident = {
        "input": {
            "time": args.time,
            "start": args.start,
            "end": args.end,
            "hint": args.hint,
            "entities": args.entity,
            "room": args.room,
            "window_before_min": args.window_before_min,
            "window_after_min": args.window_after_min,
            "include_all_history_changes": bool(args.include_all_history_changes),
        },
        "start": iso_z(start),
        "end": iso_z(end),
        "generated_at": iso_z(utc_now()),
        "window": summarize_window(start, end),
        "source": {
            "mode": "rest",
            "base_url": base_url,
        },
    }

    bundle = {
        "incident": incident,
        "primary_match": primary,
        "entity_graph": entity_graph,
        "current_states": current_states,
        "history": history,
        "logbook": logbook,
        "timeline": timeline,
        "gaps": gaps,
    }
    summary = build_summary(incident["input"], primary, entity_graph, current_states, history, logbook, timeline, gaps)
    return bundle, summary, incident


def write_outputs(output_dir: Path, bundle: dict[str, Any], summary: str, fmt: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if fmt in {"both", "json"}:
        json_path = output_dir / "incident_bundle.json"
        json_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(json_path)
    if fmt in {"both", "md"}:
        md_path = output_dir / "incident_summary.md"
        md_path.write_text(summary, encoding="utf-8")
        written.append(md_path)
    return written


def main() -> int:
    args = parse_args()
    root, env_file = configure_runtime(args)
    if not args.start and not args.end and not args.time:
        args.time = iso_z(utc_now())
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve()
    else:
        stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        hint_slug = slugify(args.hint or (args.entity[0] if args.entity else "incident"))
        output_dir = (root / "output" / "incident-bundles" / f"{stamp}_{hint_slug}").resolve()
    try:
        bundle, summary, incident = build_bundle(args, env_file)
        written = write_outputs(output_dir, bundle, summary, args.format)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Bundle written to {output_dir}")
    for path in written:
        print(f"  {path}")
    print(f"Window: {incident['window']}")
    print(f"Primary match: {bundle['primary_match']['kind']} {bundle['primary_match']['id']} ({bundle['primary_match']['confidence']:.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
