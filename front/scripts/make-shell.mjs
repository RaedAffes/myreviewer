// Generate shell.html from the prerendered index.html.
//
// With outputMode "static", only "/" is prerendered into index.html. Any other
// route (e.g. /dashboard, /reports/:id) is served by nginx from the SPA
// fallback. Serving the full prerendered landing page for those routes causes
// hydration mismatches, so the fallback is a stripped-down, empty-shell version
// of index.html with the same <head>, styles and scripts.

import { readFile, writeFile } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const inPath = join(root, 'dist', 'myreviewer', 'browser', 'index.html');
const outPath = join(root, 'dist', 'myreviewer', 'browser', 'shell.html');

const html = await readFile(inPath, 'utf8');
const shell = html.replace(/<app-root[\s\S]*?<\/app-root>/, '<app-root></app-root>');

await writeFile(outPath, shell);
console.log('shell.html generated');