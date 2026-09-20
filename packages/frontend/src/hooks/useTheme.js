import { useCallback, useEffect, useState } from 'react'

// Phase 6.C dark mode. Persisted per device (localStorage), applied by stamping data-theme on
// <html> so the CSS variables in index.css swap. Defaults to the OS preference on first run so
// a dark-room tablet doesn't flash white before anyone has chosen.

const THEME_KEY = 'tau-theme'

function initialTheme() {
  const saved = localStorage.getItem(THEME_KEY)
  if (saved === 'light' || saved === 'dark') return saved
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function useTheme() {
  const [theme, setTheme] = useState(initialTheme)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem(THEME_KEY, theme)
  }, [theme])

  const toggle = useCallback(() => setTheme((t) => (t === 'dark' ? 'light' : 'dark')), [])

  return { theme, toggle }
}
