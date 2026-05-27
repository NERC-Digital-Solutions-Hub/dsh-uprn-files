#!/usr/bin/env python3
"""Create a GeoParquet-backed ArcGIS web map from a mapped source web map.

The script reads an ArcGIS web map JSON file and a mapping file produced by
``scripts/map_layers_to_parquet.py``. For each mapped web map layer, it keeps the
existing layer id and title/name values, changes the layer type to
``ParquetLayer``, and replaces the layer URL with a URL built from the supplied
base URL and the mapped parquet file path. Unmapped layer containers with child
layers are converted to ``GroupLayer`` entries and have service-specific ``url``
and ``itemId`` values removed.

MapServer child sublayers are matched with the same synthetic ID convention used
by the mapper: a numeric child id is addressed as ``<parent-id>-<child-id>`` in
the mapping file, while the output web map keeps the original child ``id`` value.

Example:
    python scripts/create_parquet_webmap.py ^
      --web-map data-with-mapservice-children.json ^
      --base-url https://example.com/parquet-data ^
      --parquet-dir parquet-data ^
      --mapping layer-parquet-mapping-parquet-data-recursive/layer-parquet-map.json ^
      --output data-parquet-webmap.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class MappingEntry:
    """A validated web map layer to parquet file mapping."""

    web_map_id: str
    parquet_file: str


@dataclass(frozen=True)
class Replacement:
    """Resolved replacement data for one mapped web map layer."""

    web_map_id: str
    parquet_file: str
    url: str


@dataclass(frozen=True)
class ReplacementStats:
    """Summary counts for a completed web map conversion."""

    mapped_entries: int
    replaced_layers: int
    converted_group_layers: int
    missing_web_map_ids: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the web map conversion."""

    parser = argparse.ArgumentParser(
        description="Create a web map whose mapped layers load GeoParquet files as ParquetLayer entries."
    )
    parser.add_argument(
        "--web-map",
        default="data-with-mapservice-children.json",
        help="Path to the source web map JSON file.",
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="Base URL that serves the parquet directory. Relative parquet paths are appended to this URL.",
    )
    parser.add_argument(
        "--parquet-dir",
        required=True,
        help="Directory containing mapped .parquet files. Used to validate and relativize mapped paths.",
    )
    parser.add_argument(
        "--mapping",
        required=True,
        help="Path to JSON array containing webMapId and parquetFile values.",
    )
    parser.add_argument(
        "--output",
        default="data-parquet-webmap.json",
        help="Path for the generated web map JSON file.",
    )
    parser.add_argument(
        "--allow-missing-files",
        action="store_true",
        help="Build URLs even when mapped parquet files are not present in --parquet-dir.",
    )
    parser.add_argument(
        "--fail-on-unmapped",
        action="store_true",
        help="Exit with an error when a mapping entry does not match any layer in the web map.",
    )
    return parser.parse_args()


def read_json(path: Path) -> Any:
    """Read a UTF-8 JSON file."""

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data: Any) -> None:
    """Write a UTF-8 JSON file with stable two-space indentation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
        file.write("\n")


def is_numeric_layer_id(value: Any) -> bool:
    """Return True when a web map layer id is an ArcGIS numeric sublayer id."""

    if isinstance(value, int):
        return True
    if isinstance(value, str):
        return value.isdigit()
    return False


def web_map_layer_id(raw_id: Any, parent_id: str | None) -> str | None:
    """Return the mapping ID for a layer, including synthetic child IDs."""

    if raw_id is None:
        return None

    raw_id_text = str(raw_id)
    if parent_id and is_numeric_layer_id(raw_id):
        return f"{parent_id}-{raw_id_text}"
    return raw_id_text


def iter_web_map_layers(web_map: JsonObject) -> Iterable[tuple[str, JsonObject]]:
    """Yield ``(mapping_id, layer_object)`` pairs from operational layers and tables.

    The traversal follows ``operationalLayers``, nested ``layers``, and ``tables``.
    Numeric child layer IDs are yielded with the synthetic ID format used by the
    layer-to-parquet mapper.
    """

    def walk(value: Any, parent_id: str | None = None) -> Iterable[tuple[str, JsonObject]]:
        if isinstance(value, dict):
            current_id = web_map_layer_id(value.get("id"), parent_id)
            if current_id is not None:
                yield current_id, value

            children = value.get("layers")
            if isinstance(children, list):
                for child in children:
                    yield from walk(child, current_id or parent_id)

        elif isinstance(value, list):
            for item in value:
                yield from walk(item, parent_id)

    for collection_name in ("operationalLayers", "tables"):
        collection = web_map.get(collection_name)
        if isinstance(collection, list):
            yield from walk(collection)


def load_mapping(path: Path) -> dict[str, MappingEntry]:
    """Load and validate mapping entries keyed by web map ID."""

    raw_mapping = read_json(path)
    if not isinstance(raw_mapping, list):
        raise ValueError("Mapping JSON must be an array of objects.")

    mapping: dict[str, MappingEntry] = {}
    for index, item in enumerate(raw_mapping):
        if not isinstance(item, dict):
            raise ValueError(f"Mapping entry {index} is not an object.")

        web_map_id = str(item.get("webMapId") or "").strip()
        parquet_file = str(item.get("parquetFile") or "").strip()
        if not web_map_id or not parquet_file:
            raise ValueError(f"Mapping entry {index} must include webMapId and parquetFile.")
        if web_map_id in mapping:
            raise ValueError(f"Duplicate mapping for webMapId {web_map_id!r}.")

        mapping[web_map_id] = MappingEntry(web_map_id=web_map_id, parquet_file=parquet_file)

    return mapping


def relative_parquet_path(parquet_dir: Path, parquet_file: str, allow_missing_files: bool) -> Path:
    """Resolve a mapped parquet file to a path relative to ``parquet_dir``.

    ``parquetFile`` values are usually already relative paths. Absolute paths are
    accepted when they point inside ``parquet_dir``.
    """

    parquet_root = parquet_dir.resolve()
    mapped_path = Path(parquet_file)
    absolute_path = mapped_path if mapped_path.is_absolute() else parquet_root / mapped_path
    absolute_path = absolute_path.resolve(strict=False)

    try:
        relative_path = absolute_path.relative_to(parquet_root)
    except ValueError as error:
        raise ValueError(f"Mapped parquet file is outside parquet directory: {parquet_file}") from error

    if not allow_missing_files and not absolute_path.is_file():
        raise FileNotFoundError(f"Mapped parquet file does not exist: {absolute_path}")

    return relative_path


def parquet_url(base_url: str, relative_path: Path) -> str:
    """Build a URL by appending a parquet directory-relative path to ``base_url``."""

    normalized_base_url = base_url if base_url.endswith("/") else f"{base_url}/"
    quoted_path = quote(relative_path.as_posix(), safe="/")
    return f"{normalized_base_url}{quoted_path}"


def build_replacements(
    mapping: dict[str, MappingEntry], base_url: str, parquet_dir: Path, allow_missing_files: bool
) -> dict[str, Replacement]:
    """Resolve all mapping entries to final ParquetLayer replacement URLs."""

    replacements = {}
    for web_map_id, entry in mapping.items():
        relative_path = relative_parquet_path(parquet_dir, entry.parquet_file, allow_missing_files)
        replacements[web_map_id] = Replacement(
            web_map_id=web_map_id,
            parquet_file=entry.parquet_file,
            url=parquet_url(base_url, relative_path),
        )
    return replacements


def apply_replacements(web_map: JsonObject, replacements: dict[str, Replacement]) -> ReplacementStats:
    """Convert mapped layers and normalize unmapped child containers in place."""

    replaced_ids: set[str] = set()
    converted_group_layers = 0

    for layer_id, layer in iter_web_map_layers(web_map):
        replacement = replacements.get(layer_id)
        if replacement is not None:
            layer["layerType"] = "ParquetLayer"
            layer["url"] = replacement.url
            replaced_ids.add(layer_id)
            continue

        children = layer.get("layers")
        if isinstance(children, list) and children:
            layer["layerType"] = "GroupLayer"
            layer.pop("url", None)
            layer.pop("itemId", None)
            converted_group_layers += 1

    missing_ids = tuple(sorted(set(replacements) - replaced_ids))
    return ReplacementStats(
        mapped_entries=len(replacements),
        replaced_layers=len(replaced_ids),
        converted_group_layers=converted_group_layers,
        missing_web_map_ids=missing_ids,
    )


def main() -> None:
    """Run the web map conversion from command-line arguments."""

    args = parse_args()
    web_map = read_json(Path(args.web_map))
    if not isinstance(web_map, dict):
        raise SystemExit("Web map JSON must be an object.")

    try:
        mapping = load_mapping(Path(args.mapping))
        replacements = build_replacements(
            mapping=mapping,
            base_url=args.base_url,
            parquet_dir=Path(args.parquet_dir),
            allow_missing_files=args.allow_missing_files,
        )
        stats = apply_replacements(web_map, replacements)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error

    if stats.missing_web_map_ids:
        print(
            f"Warning: {len(stats.missing_web_map_ids)} mapping entrie(s) did not match a web map layer.",
            file=sys.stderr,
        )
        for web_map_id in stats.missing_web_map_ids[:25]:
            print(f"- {web_map_id}", file=sys.stderr)
        if len(stats.missing_web_map_ids) > 25:
            print(f"- ... {len(stats.missing_web_map_ids) - 25} more", file=sys.stderr)
        if args.fail_on_unmapped:
            raise SystemExit(1)

    write_json(Path(args.output), web_map)
    print(f"Wrote {args.output}")
    print(f"Converted {stats.replaced_layers}/{stats.mapped_entries} mapped layer entrie(s) to ParquetLayer.")
    print(f"Converted {stats.converted_group_layers} unmapped child container layer(s) to GroupLayer.")


if __name__ == "__main__":
    main()