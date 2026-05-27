import { mkdir, readdir, writeFile } from 'node:fs/promises';
import { basename, join, resolve } from 'node:path';

const sourceDir = resolve(process.env.PARQUET_DATA_DIR ?? 'parquet-data');
const manifestPath = resolve('static/parquet-files.json');

async function readParquetFiles(directory = sourceDir, prefix = '') {
	try {
		const entries = await readdir(directory, { withFileTypes: true });
		const nestedFiles = await Promise.all(
			entries.map(async (entry) => {
				const relativeName = prefix ? `${prefix}/${entry.name}` : entry.name;
				const fullPath = join(directory, entry.name);

				if (entry.isDirectory()) {
					return readParquetFiles(fullPath, relativeName);
				}

				if (entry.isFile() && entry.name.toLowerCase().endsWith('.parquet')) {
					return [relativeName];
				}

				return [];
			})
		);

		return nestedFiles.flat().sort((first, second) => first.localeCompare(second));
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
		url: `/parquet-data/${name.split('/').map(encodeURIComponent).join('/')}`,
	})),
};

await mkdir(resolve('static'), { recursive: true });
await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);

console.log(`Wrote ${manifestPath} with ${files.length} Parquet file${files.length === 1 ? '' : 's'}.`);
