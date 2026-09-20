import React, { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { isLikelyOlderTablet } from '../utils/device'
import './Atom.css'

// Second tap must land within this window to count as the double-tap invoke.
const DOUBLE_TAP_MS = 350

// Read the current theme's paper/ink colors from the CSS variables (index.css) so the WebGL
// atom matches light/dark mode instead of being a hardcoded white box. Falls back to white/black.
function readThemeColors() {
  const cs = getComputedStyle(document.documentElement)
  const paper = (cs.getPropertyValue('--paper') || '#ffffff').trim() || '#ffffff'
  const ink = (cs.getPropertyValue('--ink') || '#000000').trim() || '#000000'
  return { paper: new THREE.Color(paper), ink: new THREE.Color(ink) }
}

/**
 * The idle-state 3D atom. Always rendered in the same place at the same size - it never
 * shrinks to a corner or gets replaced by a panel (see project-tau-plan.md Phase 3: the
 * transcript and design panels sit alongside/below it, they don't take it over).
 *
 * Reactive states, all rendered on the outermost ring so they read at a glance:
 * - `isListening` + `voiceLevelRef`: the outer ring breathes with the speaker's live mic
 *   amplitude (see useMicLevel.js - local Web Audio analysis, refs not state, so the 60fps
 *   loop never re-renders React).
 * - `isThinking`: the outer ring tumbles around the nucleus in 3D (gyroscope-style plane
 *   rotation), easing back flat when the reply lands.
 *
 * `onClick` fires on DOUBLE-tap - a single tap is too easy to hit by accident on a kiosk
 * tablet mounted where people walk past. Double-tapping the atom is equivalent to saying the
 * wake phrase (see useVoiceInvoke.js). Keyboard Enter/Space still invokes on a single press.
 */

// The camera dollies back far enough that the outermost orbit always fits - however narrow the
// atom's box gets (6.F slides it into a ~45%-width column; portrait tablets are narrower still)
// and whatever angle the thinking animation has precessed the orbital planes to.
//
// The tilt is the part that isn't obvious. A ring lying flat at z=0 only needs its radius to fit,
// but a precessed ring swings part of itself TOWARD the camera, where perspective magnifies it:
// the point at angle t sits at lateral R·cos t and depth R·sin t, so it projects to
// R·cos t/(d - R·sin t) of the frustum's half-extent-per-unit-depth. Requiring that to stay inside
// the frustum for every t means R·cos t + k·R·sin t <= k·d for all t, and since the left side
// peaks at R·sqrt(1+k²), the safe distance is a closed form: d >= R·sqrt(1+k²)/k.
// (k = tan(fov/2)·min(1, aspect) - the tighter of the two frustum half-angles.)
//
// Fitting for the worst case at all times, rather than only while thinking, keeps the camera
// static - no dolly to animate, no state for it to be out of sync with. The cost is that the atom
// at rest is a little smaller than a naive flat fit would allow; a ring that visibly runs off the
// edge every time Tau thinks is the worse trade.
const OUTER_ORBIT_RADIUS = 2.6
const FIT_MARGIN = 1.08
const BASE_CAMERA_Z = 4
export default function Atom({
  complexity = 0.5,
  isListening = false,
  isThinking = false,
  voiceLevelRef,
  onClick,
}) {
  const containerRef = useRef(null)
  const sceneRef = useRef(null)
  const cameraRef = useRef(null)
  const rendererRef = useRef(null)
  const atomRef = useRef(null)
  const orbitsRef = useRef([])
  const ringsRef = useRef([])
  const materialsRef = useRef([]) // every line material, so a theme change can recolor them
  const lastTapRef = useRef(0)
  // Mirrors the listening/thinking props for the animation loop, which is created once and
  // would otherwise close over stale values (same pattern as useVoiceInvoke's modeRef).
  const modeRef = useRef({ isListening, isThinking })
  const [initialized, setInitialized] = useState(false)

  useEffect(() => {
    modeRef.current = { isListening, isThinking }
  }, [isListening, isThinking])

  useEffect(() => {
    if (!containerRef.current) return

    // Older iPads (Air 2 / Mini 4 class) get simpler geometry and a capped pixel ratio so
    // the animation stays smooth instead of thermal-throttling the device.
    const lowPower = isLikelyOlderTablet()
    const nucleusDetail = lowPower ? 5 : 8
    const orbitSegments = lowPower ? 20 : 32

    const theme = readThemeColors()
    const scene = new THREE.Scene()
    scene.background = theme.paper.clone()

    const camera = new THREE.PerspectiveCamera(
      75,
      containerRef.current.clientWidth / containerRef.current.clientHeight,
      0.1,
      1000
    )
    camera.position.z = BASE_CAMERA_Z

    const renderer = new THREE.WebGLRenderer({
      antialias: !lowPower,
      powerPreference: lowPower ? 'low-power' : 'default',
    })
    renderer.setSize(containerRef.current.clientWidth, containerRef.current.clientHeight)
    renderer.setPixelRatio(lowPower ? 1 : Math.min(window.devicePixelRatio || 1, 2))
    renderer.setClearColor(theme.paper.clone())
    containerRef.current.appendChild(renderer.domElement)

    const materials = []

    // Nucleus
    const nucleusGeometry = new THREE.SphereGeometry(0.3, nucleusDetail, nucleusDetail)
    const nucleusMaterial = new THREE.LineBasicMaterial({ color: theme.ink.clone(), linewidth: 3 })
    materials.push(nucleusMaterial)
    const nucleusWireframe = new THREE.EdgesGeometry(nucleusGeometry)
    const nucleus = new THREE.LineSegments(nucleusWireframe, nucleusMaterial)
    scene.add(nucleus)

    // Orbits with electrons
    const orbits = []
    const rings = []
    const orbitRadii = [1, 1.8, 2.6]
    const electronCounts = [2, 8, 4]

    for (let i = 0; i < 3; i++) {
      const orbitGeometry = new THREE.BufferGeometry()
      const points = []
      for (let j = 0; j <= orbitSegments; j++) {
        const angle = (j / orbitSegments) * Math.PI * 2
        points.push(new THREE.Vector3(Math.cos(angle) * orbitRadii[i], Math.sin(angle) * orbitRadii[i], 0))
      }
      orbitGeometry.setFromPoints(points)
      const orbitMaterial = new THREE.LineBasicMaterial({ color: theme.ink.clone(), linewidth: 1 })
      materials.push(orbitMaterial)
      const orbit = new THREE.Line(orbitGeometry, orbitMaterial)
      rings.push(orbit)
      scene.add(orbit)

      const electronGroup = new THREE.Group()
      for (let j = 0; j < electronCounts[i]; j++) {
        const angle = (j / electronCounts[i]) * Math.PI * 2
        const electronGeometry = new THREE.CircleGeometry(0.08, 4)
        const electronMaterial = new THREE.LineBasicMaterial({ color: theme.ink.clone(), linewidth: 2 })
        materials.push(electronMaterial)
        const electronWireframe = new THREE.EdgesGeometry(electronGeometry)
        const electron = new THREE.LineSegments(electronWireframe, electronMaterial)
        electron.position.x = Math.cos(angle) * orbitRadii[i]
        electron.position.y = Math.sin(angle) * orbitRadii[i]
        electronGroup.add(electron)
      }
      electronGroup.userData.orbitRadius = orbitRadii[i]
      electronGroup.userData.speed = 0.01 + i * 0.005
      orbits.push(electronGroup)
      scene.add(electronGroup)
    }

    sceneRef.current = scene
    cameraRef.current = camera
    rendererRef.current = renderer
    atomRef.current = nucleus
    orbitsRef.current = orbits
    ringsRef.current = rings
    materialsRef.current = materials

    setInitialized(true)

    const handleResize = () => {
      if (!containerRef.current) return
      const width = containerRef.current.clientWidth
      const height = containerRef.current.clientHeight
      if (width === 0 || height === 0) return
      const aspect = width / height
      camera.aspect = aspect
      // See the derivation above the component: d >= R*sqrt(1+k^2)/k fits the outer orbit at any
      // precession angle. Never dolly closer than the designed BASE_CAMERA_Z.
      const halfFovRad = (camera.fov * Math.PI) / 360
      const k = Math.tan(halfFovRad) * Math.min(1, aspect)
      const R = OUTER_ORBIT_RADIUS * FIT_MARGIN
      camera.position.z = Math.max(BASE_CAMERA_Z, (R * Math.sqrt(1 + k * k)) / k)
      camera.updateProjectionMatrix()
      renderer.setSize(width, height)
    }
    handleResize()

    // A ResizeObserver, not just window.resize: the atom's box changes width when the stage
    // slides it aside for a response (6.F) and when the design panel mounts - neither of which
    // fires a window resize. Without this the canvas keeps its old drawing buffer and gets
    // CSS-stretched to the new box.
    const resizeObserver = new ResizeObserver(handleResize)
    resizeObserver.observe(containerRef.current)
    window.addEventListener('resize', handleResize)

    return () => {
      resizeObserver.disconnect()
      window.removeEventListener('resize', handleResize)
      if (containerRef.current && renderer.domElement.parentNode === containerRef.current) {
        containerRef.current.removeChild(renderer.domElement)
      }
      nucleusGeometry.dispose()
      nucleusMaterial.dispose()
      renderer.dispose()
    }
  }, [])

  // Recolor the atom when the theme toggles (index.css stamps data-theme on <html>). Without
  // this the WebGL scene would keep its startup colors and look wrong after a light/dark switch.
  useEffect(() => {
    if (!initialized) return
    const apply = () => {
      const theme = readThemeColors()
      if (sceneRef.current) sceneRef.current.background = theme.paper.clone()
      if (rendererRef.current) rendererRef.current.setClearColor(theme.paper.clone())
      materialsRef.current.forEach((m) => m.color && m.color.copy(theme.ink))
    }
    apply()
    const obs = new MutationObserver(apply)
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => obs.disconnect()
  }, [initialized])

  useEffect(() => {
    if (!initialized) return

    const lowPower = isLikelyOlderTablet()
    const frameInterval = lowPower ? 1000 / 30 : 0 // throttle to 30fps on older hardware
    let animationId
    let rotationAngle = 0
    let lastFrameTime = 0

    const animate = (time) => {
      animationId = requestAnimationFrame(animate)

      if (frameInterval && time - lastFrameTime < frameInterval) return
      lastFrameTime = time

      if (atomRef.current) {
        atomRef.current.rotation.x += 0.003
        atomRef.current.rotation.y += 0.005
      }

      const { isListening: listening, isThinking: thinking } = modeRef.current
      const level = listening ? (voiceLevelRef?.current ?? 0) : 0

      // Electrons orbit continuously; while thinking they speed up, so the whole system reads
      // as kinetic perpetual motion rather than an idle drift.
      const orbitBoost = thinking ? 2.6 : 1
      orbitsRef.current.forEach((group) => {
        const speed = group.userData.speed * (1 + complexity) * orbitBoost
        const radius = group.userData.orbitRadius
        group.children.forEach((electron, j) => {
          const angle = rotationAngle * speed + (j / group.children.length) * Math.PI * 2
          electron.position.x = Math.cos(angle) * radius
          electron.position.y = Math.sin(angle) * radius
        })
      })

      // Ring reactions - the rings ARE the state indicator (no container border).
      // - listening: every ring breathes with the live mic amplitude (phase-staggered ripple).
      // - thinking: NO pulse. Instead each ring's orbital plane precesses continuously on its
      //   own axes/rates, so the three planes sweep through each other like a gyroscope in
      //   perpetual motion - a kinetic "working" orbit, not a jittery tumble. Constant per-frame
      //   increments keep it perfectly smooth; when the reply lands the planes ease back to flat.
      // Distinct, non-harmonic rates per ring so the motion never visibly loops.
      const precess = [
        { x: 0.0100, y: 0.0165 },
        { x: 0.0145, y: -0.0110 },
        { x: -0.0205, y: 0.0130 },
      ]

      ringsRef.current.forEach((ring, i) => {
        const group = orbitsRef.current[i]
        if (!ring || !group) return

        const amplitudeFactor = 0.5 + (i / 2) * 0.5 // 0.5 inner -> 1.0 outer
        const phase = i * 0.9 // ripple: inner leads, outer follows
        let targetScale = 1
        if (listening) {
          const baselinePulse = 0.06 * (0.5 + 0.5 * Math.sin(time * 0.005 - phase))
          targetScale = 1 + (baselinePulse + level * 0.35) * amplitudeFactor
        }
        const scale = ring.scale.x + (targetScale - ring.scale.x) * 0.35
        ring.scale.setScalar(scale)
        group.scale.setScalar(scale)

        if (thinking) {
          ring.rotation.x += precess[i].x
          ring.rotation.y += precess[i].y
        } else {
          // Ease every plane back to flat when not thinking.
          ring.rotation.x *= 0.94
          ring.rotation.y *= 0.94
          if (Math.abs(ring.rotation.x) < 0.002) ring.rotation.x = 0
          if (Math.abs(ring.rotation.y) < 0.002) ring.rotation.y = 0
        }
        group.rotation.copy(ring.rotation)
      })

      rotationAngle += 0.01
      rendererRef.current.render(sceneRef.current, cameraRef.current)
    }

    animationId = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(animationId)
  }, [initialized, complexity])

  // Double-tap: two taps within DOUBLE_TAP_MS invoke; a single tap is deliberately inert so
  // brushing the kiosk screen doesn't start a listening session.
  //
  // stopPropagation matters here: the atom sits inside the stage box, which has its own onClick
  // (App.jsx's handleStageClick) for recall/cancel-on-tap-elsewhere. Without this, EVERY tap on
  // the atom - including the second tap that just invoked - also bubbles into that handler, which
  // reads a stale `mode` from before this event's state updates land and can reopen the old
  // response surface right as invoke() fires, masking that the invoke happened at all.
  const handleTap = (e) => {
    e.stopPropagation()
    if (!onClick) return
    const now = Date.now()
    if (now - lastTapRef.current < DOUBLE_TAP_MS) {
      lastTapRef.current = 0
      onClick()
    } else {
      lastTapRef.current = now
    }
  }

  return (
    <div
      className={`atom-container ${isListening ? 'listening' : ''} ${isThinking ? 'thinking' : ''} ${onClick ? 'clickable' : ''}`}
      ref={containerRef}
      onClick={handleTap}
      role={onClick ? 'button' : undefined}
      aria-label={onClick ? 'Double-tap to talk to Tau' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onClick()
              }
            }
          : undefined
      }
    />
  )
}
