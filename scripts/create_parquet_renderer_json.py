#!/usr/bin/env python3
"""Create Parquet renderer JSON from an ArcGIS web map and layer mapping file."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
LAYER_URL_PATTERN = re.compile(r"/(FeatureServer|MapServer)(?:/\d+)?/?$", re.IGNORECASE)
GENERIC_FIELDS = {"objectid", "objectid_1", "fid", "oid", "uprn", "uprnid", "frequency"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Join web map renderers from data.json to mapped Parquet file names."
    )
    parser.add_argument("--web-map", default="data.json", help="Path to the ArcGIS web map JSON.")
    parser.add_argument(
        "--mapping",
        default="layer-parquet-mapping-test/layer-parquet-map.json",
        help="Path to JSON array containing webMapId and parquetFile values.",
    )
    parser.add_argument(
        "--output",
        default="static/parquet-renderers.json",
        help="Output JSON path. Files in static/ are served from the SvelteKit site root.",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=45.0,
        help="HTTP timeout in seconds when fetching ArcGIS layer JSON fallback renderers.",
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


def find_renderer(value: dict[str, Any]) -> Any | None:
    layer_definition = value.get("layerDefinition")
    if isinstance(layer_definition, dict):
        drawing_info = layer_definition.get("drawingInfo")
        if isinstance(drawing_info, dict) and drawing_info.get("renderer") is not None:
            return drawing_info["renderer"]

    drawing_info = value.get("drawingInfo")
    if isinstance(drawing_info, dict) and drawing_info.get("renderer") is not None:
        return drawing_info["renderer"]

    renderer = value.get("renderer")
    if renderer is not None:
        return renderer

    return None


def popup_field_names(value: dict[str, Any]) -> set[str]:
    fields: set[str] = set()
    popup_info = value.get("popupInfo")

    def add_field_infos(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, dict) and item.get("fieldName"):
                fields.add(str(item["fieldName"]).lower())

    if isinstance(popup_info, dict):
        add_field_infos(popup_info.get("fieldInfos"))
        for element in popup_info.get("popupElements") or []:
            if isinstance(element, dict):
                add_field_infos(element.get("fieldInfos"))

    layer_definition = value.get("layerDefinition")
    if isinstance(layer_definition, dict):
        add_field_infos(layer_definition.get("fields"))

    return fields


def collect_web_map_entries(web_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            layer_id = value.get("id")
            renderer = find_renderer(value)
            if layer_id is not None:
                entries[str(layer_id)] = {
                    "id": str(layer_id),
                    "title": value.get("title") or value.get("name") or "",
                    "url": value.get("url") or "",
                    "fields": popup_field_names(value),
                    "renderer": renderer,
                }

            for child_key in ("operationalLayers", "layers", "tables"):
                children = value.get(child_key)
                if isinstance(children, list):
                    for child in children:
                        walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(web_map)
    return entries


def add_arcgis_json_params(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["f"] = "pjson"
    query["returnAdvancedSymbols"] = "true"
    return urlunparse(parsed._replace(query=urlencode(query)))


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    request = Request(
        add_arcgis_json_params(url),
        headers={"User-Agent": "parquet-renderer-generator/1.0"},
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_renderer_from_url(url: str, timeout: float) -> Any | None:
    if not url:
        return None

    try:
        return find_renderer(fetch_json(url, timeout))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
        print(f"Warning: could not fetch ArcGIS renderer from {url}: {error}")
        return None


def service_base_url(url: str) -> str:
    parsed = urlparse(url)
    clean_path = LAYER_URL_PATTERN.sub(r"/\1", parsed.path.rstrip("/"))
    return urlunparse(parsed._replace(path=clean_path, params="", query="", fragment="")).lower()


def tokens(value: str) -> set[str]:
    return set(TOKEN_PATTERN.findall(value.lower()))


def overlap_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def score_related_renderer(
    missing_entry: dict[str, Any], parquet_file: str, candidate: dict[str, Any]
) -> float:
    if not candidate.get("renderer") or not missing_entry.get("url") or not candidate.get("url"):
        return 0.0
    if service_base_url(str(missing_entry["url"])) != service_base_url(str(candidate["url"])):
        return 0.0

    missing_fields = missing_entry.get("fields") or set()
    candidate_fields = candidate.get("fields") or set()
    field_score = overlap_score(set(missing_fields), set(candidate_fields))
    shared_specific_fields = (set(missing_fields) & set(candidate_fields)) - GENERIC_FIELDS

    missing_text = f"{missing_entry.get('title', '')} {Path(parquet_file).stem}"
    candidate_text = str(candidate.get("title", ""))
    name_score = overlap_score(tokens(missing_text), tokens(candidate_text))

    specific_field_bonus = 0.35 if shared_specific_fields else 0.0
    return min(field_score * 0.7 + name_score * 0.3 + specific_field_bonus, 1.0)


def find_related_web_map_renderer(
    missing_entry: dict[str, Any], parquet_file: str, web_map_entries: dict[str, dict[str, Any]]
) -> Any | None:
    best_score = 0.0
    best_renderer = None

    for candidate in web_map_entries.values():
        if candidate.get("id") == missing_entry.get("id"):
            continue
        score = score_related_renderer(missing_entry, parquet_file, candidate)
        if score > best_score:
            best_score = score
            best_renderer = candidate.get("renderer")

    return best_renderer if best_score >= 0.45 else None


def resolve_renderer(
    web_map_id: str,
    parquet_file: str,
    web_map_entries: dict[str, dict[str, Any]],
    timeout: float,
) -> Any | None:
    web_map_entry = web_map_entries.get(web_map_id, {})
    renderer = web_map_entry.get("renderer")
    if renderer is not None:
        return renderer

    renderer = fetch_renderer_from_url(str(web_map_entry.get("url") or ""), timeout)
    if renderer is not None:
        return renderer

    return find_related_web_map_renderer(web_map_entry, parquet_file, web_map_entries)


def build_parquet_renderer_entries(
    mapping: list[dict[str, Any]], web_map_entries: dict[str, dict[str, Any]], timeout: float
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    entries = []
    missing = []

    for item in mapping:
        web_map_id = str(item.get("webMapId", ""))
        parquet_file = item.get("parquetFile")

        if not web_map_id or not parquet_file:
            continue

        renderer = resolve_renderer(web_map_id, str(parquet_file), web_map_entries, timeout)
        entries.append({"parquetFile": parquet_file, "renderer": renderer})

        if renderer is None:
            missing.append({"webMapId": web_map_id, "parquetFile": parquet_file})

    return entries, missing


def main() -> None:
    args = parse_args()
    web_map = read_json(Path(args.web_map))
    mapping = read_json(Path(args.mapping))

    if not isinstance(mapping, list):
        raise SystemExit("Mapping JSON must be an array of objects.")

    web_map_entries = collect_web_map_entries(web_map)
    entries, missing = build_parquet_renderer_entries(mapping, web_map_entries, args.request_timeout)
    write_json(Path(args.output), entries)

    print(f"Wrote {len(entries)} Parquet renderer entries to {args.output}")
    print(f"Found renderers for {len(entries) - len(missing)} entries")
    if missing:
        print(f"No renderer found after web map, URL, and related-layer fallback for {len(missing)} mapped entries")


if __name__ == "__main__":
    main()