#!/usr/bin/env python3
"""Apply renderer mappings to Parquet layers in an ArcGIS webmap JSON."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


JsonObject = dict[str, Any]
SUPPORTED_POLICIES = {"fill-missing", "fill-missing-or-repair-placeholder-fields"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply renderer mappings to descendant ParquetLayer entries in a webmap JSON."
    )
    parser.add_argument("--mapping", required=True, type=Path, help="Renderer mapping JSON path.")
    parser.add_argument("--webmap", required=True, type=Path, help="Input webmap JSON path.")
    parser.add_argument("--output", required=True, type=Path, help="Output webmap JSON path.")
    parser.add_argument(
        "--parquet-root",
        type=Path,
        help="Local root containing Parquet files for rendererRange.mode='parquet-min-max'."
    )
    return parser.parse_args()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(value, file, indent=2, ensure_ascii=False)
        file.write("\n")


def resolve_relative_path(path: str, base_path: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate

    relative_to_mapping = base_path.parent / candidate
    if relative_to_mapping.exists():
        return relative_to_mapping

    return Path.cwd() / candidate


def layer_name(layer: JsonObject) -> str:
    return str(layer.get("title") or layer.get("name") or layer.get("id"))


def layer_url_basename(layer: JsonObject) -> str | None:
    url = layer.get("url")
    if not isinstance(url, str):
        urls = layer.get("urls")
        if isinstance(urls, list) and urls and isinstance(urls[0], str):
            url = urls[0]

    if not isinstance(url, str):
        return None

    path = unquote(urlparse(url).path)
    basename = Path(path).name
    return basename or None


def layer_url_path(layer: JsonObject) -> str | None:
    url = layer.get("url")
    if not isinstance(url, str):
        urls = layer.get("urls")
        if isinstance(urls, list) and urls and isinstance(urls[0], str):
            url = urls[0]

    if not isinstance(url, str):
        return None

    return unquote(urlparse(url).path).lstrip("/")


def child_layers(layer: JsonObject) -> list[JsonObject]:
    layers = layer.get("layers")
    return layers if isinstance(layers, list) else []


def resolve_layer_path(webmap: JsonObject, path: list[str]) -> JsonObject | None:
    candidates = webmap.get("operationalLayers")
    if not isinstance(candidates, list):
        return None

    current: JsonObject | None = None
    for segment in path:
        matches = [layer for layer in candidates if layer_name(layer) == segment]
        if len(matches) != 1:
            return None

        current = matches[0]
        candidates = child_layers(current)

    return current


def descendant_parquet_layers(layer: JsonObject) -> list[JsonObject]:
    layers: list[JsonObject] = []

    if layer.get("layerType") == "ParquetLayer":
        layers.append(layer)

    for child in child_layers(layer):
        layers.extend(descendant_parquet_layers(child))

    return layers


def has_renderer(layer: JsonObject) -> bool:
    return existing_renderer(layer) is not None


def existing_renderer(layer: JsonObject) -> JsonObject | None:
    renderer = layer.get("renderer")
    if isinstance(renderer, dict):
        return renderer

    drawing_info = layer.get("drawingInfo")
    renderer = drawing_info.get("renderer") if isinstance(drawing_info, dict) else None
    if isinstance(renderer, dict):
        return renderer

    layer_definition = layer.get("layerDefinition")
    if not isinstance(layer_definition, dict):
        return None

    layer_drawing_info = layer_definition.get("drawingInfo")
    renderer = layer_drawing_info.get("renderer") if isinstance(layer_drawing_info, dict) else None
    return renderer if isinstance(renderer, dict) else None


def set_layer_definition_renderer(layer: JsonObject, renderer: JsonObject) -> None:
    layer_definition = layer.setdefault("layerDefinition", {})
    if not isinstance(layer_definition, dict):
        raise ValueError(f"layerDefinition is not an object for layer {layer_name(layer)!r}.")

    drawing_info = layer_definition.setdefault("drawingInfo", {})
    if not isinstance(drawing_info, dict):
        raise ValueError(f"layerDefinition.drawingInfo is not an object for layer {layer_name(layer)!r}.")

    drawing_info["renderer"] = copy.deepcopy(renderer)


def clear_renderers(layer: JsonObject) -> None:
    layer.pop("renderer", None)

    drawing_info = layer.get("drawingInfo")
    if isinstance(drawing_info, dict):
        drawing_info.pop("renderer", None)

    layer_definition = layer.get("layerDefinition")
    if isinstance(layer_definition, dict):
        layer_drawing_info = layer_definition.get("drawingInfo")
        if isinstance(layer_drawing_info, dict):
            layer_drawing_info.pop("renderer", None)


def renderer_field_source(field_config: Any) -> str:
    if isinstance(field_config, dict) and isinstance(field_config.get("sourceField"), str):
        return field_config["sourceField"]

    return "Value"


def resolve_renderer_field(layer: JsonObject, field_config: Any) -> tuple[str | None, bool, str | None]:
    if field_config is None:
        return None, False, None

    if isinstance(field_config, str):
        return field_config, False, None

    if not isinstance(field_config, dict):
        return None, False, f"Invalid rendererField config for layer {layer_name(layer)!r}."

    field = field_config.get("field")
    if isinstance(field, str):
        return field, False, None

    basename = layer_url_basename(layer)
    fields_by_basename = field_config.get("byUrlBasename")
    if isinstance(fields_by_basename, dict) and isinstance(basename, str):
        field = fields_by_basename.get(basename)
        if isinstance(field, str):
            return field, False, None
        if field is None and basename in fields_by_basename:
            return None, True, None

    if field_config.get("skipWithoutMatch") is True:
        return None, True, None

    return None, False, f"No renderer field match for layer {layer_name(layer)!r}."


def replace_renderer_field(value: Any, source_field: str, target_field: str) -> Any:
    if isinstance(value, list):
        return [replace_renderer_field(item, source_field, target_field) for item in value]

    if not isinstance(value, dict):
        return value

    replaced: JsonObject = {}
    for key, child_value in value.items():
        if key == "field" and child_value == source_field:
            replaced[key] = target_field
        else:
            replaced[key] = replace_renderer_field(child_value, source_field, target_field)

    return replaced


def parquet_path_for_layer(layer: JsonObject, parquet_root: Path) -> Path | None:
    url_path = layer_url_path(layer)
    if url_path is None:
        return None

    path_parts = Path(url_path).parts
    if path_parts and path_parts[0].lower() == "parquet-data":
        path_parts = path_parts[1:]

    parquet_path = (parquet_root / Path(*path_parts)).resolve()
    parquet_root = parquet_root.resolve()

    if parquet_root not in parquet_path.parents and parquet_path != parquet_root:
        return None

    return parquet_path


def parquet_min_max(layer: JsonObject, field: str, parquet_root: Path) -> tuple[float, float]:
    try:
        import duckdb
    except ImportError as error:
        raise RuntimeError("DuckDB is required for rendererRange.mode='parquet-min-max'.") from error

    parquet_path = parquet_path_for_layer(layer, parquet_root)
    if parquet_path is None:
        raise ValueError(f"Could not resolve a local Parquet path for layer {layer_name(layer)!r}.")
    if not parquet_path.exists():
        raise ValueError(f"Resolved Parquet file does not exist for layer {layer_name(layer)!r}: {parquet_path}")

    with duckdb.connect() as connection:
        row = connection.execute(
            f'SELECT min("{field}"), max("{field}") FROM read_parquet(?)',
            [str(parquet_path)]
        ).fetchone()

    if row is None or row[0] is None or row[1] is None:
        raise ValueError(f"No numeric min/max values found for field {field!r} in {parquet_path}.")

    return float(row[0]), float(row[1])


def apply_renderer_range(renderer: JsonObject, min_value: float, max_value: float) -> JsonObject:
    ranged_renderer = copy.deepcopy(renderer)

    visual_variables = ranged_renderer.get("visualVariables")
    if not isinstance(visual_variables, list):
        return ranged_renderer

    for visual_variable in visual_variables:
        if not isinstance(visual_variable, dict) or visual_variable.get("type") != "colorInfo":
            continue

        stops = visual_variable.get("stops")
        if not isinstance(stops, list) or len(stops) < 2:
            continue

        stop_values = [min_value]
        if len(stops) > 2:
            interval = (max_value - min_value) / (len(stops) - 1)
            stop_values.extend(min_value + interval * index for index in range(1, len(stops) - 1))
        stop_values.append(max_value)

        for stop, stop_value in zip(stops, stop_values):
            if not isinstance(stop, dict):
                continue
            stop["value"] = stop_value
            stop["label"] = str(stop_value)

    return ranged_renderer


def renderer_contains_placeholder_field(value: Any, source_field: str) -> bool:
    if isinstance(value, list):
        return any(renderer_contains_placeholder_field(item, source_field) for item in value)

    if not isinstance(value, dict):
        return False

    for key, child_value in value.items():
        if key == "field" and child_value == source_field:
            return True
        if renderer_contains_placeholder_field(child_value, source_field):
            return True

    return False


def renderer_for_layer(
    renderer: JsonObject,
    layer: JsonObject,
    field_config: Any,
    range_config: Any,
    parquet_root: Path | None
) -> tuple[JsonObject | None, str | None]:
    field, should_skip, error = resolve_renderer_field(layer, field_config)
    if error is not None:
        return None, error
    if should_skip:
        return None, None

    source_field = renderer_field_source(field_config)
    layer_renderer = copy.deepcopy(renderer) if field is None else replace_renderer_field(renderer, source_field, field)

    if isinstance(range_config, dict) and range_config.get("mode") == "parquet-min-max":
        if field is None:
            return None, f"rendererRange.mode='parquet-min-max' requires a mapped field for layer {layer_name(layer)!r}."
        if parquet_root is None:
            return None, "rendererRange.mode='parquet-min-max' requires --parquet-root."
        try:
            min_value, max_value = parquet_min_max(layer, field, parquet_root)
        except (RuntimeError, ValueError) as error:
            return None, str(error)
        layer_renderer = apply_renderer_range(layer_renderer, min_value, max_value)

    return layer_renderer, None


def apply_mapping(
    webmap: JsonObject,
    renderer_source: JsonObject,
    mapping: JsonObject,
    policy: str,
    parquet_root: Path | None
) -> tuple[dict[str, int], list[str]]:
    renderers = renderer_source.get("renderers")
    if not isinstance(renderers, dict):
        raise ValueError("Renderer source JSON must contain a renderers object.")

    stats = {
        "mappingsProcessed": 0,
        "layersMatched": 0,
        "layersUpdated": 0,
        "layersRepairedPlaceholderRenderer": 0,
        "layersRemovedPlaceholderRenderer": 0,
        "layersSkippedExistingRenderer": 0,
        "layersSkippedNoRendererField": 0
    }
    errors: list[str] = []

    renderer_key = mapping.get("rendererKey")
    target_group_path = mapping.get("targetGroupPath")
    selection = mapping.get("selection")
    range_config = mapping.get("rendererRange")
    should_replace_existing = isinstance(range_config, dict) and range_config.get("mode") == "parquet-min-max"

    stats["mappingsProcessed"] += 1

    if not isinstance(renderer_key, str) or renderer_key not in renderers:
        errors.append(f"Unresolved renderer key: {renderer_key!r}")
        return stats, errors

    if not isinstance(target_group_path, list) or not all(isinstance(part, str) for part in target_group_path):
        errors.append(f"Invalid targetGroupPath for renderer {renderer_key!r}.")
        return stats, errors

    if selection != "descendant-parquet-layers":
        errors.append(
            f"Unsupported selection {selection!r} for target {' > '.join(target_group_path)}."
        )
        return stats, errors

    if range_config is not None:
        if not isinstance(range_config, dict):
            errors.append(f"Invalid rendererRange for target {' > '.join(target_group_path)}.")
            return stats, errors
        if range_config.get("mode") != "parquet-min-max":
            errors.append(
                f"Unsupported rendererRange mode {range_config.get('mode')!r} for target "
                f"{' > '.join(target_group_path)}."
            )
            return stats, errors

    target_group = resolve_layer_path(webmap, target_group_path)
    if target_group is None:
        errors.append(f"Unresolved target group path: {' > '.join(target_group_path)}")
        return stats, errors

    layers = descendant_parquet_layers(target_group)
    if not layers:
        errors.append(f"No descendant ParquetLayer entries found for: {' > '.join(target_group_path)}")
        return stats, errors

    renderer = renderers[renderer_key]
    if not isinstance(renderer, dict):
        errors.append(f"Renderer {renderer_key!r} is not a JSON object.")
        return stats, errors

    stats["layersMatched"] += len(layers)

    for layer in layers:
        existing = existing_renderer(layer)
        if existing is not None:
            if should_replace_existing:
                layer_renderer, renderer_error = renderer_for_layer(
                    renderer,
                    layer,
                    mapping.get("rendererField"),
                    range_config,
                    parquet_root
                )
                if renderer_error is not None:
                    errors.append(renderer_error)
                    continue
                if layer_renderer is None:
                    clear_renderers(layer)
                    stats["layersSkippedNoRendererField"] += 1
                    stats["layersRemovedPlaceholderRenderer"] += 1
                    continue

                set_layer_definition_renderer(layer, layer_renderer)
                stats["layersRepairedPlaceholderRenderer"] += 1
                continue

            if policy == "fill-missing":
                stats["layersSkippedExistingRenderer"] += 1
                continue

            field_config = mapping.get("rendererField")
            source_field = renderer_field_source(field_config)
            if not renderer_contains_placeholder_field(existing, source_field):
                stats["layersSkippedExistingRenderer"] += 1
                continue

            layer_renderer, renderer_error = renderer_for_layer(
                existing,
                layer,
                field_config,
                range_config,
                parquet_root
            )
            if renderer_error is not None:
                errors.append(renderer_error)
                continue
            if layer_renderer is None:
                clear_renderers(layer)
                stats["layersSkippedNoRendererField"] += 1
                stats["layersRemovedPlaceholderRenderer"] += 1
                continue

            set_layer_definition_renderer(layer, layer_renderer)
            stats["layersRepairedPlaceholderRenderer"] += 1
            continue

        layer_renderer, renderer_error = renderer_for_layer(
            renderer,
            layer,
            mapping.get("rendererField"),
            range_config,
            parquet_root
        )
        if renderer_error is not None:
            errors.append(renderer_error)
            continue
        if layer_renderer is None:
            stats["layersSkippedNoRendererField"] += 1
            continue

        set_layer_definition_renderer(layer, layer_renderer)
        stats["layersUpdated"] += 1

    return stats, errors


def merge_stats(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + value


def main() -> int:
    args = parse_args()
    mapping_path = args.mapping.resolve()
    webmap_path = args.webmap.resolve()
    output_path = args.output.resolve()
    parquet_root = args.parquet_root.resolve() if args.parquet_root is not None else None

    mapping_config = read_json(mapping_path)
    webmap = read_json(webmap_path)

    renderer_source_path = resolve_relative_path(str(mapping_config["rendererSource"]), mapping_path).resolve()
    renderer_source = read_json(renderer_source_path)

    policy = mapping_config.get("existingRendererPolicy")
    if policy not in SUPPORTED_POLICIES:
        supported = ", ".join(sorted(SUPPORTED_POLICIES))
        raise ValueError(f"Unsupported existingRendererPolicy={policy!r}. Supported: {supported}.")

    mappings = mapping_config.get("mappings")
    if not isinstance(mappings, list):
        raise ValueError("Mapping JSON must contain a mappings array.")

    total_stats = {
        "mappingsProcessed": 0,
        "layersMatched": 0,
        "layersUpdated": 0,
        "layersRepairedPlaceholderRenderer": 0,
        "layersRemovedPlaceholderRenderer": 0,
        "layersSkippedExistingRenderer": 0,
        "layersSkippedNoRendererField": 0
    }
    errors: list[str] = []

    for mapping in mappings:
        if not isinstance(mapping, dict):
            errors.append(f"Invalid mapping entry: {mapping!r}")
            continue

        stats, mapping_errors = apply_mapping(webmap, renderer_source, mapping, policy, parquet_root)
        merge_stats(total_stats, stats)
        errors.extend(mapping_errors)

    used_renderer_keys = {
        mapping.get("rendererKey")
        for mapping in mappings
        if isinstance(mapping, dict) and isinstance(mapping.get("rendererKey"), str)
    }
    renderer_keys = set(renderer_source.get("renderers", {}).keys())
    unused_renderer_keys = sorted(renderer_keys - used_renderer_keys)

    summary = {
        **total_stats,
        "unresolvedCount": len(errors),
        "unusedRendererKeys": unused_renderer_keys,
        "output": str(output_path)
    }

    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if errors:
        print("Errors:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    write_json(output_path, webmap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
