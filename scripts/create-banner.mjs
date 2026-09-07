import { readFile, writeFile } from 'node:fs/promises';
const root = new URL('../', import.meta.url);
const png = await readFile(new URL('assets/confident-trace-banner.png', root));
if (png.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') throw new Error('Expected a PNG');
const width = png.readUInt32BE(16);
const height = png.readUInt32BE(20);
const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-labelledby="title">
  <title id="title">Confident Trace — illuminated paths across a star-filled landscape</title>
  <style>
    @media (prefers-reduced-motion: reduce) {
      .banner { opacity: 1 !important; }
    }
  </style>
  <g class="banner">
    <animate attributeName="opacity" values="0;1" dur="6s" repeatCount="1" fill="freeze" calcMode="spline" keyTimes="0;1" keySplines="0.25 0.1 0.25 1" />
    <image width="${width}" height="${height}" href="data:image/png;base64,${png.toString('base64')}" />
  </g>
</svg>
`;
await writeFile(new URL('assets/confident-trace-banner.svg', root), svg);
console.log(`Generated ${width}×${height} banner with a six-second fade`);
