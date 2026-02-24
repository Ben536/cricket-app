import { useState, useRef, useEffect, useMemo } from 'react'
import './App.css'
import { calculateFielderZones, FIELD_PRESET_POSITIONS, SCREEN_GEOMETRY, constrainToField, fieldToScreen, screenToField, type FielderWithZone } from './fieldZones'
import { simulateDelivery, calculateTrajectory, type SimulationResult } from './gameEngine'

// Types
interface ShotLine {
  id: string
  endX: number      // Screen % (0-100)
  endY: number      // Screen % (0-100)
  outcome: BallResult
  distance: number  // metres from batter
}

interface Session {
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

interface Profile {
  id: string
  name: string
  sessions: Session[]
  currentSession: Session
}

// Simple fielder position (zone calculated dynamically)
interface FielderPosition {
  id: string
  x: number
  y: number
}

type BattingHand = 'right' | 'left'
type Difficulty = 'easy' | 'medium' | 'hard'
type BallResult = 'dot' | '1' | '2' | '3' | '4' | '6' | 'W' | 'wd' | 'nb'
type LastBallResult = null | BallResult

interface Over {
  balls: BallResult[]
  runs: number
}

const createEmptySession = (): Session => ({
  id: Date.now().toString(),
  date: new Date().toISOString(),
  runs: 0,
  balls: 0,
  fours: 0,
  sixes: 0,
  wickets: 0,
  isOut: false,
  overs: [{ balls: [], runs: 0 }],
  strikeRate: 0,
})

const createDefaultProfiles = (): Profile[] => [
  {
    id: '1',
    name: 'Player 1',
    sessions: [],
    currentSession: createEmptySession(),
  },
  {
    id: '2',
    name: 'Player 2',
    sessions: [],
    currentSession: createEmptySession(),
  },
]

// LocalStorage helpers
const STORAGE_KEY = 'cricket-app-profiles'
const CUSTOM_FIELDS_KEY = 'cricket-app-custom-fields'

type CustomFieldPreset = {
  name: string
  positions: Array<{ id: string; x: number; y: number }>
}

const loadCustomFields = (): CustomFieldPreset[] => {
  try {
    const saved = localStorage.getItem(CUSTOM_FIELDS_KEY)
    if (saved) {
      return JSON.parse(saved)
    }
  } catch (e) {
    console.error('Failed to load custom fields:', e)
  }
  return []
}

const saveCustomFields = (fields: CustomFieldPreset[]) => {
  try {
    localStorage.setItem(CUSTOM_FIELDS_KEY, JSON.stringify(fields))
  } catch (e) {
    console.error('Failed to save custom fields:', e)
  }
}

const migrateSession = (session: Session): Session => ({
  ...session,
  wickets: session.wickets ?? 0,
})

const loadProfiles = (): Profile[] => {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
      const profiles: Profile[] = JSON.parse(saved)
      // Migrate old sessions to include wickets field
      return profiles.map(profile => ({
        ...profile,
        currentSession: migrateSession(profile.currentSession),
        sessions: profile.sessions.map(migrateSession),
      }))
    }
  } catch (e) {
    console.error('Failed to load profiles:', e)
  }
  return createDefaultProfiles()
}

const saveProfiles = (profiles: Profile[]) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(profiles))
  } catch (e) {
    console.error('Failed to save profiles:', e)
  }
}

function App() {
  const [profiles, setProfiles] = useState<Profile[]>(loadProfiles)
  const [activeProfileId, setActiveProfileId] = useState<string>('1')
  const [fielderPositions, setFielderPositions] = useState<FielderPosition[]>(FIELD_PRESET_POSITIONS['Standard Pace'])
  const [batterHand, setBatterHand] = useState<BattingHand>('right')
  const [showFieldEditor, setShowFieldEditor] = useState(false)
  const [showSessionHistory, setShowSessionHistory] = useState(false)
  const [historyProfileId, setHistoryProfileId] = useState<string | null>(null)
  const [difficulty, setDifficulty] = useState<Difficulty>('medium')
  const [lastBall, setLastBall] = useState<LastBallResult>(null)
  const [isFlashing, setIsFlashing] = useState(false)
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState('')
  const [sessionHistory, setSessionHistory] = useState<Session[]>([])
  const [customFields, setCustomFields] = useState<CustomFieldPreset[]>(loadCustomFields)
  const [isSavingField, setIsSavingField] = useState(false)
  const [newFieldName, setNewFieldName] = useState('')
  const [isEditingCustomFields, setIsEditingCustomFields] = useState(false)
  const [wagonWheelShots, setWagonWheelShots] = useState<ShotLine[]>([])

  // Shot simulator state
  const [simAngle, setSimAngle] = useState('30')
  const [simElevation, setSimElevation] = useState('10')
  const [simSpeed, setSimSpeed] = useState('80')
  const [simResult, setSimResult] = useState<SimulationResult | null>(null)
  const [simError, setSimError] = useState<string | null>(null)

  // Track fielder catch position (fielder ID -> screen position where they caught it)
  const [catchDisplayPosition, setCatchDisplayPosition] = useState<{
    fielderId: string
    screenX: number
    screenY: number
  } | null>(null)

  // Track fielder ground fielding position (fielder ID -> screen position where they fielded it)
  const [fieldingDisplayPosition, setFieldingDisplayPosition] = useState<{
    fielderId: string
    screenX: number
    screenY: number
  } | null>(null)

  const activeProfile = profiles.find(p => p.id === activeProfileId) || profiles[0]
  const currentSession = activeProfile.currentSession
  const currentOver = currentSession.overs[currentSession.overs.length - 1]
  const currentOverNumber = currentSession.overs.length
  const legalBallsInOver = currentOver.balls.filter(b => b !== 'wd' && b !== 'nb').length

  // Save to localStorage when profiles change
  useEffect(() => {
    saveProfiles(profiles)
  }, [profiles])

  // Save to localStorage when custom fields change
  useEffect(() => {
    saveCustomFields(customFields)
  }, [customFields])

  const handleSaveNewCustomField = () => {
    if (newFieldName.trim()) {
      const newField: CustomFieldPreset = {
        name: newFieldName.trim(),
        positions: fielderPositions.map(f => ({ id: f.id, x: f.x, y: f.y }))
      }
      setCustomFields(prev => [...prev, newField])
      setNewFieldName('')
      setIsSavingField(false)
    }
  }

  const handleSaveOverCustomField = (name: string) => {
    setCustomFields(prev => prev.map(f =>
      f.name === name
        ? { ...f, positions: fielderPositions.map(p => ({ id: p.id, x: p.x, y: p.y })) }
        : f
    ))
    setIsSavingField(false)
  }

  const handleDeleteCustomField = (name: string) => {
    setCustomFields(prev => prev.filter(f => f.name !== name))
  }

  const calculateStrikeRate = (runs: number, balls: number): number => {
    if (balls === 0) return 0
    return (runs / balls) * 100
  }

  // Generate a random shot for wagon wheel based on outcome
  const generateShotLine = (outcome: BallResult): ShotLine | null => {
    // Don't generate shots for extras
    if (outcome === 'wd' || outcome === 'nb') return null

    // Distance ranges based on outcome (in metres)
    let minDist: number, maxDist: number
    switch (outcome) {
      case 'dot': minDist = 5; maxDist = 25; break
      case '1': minDist = 10; maxDist = 35; break
      case '2': minDist = 25; maxDist = 50; break
      case '3': minDist = 35; maxDist = 55; break
      case '4': minDist = 55; maxDist = 68; break  // Boundary
      case '6': minDist = 70; maxDist = 85; break  // Over boundary
      case 'W': minDist = 5; maxDist = 40; break   // Caught anywhere
      default: return null
    }

    // Random angle (0-360 degrees, 0 = toward bowler)
    const angle = Math.random() * 360
    const angleRad = (angle * Math.PI) / 180

    // Random distance within range
    const distance = minDist + Math.random() * (maxDist - minDist)

    // Convert to field coordinates (x, y in metres from batter)
    // y positive = toward bowler, x positive = leg side
    const fieldX = distance * Math.sin(angleRad)
    const fieldY = distance * Math.cos(angleRad)

    // Convert to screen coordinates
    const screen = fieldToScreen(fieldX, fieldY)

    return {
      id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
      endX: screen.x,
      endY: screen.y,
      outcome,
      distance
    }
  }

  // Simulate a shot using the TypeScript game engine
  const simulateShot = () => {
    setSimError(null)
    // Reset any previous catch/fielding display positions
    setCatchDisplayPosition(null)
    setFieldingDisplayPosition(null)

    try {
      const speed = parseFloat(simSpeed)
      const angle = parseFloat(simAngle)
      const elevation = parseFloat(simElevation)

      // Calculate trajectory
      const trajectory = calculateTrajectory(speed, angle, elevation)

      // Convert current fielder positions to game engine format (screen % -> metres)
      // Also track fielder ID -> zone name mapping for catch display
      const fielderIdToZone: Record<string, string> = {}
      const fieldConfig = fielderPositions.map(f => {
        const field = screenToField(f.x, f.y)
        // Find zone name for this fielder
        const zones = calculateFielderZones([f], batterHand === 'left')
        const zoneName = zones[0]?.zoneName || 'fielder'
        fielderIdToZone[f.id] = zoneName
        return {
          x: field.x,
          y: field.y,
          name: zoneName,
        }
      })

      // Run simulation
      // Use final_x/final_y (where ball stops after rolling) instead of landing point
      // This ensures the ball path direction matches the total distance
      const result = simulateDelivery(
        speed,
        angle,
        elevation,
        trajectory.final_x,
        trajectory.final_y,
        trajectory.projected_distance,
        trajectory.max_height,
        fieldConfig,
        70.0,  // Match visual field radius
        difficulty
      )

      // Combine result with trajectory
      const fullResult: SimulationResult = {
        ...result,
        trajectory,
      }
      setSimResult(fullResult)

      // Debug: log the result to verify fielding is working
      console.log('Shot result:', result.outcome, 'fielder:', result.fielder_involved, 'fielding_pos:', result.fielding_position)

      // Add to wagon wheel - use end_position (where ball ended up)
      const endPos = result.end_position
      const screen = fieldToScreen(endPos.x, endPos.y)
      const shotLine: ShotLine = {
        id: Date.now().toString(),
        endX: screen.x,
        endY: screen.y,
        outcome: result.outcome === 'caught' || result.outcome === 'dropped' ? 'W' :
                 result.outcome === 'misfield' ? String(result.runs) as BallResult :
                 result.outcome as BallResult,
        distance: Math.sqrt(endPos.x * endPos.x + endPos.y * endPos.y),
      }
      setWagonWheelShots(prev => [...prev, shotLine])

      // If caught, show fielder at catch position
      if (result.outcome === 'caught' && result.fielder_involved) {
        // Find the fielder ID that matches the zone name
        const catchingFielderId = Object.entries(fielderIdToZone)
          .find(([, zoneName]) => zoneName === result.fielder_involved)?.[0]

        if (catchingFielderId) {
          setCatchDisplayPosition({
            fielderId: catchingFielderId,
            screenX: screen.x,
            screenY: screen.y,
          })
        }
      }

      // If ground fielding occurred, show fielder at fielding position
      if (result.fielding_position && result.fielder_involved && result.outcome !== 'caught') {
        const fieldingFielderId = Object.entries(fielderIdToZone)
          .find(([, zoneName]) => zoneName === result.fielder_involved)?.[0]

        console.log('Fielding animation: fielder=', result.fielder_involved, 'id=', fieldingFielderId, 'pos=', result.fielding_position)

        if (fieldingFielderId) {
          const fieldingScreen = fieldToScreen(result.fielding_position.x, result.fielding_position.y)
          console.log('Setting fieldingDisplayPosition:', fieldingFielderId, fieldingScreen)
          setFieldingDisplayPosition({
            fielderId: fieldingFielderId,
            screenX: fieldingScreen.x,
            screenY: fieldingScreen.y,
          })
        }
      } else {
        console.log('No fielding animation: hasPos=', !!result.fielding_position, 'fielder=', result.fielder_involved, 'outcome=', result.outcome)
      }

      // Update score if not caught (skipWagonWheel=true since we already added it above)
      if (result.outcome !== 'caught') {
        addRuns(result.runs, result.is_boundary, false, false, false, true)
      } else {
        addRuns(0, false, true, false, false, true)  // Wicket
      }

    } catch (err) {
      setSimError('Simulation error: ' + (err as Error).message)
    }
  }

  const updateCurrentSession = (updater: (session: Session) => Session) => {
    setProfiles(prev => prev.map(profile => {
      if (profile.id !== activeProfileId) return profile
      const newSession = updater(profile.currentSession)
      newSession.strikeRate = calculateStrikeRate(newSession.runs, newSession.balls)
      return { ...profile, currentSession: newSession }
    }))
  }

  const addRuns = (runs: number, isBoundary: boolean = false, isWicket: boolean = false, isWide: boolean = false, isNoBall: boolean = false, skipWagonWheel: boolean = false) => {
    // Save current state for undo (push to history stack)
    setSessionHistory(prev => [...prev, { ...currentSession, overs: currentSession.overs.map(o => ({ ...o, balls: [...o.balls] })) }])

    let ballResult: BallResult
    if (isNoBall) {
      ballResult = 'nb'
    } else if (isWide) {
      ballResult = 'wd'
    } else if (isWicket) {
      ballResult = 'W'
    } else if (runs === 0) {
      ballResult = 'dot'
    } else if (runs === 6) {
      ballResult = '6'
    } else if (runs === 4 && isBoundary) {
      ballResult = '4'
    } else {
      ballResult = runs.toString() as BallResult
    }

    const isExtra = isWide || isNoBall

    updateCurrentSession(session => {
      const newOvers = [...session.overs]
      const currentOverIndex = newOvers.length - 1
      const currentOver = { ...newOvers[currentOverIndex] }

      currentOver.balls = [...currentOver.balls, ballResult]
      currentOver.runs += (isExtra ? 1 : (isWicket ? 0 : runs))
      newOvers[currentOverIndex] = currentOver

      // Count legal deliveries (not wides or no balls) to determine end of over
      const legalBalls = currentOver.balls.filter(b => b !== 'wd' && b !== 'nb').length
      if (legalBalls === 6) {
        newOvers.push({ balls: [], runs: 0 })
      }

      return {
        ...session,
        runs: session.runs + (isExtra ? 1 : (isWicket ? 0 : runs)),
        balls: isExtra ? session.balls : session.balls + 1, // Extras don't count as balls faced
        fours: runs === 4 && isBoundary ? session.fours + 1 : session.fours,
        sixes: runs === 6 ? session.sixes + 1 : session.sixes,
        wickets: isWicket ? session.wickets + 1 : session.wickets,
        isOut: isWicket ? true : session.isOut,
        overs: newOvers,
      }
    })

    setLastBall(ballResult)
    setIsFlashing(true)
    setTimeout(() => setIsFlashing(false), 500)

    // Add shot to wagon wheel (for manual input - generates random position)
    // Skip if called from simulateShot which adds its own precise trajectory
    if (!skipWagonWheel) {
      const shotLine = generateShotLine(ballResult)
      if (shotLine) {
        setWagonWheelShots(prev => [...prev, shotLine])
      }
    }
  }

  const undoLastBall = () => {
    if (sessionHistory.length === 0) return

    const previousSession = sessionHistory[sessionHistory.length - 1]

    setProfiles(prev => prev.map(profile => {
      if (profile.id !== activeProfileId) return profile
      return { ...profile, currentSession: previousSession }
    }))

    // Set last ball to the previous ball (if any)
    const prevOvers = previousSession.overs
    const prevOver = prevOvers[prevOvers.length - 1]
    if (prevOver.balls.length > 0) {
      setLastBall(prevOver.balls[prevOver.balls.length - 1])
    } else if (prevOvers.length > 1) {
      const overBefore = prevOvers[prevOvers.length - 2]
      setLastBall(overBefore.balls[overBefore.balls.length - 1])
    } else {
      setLastBall(null)
    }

    // Pop from history stack
    setSessionHistory(prev => prev.slice(0, -1))

    // Remove last wagon wheel shot
    setWagonWheelShots(prev => prev.slice(0, -1))
  }

  const addNewProfile = () => {
    const newId = Date.now().toString()
    const newProfile: Profile = {
      id: newId,
      name: `Player ${profiles.length + 1}`,
      sessions: [],
      currentSession: createEmptySession(),
    }
    setProfiles(prev => [...prev, newProfile])
    setActiveProfileId(newId)
  }

  const startNewSession = () => {
    setProfiles(prev => prev.map(profile => {
      if (profile.id !== activeProfileId) return profile

      // Only save session if there were balls faced
      const sessions = profile.currentSession.balls > 0
        ? [...profile.sessions, profile.currentSession]
        : profile.sessions

      return {
        ...profile,
        sessions,
        currentSession: createEmptySession(),
      }
    }))
    setLastBall(null)
    setSessionHistory([])
    setWagonWheelShots([])
  }

  const openSessionHistory = (profileId: string) => {
    setHistoryProfileId(profileId)
    setShowSessionHistory(true)
  }

  const startEditingName = (profileId: string, currentName: string) => {
    setEditingProfileId(profileId)
    setEditingName(currentName)
  }

  const saveProfileName = () => {
    if (editingProfileId && editingName.trim()) {
      setProfiles(prev => prev.map(profile =>
        profile.id === editingProfileId
          ? { ...profile, name: editingName.trim() }
          : profile
      ))
    }
    setEditingProfileId(null)
    setEditingName('')
  }

  const resumeSession = (sessionId: string) => {
    if (!historyProfileId) return

    setProfiles(prev => prev.map(profile => {
      if (profile.id !== historyProfileId) return profile

      const sessionIndex = profile.sessions.findIndex(s => s.id === sessionId)
      if (sessionIndex === -1) return profile

      const sessionToResume = profile.sessions[sessionIndex]
      const remainingSessions = profile.sessions.filter(s => s.id !== sessionId)

      // Save current session if it has balls, then load the old one
      const updatedSessions = profile.currentSession.balls > 0
        ? [...remainingSessions, profile.currentSession]
        : remainingSessions

      return {
        ...profile,
        sessions: updatedSessions,
        currentSession: { ...sessionToResume, date: new Date().toISOString() },
      }
    }))

    setActiveProfileId(historyProfileId)
    setShowSessionHistory(false)
    setLastBall(null)
    setSessionHistory([])
    setWagonWheelShots([])
  }

  const formatDate = (dateString: string): string => {
    const date = new Date(dateString)
    return date.toLocaleDateString('en-GB', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const formatOvers = (overs: Over[]): string => {
    const countLegalBalls = (balls: BallResult[]) => balls.filter(b => b !== 'wd' && b !== 'nb').length
    const completedOvers = overs.filter(o => countLegalBalls(o.balls) === 6).length
    const ballsInCurrentOver = countLegalBalls(overs[overs.length - 1]?.balls || [])
    return `${completedOvers}.${ballsInCurrentOver}`
  }

  const historyProfile = profiles.find(p => p.id === historyProfileId)

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div className="header-brand">
          <span className="header-logo">🏏</span>
          <h1 className="header-title">VGA Cricket 26</h1>
        </div>
        <div className="header-controls">
          <div className="overs-display">
            <span className="overs-label">Overs</span>
            <span className="overs-value">
              {currentOverNumber - 1}.{legalBallsInOver}
            </span>
          </div>
          <div className="difficulty-selector">
            <label>Difficulty</label>
            <select
              value={difficulty}
              onChange={(e) => setDifficulty(e.target.value as Difficulty)}
            >
              <option value="easy">Easy</option>
              <option value="medium">Medium</option>
              <option value="hard">Hard</option>
            </select>
          </div>
          <button className="new-session-btn" onClick={startNewSession}>
            New Session
          </button>
        </div>
      </header>

      {/* Main Content */}
      <main className="main-content">
        <div className="scoreboard-panel">
          {/* Profile Selector */}
          <div className="batsman-selector">
            <h3>Select Player</h3>
            <div className="batsman-list">
              {profiles.map(profile => (
                <button
                  key={profile.id}
                  className={`batsman-btn ${activeProfileId === profile.id ? 'active' : ''}`}
                  onClick={() => setActiveProfileId(profile.id)}
                >
                  {profile.name}
                </button>
              ))}
              <button className="batsman-btn add-batsman-btn" onClick={addNewProfile}>
                + Add
              </button>
            </div>
          </div>

          {/* Main Scoreboard */}
          <div className="scoreboard">
            <div className="scoreboard-header">
              <div className="batsman-name-section">
                {editingProfileId === activeProfileId ? (
                  <input
                    type="text"
                    className="profile-name-input large"
                    value={editingName}
                    onChange={(e) => setEditingName(e.target.value)}
                    onBlur={saveProfileName}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') saveProfileName()
                      if (e.key === 'Escape') {
                        setEditingProfileId(null)
                        setEditingName('')
                      }
                    }}
                    autoFocus
                  />
                ) : (
                  <span className="batsman-name">{activeProfile.name}</span>
                )}
                <button
                  className="inline-btn edit"
                  onClick={() => startEditingName(activeProfileId, activeProfile.name)}
                  title="Edit name"
                >
                  ✏️
                </button>
                {activeProfile.sessions.length > 0 && (
                  <button
                    className="inline-btn history"
                    onClick={() => openSessionHistory(activeProfileId)}
                    title="View session history"
                  >
                    📊 {activeProfile.sessions.length}
                  </button>
                )}
              </div>
              <span className="batsman-status">
                {currentSession.isOut ? 'OUT' : 'BATTING'}
              </span>
            </div>
            <div className="score-display">
              <div className={`runs ${isFlashing ? 'flash' : ''}`}>
                {currentSession.runs}-{currentSession.wickets}
              </div>
              <div className="score-details">
                <div className="score-stat">
                  <div className="score-stat-value">{currentSession.balls}</div>
                  <div className="score-stat-label">Balls</div>
                </div>
                <div className="score-stat">
                  <div className="score-stat-value">{currentSession.fours}</div>
                  <div className="score-stat-label">Fours</div>
                </div>
                <div className="score-stat">
                  <div className="score-stat-value">{currentSession.sixes}</div>
                  <div className="score-stat-label">Sixes</div>
                </div>
              </div>
            </div>
            <div className="strike-rate">
              <span className="strike-rate-label">Strike Rate</span>
              <span className="strike-rate-value">
                {currentSession.strikeRate.toFixed(2)}
              </span>
            </div>
          </div>

          {/* Over Tracker */}
          <div className="over-tracker">
            <div className="over-tracker-header">
              <span className="over-number">Over {currentOverNumber}</span>
              <span className="over-runs">{currentOver.runs} runs</span>
            </div>
            <div className="over-balls">
              {currentOver.balls.map((ball, idx) => (
                <span
                  key={idx}
                  className={`ball-result ${
                    ball === '4' ? 'four' :
                    ball === '6' ? 'six' :
                    ball === 'W' ? 'wicket' :
                    ball === 'wd' ? 'wide' :
                    ball === 'nb' ? 'noball' :
                    ball === 'dot' ? 'dot' : 'runs'
                  }`}
                >
                  {ball === 'dot' ? '•' : ball === 'W' ? 'W' : ball === 'wd' ? 'wd' : ball === 'nb' ? 'nb' : ball}
                </span>
              ))}
              {Array(Math.max(0, 6 - legalBallsInOver)).fill(null).map((_, idx) => (
                <span key={`empty-${idx}`} className="ball-result empty">-</span>
              ))}
            </div>
            {currentSession.overs.length > 1 && (
              <div className="previous-overs">
                {currentSession.overs.slice(0, -1).slice(-4).map((over, idx) => (
                  <div key={idx} className="prev-over">
                    <span className="prev-over-num">
                      Ov {currentSession.overs.length - (currentSession.overs.slice(0, -1).slice(-4).length - idx)}
                    </span>
                    <span className="prev-over-balls">
                      {over.balls.map(b => b === 'dot' ? '•' : b === 'W' ? 'W' : b === 'wd' ? 'wd' : b === 'nb' ? 'nb' : b).join(' ')}
                    </span>
                    <span className="prev-over-runs">{over.runs}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Last Ball */}
          <div className="last-ball">
            <h3>Last Ball</h3>
            <div className={`last-ball-result ${
              lastBall === '4' ? 'four' :
              lastBall === '6' ? 'six' :
              lastBall === 'W' ? 'wicket' :
              lastBall === 'wd' ? 'wide' :
              lastBall === 'nb' ? 'noball' :
              lastBall === 'dot' ? 'dot' : ''
            }`}>
              {lastBall === null ? '—' :
               lastBall === 'dot' ? '•' :
               lastBall === 'W' ? 'OUT!' :
               lastBall === 'wd' ? 'WIDE' :
               lastBall === 'nb' ? 'NO BALL' :
               lastBall}
            </div>
          </div>

          {/* Manual Score Input */}
          <div className="manual-input">
            <h3>Manual Input</h3>
            <div className="manual-buttons">
              <button className="score-btn runs" onClick={() => addRuns(0)}>•</button>
              <button className="score-btn runs" onClick={() => addRuns(1)}>1</button>
              <button className="score-btn runs" onClick={() => addRuns(2)}>2</button>
              <button className="score-btn runs" onClick={() => addRuns(3)}>3</button>
              <button className="score-btn four" onClick={() => addRuns(4, true)}>4</button>
              <button className="score-btn six" onClick={() => addRuns(6)}>6</button>
            </div>
            <div className="manual-buttons extras-row">
              <button className="score-btn wide" onClick={() => addRuns(0, false, false, true)}>Wide</button>
              <button className="score-btn noball" onClick={() => addRuns(0, false, false, false, true)}>No Ball</button>
              <button className="score-btn wicket" onClick={() => addRuns(0, false, true)}>OUT</button>
              <button
                className="score-btn undo"
                onClick={undoLastBall}
                disabled={sessionHistory.length === 0}
              >
                Undo
              </button>
            </div>
          </div>
        </div>
      </main>

      {/* Field Editor Toggle */}
      <button className="field-toggle" onClick={() => setShowFieldEditor(true)}>
        Field
      </button>

      {/* Field Editor Panel */}
      {showFieldEditor && (
        <>
          <div className="field-editor-overlay" onClick={() => setShowFieldEditor(false)} />
          <div className="field-editor">
            <div className="field-editor-header">
              <h2>Field Settings</h2>
              <button className="close-btn" onClick={() => setShowFieldEditor(false)}>×</button>
            </div>
            <div className="field-editor-content">
              <FieldView
                fielderPositions={fielderPositions}
                setFielderPositions={setFielderPositions}
                batterHand={batterHand}
                wagonWheelShots={wagonWheelShots}
                catchDisplayPosition={catchDisplayPosition}
                fieldingDisplayPosition={fieldingDisplayPosition}
              />
              <div className="field-controls">
                <div className="batter-hand-toggle">
                  <span>Batter:</span>
                  <button
                    className={`hand-btn ${batterHand === 'right' ? 'active' : ''}`}
                    onClick={() => setBatterHand('right')}
                  >
                    Right
                  </button>
                  <button
                    className={`hand-btn ${batterHand === 'left' ? 'active' : ''}`}
                    onClick={() => setBatterHand('left')}
                  >
                    Left
                  </button>
                </div>
                <div className="field-presets">
                  <h3>Presets</h3>
                  <div className="preset-buttons">
                    {Object.keys(FIELD_PRESET_POSITIONS).map(preset => (
                      <button
                        key={preset}
                        className="preset-btn"
                        onClick={() => setFielderPositions(FIELD_PRESET_POSITIONS[preset])}
                      >
                        {preset}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="field-presets">
                  <div className="custom-header">
                    <h3>Custom</h3>
                    {customFields.length > 0 && (
                      <button
                        className="edit-custom-btn"
                        onClick={() => setIsEditingCustomFields(!isEditingCustomFields)}
                      >
                        {isEditingCustomFields ? 'Done' : 'Edit'}
                      </button>
                    )}
                  </div>
                  <div className="preset-buttons">
                    {customFields.map(field => (
                      <div key={field.name} className="custom-field-btn-wrapper">
                        <button
                          className="preset-btn"
                          onClick={() => setFielderPositions(field.positions)}
                        >
                          {field.name}
                        </button>
                        {isEditingCustomFields && (
                          <button
                            className="delete-field-btn"
                            onClick={() => handleDeleteCustomField(field.name)}
                            title="Delete"
                          >
                            ×
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                  {isSavingField ? (
                    <div className="save-field-options">
                      {customFields.length > 0 && (
                        <>
                          <p className="save-option-label">Save over existing:</p>
                          <div className="preset-buttons">
                            {customFields.map(field => (
                              <button
                                key={field.name}
                                className="preset-btn overwrite-btn"
                                onClick={() => handleSaveOverCustomField(field.name)}
                              >
                                {field.name}
                              </button>
                            ))}
                          </div>
                          <p className="save-option-label">Or create new:</p>
                        </>
                      )}
                      <div className="save-field-form">
                        <input
                          type="text"
                          className="field-name-input"
                          placeholder="New field name..."
                          value={newFieldName}
                          onChange={(e) => setNewFieldName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') handleSaveNewCustomField()
                            if (e.key === 'Escape') {
                              setIsSavingField(false)
                              setNewFieldName('')
                            }
                          }}
                          autoFocus
                        />
                        <button className="preset-btn save-btn" onClick={handleSaveNewCustomField}>
                          Save
                        </button>
                      </div>
                      <button
                        className="preset-btn cancel-btn"
                        onClick={() => {
                          setIsSavingField(false)
                          setNewFieldName('')
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      className="preset-btn save-current-btn"
                      onClick={() => setIsSavingField(true)}
                    >
                      Save Current Field
                    </button>
                  )}
                </div>

                {/* Shot Simulator */}
                <div className="shot-simulator">
                  <h3>Shot Simulator</h3>
                  <div className="sim-inputs">
                    <div className="sim-input-group">
                      <label>Angle (°)</label>
                      <input
                        type="number"
                        value={simAngle}
                        onChange={(e) => setSimAngle(e.target.value)}
                        placeholder="0"
                      />
                      <span className="sim-hint">0=straight, +off, -leg</span>
                    </div>
                    <div className="sim-input-group">
                      <label>Elevation (°)</label>
                      <input
                        type="number"
                        value={simElevation}
                        onChange={(e) => setSimElevation(e.target.value)}
                        placeholder="10"
                      />
                      <span className="sim-hint">0=ground, 45=lofted</span>
                    </div>
                    <div className="sim-input-group">
                      <label>Speed (km/h)</label>
                      <input
                        type="number"
                        value={simSpeed}
                        onChange={(e) => setSimSpeed(e.target.value)}
                        placeholder="80"
                      />
                      <span className="sim-hint">25=soft, 110=power</span>
                    </div>
                  </div>
                  <button
                    className="sim-button"
                    onClick={simulateShot}
                  >
                    Simulate Shot
                  </button>
                  {simError && (
                    <div className="sim-error">{simError}</div>
                  )}
                  {simResult && (
                    <div className="sim-result" data-outcome={simResult.outcome}>
                      <div className="sim-outcome">{simResult.outcome.toUpperCase()}</div>
                      <div className="sim-runs">{simResult.runs} run{simResult.runs !== 1 ? 's' : ''}</div>
                      <div className="sim-description">{simResult.description}</div>
                      {simResult.fielder_involved && (
                        <div className="sim-fielder">Fielder: {simResult.fielder_involved}</div>
                      )}
                      {simResult.trajectory && (
                        <div className="sim-distance">
                          Distance: {simResult.trajectory.projected_distance.toFixed(1)}m
                        </div>
                      )}
                    </div>
                  )}
                  <button
                    className="sim-clear-btn"
                    onClick={() => setWagonWheelShots([])}
                  >
                    Clear Wagon Wheel
                  </button>
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {/* Session History Modal */}
      {showSessionHistory && historyProfile && (
        <>
          <div className="field-editor-overlay" onClick={() => setShowSessionHistory(false)} />
          <div className="session-history-modal">
            <div className="field-editor-header">
              <h2>{historyProfile.name} - Session History</h2>
              <button className="close-btn" onClick={() => setShowSessionHistory(false)}>×</button>
            </div>
            <div className="session-history-content">
              {historyProfile.sessions.length === 0 ? (
                <p className="no-sessions">No past sessions yet</p>
              ) : (
                <div className="session-list">
                  {[...historyProfile.sessions].reverse().map((session) => (
                    <div
                      key={session.id}
                      className="session-card clickable"
                      onClick={() => resumeSession(session.id)}
                      title="Click to continue this session"
                    >
                      <div className="session-card-header">
                        <span className="session-date">{formatDate(session.date)}</span>
                        <span className={`session-status ${session.isOut ? 'out' : 'not-out'}`}>
                          {session.isOut ? 'OUT' : 'NOT OUT'}
                        </span>
                      </div>
                      <div className="session-card-stats">
                        <div className="session-stat-main">
                          <span className="session-runs">{session.runs}-{session.wickets ?? 0}</span>
                          <span className="session-balls">({session.balls})</span>
                        </div>
                        <div className="session-stat-details">
                          <span>4s: {session.fours}</span>
                          <span>6s: {session.sixes}</span>
                          <span>SR: {session.strikeRate.toFixed(1)}</span>
                          <span>Overs: {formatOvers(session.overs)}</span>
                        </div>
                      </div>
                      <div className="session-resume-hint">
                        Click to continue →
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

// Field View Component with dynamic zone labels and wagon wheel
function FieldView({
  fielderPositions,
  setFielderPositions,
  batterHand,
  wagonWheelShots = [],
  catchDisplayPosition,
  fieldingDisplayPosition,
}: {
  fielderPositions: FielderPosition[]
  setFielderPositions: React.Dispatch<React.SetStateAction<FielderPosition[]>>
  batterHand: BattingHand
  wagonWheelShots?: ShotLine[]
  catchDisplayPosition: { fielderId: string; screenX: number; screenY: number } | null
  fieldingDisplayPosition: { fielderId: string; screenX: number; screenY: number } | null
}) {
  const fieldRef = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState<string | null>(null)

  // Calculate zones for all fielders (recalculated when positions or batter hand changes)
  // For left-handed: mirror positions AND mirror zone seeds (via isLeftHanded=true)
  const fieldersWithZones: FielderWithZone[] = useMemo(() => {
    const isLeftHanded = batterHand === 'left'
    const positionsToUse = isLeftHanded
      ? fielderPositions.map(f => ({ ...f, x: 100 - f.x }))
      : fielderPositions
    return calculateFielderZones(positionsToUse, isLeftHanded)
  }, [fielderPositions, batterHand])

  // Shared position update logic for mouse and touch
  const updateFielderPosition = (clientX: number, clientY: number) => {
    if (!dragging || !fieldRef.current) return

    const rect = fieldRef.current.getBoundingClientRect()
    const screenX = ((clientX - rect.left) / rect.width) * 100
    const y = ((clientY - rect.top) / rect.height) * 100

    // Constrain position to inside the circular field boundary
    const constrained = constrainToField(screenX, y)

    // Convert screen x back to stored x (mirror for left-handed)
    const storedX = batterHand === 'left' ? 100 - constrained.x : constrained.x

    setFielderPositions(prev => prev.map(f =>
      f.id === dragging ? { ...f, x: storedX, y: constrained.y } : f
    ))
  }

  const handleMouseDown = (e: React.MouseEvent, fielderId: string) => {
    e.preventDefault()
    setDragging(fielderId)
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    updateFielderPosition(e.clientX, e.clientY)
  }

  const handleMouseUp = () => {
    setDragging(null)
  }

  // Touch event handlers for mobile
  const handleTouchStart = (e: React.TouchEvent, fielderId: string) => {
    e.preventDefault()
    setDragging(fielderId)
  }

  const handleTouchMove = (e: React.TouchEvent) => {
    if (!dragging) return
    e.preventDefault()
    const touch = e.touches[0]
    updateFielderPosition(touch.clientX, touch.clientY)
  }

  const handleTouchEnd = () => {
    setDragging(null)
  }

  useEffect(() => {
    if (dragging) {
      const handleGlobalMouseUp = () => setDragging(null)
      const handleGlobalTouchEnd = () => setDragging(null)
      window.addEventListener('mouseup', handleGlobalMouseUp)
      window.addEventListener('touchend', handleGlobalTouchEnd)
      return () => {
        window.removeEventListener('mouseup', handleGlobalMouseUp)
        window.removeEventListener('touchend', handleGlobalTouchEnd)
      }
    }
  }, [dragging])

  // Pitch styling based on geometry constants (centered on field)
  const pitchStyle = {
    position: 'absolute' as const,
    left: `${SCREEN_GEOMETRY.pitchCenterX}%`,
    top: `${SCREEN_GEOMETRY.pitchCenterY}%`,
    transform: 'translate(-50%, -50%)',
    width: `${SCREEN_GEOMETRY.pitchWidth}%`,
    height: `${SCREEN_GEOMETRY.pitchLength}%`,
    background: '#c4a574',
    borderRadius: '2px',
  }

  return (
    <div
      ref={fieldRef}
      className="cricket-field"
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
    >
      <div style={pitchStyle} />

      {/* Wagon Wheel - shot lines (rendered below fielders) */}
      {wagonWheelShots.length > 0 && (
        <svg className="wagon-wheel" viewBox="0 0 100 100" preserveAspectRatio="none">
          {wagonWheelShots.map((shot, index) => {
            const isLatest = index === wagonWheelShots.length - 1
            return (
              <line
                key={shot.id}
                x1={SCREEN_GEOMETRY.batterX}
                y1={SCREEN_GEOMETRY.batterY}
                x2={shot.endX}
                y2={shot.endY}
                className={`wagon-wheel-line ${isLatest ? 'latest' : 'old'}`}
                data-outcome={shot.outcome}
              />
            )
          })}
        </svg>
      )}

      <div
        className="fielder batsman"
        style={{ left: `${SCREEN_GEOMETRY.batterX}%`, top: `${SCREEN_GEOMETRY.batterY}%` }}
      >
        BAT
      </div>
      {fieldersWithZones.map(fielder => {
        // Check if this fielder just took a catch - show them at catch position
        const isCatching = catchDisplayPosition?.fielderId === fielder.id
        // Check if this fielder just fielded the ball on the ground
        const isFielding = fieldingDisplayPosition?.fielderId === fielder.id

        let displayX = fielder.x
        let displayY = fielder.y
        if (isCatching) {
          displayX = catchDisplayPosition.screenX
          displayY = catchDisplayPosition.screenY
        } else if (isFielding) {
          displayX = fieldingDisplayPosition.screenX
          displayY = fieldingDisplayPosition.screenY
        }

        return (
          <div
            key={fielder.id}
            className={`fielder ${fielder.isKeeper ? 'keeper' : ''} ${dragging === fielder.id ? 'dragging' : ''} ${isCatching ? 'catching' : ''} ${isFielding ? 'fielding' : ''}`}
            style={{
              left: `${displayX}%`,
              top: `${displayY}%`,
              transition: (isCatching || isFielding) ? 'left 0.3s ease-out, top 0.3s ease-out' : undefined,
            }}
            onMouseDown={(e) => handleMouseDown(e, fielder.id)}
            onTouchStart={(e) => handleTouchStart(e, fielder.id)}
            title={fielder.zoneName}
          >
            {fielder.shortName}
          </div>
        )
      })}
    </div>
  )
}

export default App
