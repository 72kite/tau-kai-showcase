import { useEffect, useRef } from 'react'
import { isTauri } from '../utils/tauri'

/**
 * Phase 27.D Milestone 4: native OS notifications for pending approvals and unreviewed drafts -
 * the "someone has to walk past a kiosk" gap useDraftCount.js's own docstring names as a known
 * limitation of the browser-kiosk-only client (project-tau-plan.md, Phase 9). tau-desktop isn't a
 * kiosk; it can actually push a notification.
 *
 * Deliberately NOT a new poller - takes the already-fetched `approvals` (useApprovals) and
 * `draftCount` (useDraftCount) as inputs and only watches for what's NEW in them, so it never
 * duplicates a fetch neither hook already makes. Kiosk build: isTauri() is false, this hook does
 * nothing at all, same shape as every other Tauri-only piece since Milestone 1.
 */
export function useDesktopNotifications(approvals, draftCount) {
  const permissionCheckedRef = useRef(false)
  const notifiedApprovalIdsRef = useRef(new Set())
  const hasSeededApprovalsRef = useRef(false)
  const lastDraftCountRef = useRef(null)

  // Click-to-focus follow-up: bring the (hidden-in-tray) main window to front when the user
  // clicks a notification, instead of it just disappearing with no way back to what it was
  // about. `onAction` fires for a plain click same as a registered action button (no
  // `actionTypeId` was set on either notification below, so this is always the plain-click case).
  // `focus_main_window` is an app-defined Rust command (main.rs) - not a window-permission grant -
  // so it needs no capabilities.json change, same reasoning as every other command here.
  useEffect(() => {
    if (!isTauri()) return
    let listener
    let cancelled = false
    ;(async () => {
      const { onAction } = await import('@tauri-apps/plugin-notification')
      const { invoke } = await import('@tauri-apps/api/core')
      const l = await onAction(() => invoke('focus_main_window'))
      if (cancelled) l.unregister()
      else listener = l
    })()
    return () => {
      cancelled = true
      listener?.unregister()
    }
  }, [])

  useEffect(() => {
    if (!isTauri()) return

    let cancelled = false

    async function ensurePermission() {
      if (permissionCheckedRef.current) return true
      const { isPermissionGranted, requestPermission } = await import('@tauri-apps/plugin-notification')
      let granted = await isPermissionGranted()
      if (!granted) granted = (await requestPermission()) === 'granted'
      if (!cancelled) permissionCheckedRef.current = granted
      return granted
    }

    async function notifyNewApprovals() {
      const currentIds = new Set(approvals.map((a) => a.id))
      if (!hasSeededApprovalsRef.current) {
        // First-ever look at the queue: whatever's already pending predates this session -
        // seed silently rather than dumping a notification per backlogged item.
        notifiedApprovalIdsRef.current = currentIds
        hasSeededApprovalsRef.current = true
        return
      }
      const newOnes = approvals.filter((a) => !notifiedApprovalIdsRef.current.has(a.id))
      if (newOnes.length === 0) return
      if (!(await ensurePermission())) return
      const { sendNotification } = await import('@tauri-apps/plugin-notification')
      for (const approval of newOnes) {
        sendNotification({
          title: 'Tau',
          body: `${approval.server}.${approval.tool} needs approval`,
        })
        notifiedApprovalIdsRef.current.add(approval.id)
      }
      // Ids that dropped out of the pending list (approved/denied elsewhere) stay in the set too
      // - never re-notify about one just because it's no longer present.
      for (const id of currentIds) notifiedApprovalIdsRef.current.add(id)
    }

    notifyNewApprovals()
    return () => {
      cancelled = true
    }
  }, [approvals])

  useEffect(() => {
    if (!isTauri()) return

    async function notifyNewDrafts() {
      if (lastDraftCountRef.current === null) {
        // Same seeding reasoning as approvals above - a backlog that predates this session
        // shouldn't announce itself as "new" the moment the app opens.
        lastDraftCountRef.current = draftCount
        return
      }
      if (draftCount <= lastDraftCountRef.current) {
        lastDraftCountRef.current = draftCount
        return
      }
      const delta = draftCount - lastDraftCountRef.current
      lastDraftCountRef.current = draftCount

      const { isPermissionGranted, requestPermission, sendNotification } = await import(
        '@tauri-apps/plugin-notification'
      )
      let granted = await isPermissionGranted()
      if (!granted) granted = (await requestPermission()) === 'granted'
      if (!granted) return
      sendNotification({
        title: 'Tau',
        body: `${delta} new draft${delta === 1 ? '' : 's'} awaiting review`,
      })
    }

    notifyNewDrafts()
  }, [draftCount])
}
