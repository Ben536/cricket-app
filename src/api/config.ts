/**
 * WebSocket Configuration
 *
 * Supports multiple ways to configure the server URL:
 * 1. URL query parameter: ?server=192.168.0.191:5002
 * 2. localStorage: cricket-app-server-url
 * 3. Environment variable: VITE_WS_URL (build time)
 * 4. Default fallback
 */

const STORAGE_KEY = 'cricket-app-server-url';
const DEFAULT_PORT = 5002;

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
