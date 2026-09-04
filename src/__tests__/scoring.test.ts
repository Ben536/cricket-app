/**
 * The scoring law. These rules used to live inline in App.tsx with no test;
 * the 2026-08 P0 (undo pasting one batter's innings onto another) was only
 * ever reproduced by uncommitted jsdom scripts. Now the rules are pure and
 * every one is pinned here.
 */

import { describe, it, expect } from 'vitest'

import {
  applyDelivery,
  ballClass,
  classifyDelivery,
  cloneSession,
  countLegalBalls,
  createEmptySession,
  formatOvers,
  lastBallLabel,
  lastBallOf,
  migrateSession,
  overSymbol,
  runsScored,
  type DeliveryInput,
  type Session,
} from '../scoring'

function play(inputs: DeliveryInput[], start: Session = createEmptySession(0)): Session {
  return inputs.reduce((s, input) => applyDelivery(s, input).session, start)
}

const dot: DeliveryInput = { runs: 0 }
const one: DeliveryInput = { runs: 1 }
const two: DeliveryInput = { runs: 2 }
const four: DeliveryInput = { runs: 4, isBoundary: true }
const six: DeliveryInput = { runs: 6, isBoundary: true }
const wide: DeliveryInput = { runs: 0, isWide: true }
const noBall: DeliveryInput = { runs: 0, isNoBall: true }
const wicket: DeliveryInput = { runs: 0, isWicket: true }

describe('classifyDelivery', () => {
  it('maps inputs to over-tracker symbols', () => {
    expect(classifyDelivery(dot)).toBe('dot')
    expect(classifyDelivery(one)).toBe('1')
    expect(classifyDelivery(four)).toBe('4')
    expect(classifyDelivery(six)).toBe('6')
    expect(classifyDelivery(wicket)).toBe('W')
    expect(classifyDelivery(wide)).toBe('wd')
    expect(classifyDelivery(noBall)).toBe('nb')
  })

  it('a wicket beats runs, a no-ball beats a wide', () => {
    expect(classifyDelivery({ runs: 4, isWicket: true })).toBe('W')
    expect(classifyDelivery({ runs: 0, isWide: true, isNoBall: true })).toBe('nb')
  })

  it('four runs all run is "4" on the tracker but not a boundary', () => {
    expect(classifyDelivery({ runs: 4, isBoundary: false })).toBe('4')
    const s = play([{ runs: 4, isBoundary: false }])
    expect(s.runs).toBe(4)
    expect(s.fours).toBe(0)
  })
})

describe('runsScored', () => {
  it('extras score one, wickets score nothing', () => {
    expect(runsScored(wide)).toBe(1)
    expect(runsScored(noBall)).toBe(1)
    expect(runsScored(wicket)).toBe(0)
    expect(runsScored({ runs: 3 })).toBe(3)
  })
})

describe('applyDelivery', () => {
  it('scores an over the way the scoreboard did: 1 4 • 2 6 = 13 off 5', () => {
    const s = play([one, four, dot, two, six])
    expect(s.runs).toBe(13)
    expect(s.balls).toBe(5)
    expect(s.fours).toBe(1)
    expect(s.sixes).toBe(1)
    expect(s.wickets).toBe(0)
    expect(s.isOut).toBe(false)
    expect(s.strikeRate).toBeCloseTo(260, 5)
    expect(s.overs).toHaveLength(1)
    expect(s.overs[0].balls).toEqual(['1', '4', 'dot', '2', '6'])
    expect(s.overs[0].runs).toBe(13)
  })

  it('does not mutate its input', () => {
    const before = createEmptySession(0)
    const snapshot = JSON.stringify(before)
    applyDelivery(before, four)
    expect(JSON.stringify(before)).toBe(snapshot)
  })

  it('rolls the over on the sixth LEGAL ball, extras do not count', () => {
    const s = play([one, wide, one, noBall, one, one, one])
    expect(s.balls).toBe(5)
    expect(s.overs).toHaveLength(1)
    const rolled = applyDelivery(s, dot).session
    expect(rolled.balls).toBe(6)
    expect(rolled.overs).toHaveLength(2)
    expect(rolled.overs[0].balls).toEqual(['1', 'wd', '1', 'nb', '1', '1', '1', 'dot'])
    expect(rolled.overs[0].runs).toBe(7) // 5 singles + 2 extras
    expect(rolled.overs[1]).toEqual({ balls: [], runs: 0 })
    expect(formatOvers(rolled.overs)).toBe('1.0')
  })

  it('a wide scores a run but not a ball faced, so the strike rate is unchanged', () => {
    const s = play([two, wide])
    expect(s.runs).toBe(3)
    expect(s.balls).toBe(1)
    expect(s.strikeRate).toBeCloseTo(300, 5)
  })

  it('a wicket marks the batter out, counts a ball, scores nothing, and they keep batting', () => {
    const s = play([four, wicket, one])
    expect(s.wickets).toBe(1)
    expect(s.isOut).toBe(true)
    expect(s.runs).toBe(5)
    expect(s.balls).toBe(3)
    expect(s.overs[0].balls).toEqual(['4', 'W', '1'])
  })

  it('strike rate is 0 with no balls faced (no division by zero)', () => {
    expect(play([wide]).strikeRate).toBe(0)
  })

  it('tallies follow the input flags, as the original inline code did (unreachable combos pinned)', () => {
    // A wicket off a wide: the tracker shows 'wd', but it IS a dismissal
    const stumpedOffWide = play([{ runs: 0, isWide: true, isWicket: true }])
    expect(stumpedOffWide.overs[0].balls).toEqual(['wd'])
    expect(stumpedOffWide.wickets).toBe(1)
    expect(stumpedOffWide.isOut).toBe(true)
    expect(stumpedOffWide.runs).toBe(1)
    expect(stumpedOffWide.balls).toBe(0)
    // A boundary flag counts a four regardless of other flags; six likewise
    expect(play([{ runs: 4, isBoundary: true, isWicket: true }]).fours).toBe(1)
    expect(play([{ runs: 6, isNoBall: true }]).sixes).toBe(1)
    expect(play([{ runs: 6, isNoBall: true }]).runs).toBe(1) // extras score one
  })
})

describe('undo via snapshots', () => {
  it('restoring a cloned snapshot after an over rolled returns to 0.5', () => {
    const before = play([one, one, one, one, one])
    const snapshot = cloneSession(before)
    const after = applyDelivery(before, one).session
    expect(formatOvers(after.overs)).toBe('1.0')
    expect(formatOvers(snapshot.overs)).toBe('0.5')
    expect(snapshot).toEqual(before)
  })

  it('cloneSession is a deep copy of the overs', () => {
    const s = play([one])
    const c = cloneSession(s)
    c.overs[0].balls.push('6')
    expect(s.overs[0].balls).toEqual(['1'])
  })
})

describe('lastBallOf', () => {
  it('reads back through an over boundary', () => {
    expect(lastBallOf(createEmptySession(0))).toBeNull()
    expect(lastBallOf(play([one, four]))).toBe('4')
    const rolled = play([one, one, one, one, one, six])
    expect(rolled.overs).toHaveLength(2)
    expect(lastBallOf(rolled)).toBe('6')
  })
})

describe('display helpers', () => {
  it('formatOvers counts completed overs and legal balls', () => {
    expect(formatOvers([{ balls: [], runs: 0 }])).toBe('0.0')
    expect(formatOvers(play([one, wide, one]).overs)).toBe('0.2')
  })

  it('symbols and classes', () => {
    expect(overSymbol('dot')).toBe('•')
    expect(overSymbol('wd')).toBe('wd')
    expect(lastBallLabel(null)).toBe('—')
    expect(lastBallLabel('W')).toBe('OUT!')
    expect(lastBallLabel('nb')).toBe('NO BALL')
    expect(ballClass('4')).toBe('four')
    expect(ballClass('2')).toBe('runs')
    expect(ballClass(null)).toBe('')
  })

  it('countLegalBalls ignores extras', () => {
    expect(countLegalBalls(['1', 'wd', 'nb', 'dot'])).toBe(2)
  })

  it('migrateSession fills in wickets for pre-wickets sessions', () => {
    const old = { ...createEmptySession(0) } as Partial<Session>
    delete old.wickets
    expect(migrateSession(old as Session).wickets).toBe(0)
  })
})
