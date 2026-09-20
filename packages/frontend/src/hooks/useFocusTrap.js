import { useEffect, useRef } from 'react'

/**
 * Focus trap for the app's two real dialog-shaped overlays (Phase 50 accessibility pass:
 * SystemDrawer, ApprovalQueue). Neither had any keyboard story before this - no way to Tab
 * through them without also tabbing into the app underneath, no initial focus on open, and no
 * focus returned to whatever opened them on close. This closes that gap the same way any native
 * `<dialog>` would, for overlays that render as plain positioned `div`s instead.
 *
 * `onEscape` is optional and deliberately per-caller: SystemDrawer is dismissible (Escape closes
 * it, matching its click-outside-to-close behavior), but ApprovalQueue is documented as
 * genuinely blocking by design - passing no `onEscape` there means Tab still cycles inside it,
 * but nothing lets a keyboard user skip past a pending approval, which is the correct behavior
 * for that overlay, not an oversight.
 */
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export function useFocusTrap(containerRef, { active, onEscape } = {}) {
  const previouslyFocused = useRef(null)

  useEffect(() => {
    if (!active) return undefined
    const container = containerRef.current
    if (!container) return undefined

    previouslyFocused.current = document.activeElement

    const focusables = () => Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR))
    const first = focusables()[0]
    // Nothing focusable yet (e.g. panels still drawing in) - focus the container itself. Requires
    // the caller to give it tabIndex={-1}, same trick native modal libraries use.
    if (first) first.focus()
    else container.focus?.()

    const onKeyDown = (e) => {
      if (e.key === 'Escape') {
        if (onEscape) onEscape()
        return
      }
      if (e.key !== 'Tab') return
      const nodes = focusables()
      if (nodes.length === 0) {
        e.preventDefault()
        return
      }
      const firstEl = nodes[0]
      const lastEl = nodes[nodes.length - 1]
      if (e.shiftKey && document.activeElement === firstEl) {
        e.preventDefault()
        lastEl.focus()
      } else if (!e.shiftKey && document.activeElement === lastEl) {
        e.preventDefault()
        firstEl.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      // Restore focus to whatever opened this overlay - a toolbar button, a SEND click, etc.
      previouslyFocused.current?.focus?.()
    }
  }, [active, containerRef, onEscape])
}
