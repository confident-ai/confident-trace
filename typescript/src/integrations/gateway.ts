import { get } from '@/integrations/extract';

function endpoint(value: string): string {
  const url = new URL(value);
  if (
    !['http:', 'https:'].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  )
    throw new Error(
      'Gateway URLs must be absolute HTTP(S) URLs without credentials, queries or fragments',
    );
  return `${url.origin}${url.pathname.replace(/\/+$/, '')}`;
}

export function matchesEndpoint(
  resource: unknown,
  urls: readonly string[],
): boolean {
  if (!urls.length) return false;
  try {
    const baseURL = get(get(resource, '_client'), 'baseURL');
    return (
      typeof baseURL === 'string' &&
      urls.some((url) => endpoint(url) === endpoint(baseURL))
    );
  } catch {
    return false;
  }
}

export function gatewayName(
  resource: unknown,
  litellmUrls: readonly string[] = [],
  openrouterUrls: readonly string[] = [],
  portkeyUrls: readonly string[] = [],
  bifrostUrls: readonly string[] = [],
  truefoundryUrls: readonly string[] = [],
): string | undefined {
  if (matchesEndpoint(resource, litellmUrls)) return 'litellm';
  if (
    matchesEndpoint(resource, [
      'https://openrouter.ai/api/v1',
      ...openrouterUrls,
    ])
  )
    return 'openrouter';
  if (matchesEndpoint(resource, ['https://api.portkey.ai/v1', ...portkeyUrls]))
    return 'portkey';
  if (matchesEndpoint(resource, bifrostUrls)) return 'bifrost';
  if (matchesEndpoint(resource, truefoundryUrls)) return 'truefoundry';
  return undefined;
}
