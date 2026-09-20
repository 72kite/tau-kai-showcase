import { useRef, useState } from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { useFocusTrap } from '../src/hooks/useFocusTrap'

// Starts closed, like the real SystemDrawer/ApprovalQueue - a trigger button opens it (mirroring
// a toolbar button or an approval arriving), a close button inside closes it, so a test can
// exercise the full open -> trap -> close -> focus-restored cycle, not just a fixed-open dialog.
function Harness({ onEscape } = {}) {
  const [active, setActive] = useState(false)
  const containerRef = useRef(null)
  useFocusTrap(containerRef, { active, onEscape })

  return (
    <div>
      <button type="button" onClick={() => setActive(true)}>
        open trigger
      </button>
      {active && (
        <div ref={containerRef} tabIndex={-1} data-testid="dialog">
          <button type="button">first</button>
          <button type="button">last</button>
          <button type="button" onClick={() => setActive(false)}>
            close
          </button>
        </div>
      )}
    </div>
  )
}

describe('useFocusTrap', () => {
  it('moves focus into the container on activate', () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('open trigger'))
    expect(screen.getByText('first')).toHaveFocus()
  })

  it('wraps Tab from the actual last focusable element back to the first', () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('open trigger'))
    // "close" is the real last focusable child (first, last, close, in DOM order).
    screen.getByText('close').focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(screen.getByText('first')).toHaveFocus()
  })

  it('wraps Shift+Tab from the first element to the actual last focusable element', () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('open trigger'))
    screen.getByText('first').focus()
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    // The dialog's real last focusable child is "close" (after "first" and "last"), not the
    // button literally labeled "last" - this asserts against the true DOM order, not the label.
    expect(screen.getByText('close')).toHaveFocus()
  })

  it('calls onEscape when provided and Escape is pressed', () => {
    const onEscape = vi.fn()
    render(<Harness onEscape={onEscape} />)
    fireEvent.click(screen.getByText('open trigger'))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onEscape).toHaveBeenCalledTimes(1)
  })

  it('does nothing on Escape when onEscape is not provided (ApprovalQueue-style)', () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('open trigger'))
    expect(() => fireEvent.keyDown(document, { key: 'Escape' })).not.toThrow()
    // The dialog is still there - no dismissal happened.
    expect(screen.getByTestId('dialog')).toBeInTheDocument()
  })

  it('restores focus to the trigger that opened it, once closed', () => {
    render(<Harness />)
    const trigger = screen.getByText('open trigger')
    // jsdom's fireEvent.click doesn't also move focus the way a real browser click does -
    // focus explicitly first, matching what a real user's click would leave as document.activeElement.
    trigger.focus()
    fireEvent.click(trigger)
    expect(screen.getByText('first')).toHaveFocus()

    fireEvent.click(screen.getByText('close'))
    expect(trigger).toHaveFocus()
  })
})
