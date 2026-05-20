#!/usr/bin/env python3
"""Map ArcGIS web map layer IDs to local Parquet file names.

Requirements:
  pip install pyarrow

Optional LLM reranking uses an OpenAI-compatible chat completions endpoint:
  set OPENAI_API_KEY=...
  set OPENAI_MODEL=gpt-4o-mini
  set OPENAI_BASE_URL=https://api.openai.com/v1
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import string
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
ABBREVIATIONS = {
    "aqrean": ["air", "quality", "receptor", "exposure", "analysis"],
    "bc": ["bias", "corrected"],
    "bfc": ["boundary", "full", "clipped"],
    "bng": ["british", "national", "grid"],
    "bua": ["built", "up", "area"],
    "ceh": ["centre", "ecology", "hydrology"],
    "cj": ["climate", "just"],
    "cm": ["cell", "match"],
    "co": ["carbon"],
    "ctry": ["country"],
    "ctyua": ["county", "unitary", "authority"],
    "defra": ["environment", "food", "rural", "affairs"],
    "end": ["environmental", "noise", "directive"],
    "en": ["england"],
    "epsg": ["spatial", "reference"],
    "evi": ["ellenberg", "vegetation", "indicator"],
    "gb": ["great", "britain"],
    "laeq": ["equivalent", "continuous", "sound", "level"],
    "lden": ["day", "evening", "night", "noise"],
    "lnight": ["night", "noise"],
    "lsoa": ["lower", "layer", "super", "output", "area"],
    "mcty": ["metropolitan", "county"],
    "msoa": ["middle", "layer", "super", "output", "area"],
    "ons": ["office", "national", "statistics"],
    "rgn": ["region"],
    "shvi": ["social", "heat", "vulnerability", "index"],
    "uprn": ["unique", "property", "reference", "number"],
    "utla": ["upper", "tier", "local", "authority"],
    "wa": ["wales"],
    "wd": ["ward"],
}


@dataclass(frozen=True)
class CandidateScore:
    parquet_file: str
    deterministic_score: float
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Map ArcGIS web map layer IDs to local Parquet file names."
    )
    parser.add_argument("--web-map", default="data.json", help="Path to the web map JSON.")
    parser.add_argument(
        "--parquet-dir", default="parquet-data", help="Directory containing .parquet files."
    )
    parser.add_argument(
        "--output-dir",
        default="layer-parquet-mapping",
        help="Directory for metadata, final mapping, and debug JSON outputs.",
    )
    parser.add_argument(
        "--top-k", type=int, default=3, help="Number of deterministic candidates to rerank."
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.7,
        help="Scores below this threshold are marked for review.",
    )
    parser.add_argument(
        "--ambiguity-margin",
        type=float,
        default=0.08,
        help="Top scores closer than this margin are marked for review.",
    )
    parser.add_argument(
        "--allow-duplicate-parquet",
        action="store_true",
        help="Allow multiple web map entries to map to the same Parquet file.",
    )
    parser.add_argument(
        "--exclude-tables",
        action="store_true",
        help="Only inspect operationalLayers; skip top-level web map tables.",
    )
    parser.add_argument(
        "--refresh-layer-json",
        action="store_true",
        help="Re-download ArcGIS layer JSON even when a cached trimmed JSON exists.",
    )
    parser.add_argument(
        "--llm-provider",
        choices=("auto", "openai", "none"),
        default="auto",
        help="Use OpenAI-compatible reranking, deterministic-only reranking, or auto-detect.",
    )
    parser.add_argument(
        "--require-llm",
        action="store_true",
        help="Fail if the configured LLM cannot be used.",
    )
    parser.add_argument(
        "--llm-retries", type=int, default=2, help="Retries per LLM reranking call."
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=45.0,
        help="HTTP timeout in seconds for ArcGIS and LLM requests.",
    )
    return parser.parse_args()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
        file.write("\n")


def add_f_pjson(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["f"] = "pjson"
    return urlunparse(parsed._replace(query=urlencode(query)))


def safe_file_stem(value: str) -> str:
    safe_chars = f"-_.() {string.ascii_letters}{string.digits}"
    cleaned = "".join(char if char in safe_chars else "_" for char in value)
    return cleaned.strip().strip(".") or "layer"


def iter_url_entries(web_map: dict[str, Any], include_tables: bool) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def walk(value: Any, path: list[str]) -> None:
        if isinstance(value, dict):
            if value.get("id") and value.get("url"):
                entries.append(
                    {
                        "id": str(value["id"]),
                        "title": value.get("title") or value.get("name") or "",
                        "url": value["url"],
                        "path": path.copy(),
                        "layerType": value.get("layerType", ""),
                    }
                )
            for child_key in ("layers",):
                children = value.get(child_key)
                if isinstance(children, list):
                    for index, child in enumerate(children):
                        walk(child, [*path, child_key, str(index)])
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, [*path, str(index)])

    for index, layer in enumerate(web_map.get("operationalLayers", [])):
        walk(layer, ["operationalLayers", str(index)])

    if include_tables:
        for index, table in enumerate(web_map.get("tables", [])):
            walk(table, ["tables", str(index)])

    return entries


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    request = Request(
        add_f_pjson(url),
        headers={"User-Agent": "layer-parquet-mapper/1.0"},
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def trim_layer_json(entry: dict[str, Any], downloaded: dict[str, Any]) -> dict[str, Any]:
    fields = []
    for field in downloaded.get("fields") or []:
        field_name = str(field.get("name", "")).strip()
        if not field_name:
            continue
        fields.append(
            {
                "name": field_name,
                "alias": str(field.get("alias") or field_name).strip() or field_name,
            }
        )

    return {
        "id": entry["id"],
        "name": downloaded.get("name") or downloaded.get("title") or entry.get("title") or "",
        "description": downloaded.get("description") or downloaded.get("serviceItemId") or "",
        "type": downloaded.get("type") or entry.get("layerType") or "",
        "geometryType": downloaded.get("geometryType"),
        "hasGeometry": has_arcgis_geometry(downloaded),
        "fields": fields,
    }


def has_arcgis_geometry(downloaded: dict[str, Any]) -> bool:
    service_type = str(downloaded.get("type") or "").lower()
    geometry_type = downloaded.get("geometryType")
    return bool(geometry_type) and service_type != "table"


def layer_metadata_is_current(metadata: Any) -> bool:
    return (
        isinstance(metadata, dict)
        and "type" in metadata
        and "geometryType" in metadata
        and "hasGeometry" in metadata
    )


def load_or_fetch_layer_metadata(
    entries: list[dict[str, Any]], layer_dir: Path, refresh: bool, timeout: float
) -> list[dict[str, Any]]:
    metadata = []
    failures = []
    used_paths: Counter[str] = Counter()

    for entry in entries:
        stem = safe_file_stem(entry["id"])
        used_paths[stem] += 1
        suffix = f"-{used_paths[stem]}" if used_paths[stem] > 1 else ""
        output_path = layer_dir / f"{stem}{suffix}.json"

        if output_path.exists() and not refresh:
            cached = read_json(output_path)
            if layer_metadata_is_current(cached):
                metadata.append(cached)
                continue

        try:
            downloaded = fetch_json(entry["url"], timeout)
            trimmed = trim_layer_json(entry, downloaded)
            write_json(output_path, trimmed)
            metadata.append(trimmed)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
            failures.append({"id": entry["id"], "url": entry["url"], "error": str(error)})
            fallback = {
                "id": entry["id"],
                "name": entry.get("title", ""),
                "description": "",
                "type": entry.get("layerType", ""),
                "geometryType": None,
                "hasGeometry": False,
                "fields": [],
            }
            write_json(output_path, fallback)
            metadata.append(fallback)

    if failures:
        write_json(layer_dir / "download-failures.json", failures)
        print(f"Warning: {len(failures)} layer JSON downloads failed; see {layer_dir / 'download-failures.json'}", file=sys.stderr)

    return metadata


def read_parquet_metadata(parquet_dir: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise SystemExit("pyarrow is required to inspect Parquet schemas. Install it with: pip install pyarrow") from error

    metadata = []
    for path in sorted(parquet_dir.glob("*.parquet")):
        parquet_file = pq.ParquetFile(path)
        schema = parquet_file.schema_arrow
        geometry_metadata = read_geoparquet_metadata(schema.metadata)
        fields = []
        for field in schema:
            fields.append({"name": field.name, "alias": field.name})
        geometry_field = geometry_metadata.get("primary_column") if geometry_metadata else None
        geometry_types = []
        if geometry_metadata and isinstance(geometry_field, str):
            column_metadata = (geometry_metadata.get("columns") or {}).get(geometry_field) or {}
            geometry_types = column_metadata.get("geometry_types") or []
        metadata.append(
            {
                "name": path.name,
                "hasGeometry": bool(geometry_metadata),
                "geometryField": geometry_field,
                "geometryTypes": geometry_types,
                "fields": fields,
            }
        )
    return metadata


def read_geoparquet_metadata(metadata: dict[bytes, bytes] | None) -> dict[str, Any] | None:
    if not metadata or b"geo" not in metadata:
        return None
    try:
        geo_metadata = json.loads(metadata[b"geo"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return geo_metadata if isinstance(geo_metadata, dict) else None


def normalize_tokens(value: str) -> list[str]:
    tokens = TOKEN_PATTERN.findall(value.lower())
    expanded = []
    for token in tokens:
        expanded.append(token)
        expanded.extend(ABBREVIATIONS.get(token, []))
    return expanded


def token_set(value: str) -> set[str]:
    return set(normalize_tokens(value))


def field_tokens(fields: list[dict[str, Any]], key: str) -> set[str]:
    tokens: set[str] = set()
    for field in fields:
        tokens.update(normalize_tokens(str(field.get(key, ""))))
    return tokens


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def coverage(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def abbreviation_score(layer_text: str, parquet_name: str) -> float:
    layer_tokens = token_set(layer_text)
    parquet_tokens = token_set(Path(parquet_name).stem)
    if not layer_tokens or not parquet_tokens:
        return 0.0

    matches = 0
    for token in parquet_tokens:
        if token in layer_tokens:
            matches += 1
            continue
        expanded = set(ABBREVIATIONS.get(token, []))
        if expanded and expanded & layer_tokens:
            matches += 1
    return matches / len(parquet_tokens)


def deterministic_score(layer: dict[str, Any], parquet: dict[str, Any]) -> CandidateScore:
    parquet_name = parquet["name"]
    layer_name = str(layer.get("name", ""))
    layer_description = str(layer.get("description", ""))

    if parquet.get("hasGeometry") and not layer.get("hasGeometry"):
        layer_type = str(layer.get("type") or "unknown")
        return CandidateScore(
            parquet_name,
            0.0,
            f"incompatibleGeometry=parquet has geometry but ArcGIS type is {layer_type}",
        )

    parquet_tokens = token_set(Path(parquet_name).stem)
    layer_name_tokens = token_set(layer_name)
    layer_description_tokens = token_set(layer_description)
    layer_field_names = field_tokens(layer.get("fields", []), "name")
    layer_field_aliases = field_tokens(layer.get("fields", []), "alias")
    parquet_field_names = field_tokens(parquet.get("fields", []), "name")
    parquet_field_aliases = field_tokens(parquet.get("fields", []), "alias")

    name_match = max(jaccard(layer_name_tokens, parquet_tokens), coverage(parquet_tokens, layer_name_tokens) * 0.8)
    description_match = max(
        jaccard(layer_description_tokens, parquet_tokens),
        coverage(parquet_tokens, layer_description_tokens) * 0.7,
    )
    field_name_match = jaccard(layer_field_names, parquet_field_names)
    field_alias_match = jaccard(layer_field_aliases, parquet_field_aliases)
    abbreviation_match = max(
        abbreviation_score(layer_name, parquet_name), abbreviation_score(layer_description, parquet_name)
    )

    score = (
        name_match * 0.34
        + description_match * 0.16
        + field_name_match * 0.25
        + field_alias_match * 0.15
        + abbreviation_match * 0.10
    )
    parts = [
        f"name={name_match:.2f}",
        f"description={description_match:.2f}",
        f"fieldName={field_name_match:.2f}",
        f"fieldAlias={field_alias_match:.2f}",
        f"abbreviation={abbreviation_match:.2f}",
        f"layerType={layer.get('type') or 'unknown'}",
        f"parquetHasGeometry={bool(parquet.get('hasGeometry'))}",
    ]
    return CandidateScore(parquet_name, round(min(score, 1.0), 4), "; ".join(parts))


def deterministic_candidates(
    layers: list[dict[str, Any]], parquets: list[dict[str, Any]], top_k: int
) -> dict[str, list[CandidateScore]]:
    result = {}
    for layer in layers:
        scores = [deterministic_score(layer, parquet) for parquet in parquets]
        scores.sort(key=lambda item: item.deterministic_score, reverse=True)
        result[layer["id"]] = scores[:top_k]
    return result


def llm_is_available(provider: str) -> bool:
    if provider == "none":
        return False
    return bool(os.environ.get("OPENAI_API_KEY"))


def post_openai_chat(messages: list[dict[str, str]], timeout: float) -> dict[str, Any]:
    api_key = os.environ["OPENAI_API_KEY"]
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        f"{base_url}/chat/completions",
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    return json.loads(content)


def rerank_with_llm(
    layer: dict[str, Any], candidates: list[dict[str, Any]], timeout: float, retries: int
) -> dict[str, Any]:
    prompt_data = {"layer": layer, "candidates": candidates}
    messages = [
        {
            "role": "system",
            "content": (
                "You rerank candidate Parquet files for one ArcGIS web map layer. "
                "Return JSON only. Preserve parquet file names exactly. "
                "Scores must be numbers between 0 and 1."
            ),
        },
        {
            "role": "user",
            "content": (
                "Compare the layer JSON against only these candidates. Consider Parquet file name "
                "versus layer name and description, field name overlap, field alias overlap, and "
                "likely abbreviations or shortened forms. Return this exact structure: "
                "{\"webMapId\":\"layer-id\",\"candidates\":[{\"parquetFile\":\"file-name.parquet\","
                "\"score\":0.92,\"reason\":\"reason\"}]}.\n\n"
                f"Input JSON:\n{json.dumps(prompt_data, ensure_ascii=False)}"
            ),
        },
    ]

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            result = post_openai_chat(messages, timeout)
            validate_llm_result(layer["id"], candidates, result)
            return result
        except Exception as error:  # noqa: BLE001 - external API failures need retry context.
            last_error = error
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"LLM reranking failed for {layer['id']}: {last_error}")


def validate_llm_result(layer_id: str, candidates: list[dict[str, Any]], result: dict[str, Any]) -> None:
    allowed_files = {candidate["name"] for candidate in candidates}
    if result.get("webMapId") != layer_id:
        raise ValueError("LLM returned the wrong webMapId")
    if not isinstance(result.get("candidates"), list):
        raise ValueError("LLM result is missing candidates")
    for candidate in result["candidates"]:
        parquet_file = candidate.get("parquetFile")
        if parquet_file not in allowed_files:
            raise ValueError(f"LLM returned an unknown parquet file: {parquet_file}")
        score = candidate.get("score")
        if not isinstance(score, (int, float)) or math.isnan(float(score)):
            raise ValueError("LLM returned a non-numeric score")
        candidate["score"] = max(0.0, min(1.0, float(score)))
        candidate["reason"] = str(candidate.get("reason") or "No reason provided.")


def deterministic_rerank_result(layer: dict[str, Any], candidates: list[CandidateScore]) -> dict[str, Any]:
    return {
        "webMapId": layer["id"],
        "candidates": [
            {
                "parquetFile": candidate.parquet_file,
                "score": candidate.deterministic_score,
                "reason": f"Deterministic fallback: {candidate.reason}",
            }
            for candidate in candidates
        ],
    }


def rerank_candidates(
    layers: list[dict[str, Any]],
    parquets: list[dict[str, Any]],
    deterministic: dict[str, list[CandidateScore]],
    provider: str,
    require_llm: bool,
    timeout: float,
    retries: int,
) -> list[dict[str, Any]]:
    parquet_by_name = {parquet["name"]: parquet for parquet in parquets}
    use_llm = llm_is_available(provider)
    if provider == "openai" and not use_llm:
        message = "OPENAI_API_KEY is not set, so OpenAI-compatible reranking cannot run."
        if require_llm:
            raise SystemExit(message)
        print(f"Warning: {message} Falling back to deterministic scores.", file=sys.stderr)
    elif provider == "auto" and not use_llm:
        print("Warning: OPENAI_API_KEY is not set; using deterministic scores only.", file=sys.stderr)

    reranked = []
    for index, layer in enumerate(layers, start=1):
        layer_candidates = deterministic[layer["id"]]
        if use_llm:
            candidate_payload = [parquet_by_name[candidate.parquet_file] for candidate in layer_candidates]
            try:
                result = rerank_with_llm(layer, candidate_payload, timeout, retries)
            except RuntimeError as error:
                if require_llm:
                    raise SystemExit(str(error)) from error
                print(f"Warning: {error}; using deterministic scores for this layer.", file=sys.stderr)
                result = deterministic_rerank_result(layer, layer_candidates)
        else:
            result = deterministic_rerank_result(layer, layer_candidates)
        result["candidates"].sort(key=lambda item: item["score"], reverse=True)
        reranked.append(result)
        print(f"Reranked {index}/{len(layers)}: {layer['id']}", file=sys.stderr)
    return reranked


class MinCostMaxFlow:
    def __init__(self, node_count: int) -> None:
        self.graph: list[list[list[float]]] = [[] for _ in range(node_count)]

    def add_edge(self, source: int, target: int, capacity: int, cost: float) -> None:
        forward = [target, len(self.graph[target]), capacity, cost]
        backward = [source, len(self.graph[source]), 0, -cost]
        self.graph[source].append(forward)
        self.graph[target].append(backward)

    def flow(self, source: int, sink: int, max_flow: int) -> None:
        node_count = len(self.graph)
        sent = 0
        while sent < max_flow:
            dist = [float("inf")] * node_count
            parent_node = [-1] * node_count
            parent_edge = [-1] * node_count
            dist[source] = 0.0

            for _ in range(node_count - 1):
                changed = False
                for node, edges in enumerate(self.graph):
                    if dist[node] == float("inf"):
                        continue
                    for edge_index, edge in enumerate(edges):
                        if edge[2] <= 0:
                            continue
                        target = int(edge[0])
                        next_dist = dist[node] + edge[3]
                        if next_dist < dist[target]:
                            dist[target] = next_dist
                            parent_node[target] = node
                            parent_edge[target] = edge_index
                            changed = True
                if not changed:
                    break

            if parent_node[sink] == -1:
                break

            added = max_flow - sent
            node = sink
            path_edges: list[tuple[int, int, int]] = []
            seen_nodes = set()
            while node != source:
                if node in seen_nodes or parent_node[node] == -1:
                    return
                seen_nodes.add(node)
                edge = self.graph[parent_node[node]][parent_edge[node]]
                added = min(added, int(edge[2]))
                path_edges.append((parent_node[node], parent_edge[node], node))
                node = parent_node[node]

            if added <= 0:
                return

            for source_node, edge_index, target_node in path_edges:
                edge = self.graph[source_node][edge_index]
                edge[2] -= added
                reverse_edge = self.graph[target_node][int(edge[1])]
                reverse_edge[2] += added
            sent += added


def select_global_matches(
    reranked: list[dict[str, Any]], allow_duplicates: bool
) -> dict[str, dict[str, Any]]:
    if allow_duplicates:
        return {
            item["webMapId"]: item["candidates"][0]
            for item in reranked
            if item.get("candidates")
        }

    layer_ids = [item["webMapId"] for item in reranked]
    parquet_files = sorted(
        {candidate["parquetFile"] for item in reranked for candidate in item.get("candidates", [])}
    )
    layer_offset = 1
    parquet_offset = layer_offset + len(layer_ids)
    sink = parquet_offset + len(parquet_files)
    graph = MinCostMaxFlow(sink + 1)

    layer_index = {layer_id: layer_offset + index for index, layer_id in enumerate(layer_ids)}
    parquet_index = {name: parquet_offset + index for index, name in enumerate(parquet_files)}

    for layer_id in layer_ids:
        graph.add_edge(0, layer_index[layer_id], 1, 0.0)
    for parquet_file in parquet_files:
        graph.add_edge(parquet_index[parquet_file], sink, 1, 0.0)
    for item in reranked:
        source_node = layer_index[item["webMapId"]]
        for candidate in item.get("candidates", []):
            graph.add_edge(source_node, parquet_index[candidate["parquetFile"]], 1, -float(candidate["score"]))

    graph.flow(0, sink, min(len(layer_ids), len(parquet_files)))

    candidate_lookup = {
        (item["webMapId"], candidate["parquetFile"]): candidate
        for item in reranked
        for candidate in item.get("candidates", [])
    }
    selected = {}
    for layer_id in layer_ids:
        node = layer_index[layer_id]
        for edge in graph.graph[node]:
            target = int(edge[0])
            original_capacity_used = edge[2] == 0 and parquet_offset <= target < sink
            if original_capacity_used:
                parquet_file = parquet_files[target - parquet_offset]
                selected[layer_id] = candidate_lookup[(layer_id, parquet_file)]
                break
    return selected


def build_outputs(
    reranked: list[dict[str, Any]],
    selected: dict[str, dict[str, Any]],
    confidence_threshold: float,
    ambiguity_margin: float,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    duplicate_counts = Counter(
        candidate["parquetFile"] for candidate in selected.values() if candidate.get("parquetFile")
    )
    final = []
    debug = []

    for item in reranked:
        web_map_id = item["webMapId"]
        candidates = item.get("candidates", [])
        selected_candidate = selected.get(web_map_id)
        alternatives = [
            candidate
            for candidate in candidates
            if not selected_candidate or candidate["parquetFile"] != selected_candidate["parquetFile"]
        ]
        top_two_close = False
        if len(candidates) > 1:
            top_two_close = abs(float(candidates[0]["score"]) - float(candidates[1]["score"])) <= ambiguity_margin
        duplicate_selected = bool(
            selected_candidate and duplicate_counts[selected_candidate["parquetFile"]] > 1
        )
        low_confidence = not selected_candidate or float(selected_candidate["score"]) < confidence_threshold
        indistinguishable = bool(
            selected_candidate
            and any("cannot" in str(candidate.get("reason", "")).lower() for candidate in candidates[:2])
        )
        needs_review = low_confidence or top_two_close or duplicate_selected or indistinguishable

        if selected_candidate:
            final.append(
                {"webMapId": web_map_id, "parquetFile": selected_candidate["parquetFile"]}
            )
            debug.append(
                {
                    "webMapId": web_map_id,
                    "selectedParquetFile": selected_candidate["parquetFile"],
                    "score": selected_candidate["score"],
                    "reason": selected_candidate.get("reason", ""),
                    "alternatives": alternatives,
                    "needsReview": needs_review,
                }
            )
        else:
            debug.append(
                {
                    "webMapId": web_map_id,
                    "selectedParquetFile": None,
                    "score": 0,
                    "reason": "No candidate could be assigned in the global matching step.",
                    "alternatives": alternatives,
                    "needsReview": True,
                }
            )

    return final, debug


def main() -> None:
    args = parse_args()
    web_map_path = Path(args.web_map)
    parquet_dir = Path(args.parquet_dir)
    output_dir = Path(args.output_dir)
    layer_dir = output_dir / "layers"

    web_map = read_json(web_map_path)
    layer_entries = iter_url_entries(web_map, include_tables=not args.exclude_tables)
    if not layer_entries:
        raise SystemExit("No URL-backed web map layers were found.")

    layers = load_or_fetch_layer_metadata(
        layer_entries, layer_dir, args.refresh_layer_json, args.request_timeout
    )
    write_json(output_dir / "layer-metadata.json", layers)

    parquets = read_parquet_metadata(parquet_dir)
    if not parquets:
        raise SystemExit(f"No .parquet files found in {parquet_dir}")
    write_json(output_dir / "parquet-metadata.json", parquets)

    deterministic = deterministic_candidates(layers, parquets, args.top_k)
    write_json(
        output_dir / "deterministic-candidates.json",
        {
            layer_id: [candidate.__dict__ for candidate in candidates]
            for layer_id, candidates in deterministic.items()
        },
    )

    reranked = rerank_candidates(
        layers,
        parquets,
        deterministic,
        args.llm_provider,
        args.require_llm,
        args.request_timeout,
        args.llm_retries,
    )
    write_json(output_dir / "llm-candidates.json", reranked)

    selected = select_global_matches(reranked, args.allow_duplicate_parquet)
    final, debug = build_outputs(
        reranked, selected, args.confidence_threshold, args.ambiguity_margin
    )
    write_json(output_dir / "layer-parquet-map.json", final)
    write_json(output_dir / "layer-parquet-debug.json", debug)

    review_count = sum(1 for item in debug if item["needsReview"])
    print(f"Wrote {len(final)} mappings to {output_dir / 'layer-parquet-map.json'}")
    print(f"Wrote debug output to {output_dir / 'layer-parquet-debug.json'}")
    print(f"Manual review needed for {review_count} mappings")


if __name__ == "__main__":
    main()