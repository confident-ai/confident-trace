import { readFile, writeFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const png = await readFile(new URL('assets/confident-trace-banner.png', root));
if (png.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') {
  throw new Error('Expected a PNG');
}
const width = png.readUInt32BE(16);
const height = png.readUInt32BE(20);
const horizonY = height * 0.29;
const feather = height * 0.023;
const margin = feather * 6;
// A full-width band with separate, irregular upper/lower edges. The sky
// clears first; the lower edge rolls across the foreground more slowly.
function mistBand(sky, ground, waviness) {
  const top = [], bottom = [];
  for (let i = 0; i <= 32; i++) {
    const x = -margin + (width + 2 * margin) * i / 32;
    const ripple = Math.sin(i * 0.67) * 0.55 + Math.sin(i * 1.39 + 1.7) * 0.3;
    const lowerRipple = Math.sin(i * 0.49 + 0.8) * 0.65 + Math.sin(i * 1.17) * 0.25;
    top.push([x, horizonY - 3 - (horizonY + margin) * sky + ripple * waviness]);
    bottom.push([x, horizonY + 3 + (height - horizonY + margin) * ground + lowerRipple * waviness]);
  }
  return [...top, ...bottom.reverse()].map(([x, y], i) =>
    `${i ? 'L' : 'M'} ${x.toFixed(2)} ${y.toFixed(2)}`,
  ).join(' ') + ' Z';
}
const bands = [
  mistBand(0, 0, 2),
  mistBand(0.35, 0.12, height * 0.025),
  mistBand(0.9, 0.5, height * 0.045),
  mistBand(1, 1, 0),
];
// Sparse highlights in the sky, scaled with the original artwork.
const stars = [
  [0.078, 0.077, 1.4, 2.1, 6.8],
  [0.225, 0.105, 1.1, 2.5, 7.3],
  [0.351, 0.039, 1.5, 2.3, 8.1],
  [0.475, 0.125, 1.0, 2.9, 6.4],
  [0.628, 0.052, 1.2, 2.2, 7.6],
  [0.784, 0.072, 1.6, 2.7, 8.5],
  [0.898, 0.029, 1.0, 3.1, 7.1],
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
  <desc id="desc">A soft, uneven band of mist opens along the horizon and rolls toward the foreground, followed by gentle star twinkles.</desc>
  <style>
    @media (prefers-reduced-motion: reduce) {
      .landscape { mask: none !important; }
      .twinkles { display: none !important; }
    }
  </style>
  <defs>
    <filter id="mist-feather" filterUnits="userSpaceOnUse" x="${-margin}" y="${-margin}" width="${width + 2 * margin}" height="${height + 2 * margin}">
      <feGaussianBlur stdDeviation="${feather}" />
    </filter>
    <mask id="horizon-reveal" maskUnits="userSpaceOnUse" x="0" y="0" width="${width}" height="${height}" style="mask-type: luminance">
      <rect width="${width}" height="${height}" fill="black" />
      <path d="${bands.at(-1)}" fill="white" filter="url(#mist-feather)">
        <animate attributeName="d" values="${bands.join(';')}" keyTimes="0;0.22;0.55;1" dur="2s" repeatCount="1" fill="freeze" calcMode="spline" keySplines="0.42 0 0.58 1;0.42 0 0.58 1;0.42 0 0.58 1" />
        <animate attributeName="opacity" values="0;1" dur="0.2s" fill="freeze" />
      </path>
    </mask>
    <radialGradient id="starlight">
      <stop offset="0" stop-color="#fff5db" stop-opacity="0.7" />
      <stop offset="0.3" stop-color="#ffe8b6" stop-opacity="0.3" />
      <stop offset="1" stop-color="#ffe8b6" stop-opacity="0" />
    </radialGradient>
  </defs>
  <g class="landscape" mask="url(#horizon-reveal)">
    <image width="${width}" height="${height}" href="data:image/png;base64,${png.toString('base64')}" />
  </g>
  <g class="twinkles" pointer-events="none">${twinkles}
  </g>
</svg>
`;
await writeFile(new URL('assets/confident-trace-banner-mist-fast.svg', root), svg);
console.log(`Generated ${width}×${height} banner: two-second horizontal mist reveal, then ${stars.length} subtle star twinkles`);
