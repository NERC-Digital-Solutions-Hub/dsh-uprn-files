<svelte:options runes={true} />

<script lang="ts">
	type ParquetDataSource = {
		id: string;
		name: string;
		urls: string[];
	};

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

	type Props = {
		parquetFiles?: ParquetDataSource[];
		urlParquetFiles?: ParquetDataSource[];
		webMapLoaded?: boolean;
		selectedParquetSourceIds?: string[];
		ready?: boolean;
		loading?: boolean;
		status?: string;
		errorMessage?: string;
		basemap?: string;
		center?: string;
		zoom?: number;
		autoZoom?: boolean;
		clearExisting?: boolean;
		loadedLayerSummaries?: LoadedLayerSummary[];
		renderBenchmarks?: RenderBenchmark[];
		onLoadGeoParquet?: () => void;
		onUpdateView?: () => void;
		onRemoveLoadedLayers?: () => void;
	};

	let {
		parquetFiles = [],
		urlParquetFiles = [],
		webMapLoaded = false,
		selectedParquetSourceIds = $bindable([]),
		ready = false,
		loading = false,
		status = '',
		errorMessage = '',
		basemap = $bindable(''),
		center = $bindable(''),
		zoom = $bindable(6),
		autoZoom = $bindable(true),
		clearExisting = $bindable(true),
		loadedLayerSummaries = [],
		renderBenchmarks = [],
		onLoadGeoParquet = () => undefined,
		onUpdateView = () => undefined,
		onRemoveLoadedLayers = () => undefined
	}: Props = $props();

	let localSelectedCount = $derived(selectedCountFromSources(parquetFiles, selectedParquetSourceIds));
	let urlSelectedCount = $derived(selectedCountFromSources(urlParquetFiles, selectedParquetSourceIds));
	let localSelectionLabel = $derived(
		selectionLabelFromSources(parquetFiles, selectedParquetSourceIds, 'Select local parquet files')
	);
	let urlSelectionLabel = $derived(
		selectionLabelFromSources(urlParquetFiles, selectedParquetSourceIds, 'Select parquet URL rows')
	);

	function selectedCountFromSources(sources: ParquetDataSource[], selectedIds: string[]): number {
		return sources.filter((source) => selectedIds.includes(source.id)).length;
	}

	function selectionLabelFromSources(
		sources: ParquetDataSource[],
		selectedIds: string[],
		fallback: string
	): string {
		const selectedNames = sources
			.filter((source) => selectedIds.includes(source.id))
			.map((source) => source.name);

		return selectedNames.length > 0 ? selectedNames.join(', ') : fallback;
	}

	function toggleParquetSource(id: string): void {
		selectedParquetSourceIds = selectedParquetSourceIds.includes(id)
			? selectedParquetSourceIds.filter((selectedId) => selectedId !== id)
			: [...selectedParquetSourceIds, id];
	}

	function sourceTitle(source: ParquetDataSource): string {
		return source.urls.join('\n');
	}

	function formatElapsedTime(elapsedMs: number): string {
		if (elapsedMs < 1000) {
			return `${Math.round(elapsedMs)} ms`;
		}

		return `${(elapsedMs / 1000).toFixed(2)} s`;
	}

	function formatCompletedAt(value: string): string {
		return new Intl.DateTimeFormat(undefined, {
			hour: '2-digit',
			minute: '2-digit',
			second: '2-digit'
		}).format(new Date(value));
	}

	function urlsFromSummary(value: string): string[] {
		return value.split('\n').filter((url) => url.length > 0);
	}
</script>

<aside class="panel">
	<header>
		<p class="eyebrow">ArcGIS Maps SDK + SvelteKit</p>
		<h1>GeoParquet web map viewer</h1>
		<p>
			{webMapLoaded ? 'The converted web map is loaded.' : 'Loading the converted web map...'}
			Use the ArcGIS layer list on the map to toggle groups and layers.
		</p>
	</header>

	<!-- <section class="control-group">
		<span id="local-parquet-selector-label" class="control-label">Local GeoParquet data</span>
		<details class="multi-select">
			<summary aria-labelledby="local-parquet-selector-label">
				<span>{localSelectionLabel}</span>
				<strong>{localSelectedCount} selected</strong>
			</summary>

			<div class="options" role="listbox" aria-multiselectable="true">
				{#if parquetFiles.length === 0}
					<p class="empty-state">No .parquet files were found in parquet-data.</p>
				{:else}
					{#each parquetFiles as source (source.id)}
						<button
							type="button"
							class:selected={selectedParquetSourceIds.includes(source.id)}
							aria-pressed={selectedParquetSourceIds.includes(source.id)}
							title={sourceTitle(source)}
							onclick={() => toggleParquetSource(source.id)}
						>
							<span>{source.name}</span>
							<span class="checkmark"
								>{selectedParquetSourceIds.includes(source.id) ? 'Selected' : ''}</span
							>
						</button>
					{/each}
				{/if}
			</div>
		</details>
	</section> -->

	<section class="control-group">
		<span id="url-parquet-selector-label" class="control-label">GeoParquet URLs</span>
		<details class="multi-select">
			<summary aria-labelledby="url-parquet-selector-label">
				<span>{urlSelectionLabel}</span>
				<strong>{urlSelectedCount} selected</strong>
			</summary>

			<div class="options" role="listbox" aria-multiselectable="true">
				{#if urlParquetFiles.length === 0}
					<p class="empty-state">No .parquet URLs were found in urls.csv.</p>
				{:else}
					{#each urlParquetFiles as source (source.id)}
						<button
							type="button"
							class:selected={selectedParquetSourceIds.includes(source.id)}
							aria-pressed={selectedParquetSourceIds.includes(source.id)}
							title={sourceTitle(source)}
							onclick={() => toggleParquetSource(source.id)}
						>
							<span>{source.name}</span>
							<span class="checkmark"
								>{selectedParquetSourceIds.includes(source.id) ? 'Selected' : ''}</span
							>
						</button>
					{/each}
				{/if}
			</div>
		</details>
		<p class="hint">
			Open either list and click any source to toggle it. External URLs must allow browser access
			and byte-range requests for ArcGIS to read them.
		</p>
	</section>

	<section class="control-grid">
		<label>
			Basemap
			<input bind:value={basemap} />
		</label>

		<label>
			Center lon,lat
			<input bind:value={center} />
		</label>

		<label>
			Zoom
			<input type="number" bind:value={zoom} min="1" max="23" />
		</label>
	</section>

	<section class="checkboxes">
		<label>
			<input type="checkbox" bind:checked={autoZoom} />
			Zoom to loaded layer extent
		</label>

		<label>
			<input type="checkbox" bind:checked={clearExisting} />
			Clear existing test layers before loading
		</label>
	</section>

	<div class="actions">
		<button type="button" onclick={onLoadGeoParquet} disabled={!ready || loading}>
			{loading ? 'Loading...' : 'Load GeoParquet'}
		</button>
		<button type="button" class="secondary" onclick={onUpdateView} disabled={!ready}>
			Update view
		</button>
		<button type="button" class="secondary" onclick={onRemoveLoadedLayers} disabled={!ready}>
			Clear layers
		</button>
	</div>

	<section class="status" aria-live="polite">
		<strong>Status</strong>
		<p>{status}</p>
		{#if errorMessage}
			<pre>{errorMessage}</pre>
		{/if}
	</section>

	<section class="benchmarks">
		<details>
			<summary>
				<span>Render benchmarks</span>
				<strong>{renderBenchmarks.length} saved</strong>
			</summary>

			<div class="benchmark-list">
				{#if renderBenchmarks.length === 0}
					<p class="empty-state">No render benchmarks yet.</p>
				{:else}
					{#each renderBenchmarks as benchmark (benchmark.id)}
						<article>
							<header>
								<strong>{benchmark.title}</strong>
								<span class:pending={!benchmark.rendered}>
									{benchmark.rendered ? formatElapsedTime(benchmark.elapsedMs) : 'Render not confirmed'}
								</span>
							</header>
							<dl>
								<div>
									<dt>Completed</dt>
									<dd>{formatCompletedAt(benchmark.completedAt)}</dd>
								</div>
								<div>
									<dt>URLs</dt>
									<dd>{benchmark.urlCount}</dd>
								</div>
								<div>
									<dt>Source</dt>
									<dd>
										{#if benchmark.urls.length > 1}
											<details class="source-list">
												<summary>{benchmark.urls.length} source URLs</summary>
												<ul>
													{#each benchmark.urls as url}
														<li>{url}</li>
													{/each}
												</ul>
											</details>
										{:else}
											{benchmark.urls[0]}
										{/if}
									</dd>
								</div>
							</dl>
						</article>
					{/each}
				{/if}
			</div>
		</details>
	</section>

	{#if loadedLayerSummaries.length > 0}
		<section class="results">
			<h2>Loaded layers</h2>
			{#each loadedLayerSummaries as layer}
				{@const urls = urlsFromSummary(layer.url)}
				<article>
					<strong>{layer.title}</strong>
					<dl>
						<div>
							<dt>Geometry</dt>
							<dd>{layer.geometryType}</dd>
						</div>
						<div>
							<dt>Geom field</dt>
							<dd>{layer.geometryField}</dd>
						</div>
						<div>
							<dt>Feature count</dt>
							<dd>{layer.featureCount}</dd>
						</div>
						<div>
							<dt>URL</dt>
							<dd>
								{#if urls.length > 1}
									<details class="source-list">
										<summary>{urls.length} source URLs</summary>
										<ul>
											{#each urls as url}
												<li>{url}</li>
											{/each}
										</ul>
									</details>
								{:else}
									{urls[0]}
								{/if}
							</dd>
						</div>
					</dl>
				</article>
			{/each}
		</section>
	{/if}
</aside>

<style>
	.panel {
		overflow-y: auto;
		border-right: 1px solid #d7dce2;
		background: #ffffff;
		padding: 1.25rem;
		box-shadow: 0 0 20px rgba(15, 23, 42, 0.08);
		z-index: 1;
	}

	header h1 {
		margin: 0.2rem 0 0.6rem;
		font-size: 1.35rem;
		line-height: 1.2;
	}

	header p {
		margin: 0;
		color: #52616f;
		line-height: 1.45;
	}

	.eyebrow {
		margin: 0;
		color: #2167a9;
		font-size: 0.78rem;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
	}

	.control-group,
	.control-grid,
	.checkboxes,
	.actions,
	.status,
	.benchmarks,
	.results {
		margin-top: 1.15rem;
	}

	label,
	.control-label {
		display: grid;
		gap: 0.35rem;
		font-weight: 700;
	}

	input {
		width: 100%;
		box-sizing: border-box;
		border: 1px solid #c9d2dc;
		border-radius: 0.6rem;
		background: #ffffff;
		padding: 0.65rem 0.75rem;
		color: #102a43;
	}

	.multi-select {
		position: relative;
		margin-top: 0.35rem;
	}

	.multi-select summary {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		box-sizing: border-box;
		min-height: 2.8rem;
		border: 1px solid #c9d2dc;
		border-radius: 0.6rem;
		background: #ffffff;
		padding: 0.65rem 0.75rem;
		color: #102a43;
		cursor: pointer;
		list-style: none;
	}

	.multi-select summary::-webkit-details-marker {
		display: none;
	}

	.multi-select summary::after {
		content: '';
		width: 0.45rem;
		height: 0.45rem;
		border-right: 2px solid #52616f;
		border-bottom: 2px solid #52616f;
		transform: rotate(45deg) translateY(-0.15rem);
		flex: 0 0 auto;
	}

	.multi-select[open] summary::after {
		transform: rotate(225deg) translateY(-0.05rem);
	}

	.multi-select summary span {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.multi-select summary strong {
		flex: 0 0 auto;
		font-size: 0.78rem;
		color: #52616f;
	}

	.options {
		position: absolute;
		right: 0;
		left: 0;
		z-index: 4;
		max-height: min(22rem, 55vh);
		overflow-y: auto;
		border: 1px solid #c9d2dc;
		border-radius: 0.6rem;
		background: #ffffff;
		box-shadow: 0 14px 35px rgba(15, 23, 42, 0.16);
		margin-top: 0.35rem;
		padding: 0.35rem;
	}

	.options button {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: 0.7rem;
		width: 100%;
		border: 0;
		border-radius: 0.45rem;
		background: transparent;
		color: #1f2933;
		padding: 0.55rem 0.6rem;
		font-weight: 600;
		text-align: left;
		cursor: pointer;
	}

	.options button:hover,
	.options button:focus-visible {
		background: #eef6fc;
		outline: none;
	}

	.options button.selected {
		background: #dbeafe;
		color: #144f86;
	}

	.options button span:first-child {
		overflow-wrap: anywhere;
	}

	.checkmark {
		min-width: 3.4rem;
		color: #2167a9;
		font-size: 0.78rem;
		font-weight: 700;
		text-align: right;
	}

	.empty-state,
	.hint {
		margin: 0.45rem 0 0;
		color: #687789;
		font-size: 0.86rem;
		line-height: 1.35;
	}

	.empty-state {
		margin: 0;
		padding: 0.55rem 0.6rem;
	}

	.control-grid {
		display: grid;
		gap: 0.8rem;
	}

	.checkboxes {
		display: grid;
		gap: 0.6rem;
	}

	.checkboxes label {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-weight: 600;
	}

	.checkboxes input {
		width: auto;
	}

	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.6rem;
	}

	button {
		border: 1px solid #1f6fb2;
		border-radius: 0.65rem;
		background: #2167a9;
		color: white;
		padding: 0.65rem 0.85rem;
		font-weight: 700;
		cursor: pointer;
	}

	button.secondary {
		border-color: #c9d2dc;
		background: #ffffff;
		color: #1f2933;
	}

	button:disabled {
		cursor: not-allowed;
		opacity: 0.55;
	}

	.status {
		border: 1px solid #d7dce2;
		border-radius: 0.85rem;
		background: #f8fafc;
		padding: 0.85rem;
	}

	.status p {
		margin: 0.35rem 0 0;
		line-height: 1.4;
	}

	.benchmarks details {
		border: 1px solid #d7dce2;
		border-radius: 0.85rem;
		background: #ffffff;
	}

	.benchmarks summary {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.8rem 0.85rem;
		cursor: pointer;
		list-style: none;
		font-weight: 700;
	}

	.benchmarks summary::-webkit-details-marker {
		display: none;
	}

	.benchmarks summary::after {
		content: '';
		width: 0.45rem;
		height: 0.45rem;
		border-right: 2px solid #52616f;
		border-bottom: 2px solid #52616f;
		transform: rotate(45deg) translateY(-0.15rem);
		flex: 0 0 auto;
	}

	.benchmarks details[open] summary::after {
		transform: rotate(225deg) translateY(-0.05rem);
	}

	.benchmarks summary span {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.benchmarks summary strong {
		flex: 0 0 auto;
		color: #52616f;
		font-size: 0.78rem;
	}

	.benchmark-list {
		display: grid;
		gap: 0.7rem;
		border-top: 1px solid #d7dce2;
		padding: 0.75rem;
	}

	.benchmark-list article {
		border: 1px solid #d7dce2;
		border-radius: 0.7rem;
		background: #f8fafc;
		padding: 0.75rem;
	}

	.benchmark-list article header {
		display: grid;
		gap: 0.35rem;
	}

	.benchmark-list article header strong {
		overflow-wrap: anywhere;
	}

	.benchmark-list article header span {
		color: #144f86;
		font-size: 1rem;
		font-weight: 800;
	}

	.benchmark-list article header span.pending {
		color: #8a5a00;
		font-size: 0.86rem;
	}

	.source-list {
		max-width: 100%;
	}

	.source-list summary {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		border: 1px solid #c9d2dc;
		border-radius: 0.45rem;
		background: #ffffff;
		padding: 0.25rem 0.45rem;
		color: #144f86;
		font-size: 0.82rem;
		font-weight: 700;
		cursor: pointer;
		list-style: none;
	}

	.source-list summary::-webkit-details-marker {
		display: none;
	}

	.source-list summary::after {
		content: '';
		width: 0.35rem;
		height: 0.35rem;
		border-right: 2px solid currentColor;
		border-bottom: 2px solid currentColor;
		transform: rotate(45deg) translateY(-0.1rem);
	}

	.source-list[open] summary::after {
		transform: rotate(225deg) translateY(-0.02rem);
	}

	.source-list ul {
		display: grid;
		gap: 0.35rem;
		margin: 0.5rem 0 0;
		padding-left: 1rem;
	}

	.source-list li {
		overflow-wrap: anywhere;
	}

	pre {
		overflow-x: auto;
		border-radius: 0.6rem;
		background: #1f2933;
		color: #f8fafc;
		padding: 0.7rem;
		white-space: pre-wrap;
	}

	.results h2 {
		font-size: 1rem;
		margin: 0 0 0.7rem;
	}

	.results article {
		border: 1px solid #d7dce2;
		border-radius: 0.85rem;
		padding: 0.85rem;
		margin-bottom: 0.75rem;
	}

	dl {
		margin: 0.6rem 0 0;
		display: grid;
		gap: 0.45rem;
	}

	dl div {
		display: grid;
		grid-template-columns: 6.5rem 1fr;
		gap: 0.5rem;
	}

	dt {
		color: #687789;
		font-size: 0.84rem;
	}

	dd {
		margin: 0;
		overflow-wrap: anywhere;
	}

	@media (max-width: 860px) {
		.panel {
			border-right: none;
			border-bottom: 1px solid #d7dce2;
		}
	}
</style>
