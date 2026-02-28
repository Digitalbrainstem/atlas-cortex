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

  // Login
  console.log('→ Login page');
  await page.goto(`${BASE}/admin/`);
  await page.waitForSelector('input[type="text"]', { timeout: 10000 });
  await page.screenshot({ path: path.join(OUT, 'admin-login.png'), fullPage: true });

  // Fill credentials and submit
  await page.fill('input[type="text"]', 'admin');
  await page.fill('input[type="password"]', 'atlas-admin');
  await page.click('button[type="submit"]');
  await page.waitForURL('**/admin/#/dashboard', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(1500);

  // Dashboard
  console.log('→ Dashboard');
  await page.goto(`${BASE}/admin/#/dashboard`);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT, 'admin-dashboard.png'), fullPage: true });

  // Users
  console.log('→ Users');
  await page.goto(`${BASE}/admin/#/users`);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT, 'admin-users.png'), fullPage: true });

  // Safety
  console.log('→ Safety');
  await page.goto(`${BASE}/admin/#/safety`);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT, 'admin-safety.png'), fullPage: true });

  // Devices
  console.log('→ Devices');
  await page.goto(`${BASE}/admin/#/devices`);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT, 'admin-devices.png'), fullPage: true });

  // Voice
  console.log('→ Voice');
  await page.goto(`${BASE}/admin/#/voice`);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT, 'admin-voice.png'), fullPage: true });

  // Evolution
  console.log('→ Evolution');
  await page.goto(`${BASE}/admin/#/evolution`);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT, 'admin-evolution.png'), fullPage: true });

  // System
  console.log('→ System');
  await page.goto(`${BASE}/admin/#/system`);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT, 'admin-system.png'), fullPage: true });

  await browser.close();
  console.log(`\n✅ Screenshots saved to ${OUT}`);
}

main().catch(err => { console.error(err); process.exit(1); });
