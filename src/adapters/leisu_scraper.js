// 雷速体育（m.leisu.com）数据抓取 —— 无头浏览器方案。
//
// 为什么必须用浏览器：雷速 API 请求头 `accept` 里带签名，签名 = AES-128-CBC
// (key="FqJB6iRNVYdEGpwb", iv="0123456789ABCDEF", PKCS7) 加密
// `{session_id}-h-{时间戳ms}-{32hex随机}`，而 session_id 由阿里云 WAF 反爬 JS
// 用设备指纹初始化下发，只能在真实浏览器里跑。所以用无头浏览器加载页面，让浏览器
// 自动完成签名+解密，再从 Vuex store 抠出解密后的比赛数据。
//
// 数据路径：
//  - 完整赛程（队名/联赛/状态/开赛时间）：加载后点「赛程」tab，触发 today_list 接口，
//    结果存 `$nuxt.$store.state.mod_live.ftb.sc.scoreData.ftb_sc{日期}.matches`；
//  - 实时比分（秒级）：`...mod_live.ftb.live.mqtt_score_list`（按比赛 id 增量推送）。
//  两者按 id 合并，实时比分优先。比分在 home/away.scores 数组，总进球 = scores[4]。
//
// 输出：JSON 到 stdout。用法：node leisu_scraper.js [等待毫秒]
// 依赖：puppeteer-core（不下载 Chromium）+ 系统 Edge/Chrome。
//   环境变量 LEISU_BROWSER 指定浏览器路径（GitHub Actions 上用 Chromium）。

const puppeteer = require('puppeteer-core');

const BROWSER = process.env.LEISU_BROWSER
  || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
  || '/usr/bin/chromium'
  || '/usr/bin/google-chrome';

function score(m) {
  // 总进球在 scores 数组下标 4（实测：全 0 表示未进球，进球累加在该位）。
  const s = m && m.scores;
  return (s && s.length > 4) ? s[4] : null;
}

async function main() {
  const waitMs = parseInt(process.argv[2] || '4000', 10);
  const browser = await puppeteer.launch({
    executablePath: BROWSER,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });
  try {
    const page = await browser.newPage();
    await page.setUserAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1');
    await page.goto('https://m.leisu.com/live', { waitUntil: 'networkidle2', timeout: 60000 }).catch(() => {});
    await new Promise(r => setTimeout(r, 3000));

    // 点「赛程」tab，触发 today_list 接口拉全量赛程（含队名/联赛）
    await page.evaluate(() => {
      const els = Array.from(document.querySelectorAll('div,span,a,li,p'));
      const tab = els.find(e => (e.textContent || '').trim() === '赛程');
      if (tab) tab.click();
    }).catch(() => {});
    await new Promise(r => setTimeout(r, waitMs));

    const data = await page.evaluate(() => {
      const ftb = (window.$nuxt && window.$nuxt.$store.state.mod_live.ftb) || {};
      const all = [];
      (function walk(o) {
        for (const k of Object.keys(o)) {
          const v = o[k];
          if (k === 'matches' && Array.isArray(v)) all.push(...v);
          if (v && typeof v === 'object') walk(v);
        }
      })(ftb);
      const seen = new Map();
      for (const m of all) if (m.id && !seen.has(m.id)) seen.set(m.id, m);
      const mqtt = new Map();
      for (const m of ((ftb.live && ftb.live.mqtt_score_list) || [])) mqtt.set(m.id, m);

      const matches = Array.from(seen.values()).map(m => {
        const live = mqtt.get(m.id);
        const h = live ? live.home : m.home;
        const a = live ? live.away : m.away;
        const hg = h && h.scores && h.scores.length > 4 ? h.scores[4] : null;
        const ag = a && a.scores && a.scores.length > 4 ? a.scores[4] : null;
        return {
          id: m.id,
          competition: m.title || '',
          status: m.status,
          kickoff: m.match_time,                       // unix 秒
          home: (m.home && m.home.name) || '',
          home_en: (m.home && m.home.name_en) || '',
          away: (m.away && m.away.name) || '',
          away_en: (m.away && m.away.name_en) || '',
          home_goals: hg,
          away_goals: ag,
        };
      });
      return { generated_at: new Date().toISOString(), matches };
    });
    process.stdout.write(JSON.stringify(data));
  } finally {
    await browser.close();
  }
}

main().catch(e => { console.error(e.message); process.exit(1); });
