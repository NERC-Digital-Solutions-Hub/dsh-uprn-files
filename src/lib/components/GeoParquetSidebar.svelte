<script lang="ts">
	type ParquetDataFile = {
		name: string;
		url: string;
	};

	type LoadedLayerSummary = {
		title: string;
		url: string;
		geometryField: string;
		geometryType: string;
		featureCount: number | 'unknown';
	};

	export let parquetFiles: ParquetDataFile[] = [];
	export let urlParquetFiles: ParquetDataFile[] = [];
	export let selectedParquetUrls: string[] = [];
	export let ready = false;
	export let loading = false;
	export let status = '';
	export let errorMessage = '';
	export let basemap = '';
	export let center = '';
	export let zoom = 6;
	export let autoZoom = true;
	export let clearExisting = true;
	export let loadedLayerSummaries: LoadedLayerSummary[] = [];
	export let onLoadGeoParquet: () => void = () => undefined;
	export let onUpdateView: () => void = () => undefined;
	export let onRemoveLoadedLayers: () => void = () => undefined;

	$: localSelectedCount = selectedCountFromFiles(parquetFiles, selectedParquetUrls);
	$: urlSelectedCount = selectedCountFromFiles(urlParquetFiles, selectedParquetUrls);
	$: localSelectionLabel = selectionLabelFromFiles(
		parquetFiles,
		selectedParquetUrls,
		'Select local parquet files'
	);
	$: urlSelectionLabel = selectionLabelFromFiles(
		urlParquetFiles,
		selectedParquetUrls,
		'Select parquet URLs'
	);

	function selectedCountFromFiles(files: ParquetDataFile[], selectedUrls: string[]): number {
		return files.filter((file) => selectedUrls.includes(file.url)).length;
	}

	function selectionLabelFromFiles(
		files: ParquetDataFile[],
		selectedUrls: string[],
		fallback: string
	): string {
		const selectedNames = files
			.filter((file) => selectedUrls.includes(file.url))
			.map((file) => file.name);

		return selectedNames.length > 0 ? selectedNames.join(', ') : fallback;
	}

	function toggleParquetFile(url: string): void {
		selectedParquetUrls = selectedParquetUrls.includes(url)
			? selectedParquetUrls.filter((selectedUrl) => selectedUrl !== url)
			: [...selectedParquetUrls, url];
	}
</script>

<aside class="panel">
	<header>
		<p class="eyebrow">ArcGIS Maps SDK + SvelteKit</p>
		<h1>GeoParquet ParquetLayer test viewer</h1>
		<p>
			Copy GeoParquet files into <code>parquet-data</code> or add external GeoParquet URLs to
			<code>urls.txt</code>, then choose one or more sources.
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
					{#each parquetFiles as file}
						<button
							type="button"
							class:selected={selectedParquetUrls.includes(file.url)}
							aria-pressed={selectedParquetUrls.includes(file.url)}
							on:click={() => toggleParquetFile(file.url)}
						>
							<span>{file.name}</span>
							<span class="checkmark">{selectedParquetUrls.includes(file.url) ? 'Selected' : ''}</span>
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
					<p class="empty-state">No .parquet URLs were found in urls.txt.</p>
				{:else}
					{#each urlParquetFiles as file}
						<button
							type="button"
							class:selected={selectedParquetUrls.includes(file.url)}
							aria-pressed={selectedParquetUrls.includes(file.url)}
							title={file.url}
							on:click={() => toggleParquetFile(file.url)}
						>
							<span>{file.name}</span>
							<span class="checkmark">{selectedParquetUrls.includes(file.url) ? 'Selected' : ''}</span>
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
		<button type="button" on:click={onLoadGeoParquet} disabled={!ready || loading}>
			{loading ? 'Loading...' : 'Load GeoParquet'}
		</button>
		<button type="button" class="secondary" on:click={onUpdateView} disabled={!ready}>
			Update view
		</button>
		<button type="button" class="secondary" on:click={onRemoveLoadedLayers} disabled={!ready}>
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

	{#if loadedLayerSummaries.length > 0}
		<section class="results">
			<h2>Loaded layers</h2>
			{#each loadedLayerSummaries as layer}
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
							<dd>{layer.url}</dd>
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

	code {
		border-radius: 0.35rem;
		background: #eef2f7;
		padding: 0.08rem 0.28rem;
		font-size: 0.86em;
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
