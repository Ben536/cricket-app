/**
 * Scoring rules for a net session - pure functions, no React, no storage.
 *
 * This is the phone's system of record for the score (nothing on the Pi is
 * written to during play), so these rules ARE the product's scoring law.
 * They used to live inline in App.tsx where nothing could test them; the
 * only regression tests for the P0 undo bug of 2026-08 were ad-hoc jsdom
 * scripts that were never committed. Every rule here is pinned by
 * src/__tests__/scoring.test.ts.
 *
 * Cricket conventions applied:
 *  - a wide or no-ball scores one extra run and does NOT count as a ball
 *    faced, nor as a ball of the over (the over needs six LEGAL deliveries)
 *  - a wicket scores nothing and marks the batter out (they keep batting in
 *    the nets - `isOut` is a status, not a stop)
 *  - fours and sixes are counted only when the ball reached the rope:
 *    three runs plus a misfield is not a four, and `isBoundary` says so
 */

export type BallResult = 'dot' | '1' | '2' | '3' | '4' | '6' | 'W' | 'wd' | 'nb'
export type BattingHand = 'right' | 'left'
export type Difficulty = 'easy' | 'medium' | 'hard'

export interface Over {
  balls: BallResult[]
  runs: number
}

export interface Session {
  id: string
  date: string
  runs: number
  balls: number
  fours: number
  sixes: number
  wickets: number
  isOut: boolean
  overs: Over[]
  strikeRate: number
}

export interface Profile {
  id: string
  name: string
  sessions: Session[]
  currentSession: Session
}

/** What the UI knows about a delivery before it is scored. */
export interface DeliveryInput {
  runs: number
  isBoundary?: boolean
  isWicket?: boolean
  isWide?: boolean
  isNoBall?: boolean
}

export const BALLS_PER_OVER = 6

export function isExtra(ball: BallResult): boolean {
  return ball === 'wd' || ball === 'nb'
}

export function countLegalBalls(balls: BallResult[]): number {
  return balls.filter((b) => !isExtra(b)).length
}

export function calculateStrikeRate(runs: number, balls: number): number {
  if (balls === 0) return 0
  return (runs / balls) * 100
}

export function createEmptySession(now: number = Date.now()): Session {
  return {
    id: now.toString(),
    date: new Date(now).toISOString(),
    runs: 0,
    balls: 0,
    fours: 0,
    sixes: 0,
    wickets: 0,
    isOut: false,
    overs: [{ balls: [], runs: 0 }],
    strikeRate: 0,
  }
}

/** Sessions saved before `wickets` existed load with it undefined. */
export function migrateSession(session: Session): Session {
  return { ...session, wickets: session.wickets ?? 0 }
}

/** Deep copy - undo keeps snapshots, and `overs[].balls` are arrays. */
export function cloneSession(session: Session): Session {
  return { ...session, overs: session.overs.map((o) => ({ ...o, balls: [...o.balls] })) }
}

/** The symbol that goes on the over tracker for this delivery. */
export function classifyDelivery(input: DeliveryInput): BallResult {
  if (input.isNoBall) return 'nb'
  if (input.isWide) return 'wd'
  if (input.isWicket) return 'W'
  if (input.runs === 0) return 'dot'
  if (input.runs === 6) return '6'
  if (input.runs === 4 && input.isBoundary) return '4'
  return String(input.runs) as BallResult
}

/** Runs credited to the batter for this delivery (extras score one). */
export function runsScored(input: DeliveryInput): number {
  if (input.isWide || input.isNoBall) return 1
  if (input.isWicket) return 0
  return input.runs
}

/**
 * Score one delivery. Returns a NEW session (the input is not mutated) and
 * the ball symbol that was recorded. Rolls the over when the sixth legal
 * ball is bowled.
 *
 * The tallies use the INPUT FLAGS, exactly as the original inline code in
 * App.tsx did, not the single symbol on the tracker: a wicket flag always
 * counts a wicket even when the symbol shows 'wd' (a stumping off a wide is
 * a real dismissal), and a boundary flag always counts a four. The UI never
 * combines a wicket or an extra with runs, so the only reachable inputs are
 * the plain ones - but the rule is pinned so that it stays true.
 */
export function applyDelivery(session: Session, input: DeliveryInput): { session: Session; ballResult: BallResult } {
  const ballResult = classifyDelivery(input)
  const extra = Boolean(input.isWide || input.isNoBall)
  const isWicket = Boolean(input.isWicket)
  const scored = runsScored(input)

  const overs = session.overs.map((o) => ({ ...o, balls: [...o.balls] }))
  const current = overs[overs.length - 1]
  current.balls.push(ballResult)
  current.runs += scored
  if (countLegalBalls(current.balls) === BALLS_PER_OVER) {
    overs.push({ balls: [], runs: 0 })
  }

  const runs = session.runs + scored
  const balls = extra ? session.balls : session.balls + 1
  const next: Session = {
    ...session,
    runs,
    balls,
    fours: input.runs === 4 && input.isBoundary ? session.fours + 1 : session.fours,
    sixes: input.runs === 6 ? session.sixes + 1 : session.sixes,
    wickets: isWicket ? session.wickets + 1 : session.wickets,
    isOut: session.isOut || isWicket,
    overs,
    strikeRate: calculateStrikeRate(runs, balls),
  }
  return { session: next, ballResult }
}

/** The most recent ball symbol in a session, or null if none has been bowled. */
export function lastBallOf(session: Session): BallResult | null {
  for (let i = session.overs.length - 1; i >= 0; i--) {
    const balls = session.overs[i].balls
    if (balls.length > 0) return balls[balls.length - 1]
  }
  return null
}

/** "12.3" style: completed overs and legal balls into the current one. */
export function formatOvers(overs: Over[]): string {
  const completed = overs.filter((o) => countLegalBalls(o.balls) === BALLS_PER_OVER).length
  const inProgress = countLegalBalls(overs[overs.length - 1]?.balls ?? [])
  return `${completed}.${inProgress}`
}

export function overSymbol(ball: BallResult): string {
  return ball === 'dot' ? '•' : ball
}

export function lastBallLabel(ball: BallResult | null): string {
  if (ball === null) return '—'
  if (ball === 'dot') return '•'
  if (ball === 'W') return 'OUT!'
  if (ball === 'wd') return 'WIDE'
  if (ball === 'nb') return 'NO BALL'
  return ball
}

/** CSS modifier for a ball symbol - shared by the over tracker and last-ball panel. */
export function ballClass(ball: BallResult | null): string {
  switch (ball) {
    case '4': return 'four'
    case '6': return 'six'
    case 'W': return 'wicket'
    case 'wd': return 'wide'
    case 'nb': return 'noball'
    case 'dot': return 'dot'
    case null: return ''
    default: return 'runs'
  }
}
