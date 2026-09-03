// @vitest-environment jsdom
/**
 * Server discovery ordering.
 *
 * The rule that matters at the nets: if the Pi served this page, the Pi's
 * WebSocket is on the same host, and that must be the FIRST thing tried.
 * mDNS names are exactly what did not resolve in the field, and each failed
 * candidate costs a 3s timeout.
 */

import { describe, it, expect, beforeEach } from 'vitest'

import {
  DEFAULT_HOST,
  DEFAULT_PORT,
  getDiscoveryUrls,
  getServerUrl,
  sameOriginServerUrl,
  saveLastWorkingUrl,
  saveServerUrl,
  getSavedServerUrl,
  type OriginInfo,
} from '../api/config'

const fromPi: OriginInfo = { protocol: 'http:', hostname: '10.42.0.1', search: '' }
const fromLan: OriginInfo = { protocol: 'http:', hostname: '192.168.0.191', search: '' }
const fromVercel: OriginInfo = { protocol: 'https:', hostname: 'cricket-app.vercel.app', search: '' }
const fromDev: OriginInfo = { protocol: 'http:', hostname: 'localhost', search: '' }

beforeEach(() => {
  localStorage.clear()
})

describe('sameOriginServerUrl', () => {
  it('is the serving host on port 5002 when served over http from a real host', () => {
    expect(sameOriginServerUrl(fromPi)).toBe('ws://10.42.0.1:5002')
    expect(sameOriginServerUrl(fromLan)).toBe('ws://192.168.0.191:5002')
  })

  it('is absent for https (mixed content) and for the dev server', () => {
    expect(sameOriginServerUrl(fromVercel)).toBeNull()
    expect(sameOriginServerUrl(fromDev)).toBeNull()
    expect(sameOriginServerUrl(null)).toBeNull()
  })
})

describe('getDiscoveryUrls', () => {
  it('tries the serving host first when nothing is configured', () => {
    const urls = getDiscoveryUrls(fromPi)
    expect(urls[0]).toBe('ws://10.42.0.1:5002')
    expect(urls).toContain(`ws://${DEFAULT_HOST}:${DEFAULT_PORT}`)
    expect(new Set(urls).size).toBe(urls.length) // no duplicates
  })

  it('an explicit ?server= beats everything, then the saved URL, then same-origin, then last-working', () => {
    saveServerUrl('192.168.1.50')
    saveLastWorkingUrl('ws://192.168.1.99:5002')
    const urls = getDiscoveryUrls({ ...fromPi, search: '?server=10.0.0.7:5002' })
    expect(urls.slice(0, 4)).toEqual([
      'ws://10.0.0.7:5002',
      'ws://192.168.1.50:5002',
      'ws://10.42.0.1:5002',
      'ws://192.168.1.99:5002',
    ])
  })

  it('falls back to the mDNS names from the dev server', () => {
    expect(getDiscoveryUrls(fromDev)).toEqual([
      `ws://${DEFAULT_HOST}:${DEFAULT_PORT}`,
      `ws://raspberrypi.local:${DEFAULT_PORT}`,
    ])
  })
})

describe('getServerUrl', () => {
  it('prefers the serving host over the default name', () => {
    expect(getServerUrl(fromPi)).toBe('ws://10.42.0.1:5002')
    expect(getServerUrl(fromDev)).toBe(`ws://${DEFAULT_HOST}:${DEFAULT_PORT}`)
  })
})

describe('saveServerUrl normalisation', () => {
  it('adds the scheme and the default port when missing', () => {
    saveServerUrl(' 192.168.0.5 ')
    expect(getSavedServerUrl()).toBe('ws://192.168.0.5:5002')
  })

  it('keeps an explicit port and a wss scheme', () => {
    saveServerUrl('ws://pi.local:6000')
    expect(getSavedServerUrl()).toBe('ws://pi.local:6000')
    // The old check looked for any ':' after index 5, which "wss://" itself
    // satisfies - so a wss host never got a port appended.
    saveServerUrl('wss://pi.example')
    expect(getSavedServerUrl()).toBe('wss://pi.example:5002')
  })
})
