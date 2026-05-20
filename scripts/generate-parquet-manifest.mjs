import { mkdir, readdir, writeFile } from 'node:fs/promises';
import { basename, resolve } from 'node:path';

const sourceDir = resolve(process.env.PARQUET_DATA_DIR ?? 'parquet-data');
const manifestPath = resolve('static/parquet-files.json');

async function readParquetFiles() {
	try {
		const entries = await readdir(sourceDir, { withFileTypes: true });

		return entries
			.filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith('.parquet'))
			.map((entry) => entry.name)
			.sort((first, second) => first.localeCompare(second));
	} catch (error) {
		if (error?.code === 'ENOENT') {
			return [];
		}

		throw error;
	}
}

const files = await readParquetFiles();
const manifest = {
	source: basename(sourceDir),
	files: files.map((name) => ({
		name,
		url: `/parquet-data/${encodeURIComponent(name)}`,
	})),
};

await mkdir(resolve('static'), { recursive: true });
await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);

console.log(`Wrote ${manifestPath} with ${files.length} Parquet file${files.length === 1 ? '' : 's'}.`);
