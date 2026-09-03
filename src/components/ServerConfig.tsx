/**
 * Server Configuration Component
 *
 * Allows users to configure the Raspberry Pi server address.
 * Shows connection status and allows reconnection.
 */

import { useState, useEffect } from 'react';
import { getServerUrl, saveServerUrl, clearServerUrl, DEFAULT_HOST, DEFAULT_PORT } from '../api/config';

const DEFAULT_ADDRESS = `${DEFAULT_HOST}:${DEFAULT_PORT}`;

interface ServerConfigProps {
  isConnected: boolean;
  connectionState: string;
  statusMessage?: string | null;
  error?: string | null;
  onReconnect?: () => void;
  onClose: () => void;
}

export function ServerConfig({ isConnected, connectionState, statusMessage, error, onReconnect, onClose }: ServerConfigProps) {
  const [serverAddress, setServerAddress] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    // Load current server URL (just the host:port part)
    const currentUrl = getServerUrl();
    const match = currentUrl.match(/wss?:\/\/(.+)/);
    setServerAddress(match ? match[1] : DEFAULT_ADDRESS);
  }, []);

  const handleSave = () => {
    saveServerUrl(serverAddress);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleReset = () => {
    clearServerUrl();
    setServerAddress(DEFAULT_ADDRESS);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleReconnect = () => {
    saveServerUrl(serverAddress);
    if (onReconnect) {
      onReconnect();
    } else {
      // Fallback: reload to reconnect with the new URL
      window.location.reload();
    }
  };

  return (
    <div className="server-config-overlay" onClick={onClose}>
      <div className="server-config-modal" onClick={e => e.stopPropagation()}>
        <h3>Server Connection</h3>

        <div className="config-status">
          <span className={`status-dot ${isConnected ? 'connected' : 'disconnected'}`} />
          <span>{connectionState}</span>
        </div>
        {statusMessage && !isConnected && (
          <p className="config-status-message">{statusMessage}</p>
        )}
        {error && (
          <p className="config-error" style={{ color: '#ff7b72', fontSize: 13 }}>{error}</p>
        )}

        <div className="config-field">
          <label htmlFor="server-address">Pi Server Address:</label>
          <input
            id="server-address"
            type="text"
            value={serverAddress}
            onChange={e => setServerAddress(e.target.value)}
            placeholder={`${DEFAULT_ADDRESS} or 10.42.0.1:5002`}
          />
          <small>Enter your Raspberry Pi's IP address or hostname with port. If this page was served by the Pi, its address is tried first automatically.</small>
        </div>

        <div className="config-actions">
          <button onClick={handleSave} className="btn-save">
            {saved ? 'Saved!' : 'Save'}
          </button>
          <button onClick={handleReconnect} className="btn-reconnect">
            Save & Reconnect
          </button>
          <button onClick={handleReset} className="btn-reset">
            Reset to Default
          </button>
        </div>

        <div className="config-help">
          <p><strong>Finding your Pi's address:</strong></p>
          <ol>
            <li>On the Pi, run: <code>hostname -I</code></li>
            <li>Use the IP shown (e.g., 192.168.0.191)</li>
            <li>Or use <code>{DEFAULT_HOST}</code> if mDNS works (it often does not at the nets)</li>
            <li>On the CricketRadar hotspot the Pi is <code>10.42.0.1</code></li>
          </ol>
          <p><strong>Quick test:</strong> Add <code>?server=192.168.0.191:5002</code> to the URL</p>
        </div>

        <button onClick={onClose} className="btn-close">Close</button>
      </div>
    </div>
  );
}

export default ServerConfig;
