const puppeteer = require('puppeteer');

const BASE = process.env.BASE_URL || 'http://172.30.0.20';
const OUT  = '/out';

const SHOTS = [
  { url: '/',                  file: 'devices.png',       wait: 1500 },
  { url: '/devices/spine1',    file: 'device-detail.png', wait: 2000 },
  { url: '/configs',           file: 'configs.png',       wait: 1500 },
  { url: '/edit/spine1',       file: 'config-editor.png', wait: 2000 },
  { url: '/leases',            file: 'leases.png',        wait: 1500 },
  { url: '/events',            file: 'events.png',        wait: 1500 },
  { url: '/docs',              file: 'api-swagger.png',   wait: 2500 },
];

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1400, height: 900, deviceScaleFactor: 1 });

  for (const s of SHOTS) {
    const url = BASE + s.url;
    console.log(`-> ${url}`);
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await new Promise(r => setTimeout(r, s.wait));
    await page.screenshot({ path: `${OUT}/${s.file}`, fullPage: true });
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
