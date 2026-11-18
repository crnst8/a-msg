import { expect, type Page } from '@playwright/test'

export const composer = (page: Page) => page.getByLabel('Message', { exact: true })

export async function open(page: Page, url = '/') {
  await page.goto(url)
  await page.waitForFunction(() => document.fonts.status === 'loaded')
  await page.waitForSelector('nav')
  await expect(page.getByRole('button', { name: /^General/ })).toBeVisible()
}

export async function newSpace(page: Page, title: string) {
  await page.getByRole('button', { name: 'New space' }).click()
  await page.getByLabel('Name').fill(title)
  await page.getByRole('button', { name: 'Create' }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
}

export async function send(page: Page, text: string) {
  await composer(page).fill(text)
  await page.keyboard.press('Enter')
}

export const article = (page: Page) => page.locator('article')
export const scroller = (page: Page) => page.getByRole('log')

export function unique(prefix: string) {
  return `${prefix} ${Math.random().toString(36).slice(2, 7)}`
}

export async function noPageOverflow(page: Page) {
  const overflow = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    height: document.documentElement.scrollHeight - document.documentElement.clientHeight,
    sheet: (() => {
      const sheet = document.querySelector('main')!
      return sheet.scrollWidth - sheet.clientWidth
    })(),
  }))
  expect(overflow.width).toBeLessThanOrEqual(0)
  expect(overflow.height).toBeLessThanOrEqual(0)
  expect(overflow.sheet).toBeLessThanOrEqual(0)
}
