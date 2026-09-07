import { readFile, writeFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const png = await readFile(new URL('assets/confident-trace-banner.png', root));
if (png.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') {
  throw new Error('Expected a PNG');
}
const width = png.readUInt32BE(16);
const height = png.readUInt32BE(20);
const centerX = width / 2;
const centerY = height / 2;
// Cover every corner with the gradient's fully opaque inner region at completion.
const radius = Math.ceil(Math.hypot(centerX, centerY) / 0.75);
// Sparse highlights in the sky, scaled with the original artwork.
const stars = [
  [0.078, 0.077, 1.4, 4.1, 6.8],
  [0.225, 0.105, 1.1, 4.5, 7.3],
  [0.351, 0.039, 1.5, 4.3, 8.1],
  [0.475, 0.125, 1.0, 4.9, 6.4],
  [0.628, 0.052, 1.2, 4.2, 7.6],
  [0.784, 0.072, 1.6, 4.7, 8.5],
  [0.898, 0.029, 1.0, 5.1, 7.1],
];
const twinkles = stars.map(([x, y, size, begin, duration]) => `
    <g transform="translate(${(x * width).toFixed(2)} ${(y * height).toFixed(2)})" opacity="0">
      <animate attributeName="opacity" values="0;0;0.55;0;0" keyTimes="0;0.2;0.4;0.65;1" dur="${duration}s" begin="${begin}s" repeatCount="indefinite" calcMode="spline" keySplines="0 0 1 1;0.42 0 0.58 1;0.42 0 0.58 1;0 0 1 1" />
      <circle r="${size * 7}" fill="url(#starlight)" />
      <path d="M ${-size * 3.5} 0 H ${size * 3.5} M 0 ${-size * 3.5} V ${size * 3.5}" stroke="#fff2d4" stroke-width="0.7" stroke-linecap="round" />
      <circle r="${size}" fill="#fff8e9" />
    </g>`).join('');

const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-labelledby="title desc">
  <title id="title">Confident Trace — illuminated paths across a star-filled landscape</title>
  <desc id="desc">The landscape grows outward from the center in a soft radial reveal, followed by gentle star twinkles.</desc>
  <style>
    @media (prefers-reduced-motion: reduce) {
      .landscape { mask: none !important; }
      .twinkles { display: none !important; }
    }
  </style>
  <defs>
    <radialGradient id="reveal-softness">
      <stop offset="0" stop-color="white" />
      <stop offset="0.75" stop-color="white" />
      <stop offset="0.9" stop-color="#aaa" />
      <stop offset="1" stop-color="black" />
    </radialGradient>
    <mask id="radial-reveal" maskUnits="userSpaceOnUse" x="0" y="0" width="${width}" height="${height}" style="mask-type: luminance">
      <rect width="${width}" height="${height}" fill="black" />
      <circle cx="${centerX}" cy="${centerY}" r="${radius}" fill="url(#reveal-softness)">
        <animate attributeName="r" values="0;${radius}" dur="4s" repeatCount="1" fill="freeze" calcMode="spline" keyTimes="0;1" keySplines="0.25 0.1 0.25 1" />
      </circle>
    </mask>
    <radialGradient id="starlight">
      <stop offset="0" stop-color="#fff5db" stop-opacity="0.7" />
      <stop offset="0.3" stop-color="#ffe8b6" stop-opacity="0.3" />
      <stop offset="1" stop-color="#ffe8b6" stop-opacity="0" />
    </radialGradient>
  </defs>
  <g class="landscape" mask="url(#radial-reveal)">
    <image width="${width}" height="${height}" href="data:image/png;base64,${png.toString('base64')}" />
  </g>
  <g class="twinkles" pointer-events="none">${twinkles}
  </g>
</svg>
`;
await writeFile(new URL('assets/confident-trace-banner-radial-4s.svg', root), svg);
console.log(`Generated ${width}×${height} banner: four-second center-out radial reveal, then ${stars.length} subtle star twinkles`);
