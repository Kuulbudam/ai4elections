import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { randomBytes } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { totp } from './totp'

const here = path.dirname(fileURLToPath(import.meta.url))
const DEMO = path.resolve(here, '../../../data/synthetic/SYNTHETIC_results_seed2026.csv')
const SHEET = path.resolve(here, '../../../data/synthetic/SYNTHETIC_sheet_mismatch_row2.png')
const DECLARED = path.resolve(here, '../../../data/synthetic/SYNTHETIC_declared_totals.csv')
const SUPABASE_URL = process.env.SUPABASE_URL ?? ''
const SERVICE = process.env.SUPABASE_SERVICE_ROLE_KEY ?? ''
const RUN = process.env.GITHUB_RUN_ID ?? randomBytes(3).toString('hex')

async function createAccount() {
  const email = `e2e-${RUN}-${randomBytes(3).toString('hex')}@example.com`
  const password = randomBytes(18).toString('base64url')
  const headers: Record<string, string> = { apikey: SERVICE, 'Content-Type': 'application/json' }
  if (SERVICE.startsWith('eyJ')) headers.Authorization = `Bearer ${SERVICE}`
  const r = await fetch(`${SUPABASE_URL}/auth/v1/admin/users`, { method: 'POST', headers, body: JSON.stringify({ email, password, email_confirm: true }) })
  if (!r.ok) throw new Error(`could not create the test account: HTTP ${r.status}`)
  return { email, password }
}

async function axe(page: Page, label: string) {
  const res = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()
  const serious = res.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
  test.info().annotations.push({ type: `axe ${label}`, description: serious.map((v) => `${v.impact}: ${v.id} (${v.nodes.length})`).join('; ') || 'no serious or critical issues' })
  expect(serious, `${label}: ${serious.map((v) => `${v.id}: ${v.help}`).join(' | ')}`).toEqual([])
}

test('a new user runs the whole workflow on the deployed site', async ({ page }) => {
  test.skip(!SUPABASE_URL || !SERVICE, 'needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY')
  const { email, password } = await createAccount()

  await test.step('sign in with email and password', async () => {
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
    await axe(page, 'sign in')
    await page.getByLabel('Email').fill(email)
    await page.getByLabel('Password').fill(password)
    await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  })

  await test.step('set up two-step verification with an authenticator code', async () => {
    await expect(page.getByRole('heading', { name: 'Set up two-step verification' })).toBeVisible()
    await page.getByText('Can’t scan? Enter this key instead').click()
    const secret = (await page.locator('code.secret').innerText()).trim()
    await page.getByLabel('6-digit code').fill(totp(secret))
    await page.getByRole('button', { name: 'Verify' }).click()
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
  })

  await test.step('create an organisation and a project', async () => {
    await axe(page, 'dashboard')
    await page.getByLabel('New organisation name').fill(`E2E ${RUN}`)
    await page.getByRole('button', { name: 'Create organisation' }).click()
    await page.getByRole('row', { name: new RegExp(`E2E ${RUN}`) }).getByRole('link', { name: 'Projects' }).click()
    await page.getByLabel('Election name').fill('SYNTHETIC E2E Election')
    await page.getByLabel('Election year').fill('2027')
    await page.getByLabel('Country').fill('Synthetica')
    await page.getByRole('button', { name: 'Create project' }).click()
    await page.getByRole('link', { name: 'SYNTHETIC E2E Election' }).click()
    await expect(page.getByRole('heading', { name: 'SYNTHETIC E2E Election' })).toBeVisible()
    // the project menu shows the workflow, with step 1 as the next step
    const flow = page.getByRole('navigation', { name: 'Workflow' })
    await expect(flow.getByRole('link', { name: /Upload results.*next step/ })).toBeVisible()
  })
  const projectUrl = page.url()

  await test.step('upload the synthetic file; the worker ingests it', async () => {
    await page.getByRole('link', { name: 'Upload results', exact: true }).click()
    await page.getByLabel('Name', { exact: true }).fill('Results')
    await page.getByLabel('Source details').fill('SYNTHETIC demo data, end-to-end test')
    await page.getByLabel('CSV file (max 25 MB)').setInputFiles(DEMO)
    await page.getByRole('form', { name: 'Upload results' }).evaluate((f: HTMLFormElement) => f.requestSubmit())
    await expect(page.getByRole('link', { name: 'Match columns', exact: true })).toBeVisible({ timeout: 240_000 })
    await expect(page.getByText('12,023')).toBeVisible()
    await page.getByRole('link', { name: 'Match columns', exact: true }).click()
  })

  await test.step('save the suggested column mapping', async () => {
    await page.getByRole('button', { name: 'Save columns' }).click()
    await expect(page.getByText(/^Saved\./)).toBeVisible()
    await page.getByRole('link', { name: 'Next: check the data' }).click()
  })

  await test.step('run validation', async () => {
    await page.getByRole('button', { name: 'Check data' }).click()
    await expect(page.getByRole('list', { name: 'Validation summary' })).toBeVisible({ timeout: 300_000 })
    await expect(page.getByText('12,023').first()).toBeVisible()
    await axe(page, 'validation')
    await page.getByRole('link', { name: 'Next: analyse' }).click()
  })

  await test.step('home page shows what to do next', async () => {
    const back = page.url()
    await page.goto('/')
    await expect(page.getByRole('list', { name: 'What to do next' }).getByRole('link', { name: 'Analyse', exact: true })).toBeVisible()
    await page.goto(back)
  })

  await test.step('run analysis', async () => {
    await page.getByRole('button', { name: 'Analyse', exact: true }).click()
    const stats = page.getByRole('list', { name: 'Analysis summary' })
    await expect(stats).toBeVisible({ timeout: 300_000 })
    await expect(stats.getByText('49')).toBeVisible()
    await expect(stats.getByText('91')).toBeVisible()
    await expect(page.getByRole('img', { name: /Turnout: distribution across/ })).toBeVisible()
    await expect(page.getByRole('group', { name: /against Typical for nearby units/ })).toBeVisible()
    await expect(page.getByRole('link', { name: /Ask the data/ })).toBeVisible()
    await axe(page, 'analysis')
  })

  await test.step('area totals', async () => {
    await page.getByRole('link', { name: 'Areas' }).first().click()
    await expect(page.getByRole('img', { name: 'Turnout by area' })).toBeVisible()
    await axe(page, 'areas')
  })

  await test.step('review a finding', async () => {
    await page.goto(`${projectUrl}/findings`)
    await page.getByLabel('How unusual').selectOption('very_high')
    const first = page.getByRole('table').getByRole('link').first()
    await expect(first).toBeVisible()
    await axe(page, 'findings')
    await first.click()
    await expect(page.getByRole('table', { name: 'Original cells' })).toBeVisible()
    const form = page.getByRole('form', { name: 'Record a review decision' })
    await form.getByRole('combobox').selectOption('escalated')
    await form.getByRole('textbox').fill('End-to-end test: compare with the result sheet.')
    await form.getByRole('button', { name: 'Save decision' }).click()
    await expect(page.getByRole('list', { name: 'Review history' }).getByText('Escalated')).toBeVisible()
    await axe(page, 'finding detail')
  })

  await test.step('generate a report and download the CSV', async () => {
    await page.goto(`${projectUrl}/reports`)
    await page.getByRole('button', { name: 'Make report' }).click()
    await expect(page.getByRole('heading', { name: /Findings report/ })).toBeVisible({ timeout: 60_000 })
    await expect(page.getByText(/not evidence of fraud/).first()).toBeVisible()
    const dl = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Download CSV' }).click()
    const file = await dl
    expect(file.suggestedFilename()).toMatch(/\.csv$/)
    await axe(page, 'report')
  })

  await test.step('result sheet: AI reads a photo, a person confirms, mismatches are flagged', async () => {
    await page.goto(`${projectUrl}/sheets`)
    await expect(page.getByRole('heading', { name: /Result sheets/ })).toBeVisible()
    await page.getByLabel(/Take a photo or choose one/).setInputFiles(SHEET)
    const form = page.getByRole('form', { name: "Confirm the sheet's figures" })
    await expect(form).toBeVisible({ timeout: 90_000 })
    await expect(page.getByRole('img', { name: /Photo of the polling-unit result sheet/ })).toBeVisible()
    await axe(page, 'sheet confirm')
    // The sheet's code must be there for the comparison; type it if the AI could not read it.
    const code = form.getByLabel('Polling unit code')
    if (!(await code.inputValue())) await code.fill('SY-01-01-01-00002')
    await form.getByRole('button', { name: 'Confirm and check' }).click()
    await expect(page.getByRole('list', { name: 'Checks on this sheet' })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('✗ Problem').first()).toBeVisible()
    await expect(page.getByRole('link', { name: 'finding 1' })).toBeVisible()
    await axe(page, 'sheet results')
  })

  await test.step('collation check: declared totals vs the sum of their polling units', async () => {
    await page.goto(`${projectUrl}/collation`)
    await expect(page.getByRole('heading', { name: /Collation check/ })).toBeVisible()
    await page.getByLabel(/Choose the declared totals/).setInputFiles(DECLARED)
    await expect(page.getByRole('list', { name: 'Collation summary' })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('Differs', { exact: true })).toHaveCount(3)
    await expect(page.getByText(/PA votes: declared .*\(\+120\)/)).toBeVisible()
    await axe(page, 'collation')
    await page.getByRole('link', { name: 'Open the finding' }).first().click()
    await expect(page.getByRole('heading', { name: /Declared .* total differs from the sum of its polling units/ })).toBeVisible()
    await expect(page.getByRole('link', { name: 'See the declared totals' })).toBeVisible()
  })

  await test.step('ask the data: a question gets an answer and the page never blanks', async () => {
    const crashes: string[] = []
    page.on('pageerror', (e) => crashes.push(`${e.name}: ${e.message}\n${e.stack ?? ''}`))
    page.on('console', (m) => {
      if (m.type() === 'error') crashes.push(`console: ${m.text()}`)
    })
    await page.goto(`${projectUrl}/assistant`)
    await expect(page.getByRole('heading', { name: /Ask the data/ })).toBeVisible()
    await page.getByRole('button', { name: 'Which LGAs have the most unusual turnout?' }).click()
    try {
      await expect(page.getByRole('article', { name: 'Answer' }).or(page.getByRole('alert'))).toBeVisible({ timeout: 90_000 })
      await page.waitForTimeout(1000)
    } finally {
      if (crashes.length) console.log('ASSISTANT ERRORS:\n' + crashes.join('\n---\n'))
      console.log('ASSISTANT PAGE TEXT:\n' + (await page.locator('body').innerText()).slice(0, 1500))
    }
    await expect(page.getByRole('heading', { name: /Ask the data/ })).toBeVisible()
    expect(crashes.filter((c) => !c.startsWith('console: Failed to load resource'))).toEqual([])
  })

  await test.step('phone width: pages stay usable', async () => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${projectUrl}/findings`)
    await expect(page.getByRole('heading', { name: /Findings/ })).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow, 'page itself must not scroll sideways (tables scroll inside)').toBeLessThanOrEqual(1)
  })
})
