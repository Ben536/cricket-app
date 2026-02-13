import { useState, useRef, useEffect } from 'react'
import './App.css'

// Types
interface Session {
  id: string
  date: string
  runs: number
  balls: number
  fours: number
  sixes: number
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

interface Fielder {
  id: string
  name: string
  x: number
  y: number
  isKeeper?: boolean
}

type Difficulty = 'easy' | 'medium' | 'hard'
type BallResult = 'dot' | '1' | '2' | '3' | '4' | '6' | 'W' | 'wd'
type LastBallResult = null | BallResult

interface Over {
  balls: BallResult[]
  runs: number
}

// Field presets
const FIELD_PRESETS: Record<string, Fielder[]> = {
  'Standard Pace': [
    { id: '1', name: 'WK', x: 50, y: 72, isKeeper: true },
    { id: '2', name: '1st', x: 62, y: 68 },
    { id: '3', name: '2nd', x: 68, y: 62 },
    { id: '4', name: 'Gly', x: 72, y: 55 },
    { id: '5', name: 'Pt', x: 70, y: 40 },
    { id: '6', name: 'Cov', x: 75, y: 30 },
    { id: '7', name: 'MO', x: 55, y: 20 },
    { id: '8', name: 'MW', x: 30, y: 30 },
    { id: '9', name: 'SL', x: 28, y: 50 },
    { id: '10', name: 'FL', x: 35, y: 80 },
  ],
  'Spin Attack': [
    { id: '1', name: 'WK', x: 50, y: 72, isKeeper: true },
    { id: '2', name: 'Slp', x: 60, y: 68 },
    { id: '3', name: 'SL', x: 40, y: 55 },
    { id: '4', name: 'SP', x: 58, y: 48 },
    { id: '5', name: 'BP', x: 42, y: 48 },
    { id: '6', name: 'Cov', x: 80, y: 35 },
    { id: '7', name: 'MO', x: 55, y: 15 },
    { id: '8', name: 'MW', x: 25, y: 35 },
    { id: '9', name: 'DM', x: 20, y: 60 },
    { id: '10', name: 'LO', x: 30, y: 15 },
  ],
  'T20 Death': [
    { id: '1', name: 'WK', x: 50, y: 72, isKeeper: true },
    { id: '2', name: 'LO', x: 30, y: 12 },
    { id: '3', name: 'LOff', x: 70, y: 12 },
    { id: '4', name: 'DS', x: 15, y: 50 },
    { id: '5', name: '3M', x: 60, y: 25 },
    { id: '6', name: 'DF', x: 85, y: 50 },
    { id: '7', name: 'FL', x: 25, y: 85 },
    { id: '8', name: 'TL', x: 15, y: 70 },
    { id: '9', name: 'Cov', x: 75, y: 35 },
    { id: '10', name: 'MW', x: 35, y: 35 },
  ],
  'Defensive': [
    { id: '1', name: 'WK', x: 50, y: 72, isKeeper: true },
    { id: '2', name: 'LO', x: 25, y: 15 },
    { id: '3', name: 'LOff', x: 75, y: 15 },
    { id: '4', name: 'DS', x: 12, y: 50 },
    { id: '5', name: 'DF', x: 88, y: 50 },
    { id: '6', name: 'FL', x: 30, y: 85 },
    { id: '7', name: 'TL', x: 15, y: 75 },
    { id: '8', name: 'DP', x: 80, y: 25 },
    { id: '9', name: 'DC', x: 85, y: 40 },
    { id: '10', name: 'DM', x: 20, y: 35 },
  ],
}

const createEmptySession = (): Session => ({
  id: Date.now().toString(),
  date: new Date().toISOString(),
  runs: 0,
  balls: 0,
  fours: 0,
  sixes: 0,
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

const loadProfiles = (): Profile[] => {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
      return JSON.parse(saved)
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
  const [fielders, setFielders] = useState<Fielder[]>(FIELD_PRESETS['Standard Pace'])
  const [showFieldEditor, setShowFieldEditor] = useState(false)
  const [showSessionHistory, setShowSessionHistory] = useState(false)
  const [historyProfileId, setHistoryProfileId] = useState<string | null>(null)
  const [difficulty, setDifficulty] = useState<Difficulty>('medium')
  const [lastBall, setLastBall] = useState<LastBallResult>(null)
  const [isFlashing, setIsFlashing] = useState(false)
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState('')

  const activeProfile = profiles.find(p => p.id === activeProfileId) || profiles[0]
  const currentSession = activeProfile.currentSession
  const currentOver = currentSession.overs[currentSession.overs.length - 1]
  const currentOverNumber = currentSession.overs.length
  const legalBallsInOver = currentOver.balls.filter(b => b !== 'wd').length

  // Save to localStorage when profiles change
  useEffect(() => {
    saveProfiles(profiles)
  }, [profiles])

  const calculateStrikeRate = (runs: number, balls: number): number => {
    if (balls === 0) return 0
    return (runs / balls) * 100
  }

  const updateCurrentSession = (updater: (session: Session) => Session) => {
    setProfiles(prev => prev.map(profile => {
      if (profile.id !== activeProfileId) return profile
      const newSession = updater(profile.currentSession)
      newSession.strikeRate = calculateStrikeRate(newSession.runs, newSession.balls)
      return { ...profile, currentSession: newSession }
    }))
  }

  const addRuns = (runs: number, isBoundary: boolean = false, isWicket: boolean = false, isWide: boolean = false) => {
    let ballResult: BallResult
    if (isWide) {
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

    updateCurrentSession(session => {
      const newOvers = [...session.overs]
      const currentOverIndex = newOvers.length - 1
      const currentOver = { ...newOvers[currentOverIndex] }

      currentOver.balls = [...currentOver.balls, ballResult]
      currentOver.runs += (isWide ? 1 : (isWicket ? 0 : runs))
      newOvers[currentOverIndex] = currentOver

      // Count legal deliveries (not wides) to determine end of over
      const legalBalls = currentOver.balls.filter(b => b !== 'wd').length
      if (legalBalls === 6) {
        newOvers.push({ balls: [], runs: 0 })
      }

      return {
        ...session,
        runs: session.runs + (isWide ? 1 : (isWicket ? 0 : runs)),
        balls: isWide ? session.balls : session.balls + 1, // Wides don't count as balls faced
        fours: runs === 4 && isBoundary ? session.fours + 1 : session.fours,
        sixes: runs === 6 ? session.sixes + 1 : session.sixes,
        isOut: isWicket ? true : session.isOut,
        overs: newOvers,
      }
    })

    setLastBall(ballResult)
    setIsFlashing(true)
    setTimeout(() => setIsFlashing(false), 500)
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
    const completedOvers = overs.filter(o => o.balls.length === 6).length
    const ballsInCurrentOver = overs[overs.length - 1]?.balls.length || 0
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
                {currentSession.runs}
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
                    ball === 'dot' ? 'dot' : 'runs'
                  }`}
                >
                  {ball === 'dot' ? '•' : ball === 'W' ? 'W' : ball === 'wd' ? 'wd' : ball}
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
                      {over.balls.map(b => b === 'dot' ? '•' : b === 'W' ? 'W' : b === 'wd' ? 'wd' : b).join(' ')}
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
              lastBall === 'dot' ? 'dot' : ''
            }`}>
              {lastBall === null ? '—' :
               lastBall === 'dot' ? '•' :
               lastBall === 'W' ? 'OUT!' :
               lastBall === 'wd' ? 'WIDE' :
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
              <button className="score-btn wide" onClick={() => addRuns(0, false, false, true)}>Wd</button>
              <button className="score-btn wicket" onClick={() => addRuns(0, false, true)}>OUT</button>
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
              <FieldView fielders={fielders} setFielders={setFielders} />
              <div className="field-presets">
                <h3>Presets</h3>
                <div className="preset-buttons">
                  {Object.keys(FIELD_PRESETS).map(preset => (
                    <button
                      key={preset}
                      className="preset-btn"
                      onClick={() => setFielders(FIELD_PRESETS[preset])}
                    >
                      {preset}
                    </button>
                  ))}
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
                          <span className="session-runs">{session.runs}</span>
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

// Field View Component
function FieldView({ fielders, setFielders }: {
  fielders: Fielder[]
  setFielders: React.Dispatch<React.SetStateAction<Fielder[]>>
}) {
  const fieldRef = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState<string | null>(null)

  const handleMouseDown = (e: React.MouseEvent, fielderId: string) => {
    e.preventDefault()
    setDragging(fielderId)
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!dragging || !fieldRef.current) return

    const rect = fieldRef.current.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * 100
    const y = ((e.clientY - rect.top) / rect.height) * 100

    const clampedX = Math.max(5, Math.min(95, x))
    const clampedY = Math.max(5, Math.min(95, y))

    setFielders(prev => prev.map(f =>
      f.id === dragging ? { ...f, x: clampedX, y: clampedY } : f
    ))
  }

  const handleMouseUp = () => {
    setDragging(null)
  }

  useEffect(() => {
    if (dragging) {
      const handleGlobalMouseUp = () => setDragging(null)
      window.addEventListener('mouseup', handleGlobalMouseUp)
      return () => window.removeEventListener('mouseup', handleGlobalMouseUp)
    }
  }, [dragging])

  return (
    <div
      ref={fieldRef}
      className="cricket-field"
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      <div className="pitch" />
      <div
        className="fielder batsman"
        style={{ left: '50%', top: '62%' }}
      >
        BAT
      </div>
      {fielders.map(fielder => (
        <div
          key={fielder.id}
          className={`fielder ${fielder.isKeeper ? 'keeper' : ''}`}
          style={{ left: `${fielder.x}%`, top: `${fielder.y}%` }}
          onMouseDown={(e) => handleMouseDown(e, fielder.id)}
        >
          {fielder.name}
        </div>
      ))}
    </div>
  )
}

export default App
