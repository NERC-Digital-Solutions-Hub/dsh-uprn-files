# ArcGIS SvelteKit GeoParquet Viewer

A small SvelteKit proof-of-concept for testing whether ArcGIS Maps SDK for JavaScript can render your GeoParquet files using the current **map components** approach, not widget construction.

The app uses:

- SvelteKit
- ArcGIS Maps SDK for JavaScript
- `@arcgis/map-components`
- `ParquetLayer`
- `parquetUtils.getParquetLayerInfo()`

## Why this project exists

Your source conversion workflow creates one GeoParquet file per FileGDB feature class. This project lets you copy those `.parquet` files into the app and test whether ArcGIS JS can:

- read the GeoParquet metadata
- infer geometry encoding, geometry type, fields, and spatial reference
- render the file as a `ParquetLayer`
- show the layer in the map component, legend, and layer list
- query a feature count

## ArcGIS JS GeoParquet compatibility checklist

Before testing, make sure your GeoParquet export is compatible with ArcGIS JS `ParquetLayer`:

1. Use **Snappy**, **GZIP**, or uncompressed Parquet. Do **not** use ZSTD.
2. Use **EPSG:4326 / WGS84** or **EPSG:3857 / Web Mercator**.
3. Keep one feature class per `.parquet` file.
4. Use WKB geometry encoding unless you have a reason to use another supported encoding.
5. If using multiple URLs in one logical `ParquetLayer`, the files should represent partitions of the same layer and should share the same schema and geometry type.

The app is designed to load each URL as a separate `ParquetLayer`, which matches the "each feature class should be a GeoParquet file" task.

## Quick start

Install dependencies:

```bash
npm install
```

Copy one or more GeoParquet files into:

```text
parquet-data/
```

Example:

```text
parquet-data/Addresses.parquet
parquet-data/Roads.parquet
parquet-data/Buildings.parquet
```

Start the dev server:

```bash
npm run dev
```

Open the local URL printed by Vite, usually:

```text
http://localhost:5173
```

In the app, enter one URL per line:

```text
/parquet/Addresses.parquet
/parquet/Roads.parquet
/parquet/Buildings.parquet
```

Click **Load GeoParquet**.

## Project structure

```text
arcgis-sveltekit-geoparquet-viewer/
  src/routes/+page.svelte      Main map test page
  src/routes/+layout.ts        Disables SSR for browser-only ArcGIS components
  parquet-data/                Put test .parquet files here
  src/routes/parquet/[file]/   Streams local Parquet files with byte-range support
  package.json
  vite.config.ts
  svelte.config.js
```

## Troubleshooting

### The file fails to load

Check the browser console and confirm the URL works directly. For example:

```text
http://localhost:5173/data/Addresses.parquet
```

For files served by this app's range-aware route, use:

```text
http://localhost:5173/parquet/Addresses.parquet
```

If the browser returns 404, the file is not in `parquet-data` or the URL is misspelled.

### ArcGIS reports unsupported compression

Regenerate the GeoParquet with Snappy or GZIP compression. For GDAL/OGR, use:

```bash
-lco COMPRESSION=SNAPPY
```

### ArcGIS reports unsupported spatial reference

Regenerate the GeoParquet as WGS84 or Web Mercator. For GDAL/OGR, use:

```bash
-t_srs EPSG:4326
```

### The layer loads but nothing appears

Try these checks:

- Click **Update view** after setting a suitable center and zoom.
- Leave **Zoom to loaded layer extent** enabled.
- Confirm the file has features.
- Confirm the CRS is correct.
- Inspect the file with `ogrinfo file.parquet -al -so`.

### Multiple files

This app intentionally loads each URL as a separate `ParquetLayer`. That is usually correct when each feature class is a separate GeoParquet file.

If you have multiple Parquet files that are partitions of the same logical layer, use a single `ParquetLayer` with multiple URLs. That would require a small code change in `loadGeoParquet()`.

## Layer to Parquet mapping

The repository includes a Python utility that maps URL-backed ArcGIS web map entries from `data.json` to local `.parquet` files in `parquet-data/`.

Install the Python dependency:

```bash
python -m pip install -r requirements-mapping.txt
```

Run deterministic matching only:

```bash
python scripts/map_layers_to_parquet.py --llm-provider none
```

Run with OpenAI-compatible LLM reranking after deterministic top-3 candidate selection:

```bash
set OPENAI_API_KEY=your-key
set OPENAI_MODEL=gpt-4o-mini
python scripts/map_layers_to_parquet.py --llm-provider openai --require-llm
```

Outputs are written to `layer-parquet-mapping/` by default:

- `layers/`: one trimmed JSON file per downloaded web map layer/table ID
- `layer-metadata.json`: all trimmed layer metadata
- `parquet-metadata.json`: schema-only Parquet metadata
- `deterministic-candidates.json`: top deterministic candidates per layer
- `llm-candidates.json`: LLM reranked candidates, or deterministic fallback candidates
- `layer-parquet-map.json`: final `webMapId` to `parquetFile` mapping
- `layer-parquet-debug.json`: selected candidate, score, reason, alternatives, and manual review flag

Layer metadata includes the ArcGIS REST `type`, `geometryType`, and `hasGeometry` values. Parquet metadata includes GeoParquet geometry information from the schema-level `geo` metadata. When a Parquet file has geometry, the matcher blocks it from matching ArcGIS tables or other non-geometry resources.

Generate Parquet renderer mappings from `data.json` and a completed layer mapping:

```bash
python scripts/create_parquet_renderer_json.py --mapping layer-parquet-mapping/layer-parquet-map.json
```

This writes `static/parquet-renderers.json`, a single JSON array of `{ "parquetFile", "renderer" }` objects. The Svelte app loads this file from `/parquet-renderers.json` and applies matching ArcGIS renderer JSON to loaded `ParquetLayer` instances. Entries with no renderer fall back to the app's default geometry renderer.

If a mapped web map entry has no renderer, the script fetches that entry's ArcGIS REST layer resource with `f=pjson&returnAdvancedSymbols=true` and reads `drawingInfo.renderer`, which is the renderer location documented for Feature Service layer JSON. If the URL is a nonspatial table, the script also looks for a related same-service web map layer with matching fields and title tokens, then reuses that layer's renderer.

## Build

```bash
npm run build
npm run preview
```

## Notes

This is a testing project, not a production map application. For a production web map, you may still prefer PostGIS + vector tiles or a map service, depending on performance and data size.
