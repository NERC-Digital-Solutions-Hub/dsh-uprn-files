#!/usr/bin/env python3
"""Add missing ArcGIS MapServer child layers to a web map JSON file."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


USER_AGENT = "webmap-mapservice-child-expander/1.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find ArcGISMapServiceLayer entries in an ArcGIS web map JSON file and "
            "append missing child sublayers declared by each MapServer REST endpoint."
        )
    )
    parser.add_argument("--web-map", default="data.json", help="Path to the web map JSON file.")
    parser.add_argument(
        "--output",
        help=(
            "Path for the modified web map JSON. Defaults to updating --web-map in place. "
            "Use this for a dry output file without touching the original."
        ),
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not write a .bak copy when updating the web map in place.",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=45.0,
        help="HTTP timeout in seconds for ArcGIS REST requests.",
    )
    parser.add_argument(
        "--include-tables",
        action="store_true",
        help="Also add MapServer table entries returned by the service. Disabled by default.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-layer details for services that are already complete or fail to fetch.",
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


def arcgis_json_url(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["f"] = "pjson"
    return urlunparse(parsed._replace(query=urlencode(query)))


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    request = Request(arcgis_json_url(url), headers={"User-Agent": USER_AGENT}, method="GET")
    with urlopen(request, timeout=timeout) as response:
        downloaded = json.loads(response.read().decode("utf-8"))

    if not isinstance(downloaded, dict):
        raise ValueError("ArcGIS REST response was not a JSON object")
    if downloaded.get("error"):
        raise ValueError(f"ArcGIS REST error: {downloaded['error']}")
    return downloaded


def map_service_root_url(url: str) -> str:
    parsed = urlparse(url)
    parts = parsed.path.rstrip("/").split("/")
    for server_type in ("MapServer", "mapserver"):
        if server_type in parts:
            index = parts.index(server_type)
            root_path = "/".join(parts[: index + 1])
            return urlunparse(parsed._replace(path=root_path, params="", query="", fragment=""))
    return url


def iter_map_service_layers(web_map: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    found: list[tuple[dict[str, Any], str]] = []

    def walk(value: Any, path: list[str]) -> None:
        if isinstance(value, dict):
            if value.get("layerType") == "ArcGISMapServiceLayer" and value.get("url"):
                found.append((value, ".".join(path)))

            children = value.get("layers")
            if isinstance(children, list):
                for index, child in enumerate(children):
                    walk(child, [*path, "layers", str(index)])
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, [*path, str(index)])

    for index, layer in enumerate(web_map.get("operationalLayers", [])):
        walk(layer, ["operationalLayers", str(index)])

    return found


def declared_child_ids(layer: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    children = layer.get("layers")
    if not isinstance(children, list):
        return ids

    for child in children:
        if not isinstance(child, dict) or child.get("id") is None:
            continue
        try:
            ids.add(int(child["id"]))
        except (TypeError, ValueError):
            continue
    return ids


def service_child_entries(service_json: dict[str, Any], include_tables: bool) -> list[dict[str, Any]]:
    children = []
    for child in service_json.get("layers") or []:
        if isinstance(child, dict) and child.get("id") is not None:
            children.append(child)

    if include_tables:
        for table in service_json.get("tables") or []:
            if isinstance(table, dict) and table.get("id") is not None:
                table_entry = deepcopy(table)
                table_entry.setdefault("type", "Table")
                children.append(table_entry)

    children.sort(key=lambda item: int(item.get("id", 0)))
    return children


def webmap_child_from_service_child(child: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": int(child["id"])}

    name = child.get("name")
    if name:
        entry["name"] = name

    for key in ("parentLayerId", "defaultVisibility", "minScale", "maxScale", "type"):
        if child.get(key) is not None:
            entry[key] = child[key]

    sublayer_ids = child.get("subLayerIds")
    if isinstance(sublayer_ids, list):
        entry["subLayerIds"] = sublayer_ids

    return entry


def add_missing_children(
    layer: dict[str, Any], service_json: dict[str, Any], include_tables: bool
) -> list[dict[str, Any]]:
    declared_ids = declared_child_ids(layer)
    missing_children = [
        child
        for child in service_child_entries(service_json, include_tables)
        if int(child["id"]) not in declared_ids
    ]

    if not missing_children:
        return []

    children = layer.get("layers")
    if not isinstance(children, list):
        children = []
        layer["layers"] = children

    new_entries = [webmap_child_from_service_child(child) for child in missing_children]
    children.extend(new_entries)
    return new_entries


def main() -> None:
    args = parse_args()
    web_map_path = Path(args.web_map)
    output_path = Path(args.output) if args.output else web_map_path
    web_map = read_json(web_map_path)

    if not isinstance(web_map, dict):
        raise SystemExit("Web map JSON must be an object.")

    map_service_layers = iter_map_service_layers(web_map)
    failures = []
    updated_services = 0
    added_children = 0

    for layer, path in map_service_layers:
        title = layer.get("title") or layer.get("name") or layer.get("id") or path
        service_url = map_service_root_url(str(layer["url"]))

        try:
            service_json = fetch_json(service_url, args.request_timeout)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as error:
            failures.append({"title": str(title), "url": service_url, "error": str(error)})
            if args.verbose:
                print(f"Warning: could not inspect {title}: {error}", file=sys.stderr)
            continue

        new_entries = add_missing_children(layer, service_json, args.include_tables)
        if new_entries:
            updated_services += 1
            added_children += len(new_entries)
            print(f"Added {len(new_entries)} child layer(s) to {title}")
        elif args.verbose:
            print(f"No missing child layers for {title}")

    if output_path == web_map_path and added_children and not args.no_backup:
        backup_path = web_map_path.with_suffix(web_map_path.suffix + ".bak")
        shutil.copy2(web_map_path, backup_path)
        print(f"Backup written to {backup_path}")

    write_json(output_path, web_map)

    print(
        f"Inspected {len(map_service_layers)} ArcGISMapServiceLayer entrie(s); "
        f"updated {updated_services}; added {added_children} child layer(s)."
    )

    if failures:
        print(f"Warning: {len(failures)} service(s) could not be inspected:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure['title']}: {failure['error']} ({failure['url']})", file=sys.stderr)


if __name__ == "__main__":
    main()