import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

function readLocalAdmin() {
  try {
    const env = readFileSync(resolve(process.cwd(), '..', '.env'), 'utf8')
    const values = Object.fromEntries(env.split(/\r?\n/).filter(line => line && !line.startsWith('#') && line.includes('=')).map(line => {
      const [key, ...parts] = line.split('=')
      return [key, parts.join('=')]
    }))
    return { username: values.INFRAWATCH_ADMIN_USERNAME, password: values.INFRAWATCH_ADMIN_PASSWORD }
  } catch {
    return { username: '', password: '' }
  }
}

test('the built UI authenticates against the real Compose API', async ({ page, request }) => {
  const credentials = readLocalAdmin()
  test.skip(!credentials.username || !credentials.password, 'Requires a local Compose administrator in the ignored .env file')

  const response = await request.post('http://127.0.0.1:8080/api/v1/auth/login', { data: credentials })
  expect(response.status()).toBe(200)
  const { access_token: token } = await response.json()
  await page.addInitScript(value => sessionStorage.setItem('infrawatch.session', value), token)
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'Infrastructure at a glance' })).toBeVisible()
  await expect(page.getByText('No servers registered')).toBeVisible()
  await expect(page.getByText('Unavailable').first()).toBeVisible()
  await page.getByRole('button', { name: 'Server inventory' }).click()
  await page.getByRole('button', { name: 'Register server' }).click()
  await page.getByLabel('Server name').fill('blocked-loopback-test')
  await page.getByLabel('Hostname or private IP').fill('127.0.0.1')
  await page.getByRole('button', { name: 'Register server' }).last().click()
  await expect(page.getByRole('alert')).toContainText('approved monitoring networks')
  await page.getByRole('button', { name: 'Cancel' }).click()
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
})
