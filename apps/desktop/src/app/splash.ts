/**
 * The start-up mark in index.html paints before any script runs. It leaves once the first screen is known, but not
 * before the mark has finished drawing, so a fast start never cuts the animation short.
 */
const DRAWN_MS = 1650
const REDUCED_MS = 300
let leaving = false

export function dismissSplash(): void {
  const splash = document.getElementById('splash')
  if (!splash || leaving) return
  leaving = true
  // Automated runs skip it unless the address asks for it with ?splash.
  const automated = navigator.webdriver && !new URLSearchParams(location.search).has('splash')
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const wait = automated ? 0 : Math.max(0, (reduced ? REDUCED_MS : DRAWN_MS) - performance.now())
  window.setTimeout(() => {
    if (automated) return splash.remove()
    splash.dataset.state = 'done'
    const remove = () => splash.remove()
    splash.addEventListener('transitionend', remove, { once: true })
    window.setTimeout(remove, 900)
  }, wait)
}
