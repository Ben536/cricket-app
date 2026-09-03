/**
 * Session settings that survive a reload.
 *
 * `profiles` and `customFields` were persisted from the start, but the three
 * settings that describe the CURRENT session - the field layout, the
 * batter's hand and the difficulty - were not. iOS evicts a background tab
 * whenever it likes; coming back to the app silently reverted to Standard
 * Pace / right-handed / medium, and a left-hander's next simulated shot ran
 * against a mirrored field. Everything the operator chose is now kept.
 *
 * Stored values are validated on load: localStorage is user-writable and a
 * malformed entry must degrade to the defaults, never to a crash.
 */

import { FIELD_PRESET_POSITIONS } from './fieldZones'
import type { BattingHand, Difficulty } from './scoring'

export interface FielderPosition {
  id: string
  x: number
  y: number
}

export interface SessionSettings {
  fielderPositions: FielderPosition[]
  batterHand: BattingHand
  difficulty: Difficulty
}

export const SETTINGS_KEY = 'cricket-app-session-settings'

export const DEFAULT_SETTINGS: SessionSettings = {
  fielderPositions: FIELD_PRESET_POSITIONS['Standard Pace'],
  batterHand: 'right',
  difficulty: 'medium',
}

const HANDS: readonly BattingHand[] = ['right', 'left']
const DIFFICULTIES: readonly Difficulty[] = ['easy', 'medium', 'hard']

function isFinitePercent(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 100
}

function isFielderPosition(v: unknown): v is FielderPosition {
  return (
    typeof v === 'object' && v !== null &&
    typeof (v as FielderPosition).id === 'string' &&
    isFinitePercent((v as FielderPosition).x) &&
    isFinitePercent((v as FielderPosition).y)
  )
}

/** Parse a stored value; anything malformed falls back field-by-field. */
export function parseSettings(raw: unknown): SessionSettings {
  const out: SessionSettings = { ...DEFAULT_SETTINGS }
  if (typeof raw !== 'object' || raw === null) return out
  const r = raw as Partial<Record<keyof SessionSettings, unknown>>
  if (Array.isArray(r.fielderPositions) && r.fielderPositions.length > 0 && r.fielderPositions.every(isFielderPosition)) {
    out.fielderPositions = r.fielderPositions.map((f) => ({ id: f.id, x: f.x, y: f.y }))
  }
  if (HANDS.includes(r.batterHand as BattingHand)) out.batterHand = r.batterHand as BattingHand
  if (DIFFICULTIES.includes(r.difficulty as Difficulty)) out.difficulty = r.difficulty as Difficulty
  return out
}

export function loadSettings(storage: Storage | null = safeStorage()): SessionSettings {
  try {
    const saved = storage?.getItem(SETTINGS_KEY)
    if (saved) return parseSettings(JSON.parse(saved))
  } catch (e) {
    console.error('Failed to load session settings:', e)
  }
  return { ...DEFAULT_SETTINGS }
}

export function saveSettings(settings: SessionSettings, storage: Storage | null = safeStorage()): void {
  try {
    storage?.setItem(SETTINGS_KEY, JSON.stringify(settings))
  } catch (e) {
    console.error('Failed to save session settings:', e)
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}
