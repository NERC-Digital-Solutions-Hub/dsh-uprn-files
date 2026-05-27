import { sveltekit } from '@sveltejs/kit/vite';
import { createReadStream, statSync } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { extname, relative, resolve, sep } from 'node:path';
import { defineConfig } from 'vite';
import type { Plugin, ViteDevServer } from 'vite';

const parquetDataDir = resolve(process.env.PARQUET_DATA_DIR ?? 'parquet-data');

function isInsideDirectory(root: string, filePath: string): boolean {
  const relativePath = relative(root, filePath);
  return relativePath.length > 0 && !relativePath.startsWith('..') && !relativePath.includes(`..${sep}`);
}

function parquetDataDevServer(): Plugin {
  return {
    name: 'parquet-data-dev-server',
    configureServer(server: ViteDevServer) {
      server.middlewares.use('/parquet-data', (request: IncomingMessage, response: ServerResponse, next: () => void) => {
        if (!request.url) {
          next();
          return;
        }

        const requestPath = decodeURIComponent(request.url.split('?')[0] ?? '').replace(/^\/+/, '');
        const filePath = resolve(parquetDataDir, requestPath);

        if (!isInsideDirectory(parquetDataDir, filePath) || extname(filePath).toLowerCase() !== '.parquet') {
          next();
          return;
        }

        let stats;
        try {
          stats = statSync(filePath);
        } catch {
          response.statusCode = 404;
          response.end('Not found');
          return;
        }

        if (!stats.isFile()) {
          next();
          return;
        }

        const range = request.headers.range;
        response.setHeader('Accept-Ranges', 'bytes');
        response.setHeader('Content-Type', 'application/octet-stream');

        if (range) {
          const match = /^bytes=(\d*)-(\d*)$/.exec(range);
          if (!match) {
            response.statusCode = 416;
            response.end();
            return;
          }

          const start = match[1] ? Number(match[1]) : 0;
          const requestedEnd = match[2] ? Number(match[2]) : stats.size - 1;
          const end = Math.min(requestedEnd, stats.size - 1);

          if (start >= stats.size || end >= stats.size || start > end) {
            response.statusCode = 416;
            response.setHeader('Content-Range', `bytes */${stats.size}`);
            response.end();
            return;
          }

          response.statusCode = 206;
          response.setHeader('Content-Length', end - start + 1);
          response.setHeader('Content-Range', `bytes ${start}-${end}/${stats.size}`);
          createReadStream(filePath, { start, end }).pipe(response);
          return;
        }

        response.statusCode = 200;
        response.setHeader('Content-Length', stats.size);
        createReadStream(filePath).pipe(response);
      });
    }
  };
}

export default defineConfig({
  plugins: [parquetDataDevServer(), sveltekit()],
  optimizeDeps: {
    exclude: ['@arcgis/core', '@arcgis/map-components', '@esri/calcite-components']
  }
});
