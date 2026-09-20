import React, { useCallback, useEffect, useState } from 'react'
import { getApiBase } from '../hooks/useMCPResource'
import './Panel.css'

/**
 * Browse and search Tau's Memory Tree (memory-mcp-server, Phase 2.8) - the same store the
 * assistant recalls from at the start of every chat turn. Read paths (get_memory_tree,
 * search_memory) are CDG-allowed; the one write this panel offers is reinforce_memory, which
 * only bumps a node's relevance score (deliberately safe - creating/editing memories stays a
 * human-approved action through the normal approval queue).
 */
export default function MemoryPanel() {
  const [query, setQuery] = useState('')
  const [nodes, setNodes] = useState([])
  const [status, setStatus] = useState('loading')

  const callTool = useCallback(async (tool, args) => {
    const res = await fetch(`${getApiBase()}/api/tools/memory-mcp-server/${tool}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ arguments: args, requested_by: 'tablet-ui-memory' }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  }, [])

  const loadRoots = useCallback(async () => {
    setStatus('loading')
    try {
      const body = await callTool('get_memory_tree', {})
      const tree = JSON.parse(body.result || '{}')
      const flat = []
      const walk = (node, depth) => {
        flat.push({ ...node, depth })
        ;(node.children || []).forEach((child) => walk(child, depth + 1))
      }
      ;(tree.roots || []).forEach((root) => walk(root, 0))
      setNodes(flat)
      setStatus(flat.length === 0 ? 'empty' : 'ok')
    } catch (e) {
      setStatus(`error: ${e.message}`)
    }
  }, [callTool])

  const runSearch = useCallback(
    async (e) => {
      if (e) e.preventDefault()
      if (!query.trim()) {
        loadRoots()
        return
      }
      setStatus('loading')
      try {
        const body = await callTool('search_memory', { query: query.trim(), limit: 10 })
        const found = (body.results || []).map((text) => ({ ...JSON.parse(text), depth: 0 }))
        setNodes(found)
        setStatus(found.length === 0 ? 'empty' : 'ok')
      } catch (err) {
        setStatus(`error: ${err.message}`)
      }
    },
    [query, callTool, loadRoots]
  )

  const reinforce = useCallback(
    async (nodeId) => {
      try {
        await callTool('reinforce_memory', { node_id: nodeId })
        setNodes((prev) =>
          prev.map((n) => (n.id === nodeId ? { ...n, score: (n.score ?? 0) + 0.5 } : n))
        )
      } catch {
        // Score bump is a nicety; a failed one isn't worth an error state.
      }
    },
    [callTool]
  )

  useEffect(() => {
    loadRoots()
  }, [loadRoots])

  return (
    <div className="panel memory-panel">
      <div className="panel-title">MEMORY TREE</div>
      <div className="panel-content">
        <form className="memory-search-form" onSubmit={runSearch}>
          <input
            type="text"
            className="memory-search-input"
            placeholder="Search memories…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button type="submit" className="memory-search-submit">
            SEARCH
          </button>
        </form>

        {status === 'loading' && <div className="panel-note">Loading…</div>}
        {status === 'empty' && <div className="panel-note">No memories{query ? ' matched' : ' stored yet'}.</div>}
        {status.startsWith('error') && <div className="panel-note">{status}</div>}

        {nodes.map((node) => (
          <div
            key={node.id}
            className="memory-node"
            style={{ marginLeft: `${(node.depth || 0) * 16}px` }}
          >
            <div className="memory-node-head">
              <span className="memory-node-title">{node.title}</span>
              <span className="memory-node-score">{Number(node.score ?? 0).toFixed(1)}</span>
              <button
                type="button"
                className="memory-reinforce"
                title="Reinforce: mark this memory as relevant/accurate"
                onClick={() => reinforce(node.id)}
              >
                ▲
              </button>
            </div>
            {node.content && <div className="memory-node-content">{node.content}</div>}
            {(node.tags || []).length > 0 && (
              <div className="memory-node-tags">{node.tags.map((t) => `#${t}`).join(' ')}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
