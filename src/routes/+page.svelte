<script lang="ts">
  import { base } from '$app/paths';
  import { onMount, tick } from 'svelte';
  import GeoParquetSidebar from '$lib/components/GeoParquetSidebar.svelte';

  type ArcgisGeometryType = 'point' | 'polygon' | 'polyline' | 'multipoint';

  type ParquetField = {
    name: string;
    alias?: string;
    type?: string;
    [key: string]: unknown;
  };

  type GeoParquetColumnMetadata = {
    encoding?: string;
    bbox?: number[];
    orientation?: string;
    geometry_types?: string[];
    [key: string]: unknown;
  };

  type GeoParquetMetadata = {
    version?: string;
    primary_column?: string;
    columns?: Record<string, GeoParquetColumnMetadata>;
    [key: string]: unknown;
  };

  type ParquetLayerInfo = {
    urls?: string[];
    fields?: ParquetField[];
    geometryType?: ArcgisGeometryType | null;
    spatialReference?: unknown;
    geometryEncoding?: unknown;
    file?: {
      keyValueMetadata?: (key: string) => string | undefined;
    };
    [key: string]: unknown;
  };

  type LoadedLayerSummary = {
    title: string;
    url: string;
    geometryField: string;
    geometryType: string;
    featureCount: number | 'unknown';
  };

  type ParquetDataFile = {
    name: string;
    url: string;
  };

  type ParquetManifest = {
    files?: ParquetDataFile[];
  };

  type ParquetRendererEntry = {
    parquetFile: string;
    renderer: unknown;
  };

  const DEFAULT_PARQUET_FILENAME = 'CJ_EN_LSOA_2011_BFC_V3_2022.parquet';

  let mapElement: any;
  let ready = false;
  let loading = false;
  let status = 'Loading ArcGIS Maps SDK components...';
  let errorMessage = '';

  let basemap = 'osm';
  let center = '-2.5,54';
  let zoom = 6;
  let autoZoom = true;
  let clearExisting = true;
  let parquetFiles: ParquetDataFile[] = [];
  let urlParquetFiles: ParquetDataFile[] = [];
  let selectedParquetUrls: string[] = [];
  let parquetRenderers = new Map<string, unknown>();

  let loadedLayerSummaries: LoadedLayerSummary[] = [];
  let loadedLayers: any[] = [];

  let ArcGISMap: any;
  let Extent: any;
  let ParquetLayer: any;
  let ParquetGeometryEncodingWkb: any;
  let rendererJsonUtils: { fromJSON?: (json: object) => unknown } | undefined;
  let getParquetLayerInfo: ((urls: string[]) => Promise<any>) | undefined;

  onMount(async () => {
    const setupDataPromise = Promise.all([loadParquetSources(), loadParquetRenderers()]);

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
        parquetLayerModule,
        parquetGeometryEncodingWkbModule,
        rendererJsonUtilsModule,
        parquetUtilsModule
      ] = await Promise.all([
        import('@arcgis/core/Map.js'),
        import('@arcgis/core/geometry/Extent.js'),
        import('@arcgis/core/layers/ParquetLayer.js'),
        import('@arcgis/core/layers/support/ParquetGeometryEncodingWkb.js'),
        import('@arcgis/core/renderers/support/jsonUtils.js'),
        import('@arcgis/core/layers/support/parquetUtils.js')
      ]);

      ArcGISMap = mapModule.default;
      Extent = extentModule.default;
      ParquetLayer = parquetLayerModule.default;
      ParquetGeometryEncodingWkb = parquetGeometryEncodingWkbModule.default;
      rendererJsonUtils = rendererJsonUtilsModule;
      getParquetLayerInfo = parquetUtilsModule.getParquetLayerInfo;

      await customElements.whenDefined('arcgis-map');
      await tick();

      mapElement.map = new ArcGISMap({ basemap: resolveBasemap(basemap) });
      mapElement.center = parseCenter(center);
      mapElement.zoom = zoom;

      await mapElement.componentOnReady();
      await mapElement.viewOnReady();

      ready = true;
      await setupDataPromise;
      status = 'Ready. Select one or more GeoParquet files or URLs and click Load GeoParquet.';
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

    if (selectedParquetUrls.length === 0) {
      const defaultFile =
        parquetFiles.find((file) => file.name === DEFAULT_PARQUET_FILENAME) ??
        parquetFiles[0] ??
        urlParquetFiles[0];

      selectedParquetUrls = defaultFile ? [defaultFile.url] : [];
    }
  }

  async function loadAvailableParquetFiles(): Promise<ParquetDataFile[]> {
    try {
      const response = await fetch(appPath('/parquet-files.json'), { cache: 'no-store' });

      if (!response.ok) {
        throw new Error(`Could not list parquet files (${response.status}).`);
      }

      const result = (await response.json()) as ParquetManifest;
      return Array.isArray(result.files)
        ? result.files.map((file) => ({
            ...file,
            url: normalizeAppAssetUrl(file.url)
          }))
        : [];
    } catch (error) {
      errorMessage = errorToString(error);
      status = 'Could not read parquet-data manifest.';
      return [];
    }
  }

  async function loadParquetUrlList(): Promise<ParquetDataFile[]> {
    try {
      const response = await fetch(appPath('/urls.txt'), { cache: 'no-store' });

      if (!response.ok) {
        throw new Error(`Could not read urls.txt (${response.status}).`);
      }

      const urlsText = await response.text();
      const seen = new Set<string>();

      return urlsText
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => line.length > 0 && !line.startsWith('#'))
        .map((line) => normalizeListedParquetUrl(line))
        .filter((url) => {
          if (seen.has(url)) {
            return false;
          }

          seen.add(url);
          return true;
        })
        .map((url) => ({
          name: parquetFileNameFromUrl(url),
          url
        }));
    } catch (error) {
      console.warn('Could not read urls.txt.', error);
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
    } catch (error) {
      console.warn('Could not load parquet renderer JSON.', error);
    }
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
      !ParquetLayer ||
      !ParquetGeometryEncodingWkb ||
      !getParquetLayerInfo
    ) {
      status = 'Map is not ready yet.';
      return;
    }

    const urls = selectedParquetUrls.map((url) => toAbsoluteUrl(url));

    if (urls.length === 0) {
      status = 'Select at least one .parquet file.';
      return;
    }

    loading = true;
    errorMessage = '';
    status = `Loading ${urls.length} GeoParquet file${urls.length === 1 ? '' : 's'}...`;

    try {
      if (clearExisting) {
        removeLoadedLayers();
      }

      const summaries: LoadedLayerSummary[] = [];
      const extents: any[] = [];

      for (const url of urls) {
        const title = titleFromUrl(url);
        const parquetFile = parquetFileNameFromUrl(url);
        status = `Reading GeoParquet metadata: ${title}`;

        const layerInfo = await getParquetLayerInfo([url]);
        const geoMetadata = readGeoParquetMetadata(layerInfo);
        const geometryField = getWkbGeometryField(layerInfo, geoMetadata);
        const geometryType = getArcgisGeometryType(layerInfo, geoMetadata, geometryField);
        const spatialReference = getSpatialReference(layerInfo);
        const fields = fieldsWithWkbGeometryField(layerInfo, geometryField);
        const orientation = getWkbOrientation(geoMetadata, geometryField);

        console.log('Parquet layerInfo', layerInfo);
        console.log('Resolved ParquetLayer settings', {
          title,
          geometryField,
          geometryType,
          spatialReference,
          orientation
        });

        const layer = new ParquetLayer({
          ...layerInfo,
          urls: [url],
          title,
          fields,
          geometryEncoding: new ParquetGeometryEncodingWkb({
            field: geometryField,
            ...(orientation ? { orientation } : {})
          }),
          geometryType,
          spatialReference,
          renderer: rendererForParquetFile(parquetFile, geometryType),
          popupTemplate: popupTemplateFromFields(fields, geometryField)
        });

        mapElement.map.add(layer);
        loadedLayers = [...loadedLayers, layer];

        await layer.when();

        let count: number | 'unknown' = 'unknown';
        try {
          count = await layer.queryFeatureCount();
        } catch (countError) {
          console.warn(`Could not count features for ${title}`, countError);
        }

        const layerExtent = await getLayerExtent(layer, layerInfo, geoMetadata, spatialReference);
        if (layerExtent) {
          extents.push(layerExtent);
        }

        summaries.push({
          title,
          url,
          geometryField,
          geometryType,
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

  function readGeoParquetMetadata(layerInfo: ParquetLayerInfo): GeoParquetMetadata | undefined {
    try {
      const geoMetadataText = layerInfo.file?.keyValueMetadata?.('geo');
      return geoMetadataText ? JSON.parse(geoMetadataText) : undefined;
    } catch (metadataError) {
      console.warn('Could not parse GeoParquet metadata', metadataError);
      return undefined;
    }
  }

  function getWkbGeometryField(
    layerInfo: ParquetLayerInfo,
    geoMetadata: GeoParquetMetadata | undefined
  ): string {
    const fromGeoMetadata = geoMetadata?.primary_column;
    if (typeof fromGeoMetadata === 'string' && fromGeoMetadata.length > 0) {
      return fromGeoMetadata;
    }

    const fromEncoding = geometryEncodingFieldName(layerInfo.geometryEncoding);
    if (fromEncoding) {
      return fromEncoding;
    }

    const fieldNames = (layerInfo.fields ?? []).map((field) => field.name);

    if (fieldNames.includes('Shape')) {
      return 'Shape';
    }

    if (fieldNames.includes('geometry')) {
      return 'geometry';
    }

    if (fieldNames.includes('geom')) {
      return 'geom';
    }

    return 'Shape';
  }

  function getArcgisGeometryType(
    layerInfo: ParquetLayerInfo,
    geoMetadata: GeoParquetMetadata | undefined,
    geometryField: string
  ): ArcgisGeometryType {
    if (isArcgisGeometryType(layerInfo.geometryType)) {
      return layerInfo.geometryType;
    }

    const metadataGeometryType = geoMetadata?.columns?.[geometryField]?.geometry_types?.[0];

    switch (metadataGeometryType?.toLowerCase()) {
      case 'point':
        return 'point';
      case 'multipoint':
        return 'multipoint';
      case 'linestring':
      case 'multilinestring':
        return 'polyline';
      case 'polygon':
      case 'multipolygon':
        return 'polygon';
      default:
        console.warn('Could not infer geometry type; defaulting to point.', {
          layerInfoGeometryType: layerInfo.geometryType,
          metadataGeometryType
        });
        return 'point';
    }
  }

  function isArcgisGeometryType(value: unknown): value is ArcgisGeometryType {
    return value === 'point' || value === 'multipoint' || value === 'polyline' || value === 'polygon';
  }

  function getSpatialReference(layerInfo: ParquetLayerInfo): unknown {
    // If ArcGIS inferred a supported spatial reference from GeoParquet metadata, keep it.
    if (layerInfo.spatialReference) {
      return layerInfo.spatialReference;
    }

    // Your conversion workflow should export GeoParquet as EPSG:4326 for ArcGIS JS.
    // This fallback only applies when the metadata does not expose an SR object.
    return { wkid: 4326 };
  }

  function getWkbOrientation(
    geoMetadata: GeoParquetMetadata | undefined,
    geometryField: string
  ): 'counter-clockwise' | undefined {
    const orientation = geoMetadata?.columns?.[geometryField]?.orientation;

    if (orientation === 'counter-clockwise' || orientation === 'counterclockwise') {
      return 'counter-clockwise';
    }

    return undefined;
  }

  function fieldsWithWkbGeometryField(
    layerInfo: ParquetLayerInfo,
    geometryField: string
  ): ParquetField[] {
    const fields = [...(layerInfo.fields ?? [])];

    if (!fields.some((field) => field.name === geometryField)) {
      fields.push({
        name: geometryField,
        alias: geometryField,
        type: 'blob'
      });
    }

    return fields;
  }

  async function getLayerExtent(
    layer: any,
    layerInfo: ParquetLayerInfo,
    geoMetadata: GeoParquetMetadata | undefined,
    spatialReference: unknown
  ): Promise<any | undefined> {
    const metadataExtent = extentFromGeoMetadata(geoMetadata, spatialReference);
    if (metadataExtent) {
      return metadataExtent;
    }

    if (isValidExtent(layer.fullExtent)) {
      return layer.fullExtent;
    }

    try {
      const result = await layer.queryExtent?.();
      return isValidExtent(result?.extent) ? result.extent : undefined;
    } catch (extentError) {
      console.warn(`Could not query extent for ${layer.title}`, extentError, layerInfo);
      return undefined;
    }
  }

  function extentFromGeoMetadata(
    geoMetadata: GeoParquetMetadata | undefined,
    spatialReference: unknown
  ): any | undefined {
    if (!Extent || !geoMetadata?.primary_column) {
      return undefined;
    }

    const bbox = geoMetadata.columns?.[geoMetadata.primary_column]?.bbox;

    if (!Array.isArray(bbox) || bbox.length < 4 || !bbox.every(Number.isFinite)) {
      return undefined;
    }

    return new Extent({
      xmin: bbox[0],
      ymin: bbox[1],
      xmax: bbox[2],
      ymax: bbox[3],
      spatialReference
    });
  }

  function isValidExtent(extent: any): boolean {
    return Boolean(
      extent &&
        Number.isFinite(extent.xmin) &&
        Number.isFinite(extent.ymin) &&
        Number.isFinite(extent.xmax) &&
        Number.isFinite(extent.ymax) &&
        extent.xmax > extent.xmin &&
        extent.ymax > extent.ymin
    );
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

  function titleFromUrl(url: string): string {
    return parquetFileNameFromUrl(url).replace(/\.parquet$/i, '');
  }

  function parquetFileNameFromUrl(url: string): string {
    const cleanUrl = url.split('?')[0].split('#')[0];
    const fileName = cleanUrl.substring(cleanUrl.lastIndexOf('/') + 1) || cleanUrl;
    return decodeURIComponent(fileName);
  }

  function geometryEncodingFieldName(geometryEncoding: unknown): string | undefined {
    if (
      geometryEncoding &&
      typeof geometryEncoding === 'object' &&
      'field' in geometryEncoding &&
      typeof geometryEncoding.field === 'string'
    ) {
      return geometryEncoding.field;
    }

    return undefined;
  }

  function popupTemplateFromFields(fields: ParquetField[], geometryField: string): any {
    const fieldInfos = fields
      .filter((field) => field.name !== geometryField)
      .filter((field) => field.type !== 'geometry' && field.type !== 'blob')
      .filter((field) => !field.name.endsWith('_bbox'))
      .slice(0, 20)
      .map((field) => ({
        fieldName: field.name,
        label: field.alias || field.name
      }));

    if (fieldInfos.length === 0) {
      return {
        title: 'GeoParquet feature',
        content: 'No displayable attribute fields were inferred.'
      };
    }

    return {
      title: 'GeoParquet feature',
      content: [
        {
          type: 'fields',
          fieldInfos
        }
      ]
    };
  }

  function rendererForGeometry(geometryType: ArcgisGeometryType): any {
    if (geometryType === 'polygon') {
      return {
        type: 'simple',
        symbol: {
          type: 'simple-fill',
          color: [0, 122, 194, 0.55],
          outline: {
            color: [0, 48, 84, 1],
            width: 0.8
          }
        }
      };
    }

    if (geometryType === 'polyline') {
      return {
        type: 'simple',
        symbol: {
          type: 'simple-line',
          color: [31, 82, 130, 1],
          width: 1.5
        }
      };
    }

    return {
      type: 'simple',
      symbol: {
        type: 'simple-marker',
        color: [31, 82, 130, 0.85],
        size: geometryType === 'multipoint' ? 4 : 6,
        outline: {
          color: [255, 255, 255, 1],
          width: 0.75
        }
      }
    };
  }

  function rendererForParquetFile(parquetFile: string, geometryType: ArcgisGeometryType): any {
    const mappedRenderer = parquetRenderers.get(parquetFile);

    if (!mappedRenderer) {
      return rendererForGeometry(geometryType);
    }

    try {
      const rendererJson = JSON.parse(JSON.stringify(mappedRenderer));

      if (!rendererJson || typeof rendererJson !== 'object') {
        return rendererForGeometry(geometryType);
      }

      return rendererJsonUtils?.fromJSON?.(rendererJson) ?? rendererJson;
    } catch (rendererError) {
      console.warn(`Could not apply mapped renderer for ${parquetFile}; using fallback.`, rendererError);
      return rendererForGeometry(geometryType);
    }
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
    bind:selectedParquetUrls
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
