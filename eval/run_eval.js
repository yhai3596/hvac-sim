#!/usr/bin/env node
/**
 * 智能助手 LLM 解析路径评测。
 *
 * 目标：验证「LLM 能力 + 工作台接口契约 → 可执行指令」这条链路，而不是验证正则兜底。
 * 做法：对每条语料，取出页面 buildParsePrompt() 生成的**真实提示词**发给模型，
 *      把模型返回的 JSON 喂回页面的 validatePlan() 校验，再按 7 个维度打分；
 *      可选地把计划真的跑一遍（--execute），证明产出的是可执行指令。
 *
 * 用法：
 *   # 打真实 API（推荐；也可指向 serve.py --proxy 的同源反代）
 *   node eval/run_eval.js --base https://api.deepseek.com --model deepseek-chat --key sk-xxx
 *   node eval/run_eval.js --base https://api.anthropic.com --protocol anthropic --model claude-opus-5 --key sk-ant-xxx
 *
 *   # 离线：用事先准备好的模型回复（键为语料 id），用于回归与无网环境
 *   node eval/run_eval.js --responses eval/responses.json
 *
 *   # 额外把每个计划真的跑起来（慢一些，验证可执行性）
 *   node eval/run_eval.js --responses eval/responses.json --execute
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const args = process.argv.slice(2);
const opt = (n, d) => { const i = args.indexOf('--' + n); return i >= 0 ? args[i + 1] : d; };
const flag = n => args.includes('--' + n);

const CORPUS = JSON.parse(fs.readFileSync(path.join(__dirname, opt('corpus', 'corpus.json')), 'utf8')).cases;
const RESP = opt('responses') ? JSON.parse(fs.readFileSync(opt('responses'), 'utf8')) : null;
const BASE = opt('base'), KEY = opt('key'), MODEL = opt('model', 'unknown');
const PROTOCOL = opt('protocol', 'openai');
const ONLY = opt('only');

async function callModel(sys, user) {
  const b = BASE.replace(/\/+$/, '');
  let url, headers, body;
  if (PROTOCOL === 'anthropic') {
    url = b + '/v1/messages';
    headers = { 'content-type': 'application/json', 'x-api-key': KEY, 'anthropic-version': '2023-06-01' };
    body = { model: MODEL, max_tokens: 2000, system: sys, messages: [{ role: 'user', content: user }] };
  } else {
    url = /chat\/completions|chatcompletion/.test(b) ? b : b + '/chat/completions';
    headers = { 'content-type': 'application/json', authorization: 'Bearer ' + KEY };
    body = { model: MODEL, max_tokens: 2000, messages: [{ role: 'system', content: sys }, { role: 'user', content: user }] };
  }
  const r = await fetch(url, { method: 'POST', headers, body: JSON.stringify(body) });
  const j = await r.json();
  if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + JSON.stringify(j).slice(0, 200));
  return PROTOCOL === 'anthropic'
    ? (j.content || []).filter(x => x.type === 'text').map(x => x.text).join('')
    : j.choices[0].message.content;
}

const ser = list => list.map(x => x.name + '{' +
  Object.entries(x.set).map(([k, v]) => k + '=' + v).join(',') + '}').join(' ');

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
  const page = await browser.newPage();
  const pageErrs = [];
  page.on('pageerror', e => pageErrs.push(e.message));
  const wrapped = path.join(require('os').tmpdir(), 'ac_eval_page.html');
  fs.writeFileSync(wrapped, '<!doctype html><html><head><meta charset="utf-8"></head><body>' +
    fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8') + '</body></html>');
  await page.goto('file://' + wrapped);
  await page.waitForTimeout(400);
  await page.evaluate(() => { running = false; });

  const cases = ONLY ? CORPUS.filter(c => c.id === ONLY) : CORPUS;
  const rows = [];
  for (const c of cases) {
    const { sys, user } = await page.evaluate(t => buildParsePrompt(t), c.text);
    let raw, err = null;
    try {
      raw = RESP ? RESP[c.id] : await callModel(sys, user);
      if (raw === undefined) throw new Error('离线回复文件里没有 id=' + c.id);
    } catch (e) { err = e.message; raw = ''; }

    const res = await page.evaluate(([raw, expect]) => {
      const out = { schema: false, keysOk: false, rangeOk: false, oneVar: false,
                    semantic: false, sizeOk: false, physicsOk: false, issues: [] };
      const j = extractJson(raw);
      if (!j) { out.issues.push('返回不是可解析的 JSON'); return out; }
      if (!j.compare && !j.conditions && !j.variants) { out.issues.push('缺少 compare/conditions'); return out; }
      out.schema = true;
      // 未知键 / 越界：validatePlan 的 warnings 会明确报出
      const vp = validatePlan(j);
      const w = vp.warnings.join(' ');
      out.keysOk = !/忽略未知变量/.test(w);
      out.rangeOk = !/超出范围|已截取为/.test(w);
      if (!out.keysOk) out.issues.push('用了平台没有的键名');
      if (!out.rangeOk) out.issues.push('取值越界被截取');
      // 单变量纪律：臂之间的"根变量"只能有一个
      const roots = a => new Set(a.flatMap(x => Object.keys(x.set).map(rootKey)));
      out.oneVar = vp.compare.length < 2 || roots(vp.compare).size <= 1;
      if (!out.oneVar) out.issues.push('对比臂之间改了多个变量：' + [...roots(vp.compare)].join('/'));
      const condBad = vp.conditions.slice(1).filter(c => roots([c]).size > 1);
      if (condBad.length) { out.oneVar = false; out.issues.push('条件档改了多个变量：' + condBad.map(c => c.name).join('/')); }
      // 规模
      out.runs = vp.variants.length;
      out.sizeOk = vp.variants.length >= 1 && vp.variants.length <= (expect.maxRuns || 24);
      if (!out.sizeOk) out.issues.push('方案数 ' + vp.variants.length + ' 超出预期上限');
      // 语义覆盖
      const A = vp.compare.map(x => x.name + '{' + Object.entries(x.set).map(([k, v]) => k + '=' + v).join(',') + '}').join(' ');
      const C = vp.conditions.map(x => x.name + '{' + Object.entries(x.set).map(([k, v]) => k + '=' + v).join(',') + '}').join(' ');
      const B = Object.entries(vp.base).map(([k, v]) => k + '=' + v).join(',');
      const ALL = A + ' ' + C + ' ' + B;
      const miss = [];
      const has = (re, s) => new RegExp(re).test(s);
      for (const re of expect.arms || []) if (!has(re, A)) miss.push('臂缺 ' + re);
      for (const re of expect.conds || []) if (!has(re, C)) miss.push('条件缺 ' + re);
      for (const re of expect.base || []) if (!has(re, B)) miss.push('共同设定缺 ' + re);
      for (const re of expect.any || []) if (!has(re, ALL)) miss.push('计划中缺 ' + re);
      for (const re of expect.noArms || []) if (has(re, A)) miss.push('臂不该有 ' + re);
      for (const re of expect.noBase || []) if (has(re, B)) miss.push('共同设定不该有 ' + re);
      for (const re of expect.noConds || []) if (has(re, C)) miss.push('条件不该有 ' + re);
      for (const re of expect.noAny || []) if (has(re, ALL)) miss.push('计划中不该有 ' + re);
      if (expect.minArms && vp.compare.length < expect.minArms) miss.push('臂数应≥' + expect.minArms + '，实到 ' + vp.compare.length);
      // 物理有效性：自动检查，不依赖语料声明——两个臂若参数等价，跑出来必然重合
      const kv = st => Object.entries(st).map(([k, v]) => k + '=' + v).join(',');
      const eff = st => {
        const fc = st.fanCtrl !== undefined ? st.fanCtrl : (vp.base.fanCtrl ?? 'fixed');
        const ts = st.twoStage !== undefined ? st.twoStage : (vp.base.twoStage ?? 2);
        return JSON.stringify({ ...st, _air: (fc === 'two' && ts === 2) ? 'fixed' : fc });
      };
      // 注意：平台会自动修掉等价臂并留下 warning。评的是**模型的输出**，
      // 所以平台一旦出手修复，就算模型这一项没做对。
      out.physicsOk = !/等价|完全相同/.test(w);
      if (!out.physicsOk) out.issues.push('模型排了物理等价的对比臂（平台已自动修正）');
      if (vp.compare.length > 1) {
        const sig = vp.compare.map(x => eff(x.set));
        if (new Set(sig).size < sig.length) {
          out.physicsOk = false;
          out.issues.push('存在物理等价/重复的对比臂，跑出来必然重合');
        }
      }
      if (expect.noArmsPair) {
        const [p, q] = expect.noArmsPair;
        const S = vp.compare.map(x => kv(x.set));
        if (S.some(x => new RegExp(p).test(x)) && S.some(x => new RegExp(q).test(x)))
          miss.push('排了物理等价的两个臂');
      }
      if (expect.days !== undefined && vp.days !== expect.days) miss.push('天数应为 ' + expect.days + '，实到 ' + vp.days);
      if (expect.warmup !== undefined && vp.warmup_h !== expect.warmup) miss.push('预热应为 ' + expect.warmup + '，实到 ' + vp.warmup_h);
      out.semantic = miss.length === 0;
      out.issues.push(...miss);
      out.plan = { arms: A, conds: C, base: B, days: vp.days, warm: vp.warmup_h,
                   notes: vp.guessed.filter(g => g.startsWith('模型说明：')) };
      return out;
    }, [raw, c.expect]);

    if (err) { res.issues.unshift('调用失败：' + err); }
    // 可执行性：真的跑一遍（短窗口，只验证能跑通并产出指标）
    if (flag('execute') && res.schema && !err) {
      try {
        res.exec = await page.evaluate(async raw => {
          const vp = validatePlan(extractJson(raw));
          vp.days = 0.5; vp.warmup_h = 2;                 // 评测用短窗口
          const recs = await runExperiment(vp);
          return { ok: true, n: recs.length, t2: recs[0].m.t2, kwh: recs[0].m.kwh };
        }, raw);
      } catch (e) { res.exec = { ok: false, error: String(e.message).slice(0, 120) }; }
    }
    rows.push({ id: c.id, text: c.text, ...res });
    const mark = x => x ? '✓' : '✗';
    console.log(`${mark(res.schema)}${mark(res.keysOk)}${mark(res.rangeOk)}${mark(res.oneVar)}${mark(res.physicsOk)}${mark(res.sizeOk)}${mark(res.semantic)} ` +
      `${c.id.padEnd(18)} runs=${res.runs ?? '-'}` + (res.issues.length ? '  ← ' + res.issues.join('；') : ''));
  }

  const dim = k => rows.filter(r => r[k]).length;
  const n = rows.length;
  console.log('\n=== 评分（' + (RESP ? '离线回复' : MODEL) + '，' + n + ' 条语料）===');
  const score = [['JSON 结构合法', 'schema'], ['键名合法', 'keysOk'], ['取值在范围内', 'rangeOk'],
                 ['单变量纪律', 'oneVar'], ['物理有效性', 'physicsOk'], ['规模合理', 'sizeOk'], ['语义覆盖', 'semantic']];
  for (const [label, k] of score) console.log(`  ${label.padEnd(14)} ${dim(k)}/${n}  ${(dim(k) / n * 100).toFixed(0)}%`);
  const allPass = rows.filter(r => score.every(([, k]) => r[k])).length;
  console.log(`  ${'全部通过'.padEnd(14)} ${allPass}/${n}  ${(allPass / n * 100).toFixed(0)}%`);
  if (flag('execute')) {
    const ex = rows.filter(r => r.exec);
    console.log(`  ${'可执行'.padEnd(14)} ${ex.filter(r => r.exec.ok).length}/${ex.length}`);
  }
  if (pageErrs.length) console.log('页面错误：' + pageErrs.join(' | '));
  fs.writeFileSync(path.join(__dirname, 'last-report.json'), JSON.stringify({ model: RESP ? 'offline' : MODEL, rows }, null, 1));
  console.log('\n明细已写入 eval/last-report.json');
  await browser.close();
  process.exit(allPass === n ? 0 : 1);
})().catch(e => { console.error('FAIL:', e.message); process.exit(2); });
