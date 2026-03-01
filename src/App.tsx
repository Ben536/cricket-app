import { useState, useRef, useEffect, useMemo } from 'react'
import './App.css'
import { calculateFielderZones, FIELD_PRESET_POSITIONS, SCREEN_GEOMETRY, constrainToField, fieldToScreen, type FielderWithZone } from './fieldZones'
import { useGameState } from './api/hooks/useGameState'
import { useGameActions } from './api/hooks/useGameActions'
import { ServerConfig } from './components/ServerConfig'
import type { BallResult, Over, WagonWheelShot, FielderConfig } from './api/types'

// Types for UI-only state
interface ShotLine {
  id: string
  endX: number      // Screen % (0-100)
  endY: number      // Screen % (0-100)
  outcome: BallResult
  distance: number  // metres from batter
}

// Simple fielder position (zone calculated dynamically)
interface FielderPosition {
  id: string
  x: number
  y: number
}

type BattingHand = 'right' | 'left'

type CustomFieldPreset = {
  name: string
  positions: Array<{ id: string; x: number; y: number }>
}

// LocalStorage helpers for custom fields (UI-only)
const CUSTOM_FIELDS_KEY = 'cricket-app-custom-fields'

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

// Convert server WagonWheelShot to UI ShotLine
function serverShotToShotLine(shot: WagonWheelShot): ShotLine {
  return {
    id: shot.id,
    endX: shot.end_x,
    endY: shot.end_y,
    outcome: shot.outcome,
    distance: shot.distance,
  }
}

// Convert FielderConfig to FielderPosition
function fielderConfigToPosition(config: FielderConfig): FielderPosition {
  return {
    id: config.id || config.name,
    x: config.x,
    y: config.y,
  }
}

function App() {
  // ============================================================================
  // SERVER STATE (from WebSocket)
  // ============================================================================
  const { gameState, isLoading, error, connectionStatus, isConnected, client } = useGameState()
  const actions = useGameActions(client)

  // ============================================================================
  // UI-ONLY STATE (animations, modals, drag positions, local preferences)
  // ============================================================================

  // Field editor state
  const [fielderPositions, setFielderPositions] = useState<FielderPosition[]>(FIELD_PRESET_POSITIONS['Standard Pace'])
  const [batterHand, setBatterHand] = useState<BattingHand>('right')
  const [showFieldEditor, setShowFieldEditor] = useState(false)
  const [customFields, setCustomFields] = useState<CustomFieldPreset[]>(loadCustomFields)
  const [isSavingField, setIsSavingField] = useState(false)
  const [newFieldName, setNewFieldName] = useState('')
  const [isEditingCustomFields, setIsEditingCustomFields] = useState(false)

  // Session history modal
  const [showSessionHistory, setShowSessionHistory] = useState(false)
  const [historyProfileId, setHistoryProfileId] = useState<string | null>(null)

  // Server settings modal
  const [showServerConfig, setShowServerConfig] = useState(false)

  // Profile editing
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState('')

  // Animation state
  const [lastBall, setLastBall] = useState<BallResult | null>(null)
  const [isFlashing, setIsFlashing] = useState(false)

  // Local wagon wheel shots for animation (synced from server)
  const [wagonWheelShots, setWagonWheelShots] = useState<ShotLine[]>([])

  // Track fielder catch position (fielder ID -> screen position where they caught it)
  const [catchDisplayPosition, setCatchDisplayPosition] = useState<{
    fielderId: string
    screenX: number
    screenY: number
  } | null>(null)

  // Track fielder ground fielding position
  const [fieldingDisplayPosition, setFieldingDisplayPosition] = useState<{
    fielderId: string
    screenX: number
    screenY: number
  } | null>(null)

  // Timeout refs to reset fielder positions after animations
  const catchResetTimeout = useRef<number | null>(null)
  const fieldingResetTimeout = useRef<number | null>(null)

  // ============================================================================
  // DERIVED STATE
  // ============================================================================

  // Get current session data from server state
  const currentSession = gameState?.session
  const profiles = gameState?.profiles || []
  const activeProfileId = gameState?.activeProfileId
  const activeProfile = profiles.find(p => p.id === activeProfileId)
  const difficulty = gameState?.difficulty || 'medium'

  // Current over calculations
  const currentOver: Over | null = currentSession?.overs?.[currentSession.overs.length - 1] || null
  const currentOverNumber = currentSession?.overs?.length || 1
  const legalBallsInOver = currentOver?.balls.filter(b => b !== 'wd' && b !== 'nb').length || 0

  // ============================================================================
  // EFFECTS
  // ============================================================================

  // Sync wagon wheel shots from server
  useEffect(() => {
    if (gameState?.wagonWheelShots) {
      setWagonWheelShots(gameState.wagonWheelShots.map(serverShotToShotLine))
    }
  }, [gameState?.wagonWheelShots])

  // Sync field config from server
  useEffect(() => {
    if (gameState?.fieldConfig && gameState.fieldConfig.length > 0) {
      setFielderPositions(gameState.fieldConfig.map(fielderConfigToPosition))
    }
  }, [gameState?.fieldConfig])

  // Handle shot results for animations
  useEffect(() => {
    if (gameState?.lastShotResult) {
      const result = gameState.lastShotResult

      // Determine ball result for display
      let ballResult: BallResult
      if (result.outcome === 'caught') {
        ballResult = 'W'
      } else if (result.outcome === 'dropped' || result.outcome === 'misfield') {
        ballResult = result.runs.toString() as BallResult
      } else if (result.runs === 4) {
        ballResult = '4'
      } else if (result.runs === 6) {
        ballResult = '6'
      } else if (result.runs === 0) {
        ballResult = 'dot'
      } else {
        ballResult = result.runs.toString() as BallResult
      }

      setLastBall(ballResult)
      setIsFlashing(true)
      setTimeout(() => setIsFlashing(false), 500)

      // Handle fielder animations
      if (result.outcome === 'caught' && result.fielder_involved && result.end_position) {
        const screen = fieldToScreen(result.end_position.x, result.end_position.y)
        setCatchDisplayPosition({
          fielderId: result.fielder_involved,
          screenX: screen.x,
          screenY: screen.y,
        })
        if (catchResetTimeout.current) clearTimeout(catchResetTimeout.current)
        catchResetTimeout.current = window.setTimeout(() => {
          setCatchDisplayPosition(null)
        }, 1500)
      }

      // Fielding animation - use end_position for non-catches
      if (result.fielder_involved && result.end_position && result.outcome !== 'caught') {
        const fieldingScreen = fieldToScreen(result.end_position.x, result.end_position.y)
        setFieldingDisplayPosition({
          fielderId: result.fielder_involved,
          screenX: fieldingScreen.x,
          screenY: fieldingScreen.y,
        })
        if (fieldingResetTimeout.current) clearTimeout(fieldingResetTimeout.current)
        fieldingResetTimeout.current = window.setTimeout(() => {
          setFieldingDisplayPosition(null)
        }, 1500)
      }
    }
  }, [gameState?.lastShotResult])

  // Save custom fields to localStorage
  useEffect(() => {
    saveCustomFields(customFields)
  }, [customFields])

  // ============================================================================
  // HANDLERS
  // ============================================================================

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

  const handleDifficultyChange = (newDifficulty: 'easy' | 'medium' | 'hard') => {
    actions.setDifficulty(newDifficulty)
  }

  const handleNewSession = () => {
    if (activeProfileId) {
      // End current session first if it exists
      if (currentSession?.id) {
        actions.endSession(currentSession.id)
      }
      // Start new session
      const fieldConfig: FielderConfig[] = fielderPositions.map(f => ({
        id: f.id,
        x: f.x,
        y: f.y,
        name: f.id, // Use ID as name for now
      }))
      actions.startSession(activeProfileId, fieldConfig, difficulty)
      setLastBall(null)
      setWagonWheelShots([])
    }
  }

  const handleSelectProfile = (profileId: string) => {
    actions.selectProfile(profileId)
  }

  const handleAddNewProfile = () => {
    const name = `Player ${profiles.length + 1}`
    actions.createProfile(name, 'right')
  }

  const startEditingName = (profileId: string, currentName: string) => {
    setEditingProfileId(profileId)
    setEditingName(currentName)
  }

  const saveProfileName = () => {
    if (editingProfileId && editingName.trim()) {
      actions.updateProfile(editingProfileId, editingName.trim())
    }
    setEditingProfileId(null)
    setEditingName('')
  }

  const handleManualInput = (result: BallResult, isBoundary: boolean = false) => {
    actions.manualInput(result, isBoundary)
    setLastBall(result)
    setIsFlashing(true)
    setTimeout(() => setIsFlashing(false), 500)
  }

  const handleUndo = () => {
    if (currentSession?.id) {
      actions.undo(currentSession.id)
    }
  }

  const openSessionHistory = (profileId: string) => {
    setHistoryProfileId(profileId)
    setShowSessionHistory(true)
  }

  const handleFieldUpdate = (newPositions: FielderPosition[]) => {
    setFielderPositions(newPositions)
    // Send to server
    const fieldConfig: FielderConfig[] = newPositions.map(f => ({
      id: f.id,
      x: f.x,
      y: f.y,
      name: f.id,
    }))
    actions.setField(fieldConfig, 70)
  }

  // ============================================================================
  // CONNECTION STATUS UI
  // ============================================================================

  if (!isConnected) {
    return (
      <div className="app">
        <div className="connection-overlay">
          <div className="connection-status">
            <div className="connection-icon">
              {connectionStatus.state === 'connecting' || connectionStatus.state === 'reconnecting' ? (
                <div className="spinner" />
              ) : (
                <span className="disconnected-icon">!</span>
              )}
            </div>
            <h2>
              {connectionStatus.state === 'connecting' && 'Connecting to server...'}
              {connectionStatus.state === 'reconnecting' && `Reconnecting... (attempt ${connectionStatus.reconnectAttempts})`}
              {connectionStatus.state === 'disconnected' && 'Disconnected'}
            </h2>
            {connectionStatus.lastError && (
              <p className="connection-error">{connectionStatus.lastError.message}</p>
            )}
            {connectionStatus.state === 'disconnected' && (
              <button className="reconnect-btn" onClick={() => client?.connect()}>
                Reconnect
              </button>
            )}
          </div>
        </div>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="app">
        <div className="connection-overlay">
          <div className="connection-status">
            <div className="spinner" />
            <h2>Loading game state...</h2>
          </div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="app">
        <div className="connection-overlay">
          <div className="connection-status error">
            <span className="error-icon">!</span>
            <h2>Error</h2>
            <p className="connection-error">{error}</p>
          </div>
        </div>
      </div>
    )
  }

  // ============================================================================
  // MAIN RENDER
  // ============================================================================

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
              onChange={(e) => handleDifficultyChange(e.target.value as 'easy' | 'medium' | 'hard')}
            >
              <option value="easy">Easy</option>
              <option value="medium">Medium</option>
              <option value="hard">Hard</option>
            </select>
          </div>
          <button className="new-session-btn" onClick={handleNewSession}>
            New Session
          </button>
          <button
            className="settings-btn"
            onClick={() => setShowServerConfig(true)}
            title="Server Settings"
          >
            <span className={`status-indicator ${isConnected ? 'connected' : 'disconnected'}`} />
            Settings
          </button>
        </div>
      </header>

      {/* Server Config Modal */}
      {showServerConfig && (
        <ServerConfig
          isConnected={isConnected}
          connectionState={connectionStatus.state}
          onClose={() => setShowServerConfig(false)}
        />
      )}

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
                  onClick={() => handleSelectProfile(profile.id)}
                >
                  {profile.name}
                </button>
              ))}
              <button className="batsman-btn add-batsman-btn" onClick={handleAddNewProfile}>
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
                  <span className="batsman-name">{activeProfile?.name || 'No player selected'}</span>
                )}
                {activeProfileId && (
                  <button
                    className="inline-btn edit"
                    onClick={() => startEditingName(activeProfileId, activeProfile?.name || '')}
                    title="Edit name"
                  >
                    ✏️
                  </button>
                )}
                {activeProfileId && (
                  <button
                    className="inline-btn history"
                    onClick={() => openSessionHistory(activeProfileId)}
                    title="View session history"
                  >
                    📊
                  </button>
                )}
              </div>
              <span className="batsman-status">
                {currentSession?.is_out ? 'OUT' : 'BATTING'}
              </span>
            </div>
            <div className="score-display">
              <div className={`runs ${isFlashing ? 'flash' : ''}`}>
                {currentSession?.runs ?? 0}-{currentSession?.wickets ?? 0}
              </div>
              <div className="score-details">
                <div className="score-stat">
                  <div className="score-stat-value">{currentSession?.balls ?? 0}</div>
                  <div className="score-stat-label">Balls</div>
                </div>
                <div className="score-stat">
                  <div className="score-stat-value">{currentSession?.fours ?? 0}</div>
                  <div className="score-stat-label">Fours</div>
                </div>
                <div className="score-stat">
                  <div className="score-stat-value">{currentSession?.sixes ?? 0}</div>
                  <div className="score-stat-label">Sixes</div>
                </div>
              </div>
            </div>
            <div className="strike-rate">
              <span className="strike-rate-label">Strike Rate</span>
              <span className="strike-rate-value">
                {(currentSession?.strike_rate ?? 0).toFixed(2)}
              </span>
            </div>
          </div>

          {/* Over Tracker */}
          <div className="over-tracker">
            <div className="over-tracker-header">
              <span className="over-number">Over {currentOverNumber}</span>
              <span className="over-runs">{currentOver?.runs ?? 0} runs</span>
            </div>
            <div className="over-balls">
              {(currentOver?.balls || []).map((ball, idx) => (
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
            {currentSession && currentSession.overs.length > 1 && (
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
              <button className="score-btn runs" onClick={() => handleManualInput('dot')}>•</button>
              <button className="score-btn runs" onClick={() => handleManualInput('1')}>1</button>
              <button className="score-btn runs" onClick={() => handleManualInput('2')}>2</button>
              <button className="score-btn runs" onClick={() => handleManualInput('3')}>3</button>
              <button className="score-btn four" onClick={() => handleManualInput('4', true)}>4</button>
              <button className="score-btn six" onClick={() => handleManualInput('6')}>6</button>
            </div>
            <div className="manual-buttons extras-row">
              <button className="score-btn wide" onClick={() => handleManualInput('wd')}>Wide</button>
              <button className="score-btn noball" onClick={() => handleManualInput('nb')}>No Ball</button>
              <button className="score-btn wicket" onClick={() => handleManualInput('W')}>OUT</button>
              <button
                className="score-btn undo"
                onClick={handleUndo}
                disabled={!currentSession}
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
                setFielderPositions={handleFieldUpdate}
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
                        onClick={() => handleFieldUpdate(FIELD_PRESET_POSITIONS[preset])}
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
                          onClick={() => handleFieldUpdate(field.positions)}
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
              </div>
            </div>
          </div>
        </>
      )}

      {/* Session History Modal */}
      {showSessionHistory && historyProfileId && (
        <>
          <div className="field-editor-overlay" onClick={() => setShowSessionHistory(false)} />
          <div className="session-history-modal">
            <div className="field-editor-header">
              <h2>{profiles.find(p => p.id === historyProfileId)?.name} - Session History</h2>
              <button className="close-btn" onClick={() => setShowSessionHistory(false)}>×</button>
            </div>
            <div className="session-history-content">
              <p className="no-sessions">Session history is managed by the server</p>
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
  fielderPositions: { id: string; x: number; y: number }[]
  setFielderPositions: (positions: { id: string; x: number; y: number }[]) => void
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

    setFielderPositions(fielderPositions.map(f =>
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
