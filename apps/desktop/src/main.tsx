import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from '@/app/App'
import { preferences, usePreferences } from '@/state/preferences'
import { AppContext } from '@/state/queries'
import { Sync } from '@/state/sync'
import { applyTheme } from '@/state/theme'
import { createTransport } from '@/transport/create'
import '@/styles/base.css'
import { useEffect } from 'react'

applyTheme(preferences())

function ThemeBridge() {
  const prefs = usePreferences()
  useEffect(() => applyTheme(prefs), [prefs])
  return null
}

const transport = await createTransport()
const client = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } })
const value = { transport, sync: new Sync(transport, client) }

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <AppContext.Provider value={value}>
        <ThemeBridge />
        <App />
      </AppContext.Provider>
    </QueryClientProvider>
  </StrictMode>,
)
