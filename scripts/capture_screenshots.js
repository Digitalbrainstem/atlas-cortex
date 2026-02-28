#!/usr/bin/env node
/**
 * Capture admin panel screenshots for documentation.
 * Usage: npx playwright install chromium && node scripts/capture_screenshots.js
 */
const { chromium } = require('playwright');
const path = require('path');

const BASE = 'http://localhost:5199';
const OUT = path.join(__dirname, '..', 'docs', 'images');

async function main() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();

  // Login page
  console.log('→ Login page');
  await page.goto(`${BASE}/admin/`);
  await page.waitForSelector('input[type="text"]', { timeout: 10000 });
  await page.screenshot({ path: path.join(OUT, 'admin-login.png'), fullPage: true });

  // Fill credentials and submit
  await page.fill('input[type="text"]', 'admin');
  await page.fill('input[type="password"]', 'atlas-admin');
  await page.click('button[type="submit"]');
  await page.waitForTimeout(2000);

  // Dashboard (should land here after login)
  console.log('→ Dashboard');
  await page.waitForSelector('.nav-item', { timeout: 10000 });
  await page.screenshot({ path: path.join(OUT, 'admin-dashboard.png'), fullPage: true });

  // Navigate by clicking sidebar links
  const pages = [
    { label: 'Users', file: 'admin-users.png' },
    { label: 'Safety', file: 'admin-safety.png' },
    { label: 'Devices', file: 'admin-devices.png' },
    { label: 'Voice', file: 'admin-voice.png' },
    { label: 'Evolution', file: 'admin-evolution.png' },
    { label: 'System', file: 'admin-system.png' },
  ];

  for (const p of pages) {
    console.log(`→ ${p.label}`);
    await page.click(`.nav-item:has-text("${p.label}")`);
    await page.waitForTimeout(2000);
    await page.screenshot({ path: path.join(OUT, p.file), fullPage: true });
  }

  await browser.close();
  console.log(`\n✅ Screenshots saved to ${OUT}`);
}

main().catch(err => { console.error(err); process.exit(1); });
