/**
 * wiki3-dashboard — one-click open Wiki 3 dashboard (127.0.0.1:9120)
 * Checks if running, then opens browser. No backend needed.
 * Mirrors dashboard-launcher pattern, but for the wiki3 dashboard (9120).
 */
import { cn, haptic, host, Tip, STATUSBAR_AREAS, PALETTE_AREA } from '@hermes/plugin-sdk'
import { jsx } from 'react/jsx-runtime'

const URL = 'http://127.0.0.1:9120/'
const busy = { _v: false }

async function openDashboard() {
  haptic('tap')
  if (busy._v) return
  busy._v = true
  try {
    // Check if dashboard is already running
    try {
      const r = await fetch(URL, { method: 'HEAD', signal: AbortSignal.timeout(3000) })
      if (r.ok || r.status < 500) {
        window.open(URL, '_blank')
        host.notify({ kind: 'success', message: 'Wiki 3 Dashboard открыт' })
        return
      }
    } catch (_) {}

    // Not running — try to start the server via the Python backend (if present)
    host.notify({ kind: 'info', message: 'Wiki 3 Dashboard не запущен. Запускаю…' })
    try {
      const launchUrl = 'http://127.0.0.1:5312/api/plugins/wiki3-dashboard/launch'
      const lr = await fetch(launchUrl, { signal: AbortSignal.timeout(15000) })
      const data = await lr.json().catch(() => ({}))
      if (data?.ok) {
        window.open(URL, '_blank')
        host.notify({ kind: 'success', message: 'Wiki 3 Dashboard запущен и открыт!' })
        return
      }
    } catch (_) {}

    // Fallback — just open and hope
    window.open(URL, '_blank')
    host.notify({ kind: 'warning', message: 'Wiki 3 Dashboard мог не запуститься. Если пусто — запусти сервер вручную' })
  } finally {
    busy._v = false
  }
}

export default {
  id: 'wiki3-dashboard',
  name: 'Wiki 3 Dashboard',
  register(ctx) {
    function Chip() {
      return jsx(Tip, {
        label: 'Открыть Wiki 3 Dashboard',
        children: jsx('button', {
          type: 'button',
          className: cn(
            'inline-flex h-full items-center gap-1.5 px-1.5 text-[0.6875rem] transition-colors',
            'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
          ),
          onClick: openDashboard,
          children: jsx('span', { children: 'Wiki3' }),
        }),
      })
    }

    ctx.register({
      id: 'chip',
      area: STATUSBAR_AREAS.right,
      order: 141,
      render: () => jsx(Chip, {}),
    })

    ctx.register({
      id: 'palette',
      area: PALETTE_AREA,
      data: {
        id: 'wiki3-dashboard.open',
        label: 'Open Wiki 3 Dashboard',
        keywords: ['wiki', 'dashboard', 'wiki3'],
        run: () => void openDashboard(),
      },
    })
  },
}
