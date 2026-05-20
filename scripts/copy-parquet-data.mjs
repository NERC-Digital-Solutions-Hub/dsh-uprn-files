import { copyFile, mkdir, readdir, rm } from 'node:fs/promises';
import { join, resolve } from 'node:path';

const sourceDir = resolve(process.env.PARQUET_DATA_DIR ?? 'parquet-data');
const outputDir = resolve('build/parquet-data');

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

await rm(outputDir, { recursive: true, force: true });
await mkdir(outputDir, { recursive: true });

for (const file of files) {
  await copyFile(join(sourceDir, file), join(outputDir, file));
}

console.log(`Copied ${files.length} Parquet file${files.length === 1 ? '' : 's'} to ${outputDir}.`);