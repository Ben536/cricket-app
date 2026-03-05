/**
 * WebSocket Configuration
 *
 * Supports multiple ways to configure the server URL:
 * 1. URL query parameter: ?server=192.168.0.191:5002
 * 2. localStorage: cricket-app-server-url
 * 3. Environment variable: VITE_WS_URL (build time)
 * 4. Auto-discovery: tries cricketradar.local, raspberrypi.local, etc.
 * 5. Default fallback
 */

const STORAGE_KEY = 'cricket-app-server-url';
const LAST_WORKING_KEY = 'cricket-app-last-working-url';
const DEFAULT_PORT = 5002;

// Discovery URLs to try, in order
const DISCOVERY_URLS = [
  `ws://cricketradar.local:${DEFAULT_PORT}`,
  `ws://raspberrypi.local:${DEFAULT_PORT}`,
];

/**
 * Get the WebSocket server URL from various sources.
 * Priority: URL param > localStorage > env var > default
 */
export function getServerUrl(): string {
  // 1. Check URL query parameter (highest priority, for testing)
  if (typeof window !== 'undefined') {
    const params = new URLSearchParams(window.location.search);
    const serverParam = params.get('server');
    if (serverParam) {
      // Handle both "192.168.0.191:5002" and "ws://192.168.0.191:5002"
      if (serverParam.startsWith('ws://') || serverParam.startsWith('wss://')) {
        return serverParam;
      }
      return `ws://${serverParam}`;
    }
  }

  // 2. Check localStorage
  if (typeof window !== 'undefined') {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      return stored;
    }
  }

  // 3. Check environment variable (set at build time)
  if (import.meta.env.VITE_WS_URL) {
    return import.meta.env.VITE_WS_URL;
  }

  // 4. Default - try raspberrypi.local first
  return `ws://raspberrypi.local:${DEFAULT_PORT}`;
}

/**
 * Save the server URL to localStorage for persistence.
 */
export function saveServerUrl(url: string): void {
  if (typeof window !== 'undefined') {
    // Normalize the URL
    let normalized = url.trim();
    if (!normalized.startsWith('ws://') && !normalized.startsWith('wss://')) {
      normalized = `ws://${normalized}`;
    }
    // Ensure port is included
    if (!normalized.includes(':', 5)) { // Check for port after ws://
      normalized = `${normalized}:${DEFAULT_PORT}`;
    }
    localStorage.setItem(STORAGE_KEY, normalized);
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
 * Get the list of URLs to try for auto-discovery.
 * Includes: configured URL, last working URL, and discovery URLs.
 */
export function getDiscoveryUrls(): string[] {
  const urls: string[] = [];

  // 1. Explicit configuration (highest priority)
  if (typeof window !== 'undefined') {
    const params = new URLSearchParams(window.location.search);
    const serverParam = params.get('server');
    if (serverParam) {
      const url = serverParam.startsWith('ws://') || serverParam.startsWith('wss://')
        ? serverParam
        : `ws://${serverParam}`;
      urls.push(url);
    }
  }

  // 2. Saved server URL
  const saved = getSavedServerUrl();
  if (saved && !urls.includes(saved)) {
    urls.push(saved);
  }

  // 3. Last working URL
  const lastWorking = getLastWorkingUrl();
  if (lastWorking && !urls.includes(lastWorking)) {
    urls.push(lastWorking);
  }

  // 4. Discovery URLs
  for (const url of DISCOVERY_URLS) {
    if (!urls.includes(url)) {
      urls.push(url);
    }
  }

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
