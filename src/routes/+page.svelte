<svelte:options runes={true} />

<script lang="ts">
  import { base } from '$app/paths';
  import { onMount, tick } from 'svelte';
  import GeoParquetSidebar from '$lib/components/GeoParquetSidebar.svelte';
  import {
    GeoParquetPipeline,
    type GeoParquetLayerOptions
  } from '$lib/geoparquetPipeline';

  type LoadedLayerSummary = {
    title: string;
    url: string;
    geometryField: string;
    geometryType: string;
    featureCount: number | 'unknown';
  };

  type RenderBenchmark = {
    id: number;
    title: string;
    urlCount: number;
    urls: string[];
    elapsedMs: number;
    completedAt: string;
    rendered: boolean;
  };

  type ParquetDataSource = {
    id: string;
    name: string;
    urls: string[];
  };

  type ParquetManifest = {
    files?: Array<{
      name: string;
      url: string;
    }>;
  };

  type CsvRow = Record<string, string>;

  type ParquetRendererEntry = {
    parquetFile: string;
    renderer: unknown;
  };

  type WebMapLayerJson = {
    id?: string | number;
    title?: string;
    name?: string;
    url?: string;
    layerType?: string;
    visibility?: boolean;
    defaultVisibility?: boolean;
    minScale?: number;
    maxScale?: number;
    renderer?: unknown;
    drawingInfo?: {
      renderer?: unknown;
    };
    layerDefinition?: {
      minScale?: number;
      maxScale?: number;
      drawingInfo?: {
        renderer?: unknown;
      };
    };
    layers?: WebMapLayerJson[];
  };

  type WebMapJson = {
    operationalLayers?: WebMapLayerJson[];
  };

  type WebMapTreeNode = {
    key: string;
    id: string;
    title: string;
    layerType: string;
    url?: string;
    visible: boolean;
    minScale?: number;
    maxScale?: number;
    renderer?: unknown;
    children: WebMapTreeNode[];
  };

  const DEFAULT_PARQUET_FILENAME = 'CJ_EN_LSOA_2011_BFC_V3_2022.parquet';

  let mapElement = $state<any>();
  let ready = $state(false);
  let loading = $state(false);
  let status = $state('Loading ArcGIS Maps SDK components...');
  let errorMessage = $state('');

  let basemap = $state('gray');
  let center = $state('-2.5,54');
  let zoom = $state(6);
  let autoZoom = $state(true);
  let clearExisting = $state(true);
  let parquetFiles = $state<ParquetDataSource[]>([]);
  let urlParquetFiles = $state<ParquetDataSource[]>([]);
  let selectedParquetSourceIds = $state<string[]>([]);
  let parquetRenderers = $state(new Map<string, unknown>());
  let webMapTree = $state<WebMapTreeNode[]>([]);
  let webMapLoaded = $state(false);

  let loadedLayerSummaries = $state<LoadedLayerSummary[]>([]);
  let loadedLayers = $state<any[]>([]);
  let loadedWebMapRootLayers: any[] = [];
  let geoParquetPipeline: GeoParquetPipeline | undefined;
  let layerVisibilityWatchHandles: any[] = [];
  let renderBenchmarks = $state<RenderBenchmark[]>([]);
  let benchmarkId = 0;

  let ArcGISMap: any;
  let GroupLayer: any;
  let reactiveUtils:
    | {
        watch?: (condition: () => unknown, callback: (value: any) => void) => { remove?: () => void };
        whenOnce?: (condition: () => boolean) => Promise<unknown>;
      }
    | undefined;

  onMount(async () => {
    const setupDataPromise = Promise.all([
      loadParquetSources(),
      loadParquetRenderers(),
      loadWebMapTree()
    ]);

    try {
      await Promise.all([
        import('@arcgis/map-components/components/arcgis-map'),
        import('@arcgis/map-components/components/arcgis-zoom'),
        import('@arcgis/map-components/components/arcgis-home'),
        import('@arcgis/map-components/components/arcgis-legend'),
        import('@arcgis/map-components/components/arcgis-layer-list')
      ]);

      const [
        mapModule,
        extentModule,
        groupLayerModule,
        graphicsLayerModule,
        parquetLayerModule,
        parquetGeometryEncodingWkbModule,
        rendererJsonUtilsModule,
        reactiveUtilsModule,
        parquetUtilsModule
      ] = await Promise.all([
        import('@arcgis/core/Map.js'),
        import('@arcgis/core/geometry/Extent.js'),
        import('@arcgis/core/layers/GroupLayer.js'),
        import('@arcgis/core/layers/GraphicsLayer.js'),
        import('@arcgis/core/layers/ParquetLayer.js'),
        import('@arcgis/core/layers/support/ParquetGeometryEncodingWkb.js'),
        import('@arcgis/core/renderers/support/jsonUtils.js'),
        import('@arcgis/core/core/reactiveUtils.js'),
        import('@arcgis/core/layers/support/parquetUtils.js')
      ]);

      ArcGISMap = mapModule.default;
      GroupLayer = groupLayerModule.default;
      reactiveUtils = reactiveUtilsModule;

      await customElements.whenDefined('arcgis-map');
      await tick();

      mapElement.map = new ArcGISMap({ basemap: resolveBasemap(basemap) });
      mapElement.center = parseCenter(center);
      mapElement.zoom = zoom;

      await mapElement.componentOnReady();
      await mapElement.viewOnReady();

      await setupDataPromise;
      geoParquetPipeline = new GeoParquetPipeline(
        {
          ParquetLayer: parquetLayerModule.default,
          PlaceholderLayer: graphicsLayerModule.default,
          ParquetGeometryEncodingWkb: parquetGeometryEncodingWkbModule.default,
          Extent: extentModule.default,
          getParquetLayerInfo: (urls: string[]) => parquetUtilsModule.getParquetLayerInfo(urls) as Promise<any>,
          rendererFromJson: rendererJsonUtilsModule.fromJSON
        },
        parquetRenderers
      );
      ready = true;
      await loadParquetWebMap();
    } catch (error) {
      errorMessage = errorToString(error);
      status = 'Failed to initialise the map.';
      console.error(error);
    }
  });

  async function loadParquetSources(): Promise<void> {
    const [localFiles, listedUrls] = await Promise.all([
      loadAvailableParquetFiles(),
      loadParquetUrlList()
    ]);

    parquetFiles = localFiles;
    urlParquetFiles = listedUrls;

    if (selectedParquetSourceIds.length === 0) {
      const defaultFile =
        urlParquetFiles.find((file) => file.name === DEFAULT_PARQUET_FILENAME) ??
        urlParquetFiles[0] ??
        parquetFiles.find((file) => file.name === DEFAULT_PARQUET_FILENAME) ??
        parquetFiles[0];

      selectedParquetSourceIds = []; // defaultFile ? [defaultFile.id] : [];
    }
  }

  async function loadAvailableParquetFiles(): Promise<ParquetDataSource[]> {
    try {
      const response = await fetch(appPath('/parquet-files.json'), { cache: 'no-store' });

      if (!response.ok) {
        throw new Error(`Could not list parquet files (${response.status}).`);
      }

      const result = (await response.json()) as ParquetManifest;
      return Array.isArray(result.files)
        ? result.files.map((file) => {
            const url = normalizeAppAssetUrl(file.url);

            return {
              id: sourceId([url]),
              name: file.name,
              urls: [url]
            };
          })
        : [];
    } catch (error) {
      errorMessage = errorToString(error);
      status = 'Could not read parquet-data manifest.';
      return [];
    }
  }

  async function loadParquetUrlList(): Promise<ParquetDataSource[]> {
    try {
      const response = await fetch(appPath('/urls.csv'), { cache: 'no-store' });

      if (!response.ok) {
        throw new Error(`Could not read urls.csv (${response.status}).`);
      }

      const csvText = await response.text();
      const seen = new Set<string>();

      return parseCsvRows(csvText)
        .map((row) => {
          const urls = parseUrlsCell(row.urls ?? '')
            .map((url) => normalizeListedParquetUrl(url))
            .filter((url) => url.length > 0);
          const displayName = nullIfBlank(row.display_name ?? '');

          return {
            id: sourceId(urls),
            name: displayName ?? defaultSourceName(urls),
            urls
          };
        })
        .filter((source) => source.urls.length > 0)
        .filter((source) => {
          if (seen.has(source.id)) {
            return false;
          }

          seen.add(source.id);
          return true;
        });
    } catch (error) {
      console.warn('Could not read urls.csv.', error);
      return [];
    }
  }

  async function loadParquetRenderers(): Promise<void> {
    try {
      const response = await fetch(appPath('/parquet-renderers.json'), { cache: 'no-store' });

      if (!response.ok) {
        console.warn(`Could not load parquet renderer JSON (${response.status}).`);
        return;
      }

      const entries = (await response.json()) as ParquetRendererEntry[];

      parquetRenderers = new Map(
        entries
          .filter((entry) => entry?.parquetFile && entry.renderer)
          .map((entry) => [entry.parquetFile, entry.renderer])
      );
      geoParquetPipeline?.setRendererByParquetFile(parquetRenderers);
    } catch (error) {
      console.warn('Could not load parquet renderer JSON.', error);
    }
  }

  async function loadWebMapTree(): Promise<void> {
    try {
      const response = await fetch(appPath('/webmap.json'), { cache: 'no-store' });

      if (!response.ok) {
        throw new Error(`Could not read webmap.json (${response.status}).`);
      }

      webMapTree = buildWebMapTree((await response.json()) as WebMapJson);
    } catch (error) {
      webMapTree = [];
      errorMessage = errorToString(error);
      status = 'Could not read webmap.json.';
      console.error(error);
    }
  }

  function buildWebMapTree(webMap: WebMapJson): WebMapTreeNode[] {
    return (webMap.operationalLayers ?? []).map((layer, index) =>
      buildWebMapTreeNode(layer, `operationalLayers.${index}`)
    );
  }

  function buildWebMapTreeNode(layer: WebMapLayerJson, path: string): WebMapTreeNode {
    const rawId = layer.id ?? path;
    const title = layer.title || layer.name || String(rawId);
    const children = (layer.layers ?? []).map((child, index) =>
      buildWebMapTreeNode(child, `${path}.layers.${index}`)
    );

    return {
      key: `${path}:${String(rawId)}`,
      id: String(rawId),
      title,
      layerType: layer.layerType || (children.length > 0 ? 'GroupLayer' : 'Layer'),
      url: layer.url,
      visible: false,
      minScale: layer.minScale ?? layer.layerDefinition?.minScale,
      maxScale: layer.maxScale ?? layer.layerDefinition?.maxScale,
      renderer: rendererFromWebMapLayer(layer),
      children
    };
  }

  function rendererFromWebMapLayer(layer: WebMapLayerJson): unknown {
    return layer.renderer ?? layer.drawingInfo?.renderer ?? layer.layerDefinition?.drawingInfo?.renderer;
  }

  async function loadParquetWebMap(): Promise<void> {
    if (
      !ready ||
      !mapElement?.map ||
      !GroupLayer ||
      !geoParquetPipeline
    ) {
      status = 'Map is not ready yet.';
      return;
    }

    loading = true;
    errorMessage = '';
    status = 'Loading GeoParquet web map...';

    try {
      if (webMapTree.length === 0) {
        status = 'No web map layers found in webmap.json.';
        webMapLoaded = false;
        return;
      }

      if (clearExisting) {
        removeLoadedLayers();
      }

      removeLoadedWebMapLayers();

      const rootLayers: any[] = [];
      for (const node of webMapTree) {
        status = `Preparing web map layer: ${node.title}`;
        const layer = await createArcgisLayerFromWebMapNode(node);
        if (layer) {
          rootLayers.push(layer);
        }
      }

      mapElement.map.addMany(rootLayers);
      loadedWebMapRootLayers = rootLayers;
      webMapLoaded = true;
      status = `Loaded web map tree with ${rootLayers.length} root layer${rootLayers.length === 1 ? '' : 's'}.`;
    } catch (error) {
      errorMessage = errorToString(error);
      status = 'Failed to load the GeoParquet web map.';
      console.error(error);
    } finally {
      loading = false;
    }
  }

  async function createArcgisLayerFromWebMapNode(
    node: WebMapTreeNode
  ): Promise<any | null> {
    if (node.children.length > 0) {
      const groupLayer = new GroupLayer({
        id: node.key,
        title: node.title,
        visible: node.visible,
        visibilityMode: 'independent'
      });

      const childLayers: any[] = [];
      for (const child of node.children) {
        const layer = await createArcgisLayerFromWebMapNode(child);
        if (layer) {
          childLayers.push(layer);
        }
      }

      groupLayer.layers.addMany(childLayers);
      return groupLayer;
    }

    if (node.layerType !== 'ParquetLayer' || !node.url) {
      return null;
    }

    const options = webMapLayerOptions(node);
    const layer = node.visible
      ? (await requireGeoParquetPipeline().createLayer(options)).layer
      : requireGeoParquetPipeline().createPlaceholderLayer({ ...options, visible: false });

    watchParquetLayerVisibility(layer, options);
    return layer;
  }

  function webMapLayerOptions(node: WebMapTreeNode): GeoParquetLayerOptions {
    return {
      id: node.key,
      title: node.title,
      urls: [toAbsoluteUrl(normalizeAppAssetUrl(node.url ?? ''))],
      minScale: node.minScale,
      maxScale: node.maxScale,
      visible: node.visible,
      renderer: node.renderer
    };
  }

  function watchParquetLayerVisibility(layer: any, options: GeoParquetLayerOptions): void {
    const hydrateIfVisible = (visible: boolean) => {
      if (visible && !geoParquetPipeline?.getLayerResult(layer)) {
        void hydrateWebMapLayer(layer, options);
      }
    };

    const handle =
      reactiveUtils?.watch?.(() => layer.visible, hydrateIfVisible) ??
      (typeof layer.watch === 'function' ? layer.watch('visible', hydrateIfVisible) : undefined);

    if (handle?.remove) {
      layerVisibilityWatchHandles.push(handle);
    }
  }

  async function hydrateWebMapLayer(layer: any, options: GeoParquetLayerOptions): Promise<void> {
    const pipeline = requireGeoParquetPipeline();
    const shouldRestoreVisible = Boolean(layer.visible);

    try {
      layer.visible = false;
      status = `Reading GeoParquet metadata: ${options.title}`;

      if (layer.type === 'parquet') {
        await pipeline.hydrateLayer(layer, { ...options, visible: shouldRestoreVisible });
      } else {
        const result = await pipeline.createLayer({ ...options, visible: shouldRestoreVisible });
        replaceWebMapLayer(layer, result.layer);
      }

      status = `Loaded ${options.title}.`;
    } catch (error) {
      if (shouldRestoreVisible) {
        layer.visible = false;
      }
      errorMessage = errorToString(error);
      status = `Failed to prepare ${options.title}.`;
      console.error(error);
      throw error;
    }
  }

  function replaceWebMapLayer(previousLayer: any, nextLayer: any): void {
    const collection = previousLayer.parent?.layers ?? mapElement?.map?.layers;

    if (!collection) {
      mapElement.map.add(nextLayer);
      return;
    }

    const layers = collection.toArray?.() ?? [];
    const index = layers.indexOf(previousLayer);

    if (index === -1) {
      collection.add(nextLayer);
    } else if (typeof collection.splice === 'function') {
      collection.splice(index, 1, nextLayer);
    } else {
      collection.remove(previousLayer);
      collection.add(nextLayer, index);
    }

    const rootIndex = loadedWebMapRootLayers.indexOf(previousLayer);
    if (rootIndex !== -1) {
      loadedWebMapRootLayers[rootIndex] = nextLayer;
    }
  }

  function requireGeoParquetPipeline(): GeoParquetPipeline {
    if (!geoParquetPipeline) {
      throw new Error('GeoParquet loader is not ready.');
    }

    return geoParquetPipeline;
  }

  function removeLoadedWebMapLayers(): void {
    for (const handle of layerVisibilityWatchHandles) {
      try {
        handle.remove?.();
      } catch {
        // Ignore stale ArcGIS handles.
      }
    }
    layerVisibilityWatchHandles = [];

    for (const layer of loadedWebMapRootLayers) {
      try {
        mapElement.map.remove(layer);
      } catch {
        // Ignore layers that were already removed.
      }
    }

    loadedWebMapRootLayers = [];
    webMapLoaded = false;
  }

  function resolveBasemap(value: string): string | null {
    const cleaned = value.trim().toLowerCase();

    if (cleaned === '' || cleaned === 'none') {
      return null;
    }

    return value.trim();
  }

  async function loadGeoParquet(): Promise<void> {
    if (
      !ready ||
      !mapElement?.map ||
      !geoParquetPipeline
    ) {
      status = 'Map is not ready yet.';
      return;
    }

    const selectedSources = [...urlParquetFiles, ...parquetFiles].filter((source) =>
      selectedParquetSourceIds.includes(source.id)
    );

    if (selectedSources.length === 0) {
      status = 'Select at least one GeoParquet source.';
      return;
    }

    loading = true;
    errorMessage = '';
    status = `Loading ${selectedSources.length} GeoParquet source${selectedSources.length === 1 ? '' : 's'}...`;

    try {
      if (clearExisting) {
        removeLoadedLayers();
      }

      const summaries: LoadedLayerSummary[] = [];
      const extents: any[] = [];
      const pipeline = requireGeoParquetPipeline();

      for (const source of selectedSources) {
        const benchmarkStart = performance.now();
        const urls = source.urls.map((url) => toAbsoluteUrl(url));
        const title = source.name;
        status = `Reading GeoParquet metadata: ${title}`;

        const { layer, settings } = await pipeline.createLayer({ title, urls });

        mapElement.map.add(layer);
        loadedLayers = [...loadedLayers, layer];

        await layer.when();
        status = `Rendering GeoParquet layer: ${title}`;
        const rendered = await waitForLayerRender(layer);
        renderBenchmarks = [
          {
            id: (benchmarkId += 1),
            title,
            urlCount: urls.length,
            urls,
            elapsedMs: performance.now() - benchmarkStart,
            completedAt: new Date().toISOString(),
            rendered
          },
          ...renderBenchmarks
        ];

        let count: number | 'unknown' = 'unknown';
        try {
          count = await layer.queryFeatureCount();
        } catch (countError) {
          console.warn(`Could not count features for ${title}`, countError);
        }

        const layerExtent = await pipeline.getLayerExtent(layer, settings);
        if (layerExtent) {
          extents.push(layerExtent);
        }

        summaries.push({
          title,
          url: urls.join('\n'),
          geometryField: settings.geometryField,
          geometryType: settings.geometryType,
          featureCount: count
        });
      }

      loadedLayerSummaries = clearExisting ? summaries : [...loadedLayerSummaries, ...summaries];

      if (autoZoom && extents.length > 0) {
        const targetExtent = combineExtents(extents);
        if (targetExtent) {
          await zoomToLoadedExtent(targetExtent);
        }
      }

      status = `Loaded ${summaries.length} GeoParquet layer${summaries.length === 1 ? '' : 's'}.`;
    } catch (error) {
      errorMessage = errorToString(error);
      status = 'Failed to load GeoParquet.';
      console.error(error);
    } finally {
      loading = false;
    }
  }

  function removeLoadedLayers(): void {
    for (const layer of loadedLayers) {
      try {
        mapElement.map.remove(layer);
      } catch {
        // Ignore layers that were already removed.
      }
    }

    loadedLayers = [];
    loadedLayerSummaries = [];
    removeLoadedWebMapLayers();
  }

  function updateView(): void {
    if (!ready || !mapElement) {
      return;
    }

    try {
      mapElement.map.basemap = resolveBasemap(basemap);
      mapElement.center = parseCenter(center);
      mapElement.zoom = Number(zoom);
      status = 'View updated.';
    } catch (error) {
      errorMessage = errorToString(error);
    }
  }

  async function zoomToLoadedExtent(extent: any): Promise<void> {
    const targetExtent = extent.expand?.(1.05) ?? extent;

    await mapElement.goTo(targetExtent).catch(() => undefined);

    if ((mapElement.zoom ?? 0) < 7 && targetExtent.center) {
      await mapElement.goTo({ center: targetExtent.center, zoom: 7 }).catch(() => undefined);
    }
  }

  async function waitForLayerRender(layer: any): Promise<boolean> {
    try {
      const layerView = await mapElement.whenLayerView(layer);
      await nextAnimationFrame();

      if (layerView.updating === false) {
        return true;
      }

      if (!reactiveUtils?.whenOnce) {
        return false;
      }

      await withTimeout(reactiveUtils.whenOnce(() => layerView.updating === false), 120000);
      return layerView.updating === false;
    } catch (renderError) {
      console.warn(`Could not confirm render completion for ${layer.title}.`, renderError);
      return false;
    }
  }

  function nextAnimationFrame(): Promise<void> {
    return new Promise((resolve) => requestAnimationFrame(() => resolve()));
  }

  function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
    return new Promise((resolve, reject) => {
      const timeout = window.setTimeout(() => reject(new Error('Timed out waiting for layer render.')), timeoutMs);

      promise.then(
        (value) => {
          window.clearTimeout(timeout);
          resolve(value);
        },
        (error: unknown) => {
          window.clearTimeout(timeout);
          reject(error);
        }
      );
    });
  }

  function toAbsoluteUrl(value: string): string {
    if (/^[a-z][a-z\d+.-]*:\/\//i.test(value)) {
      return value;
    }

    if (!value.startsWith('/') && /^[^/?#]+\.[^/?#]+(?:[/:?#]|$)/.test(value)) {
      return `https://${value}`;
    }

    return new URL(value, window.location.href).href;
  }

  function normalizeListedParquetUrl(value: string): string {
    if (/^[a-z][a-z\d+.-]*:\/\//i.test(value)) {
      return value;
    }

    if (value.startsWith('/')) {
      return appPath(value);
    }

    return `https://${value}`;
  }

  function normalizeAppAssetUrl(value: string): string {
    if (/^[a-z][a-z\d+.-]*:\/\//i.test(value)) {
      return value;
    }

    return value.startsWith('/') ? appPath(value) : value;
  }

  function parseCsvRows(csvText: string): CsvRow[] {
    const records = parseCsvRecords(csvText).filter((record) =>
      record.some((value) => value.trim().length > 0)
    );
    const [headers, ...rows] = records;

    if (!headers) {
      return [];
    }

    return rows.map((row) =>
      Object.fromEntries(headers.map((header, index) => [header.trim(), row[index]?.trim() ?? '']))
    );
  }

  function parseCsvRecords(csvText: string): string[][] {
    const records: string[][] = [];
    let record: string[] = [];
    let field = '';
    let inQuotes = false;

    for (let index = 0; index < csvText.length; index += 1) {
      const character = csvText[index];
      const nextCharacter = csvText[index + 1];

      if (character === '"') {
        if (inQuotes && nextCharacter === '"') {
          field += '"';
          index += 1;
        } else {
          inQuotes = !inQuotes;
        }
      } else if (character === ',' && !inQuotes) {
        record.push(field);
        field = '';
      } else if ((character === '\n' || character === '\r') && !inQuotes) {
        record.push(field);
        records.push(record);
        record = [];
        field = '';

        if (character === '\r' && nextCharacter === '\n') {
          index += 1;
        }
      } else {
        field += character;
      }
    }

    if (field.length > 0 || record.length > 0) {
      record.push(field);
      records.push(record);
    }

    return records;
  }

  function parseUrlsCell(value: string): string[] {
    const cleaned = value.trim();

    if (cleaned.length === 0) {
      return [];
    }

    if (cleaned.startsWith('[')) {
      const parsed = JSON.parse(cleaned) as unknown;

      if (!Array.isArray(parsed) || !parsed.every((entry) => typeof entry === 'string')) {
        throw new Error('The urls CSV column must contain a URL string or an array of URL strings.');
      }

      return parsed.map((entry) => entry.trim()).filter((entry) => entry.length > 0);
    }

    return cleaned.split(/[|;\s]+/).filter((url) => url.length > 0);
  }

  function nullIfBlank(value: string): string | null {
    const cleaned = value.trim();
    return cleaned.length === 0 ? null : cleaned;
  }

  function defaultSourceName(urls: string[]): string {
    if (urls.length === 1) {
      return parquetFileNameFromUrl(urls[0]);
    }

    return `${parquetFileNameFromUrl(urls[0])} + ${urls.length - 1} more`;
  }

  function sourceId(urls: string[]): string {
    return urls.join('|');
  }

  function appPath(path: string): string {
    return `${base}${path}`;
  }

  function parseCenter(value: string): [number, number] {
    const [x, y] = value.split(',').map((part) => Number(part.trim()));

    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      return [-2.5, 54];
    }

    return [x, y];
  }

  function parquetFileNameFromUrl(url: string): string {
    const cleanUrl = url.split('?')[0].split('#')[0];
    const fileName = cleanUrl.substring(cleanUrl.lastIndexOf('/') + 1) || cleanUrl;
    return decodeURIComponent(fileName);
  }

  function combineExtents(extents: any[]): any | undefined {
    if (extents.length === 0) {
      return undefined;
    }

    const combined = extents[0].clone?.() ?? extents[0];

    for (const extent of extents.slice(1)) {
      if (combined.union) {
        combined.union(extent);
      }
    }

    return combined;
  }

  function errorToString(error: unknown): string {
    const detailsError = (error as { details?: { error?: unknown } })?.details?.error;

    if (error instanceof Error) {
      if (detailsError instanceof Error) {
        return `${error.message}\n${detailsError.message}`;
      }

      return error.message;
    }

    return String(error);
  }
</script>

<svelte:head>
  <title>ArcGIS SvelteKit GeoParquet Viewer</title>
  <meta
    name="description"
    content="SvelteKit proof-of-concept for testing ArcGIS Maps SDK ParquetLayer with GeoParquet files."
  />
</svelte:head>

<div class="app-shell">
  <GeoParquetSidebar
    {parquetFiles}
    {urlParquetFiles}
    {webMapLoaded}
    bind:selectedParquetSourceIds
    bind:basemap
    bind:center
    bind:zoom
    bind:autoZoom
    bind:clearExisting
    {ready}
    {loading}
    {status}
    {errorMessage}
    {loadedLayerSummaries}
    {renderBenchmarks}
    onLoadGeoParquet={loadGeoParquet}
    onUpdateView={updateView}
    onRemoveLoadedLayers={removeLoadedLayers}
  />

  <main class="map-wrap">
    <arcgis-map bind:this={mapElement} class="map-viewer">
      <arcgis-zoom slot="top-left"></arcgis-zoom>
      <arcgis-home slot="top-left"></arcgis-home>
      <arcgis-layer-list slot="top-right"></arcgis-layer-list>
      <arcgis-legend slot="bottom-left"></arcgis-legend>
    </arcgis-map>
  </main>
</div>

<style>
  :global(html),
  :global(body) {
    height: 100%;
    margin: 0;
    font-family:
      Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }

  :global(body) {
    background: #f6f7f9;
    color: #1f2933;
  }

  :global(button),
  :global(input),
  :global(textarea) {
    font: inherit;
  }

  .app-shell {
    display: grid;
    grid-template-columns: minmax(340px, 430px) 1fr;
    height: 100vh;
    overflow: hidden;
  }

  .map-wrap {
    min-width: 0;
    height: 100vh;
  }

  .map-viewer {
    display: block;
    height: 100%;
    width: 100%;
  }

  @media (max-width: 860px) {
    .app-shell {
      grid-template-columns: 1fr;
      grid-template-rows: minmax(340px, 48vh) 1fr;
    }

    .map-wrap {
      height: auto;
      min-height: 52vh;
    }
  }
</style>
