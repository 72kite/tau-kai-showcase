import React, { useState } from 'react'
import { getApiBase, setApiBase } from '../hooks/useMCPResource'
import {
  clearPairedHost,
  clearPinnedFingerprint,
  discoverTauCore,
  fetchPairingFingerprint,
  getPairedHost,
  pinCertificate,
  setPairedHost,
  startPinningProxy,
} from '../utils/pairing'
import './Panel.css'

/**
 * Lets the Tauri desktop shell (Phase 27.D) point itself at a tau-core host - the one thing the
 * browser kiosk build never needs, since it's always same-origin or build-time configured (see
 * useMCPResource.js's getApiBase()). Reused in two places: App.jsx's first-run gate (onSaved
 * dismisses it) and SystemDrawer, for changing the host later without reinstalling.
 *
 * Two ways in: manual entry (plain HTTP, unchanged since Milestone 1 - the fallback for subnets
 * mDNS can't cross), or Milestone 3's "Discover" flow - mDNS-find tau-core, compare its TLS
 * certificate's fingerprint against what the admin dashboard shows, and only pin it on explicit
 * human confirmation. Pairing never auto-completes.
 */
export default function ConnectionSettings({ onSaved }) {
  const [value, setValue] = useState(getApiBase())
  const [saved, setSaved] = useState(false)
  const [pairedHost, setPairedHostState] = useState(() => getPairedHost())

  const [discovered, setDiscovered] = useState(null) // null = not searched yet
  const [discovering, setDiscovering] = useState(false)
  const [pairing, setPairing] = useState(null) // { service, fingerprint } while confirming
  const [pairError, setPairError] = useState('')

  const save = () => {
    const trimmed = value.trim()
    if (!trimmed) return
    setApiBase(trimmed)
    setSaved(true)
    onSaved?.(trimmed)
  }

  const discover = async () => {
    setDiscovering(true)
    setPairError('')
    try {
      setDiscovered(await discoverTauCore())
    } catch (e) {
      setPairError(e?.message || String(e))
      setDiscovered([])
    } finally {
      setDiscovering(false)
    }
  }

  const startPairing = async (service) => {
    setPairError('')
    try {
      const fingerprint = await fetchPairingFingerprint(service.host, service.port)
      setPairing({ service, fingerprint })
    } catch (e) {
      setPairError(e?.message || String(e))
    }
  }

  const confirmPairing = async () => {
    if (!pairing) return
    const { service, fingerprint } = pairing
    try {
      await pinCertificate(fingerprint)
      setPairedHost(service.host, service.port)
      setPairedHostState({ host: service.host, port: service.port })
      const localPort = await startPinningProxy(service.host, service.port, fingerprint)
      const base = `http://127.0.0.1:${localPort}`
      setApiBase(base)
      setValue(base)
      setSaved(true)
      setPairing(null)
      onSaved?.(base)
    } catch (e) {
      setPairError(e?.message || String(e))
    }
  }

  // "Forget this pairing": drops the pinned fingerprint and the stored host/port, and resets the
  // panel back to its fresh-install state (blank input, no Discover results) so the user lands
  // straight back in the manual/Discover flow rather than seeing a stale "Saved" note next to an
  // address that's no longer trusted.
  const forget = async () => {
    setPairError('')
    try {
      clearPairedHost()
      await clearPinnedFingerprint()
      setApiBase('')
      setPairedHostState(null)
      setValue('')
      setSaved(false)
      setDiscovered(null)
    } catch (e) {
      setPairError(e?.message || String(e))
    }
  }

  return (
    <div className="panel connection-settings-panel">
      <div className="panel-title">TAU-CORE CONNECTION</div>
      <div className="panel-content">
        {pairing ? (
          <>
            <div className="panel-note">
              Certificate fingerprint from {pairing.service.name || pairing.service.host}:
            </div>
            <div className="panel-note" style={{ fontSize: '1.1em', letterSpacing: '0.05em' }}>
              {pairing.fingerprint}
            </div>
            <div className="panel-note">Does this match the fingerprint shown in your Tau admin dashboard?</div>
            <div className="enroll-form">
              <button type="button" className="memory-search-submit" onClick={confirmPairing}>
                MATCHES - PAIR
              </button>
              <button type="button" className="memory-search-submit" onClick={() => setPairing(null)}>
                CANCEL
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="enroll-form">
              <input
                type="text"
                className="memory-search-input"
                placeholder="http://192.168.1.100:8000"
                value={value}
                onChange={(e) => {
                  setValue(e.target.value)
                  setSaved(false)
                }}
                onKeyDown={(e) => e.key === 'Enter' && save()}
              />
              <button type="button" className="memory-search-submit" onClick={save}>
                SAVE
              </button>
            </div>
            {saved && <div className="panel-note">Saved - takes effect on the next request.</div>}

            <div className="enroll-form">
              <button type="button" className="memory-search-submit" onClick={discover} disabled={discovering}>
                {discovering ? 'SEARCHING…' : 'DISCOVER'}
              </button>
              {pairedHost && (
                <button type="button" className="memory-search-submit" onClick={forget}>
                  FORGET PAIRING
                </button>
              )}
            </div>
            {pairedHost && (
              <div className="panel-note">
                Paired with {pairedHost.host}:{pairedHost.port}.
              </div>
            )}
            {pairError && <div className="panel-note">{pairError}</div>}
            {discovered && discovered.length === 0 && !pairError && (
              <div className="panel-note">No tau-core found on this network.</div>
            )}
            {discovered?.map((service) => (
              <div key={service.name} className="stat-line">
                <span className="stat-label">{service.name}</span>
                <button type="button" className="memory-search-submit" onClick={() => startPairing(service)}>
                  PAIR
                </button>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  )
}
