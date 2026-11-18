// Takes one or more named screenshots of the running dev stack.
// node shot.mjs OUTPUT_DIR WxH[:light|:dark] name=js-to-run ...
import { chromium } from '@playwright/test'

const [output, size, ...steps] = process.argv.slice(2)
const [dimensions, scheme = 'light'] = size.split(':')
const [width, height] = dimensions.split('x').map(Number)
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM ?? '/usr/bin/chromium' })
const page = await browser.newPage({ viewport: { width, height }, colorScheme: scheme, deviceScaleFactor: Number(process.env.DPR ?? 1) })
const errors = []
page.on('console', (message) => message.type() === 'error' && errors.push(message.text()))
page.on('pageerror', (error) => errors.push(String(error)))
await page.goto(process.env.URL ?? 'http://127.0.0.1:1420/')
await page.waitForFunction(() => document.fonts.status === 'loaded')
await page.waitForSelector('nav', { timeout: 15000 })
await page.waitForTimeout(700)
for (const step of steps) {
  const at = step.indexOf('=')
  const name = at < 0 ? step : step.slice(0, at)
  const code = at < 0 ? '' : step.slice(at + 1)
  page.setDefaultTimeout(4000)
  try {
    if (code) await new Function('page', `return (async () => { ${code} })()`)(page)
  } catch (error) {
    console.log(`step ${name} failed: ${String(error.message).split('\n')[0]}`)
  }
  await page.waitForTimeout(450)
  await page.screenshot({ path: `${output}/${name}.png` })
}
if (errors.length) console.log('console errors:', [...new Set(errors)].slice(0, 8).join('\n'))
await browser.close()
