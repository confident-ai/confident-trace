// Select an unpublished npm version. Keep stdout machine-readable for the shell.
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
// Dependencies live in the TypeScript package, not alongside this helper.
const semver = require(require.resolve('semver', { paths: [process.cwd()] }));

async function main() {
  const pkg = JSON.parse(fs.readFileSync(path.join(process.cwd(), 'package.json'), 'utf8'));
  let published;
  try {
    published = [].concat(JSON.parse(execFileSync('npm', ['view', pkg.name, 'versions', '--json',
      '--registry=https://registry.npmjs.org/'], { encoding: 'utf8', timeout: 30000, stdio: ['ignore', 'pipe', 'pipe'] })));
  } catch (error) {
    let response;
    try { response = JSON.parse(String(error.stdout)); } catch {}
    if (response?.error?.code !== 'E404') throw new Error('Could not check published npm versions. Resolve registry connectivity/authentication and retry.');
    published = [];
  }
  const base = semver.rsort([pkg.version, ...published])[0];
  let suggested = semver.inc(base, semver.prerelease(base) ? 'prerelease' : 'patch');
  while (published.includes(suggested)) {
    suggested = semver.inc(suggested, semver.prerelease(suggested) ? 'prerelease' : 'patch');
  }
  let version = process.argv[2];
  if (!version) {
    console.error(`Local: ${pkg.version}; latest published: ${semver.rsort([...published])[0] || 'none'}`);
    process.stderr.write(`Next version [${suggested}]: press Enter to continue or enter your own: `);
    const byte = Buffer.alloc(1);
    let answer = '';
    while (true) {
      if (fs.readSync(0, byte, 0, 1, null) === 0) throw new Error('No input received; cancelled. Pass VERSION for noninteractive use.');
      if (byte[0] === 10) break;
      answer += byte.toString();
    }
    version = answer.trim() || suggested;
  }
  if (semver.valid(version) !== version || version.includes('+')) throw new Error('Provide an exact version without build metadata, e.g. 0.1.0-alpha.2 or 0.1.1.');
  if (semver.lt(version, pkg.version)) throw new Error(`Version ${version} is older than local version ${pkg.version}.`);
  if (published.some(v => semver.eq(v, version))) throw new Error(`${pkg.name}@${version} is already published. Choose a new version.`);
  const pre = semver.prerelease(version);
  const tag = pre ? (typeof pre[0] === 'string' && /^[a-zA-Z][a-zA-Z0-9-]*$/.test(pre[0]) ? pre[0] : 'next') : 'latest';
  console.log(`${version} ${tag}`);
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
