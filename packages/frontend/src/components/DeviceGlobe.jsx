import React, { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { useDevicePresence } from '../hooks/useDevicePresence'
import './DeviceGlobe.css'
import './Panel.css'

const GLOBE_RADIUS = 1.4
const HOME_LAT = Number.parseFloat(import.meta.env.VITE_HOME_LAT ?? '')
const HOME_LON = Number.parseFloat(import.meta.env.VITE_HOME_LON ?? '')
const HAS_HOME_COORDS = Number.isFinite(HOME_LAT) && Number.isFinite(HOME_LON)

// Same helper as Atom.jsx - reads the live theme so this renders in the same two colors as
// everything else, light or dark.
function readThemeColors() {
  const cs = getComputedStyle(document.documentElement)
  const paper = (cs.getPropertyValue('--paper') || '#ffffff').trim() || '#ffffff'
  const ink = (cs.getPropertyValue('--ink') || '#000000').trim() || '#000000'
  return { paper: new THREE.Color(paper), ink: new THREE.Color(ink) }
}

function latLonToVector3(lat, lon, radius) {
  const phi = (90 - lat) * (Math.PI / 180)
  const theta = (lon + 180) * (Math.PI / 180)
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta)
  )
}

// A wireframe meridian/parallel grid rather than a solid textured sphere - the same "line-art,
// no shading" language as the atom's orbits, not a photoreal Earth.
function buildGridLines(radius) {
  const points = []
  const segments = 48
  for (let m = 0; m < 12; m++) {
    const lon = (m / 12) * 360 - 180
    for (let s = 0; s < segments; s++) {
      const lat1 = (s / segments) * 180 - 90
      const lat2 = ((s + 1) / segments) * 180 - 90
      points.push(latLonToVector3(lat1, lon, radius), latLonToVector3(lat2, lon, radius))
    }
  }
  for (let p = 1; p < 6; p++) {
    const lat = (p / 6) * 180 - 90
    for (let s = 0; s < segments; s++) {
      const lon1 = (s / segments) * 360 - 180
      const lon2 = ((s + 1) / segments) * 360 - 180
      points.push(latLonToVector3(lat, lon1, radius), latLonToVector3(lat, lon2, radius))
    }
  }
  const geometry = new THREE.BufferGeometry().setFromPoints(points)
  return geometry
}

function GlobeCanvas({ pinCount }) {
  const containerRef = useRef(null)
  const pinsGroupRef = useRef(null)
  const materialsRef = useRef([])
  const sceneRef = useRef(null)
  const rendererRef = useRef(null)

  useEffect(() => {
    if (!containerRef.current) return
    const theme = readThemeColors()
    const scene = new THREE.Scene()
    scene.background = theme.paper.clone()

    const width = containerRef.current.clientWidth
    const height = containerRef.current.clientHeight
    const camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 100)
    camera.position.z = 4

    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setSize(width, height)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.setClearColor(theme.paper.clone())
    containerRef.current.appendChild(renderer.domElement)

    const materials = []
    const gridMaterial = new THREE.LineBasicMaterial({ color: theme.ink.clone() })
    materials.push(gridMaterial)
    const grid = new THREE.LineSegments(buildGridLines(GLOBE_RADIUS), gridMaterial)
    scene.add(grid)

    // The home pin: a small ring at the configured (or 0,0 placeholder) coordinate. Away devices
    // have no coordinate to plot - see DeviceGlobe's own list below the canvas for those.
    const pinsGroup = new THREE.Group()
    const homeLat = HAS_HOME_COORDS ? HOME_LAT : 0
    const homeLon = HAS_HOME_COORDS ? HOME_LON : 0
    const pinPos = latLonToVector3(homeLat, homeLon, GLOBE_RADIUS)
    const pinMaterial = new THREE.LineBasicMaterial({ color: theme.ink.clone(), linewidth: 2 })
    materials.push(pinMaterial)
    const pinGeometry = new THREE.RingGeometry(0.05, 0.075, 16)
    const pin = new THREE.LineSegments(new THREE.EdgesGeometry(pinGeometry), pinMaterial)
    pin.position.copy(pinPos)
    pin.lookAt(pinPos.clone().multiplyScalar(2))
    pinsGroup.add(pin)
    scene.add(pinsGroup)

    sceneRef.current = scene
    rendererRef.current = renderer
    pinsGroupRef.current = pinsGroup
    materialsRef.current = materials

    const handleResize = () => {
      if (!containerRef.current) return
      const w = containerRef.current.clientWidth
      const h = containerRef.current.clientHeight
      if (w === 0 || h === 0) return
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      renderer.setSize(w, h)
    }
    const resizeObserver = new ResizeObserver(handleResize)
    resizeObserver.observe(containerRef.current)

    let animationId
    const animate = () => {
      animationId = requestAnimationFrame(animate)
      grid.rotation.y += 0.0015
      pinsGroup.rotation.y += 0.0015
      renderer.render(scene, camera)
    }
    animationId = requestAnimationFrame(animate)

    return () => {
      cancelAnimationFrame(animationId)
      resizeObserver.disconnect()
      if (containerRef.current && renderer.domElement.parentNode === containerRef.current) {
        containerRef.current.removeChild(renderer.domElement)
      }
      grid.geometry.dispose()
      pinGeometry.dispose()
      renderer.dispose()
    }
  }, [])

  // Recolor on theme toggle, same pattern as Atom.jsx.
  useEffect(() => {
    const apply = () => {
      const theme = readThemeColors()
      if (sceneRef.current) sceneRef.current.background = theme.paper.clone()
      if (rendererRef.current) rendererRef.current.setClearColor(theme.paper.clone())
      materialsRef.current.forEach((m) => m.color && m.color.copy(theme.ink))
    }
    const obs = new MutationObserver(apply)
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => obs.disconnect()
  }, [])

  // The pin pulses gently while at least one device is actually home, so "someone's here" reads
  // at a glance without needing to zoom in on the globe itself.
  useEffect(() => {
    if (!pinsGroupRef.current) return
    pinsGroupRef.current.visible = pinCount > 0
  }, [pinCount])

  return <div className="device-globe-canvas" ref={containerRef} />
}

/**
 * Device Globe panel (project mode UI, inspired by eDEX-UI's information-dense layout - rendered
 * in Tau's own flat, no-glow house style rather than eDEX-UI's neon look). Shows which tracked
 * devices/people are home, clustered at one configurable pin, and lists everyone away - there's
 * no real GPS coordinate for "away" yet (see useDevicePresence.js), so this deliberately doesn't
 * fabricate a location for them.
 */
export default function DeviceGlobe() {
  const { home, away, unknown, error } = useDevicePresence()

  return (
    <div className="panel device-globe-panel">
      <div className="panel-title">DEVICE GLOBE</div>
      <div className="panel-content">
        {error && <div className="panel-note">Presence data unavailable: {error}</div>}
        {!HAS_HOME_COORDS && (
          <div className="panel-note">
            Set VITE_HOME_LAT / VITE_HOME_LON to place the home pin - showing 0,0 for now.
          </div>
        )}
        <GlobeCanvas pinCount={home.length} />
        <div className="stat-line online">
          <span className="stat-label">HOME:</span>
          <span className="stat-value">
            {home.length === 0 ? 'NONE' : home.map((e) => e.friendly_name || e.entity_id).join(', ')}
          </span>
        </div>
        <div className="stat-line">
          <span className="stat-label">AWAY:</span>
          <span className="stat-value">
            {away.length === 0 ? 'NONE' : away.map((e) => e.friendly_name || e.entity_id).join(', ')}
          </span>
        </div>
        {unknown.length > 0 && (
          <div className="stat-line">
            <span className="stat-label">UNKNOWN:</span>
            <span className="stat-value">
              {unknown.map((e) => e.friendly_name || e.entity_id).join(', ')}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
