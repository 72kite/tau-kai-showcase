import { useEffect } from 'react'

/**
 * Kiosk-mode helpers for the browser-locked tablet deployment (project-tau-plan.md Phase 3):
 * request fullscreen on first touch (the Fullscreen API requires a user gesture), and suppress
 * pinch-zoom / double-tap-zoom / long-press context menu so a plain browser tab locked to this
 * page behaves like a dedicated kiosk app rather than a normal mobile Safari tab.
 */
export function useKioskMode() {
  useEffect(() => {
    const requestFullscreen = () => {
      const el = document.documentElement
      if (document.fullscreenElement) return
      const request = el.requestFullscreen || el.webkitRequestFullscreen
      if (request) {
        request.call(el).catch(() => {
          // Older iPad Safari (pre-iOS 16) has no element Fullscreen API at all - the
          // "Add to Home Screen" standalone-mode meta tags in index.html cover that case.
        })
      }
    }
    document.addEventListener('touchend', requestFullscreen, { once: true })

    const preventGesture = (e) => e.preventDefault()
    document.addEventListener('gesturestart', preventGesture)
    document.addEventListener('contextmenu', preventGesture)

    let lastTouchEnd = 0
    const preventDoubleTapZoom = (e) => {
      const now = Date.now()
      if (now - lastTouchEnd <= 300) e.preventDefault()
      lastTouchEnd = now
    }
    document.addEventListener('touchend', preventDoubleTapZoom, { passive: false })

    return () => {
      document.removeEventListener('touchend', requestFullscreen)
      document.removeEventListener('gesturestart', preventGesture)
      document.removeEventListener('contextmenu', preventGesture)
      document.removeEventListener('touchend', preventDoubleTapZoom)
    }
  }, [])
}
