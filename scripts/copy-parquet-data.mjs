import { copyFile, mkdir, readdir, rm } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';

const sourceDir = resolve(process.env.PARQUET_DATA_DIR ?? 'parquet-data');
const outputDir = resolve('build/parquet-data');

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

await rm(outputDir, { recursive: true, force: true });
await mkdir(outputDir, { recursive: true });

for (const file of files) {
  const outputPath = join(outputDir, file);
  await mkdir(dirname(outputPath), { recursive: true });
  await copyFile(join(sourceDir, file), outputPath);
}

console.log(`Copied ${files.length} Parquet file${files.length === 1 ? '' : 's'} to ${outputDir}.`);
