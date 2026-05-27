export type ArcgisGeometryType = 'point' | 'polygon' | 'polyline' | 'multipoint';

export type ParquetField = {
  name: string;
  alias?: string;
  type?: string;
  [key: string]: unknown;
};

export type GeoParquetColumnMetadata = {
  encoding?: string;
  bbox?: number[];
  orientation?: string;
  geometry_types?: string[];
  [key: string]: unknown;
};

export type GeoParquetMetadata = {
  version?: string;
  primary_column?: string;
  columns?: Record<string, GeoParquetColumnMetadata>;
  [key: string]: unknown;
};

export type ParquetLayerInfo = {
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

export type GeoParquetLayerSettings = {
  urls: string[];
  layerInfo: ParquetLayerInfo;
  geoMetadata: GeoParquetMetadata | undefined;
  fields: ParquetField[];
  geometryField: string;
  geometryType: ArcgisGeometryType;
  spatialReference: unknown;
  orientation: 'counter-clockwise' | undefined;
  renderer: any;
};

export type GeoParquetLayerOptions = {
  id?: string;
  title: string;
  urls: string[];
  minScale?: number;
  maxScale?: number;
  visible?: boolean;
  renderer?: unknown;
};

export type GeoParquetLayerResult = {
  layer: any;
  settings: GeoParquetLayerSettings;
};

export type GeoParquetPipelineRuntime = {
  ParquetLayer: new (properties: Record<string, unknown>) => any;
  PlaceholderLayer?: new (properties: Record<string, unknown>) => any;
  ParquetGeometryEncodingWkb: new (properties: Record<string, unknown>) => any;
  Extent: new (properties: Record<string, unknown>) => any;
  getParquetLayerInfo: (urls: string[]) => Promise<ParquetLayerInfo>;
  rendererFromJson?: (json: object) => unknown;
};

export class GeoParquetPipeline {
  private readonly runtime: GeoParquetPipelineRuntime;
  private rendererByParquetFile: Map<string, unknown>;
  private readonly layerInfoCache = new Map<string, Promise<ParquetLayerInfo>>();
  private readonly layerResults = new WeakMap<any, GeoParquetLayerResult>();
  private readonly layerHydrationRequests = new WeakMap<any, Promise<GeoParquetLayerResult>>();

  constructor(runtime: GeoParquetPipelineRuntime, rendererByParquetFile = new Map<string, unknown>()) {
    this.runtime = runtime;
    this.rendererByParquetFile = rendererByParquetFile;
  }

  setRendererByParquetFile(rendererByParquetFile: Map<string, unknown>): void {
    this.rendererByParquetFile = rendererByParquetFile;
  }

  createPlaceholderLayer(options: GeoParquetLayerOptions): any {
    const PlaceholderLayer = this.runtime.PlaceholderLayer ?? this.runtime.ParquetLayer;

    return new PlaceholderLayer({
      id: options.id,
      title: options.title,
      minScale: options.minScale,
      maxScale: options.maxScale,
      visible: options.visible ?? false
    });
  }

  async createLayer(options: GeoParquetLayerOptions): Promise<GeoParquetLayerResult> {
    const settings = await this.resolveSettings(options);
    const layer = new this.runtime.ParquetLayer({
      ...settings.layerInfo,
      id: options.id,
      title: options.title,
      urls: settings.urls,
      fields: settings.fields,
      minScale: options.minScale,
      maxScale: options.maxScale,
      geometryEncoding: this.createWkbGeometryEncoding(settings),
      geometryType: settings.geometryType,
      spatialReference: settings.spatialReference,
      renderer: settings.renderer,
      popupTemplate: this.popupTemplateFromFields(settings.fields, settings.geometryField),
      ...(options.visible === undefined ? {} : { visible: options.visible })
    });
    const result = { layer, settings };
    this.layerResults.set(layer, result);
    return result;
  }

  hydrateLayer(layer: any, options: GeoParquetLayerOptions): Promise<GeoParquetLayerResult> {
    const existing = this.layerResults.get(layer);
    if (existing) {
      return Promise.resolve(existing);
    }

    const inFlight = this.layerHydrationRequests.get(layer);
    if (inFlight) {
      return inFlight;
    }

    const request = (async () => {
      const settings = await this.resolveSettings(options);
      this.applySettingsToLayer(layer, options, settings);
      const result = { layer, settings };
      this.layerResults.set(layer, result);
      this.layerHydrationRequests.delete(layer);
      return result;
    })().catch((error) => {
      this.layerHydrationRequests.delete(layer);
      throw error;
    });

    this.layerHydrationRequests.set(layer, request);
    return request;
  }

  getLayerResult(layer: any): GeoParquetLayerResult | undefined {
    return this.layerResults.get(layer);
  }

  async getLayerExtent(
    layer: any,
    settings: GeoParquetLayerSettings
  ): Promise<any | undefined> {
    const metadataExtent = this.extentFromGeoMetadata(settings.geoMetadata, settings.spatialReference);
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
      console.warn(`Could not query extent for ${layer.title}`, extentError, settings.layerInfo);
      return undefined;
    }
  }

  private async resolveSettings(options: GeoParquetLayerOptions): Promise<GeoParquetLayerSettings> {
    const { title, urls } = options;
    const layerInfo = await this.getCachedParquetLayerInfo(urls);
    const geoMetadata = readGeoParquetMetadata(layerInfo);
    const geometryField = getWkbGeometryField(layerInfo, geoMetadata);
    const geometryType = getArcgisGeometryType(layerInfo, geoMetadata, geometryField);
    const spatialReference = getSpatialReference(layerInfo);
    const fields = fieldsWithWkbGeometryField(layerInfo, geometryField);
    const orientation = getWkbOrientation(geoMetadata, geometryField);
    const renderer = this.rendererForLayerOptions(options, geometryType);

    console.log('Resolved ParquetLayer settings', {
      title,
      urls,
      geometryField,
      geometryType,
      spatialReference,
      orientation
    });

    return {
      urls,
      layerInfo,
      geoMetadata,
      fields,
      geometryField,
      geometryType,
      spatialReference,
      orientation,
      renderer
    };
  }

  private getCachedParquetLayerInfo(urls: string[]): Promise<ParquetLayerInfo> {
    const cacheKey = urls.join('|');
    const cached = this.layerInfoCache.get(cacheKey);
    if (cached) {
      return cached;
    }

    const request = this.runtime.getParquetLayerInfo(urls);
    this.layerInfoCache.set(cacheKey, request);
    return request;
  }

  private applySettingsToLayer(
    layer: any,
    options: GeoParquetLayerOptions,
    settings: GeoParquetLayerSettings
  ): void {
    layer.title = options.title;
    layer.urls = settings.urls;
    layer.fields = settings.fields;
    layer.minScale = options.minScale;
    layer.maxScale = options.maxScale;
    layer.geometryEncoding = this.createWkbGeometryEncoding(settings);
    layer.geometryType = settings.geometryType;
    layer.spatialReference = settings.spatialReference;
    layer.renderer = settings.renderer;
    layer.popupTemplate = this.popupTemplateFromFields(settings.fields, settings.geometryField);

    if (options.visible !== undefined) {
      layer.visible = options.visible;
    }
  }

  private createWkbGeometryEncoding(settings: GeoParquetLayerSettings): any {
    return new this.runtime.ParquetGeometryEncodingWkb({
      field: settings.geometryField,
      ...(settings.orientation ? { orientation: settings.orientation } : {})
    });
  }

  private extentFromGeoMetadata(
    geoMetadata: GeoParquetMetadata | undefined,
    spatialReference: unknown
  ): any | undefined {
    if (!geoMetadata?.primary_column) {
      return undefined;
    }

    const bbox = geoMetadata.columns?.[geoMetadata.primary_column]?.bbox;

    if (!Array.isArray(bbox) || bbox.length < 4 || !bbox.every(Number.isFinite)) {
      return undefined;
    }

    return new this.runtime.Extent({
      xmin: bbox[0],
      ymin: bbox[1],
      xmax: bbox[2],
      ymax: bbox[3],
      spatialReference
    });
  }

  private popupTemplateFromFields(fields: ParquetField[], geometryField: string): any {
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

  private rendererFromJson(renderer: unknown): any | undefined {
    if (!renderer) {
      return undefined;
    }

    try {
      const rendererJson = JSON.parse(JSON.stringify(renderer));

      if (!rendererJson || typeof rendererJson !== 'object') {
        return undefined;
      }

      return this.runtime.rendererFromJson?.(rendererJson) ?? rendererJson;
    } catch (rendererError) {
      console.warn('Could not apply renderer JSON.', rendererError);
      return undefined;
    }
  }

  private rendererForLayerOptions(options: GeoParquetLayerOptions, geometryType: ArcgisGeometryType): any {
    const optionRenderer = this.rendererFromJson(options.renderer);

    if (optionRenderer) {
      return optionRenderer;
    }

    const parquetFile = parquetFileNameFromUrl(options.urls[0]);
    const mappedRenderer = this.rendererFromJson(this.rendererByParquetFile.get(parquetFile));

    return mappedRenderer ?? rendererForGeometry(geometryType);
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
  if (layerInfo.spatialReference) {
    return layerInfo.spatialReference;
  }

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

function parquetFileNameFromUrl(url: string): string {
  const cleanUrl = url.split('?')[0].split('#')[0];
  const fileName = cleanUrl.substring(cleanUrl.lastIndexOf('/') + 1) || cleanUrl;
  return decodeURIComponent(fileName);
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
