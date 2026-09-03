/**
 * WebSocket Configuration
 *
 * Supports multiple ways to configure the server URL:
 * 1. URL query parameter: ?server=192.168.0.191:5002
 * 2. localStorage: cricket-app-server-url
 * 3. The host that served this page (the Pi serves the UI itself)
 * 4. Last working URL
 * 5. Environment variable: VITE_WS_URL (build time)
 * 6. Auto-discovery: tries cricketradar.local, raspberrypi.local
 * 7. Default fallback
 */

const STORAGE_KEY = 'cricket-app-server-url';
const LAST_WORKING_KEY = 'cricket-app-last-working-url';
export const DEFAULT_PORT = 5002;

// The Pi's hostname is `cricketradar`; `raspberrypi.local` is the stock image
// default, kept for a freshly-flashed card.
export const DEFAULT_HOST = 'cricketradar.local';

// Discovery URLs to try, in order
const DISCOVERY_URLS = [
  `ws://${DEFAULT_HOST}:${DEFAULT_PORT}`,
  `ws://raspberrypi.local:${DEFAULT_PORT}`,
];

/** The subset of window.location the discovery logic reads - injectable so
 * the ordering rules can be tested without faking a browser origin. */
export interface OriginInfo {
  protocol: string;
  hostname: string;
  search: string;
}

function currentOrigin(): OriginInfo | null {
  if (typeof window === 'undefined') return null;
  const { protocol, hostname, search } = window.location;
  return { protocol, hostname, search };
}

function normalizeWsUrl(value: string): string {
  let normalized = value.trim();
  if (!normalized.startsWith('ws://') && !normalized.startsWith('wss://')) {
    normalized = `ws://${normalized}`;
  }
  // Append the default port only when the authority has none. (The previous
  // check looked for ANY colon after index 5, which the `wss://` scheme
  // itself satisfies - so a wss host never got its port.)
  const authority = normalized.replace(/^wss?:\/\//, '').split('/')[0];
  if (!/:\d+$/.test(authority)) {
    normalized = `${normalized}:${DEFAULT_PORT}`;
  }
  return normalized;
}

function serverParamUrl(origin: OriginInfo | null): string | null {
  if (!origin) return null;
  const serverParam = new URLSearchParams(origin.search).get('server');
  return serverParam ? normalizeWsUrl(serverParam) : null;
}

/**
 * The server on the SAME HOST that served this page.
 *
 * At the nets the phone loads the UI from the Pi (its AP address or LAN IP),
 * so the Pi's WebSocket is at that very hostname. mDNS names are exactly what
 * did not resolve in the field, and every failed discovery candidate costs a
 * 3s timeout before the operator gets to type the IP by hand. This candidate
 * removes that path entirely. Excluded: https origins (they cannot open
 * ws:// at all) and localhost (the Vite dev server - the Pi is not here).
 */
export function sameOriginServerUrl(origin: OriginInfo | null = currentOrigin()): string | null {
  if (!origin) return null;
  if (origin.protocol !== 'http:') return null;
  const host = origin.hostname;
  if (!host || host === 'localhost' || host === '127.0.0.1' || host === '[::1]') return null;
  return `ws://${host}:${DEFAULT_PORT}`;
}

/**
 * Get the WebSocket server URL from various sources.
 * Priority: URL param > localStorage > same-origin host > env var > default
 */
export function getServerUrl(origin: OriginInfo | null = currentOrigin()): string {
  const fromParam = serverParamUrl(origin);
  if (fromParam) return fromParam;

  const stored = getSavedServerUrl();
  if (stored) return stored;

  const sameOrigin = sameOriginServerUrl(origin);
  if (sameOrigin) return sameOrigin;

  if (import.meta.env.VITE_WS_URL) {
    return import.meta.env.VITE_WS_URL;
  }

  return `ws://${DEFAULT_HOST}:${DEFAULT_PORT}`;
}

/**
 * Save the server URL to localStorage for persistence.
 */
export function saveServerUrl(url: string): void {
  if (typeof window !== 'undefined') {
    localStorage.setItem(STORAGE_KEY, normalizeWsUrl(url));
  }
}

/**
 * Clear the saved server URL (revert to defaults).
 */
export function clearServerUrl(): void {
  if (typeof window !== 'undefined') {
    localStorage.removeItem(STORAGE_KEY);
  }
}

/**
 * Get the saved server URL from localStorage (without fallbacks).
 * Returns null if not configured.
 */
export function getSavedServerUrl(): string | null {
  if (typeof window !== 'undefined') {
    return localStorage.getItem(STORAGE_KEY);
  }
  return null;
}

/**
 * Check if the server URL is using the default/unconfigured value.
 */
export function isServerConfigured(): boolean {
  return getSavedServerUrl() !== null ||
         new URLSearchParams(window.location.search).has('server') ||
         !!import.meta.env.VITE_WS_URL;
}

/**
 * Get the last working server URL.
 */
export function getLastWorkingUrl(): string | null {
  if (typeof window !== 'undefined') {
    return localStorage.getItem(LAST_WORKING_KEY);
  }
  return null;
}

/**
 * Save the last working server URL.
 */
export function saveLastWorkingUrl(url: string): void {
  if (typeof window !== 'undefined') {
    localStorage.setItem(LAST_WORKING_KEY, url);
  }
}

/**
 * Get the list of URLs to try for auto-discovery, most likely first:
 * explicit ?server= > saved > same-origin host > last working > mDNS names.
 */
export function getDiscoveryUrls(origin: OriginInfo | null = currentOrigin()): string[] {
  const urls: string[] = [];
  const push = (url: string | null) => {
    if (url && !urls.includes(url)) urls.push(url);
  };

  push(serverParamUrl(origin));
  push(getSavedServerUrl());
  push(sameOriginServerUrl(origin));
  push(getLastWorkingUrl());
  for (const url of DISCOVERY_URLS) push(url);

  return urls;
}

/**
 * Attempt to connect to a WebSocket URL with timeout.
 * Returns the URL if successful, null if failed.
 */
export async function tryConnect(url: string, timeoutMs: number = 3000): Promise<boolean> {
  return new Promise((resolve) => {
    try {
      const ws = new WebSocket(url);
      const timeout = setTimeout(() => {
        ws.close();
        resolve(false);
      }, timeoutMs);

      ws.onopen = () => {
        clearTimeout(timeout);
        ws.close();
        resolve(true);
      };

      ws.onerror = () => {
        clearTimeout(timeout);
        resolve(false);
      };
    } catch {
      resolve(false);
    }
  });
}

/**
 * Auto-discover the server by trying multiple URLs.
 * Returns the first working URL, or null if none work.
 */
export async function discoverServer(
  onStatus?: (message: string) => void
): Promise<string | null> {
  const urls = getDiscoveryUrls();

  for (const url of urls) {
    onStatus?.(`Trying ${url}...`);

    const success = await tryConnect(url, 3000);
    if (success) {
      onStatus?.(`Connected to ${url}`);
      saveLastWorkingUrl(url);
      return url;
    }
  }

  onStatus?.('No server found');
  return null;
}
