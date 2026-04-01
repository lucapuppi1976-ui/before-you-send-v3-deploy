
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const STORE = {
  onboarding: 'bys_onboarding_done_liveai_v2',
  history: 'bys_history_liveai_v2',
  settings: 'bys_settings_liveai_v2',
  lang: 'bys_lang_liveai_v2',
};

const defaultSettings = { blurNames: true };
let settings = loadJSON(STORE.settings, defaultSettings);
let historyLog = loadJSON(STORE.history, []);
let currentDecodeMode = 'text';
let currentRewriteMode = 'clear';
let lastSharePayload = null;
let latestSendResult = null;
let latestDecodeResult = null;
let apiHealthy = false;
let locales = {};
let currentLang = localStorage.getItem(STORE.lang) || detectBrowserLang();

const samples = {
  decodeText: 'No tranquilla, vediamo più avanti :)',
  voiceTranscript: 'No tranquilla, vediamo più avanti :)',
  screenshotText: 'No tranquilla, vediamo più avanti :)',
  sendText: 'Ok allora fammi sapere tu perché non capisco mai cosa vuoi',
};

init();

async function init() {
  await loadLocales();
  hydrateSettingsUI();
  bindNavigation();
  bindOnboarding();
  bindHome();
  bindDecode();
  bindSendScore();
  bindSettings();
  bindLanguageSelectors();
  registerInstall();
  registerSW();
  applyTranslations();
  renderHistory();
  renderStats();
  checkApiHealth();

  setTimeout(() => {
    const done = localStorage.getItem(STORE.onboarding) === '1';
    openScreen(done ? 'screen-home' : 'screen-onboarding-1');
  }, 350);
}

async function loadLocales() {
  const langs = ['it', 'en', 'es'];
  const loaded = await Promise.all(langs.map(async (lang) => {
    const res = await fetch(`locales/${lang}.json`);
    const data = await res.json();
    return [lang, data];
  }));
  locales = Object.fromEntries(loaded);
  if (!locales[currentLang]) currentLang = 'it';
}

function detectBrowserLang() {
  const raw = (navigator.language || 'it').slice(0, 2).toLowerCase();
  return ['it', 'en', 'es'].includes(raw) ? raw : 'it';
}

function t(key, vars = {}) {
  const dict = locales[currentLang] || locales.it || {};
  let out = dict[key] || (locales.en || {})[key] || key;
  Object.entries(vars).forEach(([k, v]) => {
    out = out.replaceAll(`{${k}}`, String(v));
  });
  return out;
}

function applyTranslations() {
  document.documentElement.lang = currentLang;
  document.title = t('app.title');
  $$('[data-i18n]').forEach((el) => {
    const key = el.dataset.i18n;
    let vars = {};
    if (el.dataset.i18nVars) {
      try { vars = JSON.parse(el.dataset.i18nVars); } catch {}
    }
    el.textContent = t(key, vars);
  });
  $$('[data-i18n-placeholder]').forEach((el) => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  const homeSel = $('#langSelectHome');
  const settingsSel = $('#langSelectSettings');
  if (homeSel) homeSel.value = currentLang;
  if (settingsSel) settingsSel.value = currentLang;
  renderHistory();
  renderStats();
  if (latestDecodeResult) renderDecodeResult(latestDecodeResult, latestDecodeResult.source || 'text', latestDecodeResult.input || '');
  if (latestSendResult) renderSendResult(latestSendResult, latestSendResult.input || '');
}

function bindLanguageSelectors() {
  ['#langSelectHome', '#langSelectSettings'].forEach((sel) => {
    $(sel)?.addEventListener('change', (e) => setLanguage(e.target.value));
  });
}

function setLanguage(lang) {
  currentLang = ['it','en','es'].includes(lang) ? lang : 'it';
  localStorage.setItem(STORE.lang, currentLang);
  applyTranslations();
  checkApiHealth();
}

function bindNavigation() {
  $$('[data-open]').forEach((btn) => btn.addEventListener('click', () => openScreen(btn.dataset.open)));
  $$('[data-back]').forEach((btn) => btn.addEventListener('click', () => openScreen(btn.dataset.back)));
  $$('.nav-btn').forEach((btn) => btn.addEventListener('click', () => openScreen(btn.dataset.open)));
  $('#resetAllBtn')?.addEventListener('click', hardReset);
}

function bindOnboarding() {
  $$('[data-next-onboarding]').forEach((btn) => btn.addEventListener('click', () => openScreen(`screen-onboarding-${btn.dataset.nextOnboarding}`)));
  $$('[data-prev-onboarding]').forEach((btn) => btn.addEventListener('click', () => openScreen(`screen-onboarding-${btn.dataset.prevOnboarding}`)));
  $$('[data-skip-onboarding]').forEach((btn) => btn.addEventListener('click', finishOnboarding));
  $('#finishOnboarding')?.addEventListener('click', finishOnboarding);
}

function bindHome() {
  $('#runQuickDemo')?.addEventListener('click', async () => {
    $('#decodeTextInput').value = samples.decodeText;
    setDecodeMode('text');
    openScreen('screen-decode');
    await runDecodeText(samples.decodeText);
  });
}

function bindDecode() {
  $$('.tab').forEach((tab) => tab.addEventListener('click', () => setDecodeMode(tab.dataset.decodeMode)));
  $('#decodeTextSample')?.addEventListener('click', () => $('#decodeTextInput').value = samples.decodeText);
  $('#decodeTextAnalyze')?.addEventListener('click', async () => {
    const text = $('#decodeTextInput').value.trim() || samples.decodeText;
    await runDecodeText(text);
  });

  $('#useDemoVoice')?.addEventListener('click', () => {
    $('#voiceTranscript').value = samples.voiceTranscript;
    const player = $('#voicePlayer');
    player.src = 'assets/voice_note_demo_27s.wav';
    player.classList.remove('hidden');
  });

  $('#voiceUpload')?.addEventListener('change', (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const player = $('#voicePlayer');
    player.src = URL.createObjectURL(file);
    player.classList.remove('hidden');
  });

  $('#decodeVoiceAnalyze')?.addEventListener('click', async () => { await runDecodeVoice(); });

  $('#useDemoScreenshot')?.addEventListener('click', () => {
    $('#imageExtractedText').value = samples.screenshotText;
    $('#imagePreview').src = 'assets/chat_demo_final.png';
    $('#imagePreview').classList.remove('hidden');
  });

  $('#imageUpload')?.addEventListener('change', (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    $('#imagePreview').src = URL.createObjectURL(file);
    $('#imagePreview').classList.remove('hidden');
  });

  $('#decodeImageAnalyze')?.addEventListener('click', async () => { await runDecodeImage(); });
}

function bindSendScore() {
  $('#sendSample')?.addEventListener('click', () => $('#sendInput').value = samples.sendText);
  $('#sendAnalyze')?.addEventListener('click', async () => {
    const text = $('#sendInput').value.trim() || samples.sendText;
    await runSendAnalysis(text);
  });
}

function bindSettings() {
  $('#toggleBlur')?.addEventListener('click', () => {
    settings.blurNames = !settings.blurNames;
    persistSettings();
  });
  $('#rerunOnboarding')?.addEventListener('click', () => {
    localStorage.removeItem(STORE.onboarding);
    openScreen('screen-onboarding-1');
  });
  $('#hardResetBtn')?.addEventListener('click', hardReset);
  $('#clearHistoryBtn')?.addEventListener('click', () => {
    historyLog = [];
    persistHistory();
    renderHistory();
    renderStats();
  });
  $('#refreshApiHealth')?.addEventListener('click', checkApiHealth);
}

async function checkApiHealth() {
  const badge = $('#apiStatusBadge');
  const detail = $('#apiStatusDetail');
  if (badge) {
    badge.textContent = t('home.health.checking.badge');
    badge.className = 'badge waiting';
  }
  if (detail) detail.textContent = t('home.health.checking.detail');
  try {
    const res = await fetch('/api/health');
    const data = await res.json();
    apiHealthy = Boolean(data.ok && data.apiConfigured);
    if (badge) {
      badge.textContent = apiHealthy ? t('home.health.ok.badge') : t('home.health.bad.badge');
      badge.className = `badge ${apiHealthy ? 'success' : 'warn'}`;
    }
    if (detail) {
      detail.textContent = apiHealthy ? t('home.health.ok.detail') : t('home.health.bad.detail');
    }
  } catch (error) {
    apiHealthy = false;
    if (badge) {
      badge.textContent = t('home.health.off.badge');
      badge.className = 'badge danger';
    }
    if (detail) detail.textContent = t('home.health.off.detail');
  }
}

function registerInstall() {
  let deferredPrompt = null;
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    $('#installBanner')?.classList.remove('hidden');
  });
  $('#installBtn')?.addEventListener('click', async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    await deferredPrompt.userChoice;
    deferredPrompt = null;
    $('#installBanner')?.classList.add('hidden');
  });
}

function registerSW() {
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('./sw.js').catch(console.warn));
  }
}

function finishOnboarding() {
  localStorage.setItem(STORE.onboarding, '1');
  openScreen('screen-home');
}

function openScreen(id) {
  $$('.screen').forEach((screen) => screen.classList.remove('active'));
  $('#' + id)?.classList.add('active');
  $$('.nav-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.open === id));
  const showNav = ['screen-home', 'screen-history', 'screen-settings'].includes(id);
  $('.bottom-nav')?.classList.toggle('hidden', !showNav);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function setDecodeMode(mode) {
  currentDecodeMode = mode;
  $$('.tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.decodeMode === mode));
  ['text', 'voice', 'image'].forEach((key) => {
    $('#decode-mode-' + key)?.classList.toggle('hidden', key !== mode);
  });
}

async function runDecodeText(text) {
  showLoadingDecode(t('results.loading.decode'));
  try {
    const result = await apiJson('/api/decode/text', { text, lang: currentLang });
    latestDecodeResult = result;
    renderDecodeResult(result, 'text', text);
    saveHistory({
      type: 'decode', title: result.verdict, subtitle: sourceLabel('text'), createdAt: new Date().toISOString(),
      payload: { ...result, source: 'text', input: text },
    });
  } catch (error) {
    showDecodeError(error.message);
  }
}

async function runDecodeVoice() {
  showLoadingDecode(t('results.loading.voice'));
  try {
    const form = new FormData();
    const file = $('#voiceUpload')?.files?.[0];
    const transcript = $('#voiceTranscript')?.value?.trim();
    if (file) form.append('file', file);
    if (transcript) form.append('transcript', transcript);
    form.append('lang', currentLang);
    if (!file && !transcript) throw new Error(currentLang === 'it' ? 'Carica un file audio o incolla una trascrizione.' : currentLang === 'es' ? 'Sube un audio o pega una transcripción.' : 'Upload audio or paste a transcript.');
    const result = await apiForm('/api/decode/audio', form);
    latestDecodeResult = result;
    renderDecodeResult(result, 'voice', result.transcript || transcript || '');
    saveHistory({ type:'decode', title: result.verdict, subtitle: sourceLabel('voice'), createdAt: new Date().toISOString(), payload: { ...result, source:'voice', input: result.transcript || transcript || '' } });
  } catch (error) { showDecodeError(error.message); }
}

async function runDecodeImage() {
  showLoadingDecode(t('results.loading.image'));
  try {
    const form = new FormData();
    const file = $('#imageUpload')?.files?.[0];
    const extractedText = $('#imageExtractedText')?.value?.trim();
    if (file) form.append('file', file);
    if (extractedText) form.append('extracted_text', extractedText);
    form.append('lang', currentLang);
    if (!file && !extractedText) throw new Error(currentLang === 'it' ? 'Carica uno screenshot o incolla il testo estratto.' : currentLang === 'es' ? 'Sube una captura o pega el texto extraído.' : 'Upload a screenshot or paste extracted text.');
    const result = await apiForm('/api/decode/image', form);
    latestDecodeResult = result;
    renderDecodeResult(result, 'image', result.extracted_text || extractedText || '');
    saveHistory({ type:'decode', title: result.verdict, subtitle: sourceLabel('image'), createdAt: new Date().toISOString(), payload: { ...result, source:'image', input: result.extracted_text || extractedText || '' } });
  } catch (error) { showDecodeError(error.message); }
}

async function runSendAnalysis(text) {
  showLoadingSend(t('results.loading.send'));
  try {
    const result = await apiJson('/api/score/text', { text, lang: currentLang });
    latestSendResult = result;
    renderSendResult(result, text);
    saveHistory({ type:'send', title:`${result.score}/100 — ${result.label}`, subtitle:t('send.title'), createdAt:new Date().toISOString(), payload: result });
  } catch (error) { showSendError(error.message); }
}

async function apiJson(url, body) {
  const res = await fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || 'API error');
  return data;
}

async function apiForm(url, formData) {
  const res = await fetch(url, { method:'POST', body: formData });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || 'API error');
  return data;
}

function showLoadingDecode(message) {
  const target = $('#decodeResult');
  target.classList.remove('hidden');
  target.innerHTML = `<section class="card loading-card"><div class="spinner"></div><p>${escapeHTML(message)}</p></section>`;
}
function showLoadingSend(message) {
  const target = $('#sendResult');
  target.classList.remove('hidden');
  target.innerHTML = `<section class="card loading-card"><div class="spinner"></div><p>${escapeHTML(message)}</p></section>`;
}
function showDecodeError(message) {
  const target = $('#decodeResult');
  target.classList.remove('hidden');
  target.innerHTML = `<section class="card error-card"><strong>${escapeHTML(t('error.decode'))}</strong><p>${escapeHTML(message)}</p></section>`;
}
function showSendError(message) {
  const target = $('#sendResult');
  target.classList.remove('hidden');
  target.innerHTML = `<section class="card error-card"><strong>${escapeHTML(t('error.send'))}</strong><p>${escapeHTML(message)}</p></section>`;
}

function renderDecodeResult(result, source, text) {
  const target = $('#decodeResult');
  target.classList.remove('hidden');
  const extractedBlock = source === 'image' && result.extracted_text
    ? `<section class="card stack-sm"><strong>${escapeHTML(t('results.extracted'))}</strong><p class="muted">${escapeHTML(result.extracted_text)}</p></section>`
    : source === 'voice' && result.transcript
    ? `<section class="card stack-sm"><strong>${escapeHTML(t('results.transcript'))}</strong><p class="muted">${escapeHTML(result.transcript)}</p></section>`
    : '';
  target.innerHTML = `
    ${extractedBlock}
    <section class="card stack-sm">
      <div class="result-header">
        <p class="eyebrow">${escapeHTML(sourceLabel(source))}</p>
        <div class="result-block"><span class="result-kicker">${escapeHTML(t('results.verdict'))}</span><h2 class="result-title">${escapeHTML(result.verdict)}</h2></div>
        <div class="result-block"><span class="result-kicker">${escapeHTML(t('results.quick'))}</span><p class="muted">${escapeHTML(result.meaning)}</p></div>
      </div>
      <div class="result-block"><span class="result-kicker">${escapeHTML(t('results.signals'))}</span><div class="chips">${result.flags.map((flag) => `<div class="chip">${escapeHTML(flag)}</div>`).join('')}</div></div>
      <div class="metric-list">${metricRows(result.tones)}</div>
    </section>
    <section class="card stack-sm">
      <strong>${escapeHTML(t('results.keep'))}</strong>
      <div class="reply-grid">${result.guardrails.map((g) => `<div class="reply-card"><p>${escapeHTML(g)}</p></div>`).join('')}</div>
    </section>
    <section class="card stack-sm">
      <strong>${escapeHTML(t('results.replies'))}</strong>
      <div class="reply-grid">${result.replies.map((r) => `<div class="reply-card"><h4>${escapeHTML(r.style)}</h4><p>${escapeHTML(r.text)}</p></div>`).join('')}</div>
      <div class="download-row">
        <button class="ghost" id="copyBestReply">${escapeHTML(t('results.copyFirst'))}</button>
        <button class="primary" id="shareDecodeBtn">${escapeHTML(t('results.download'))}</button>
      </div>
    </section>`;
  $('#copyBestReply')?.addEventListener('click', async () => { await copyText(result.replies[0]?.text || ''); });
  $('#shareDecodeBtn')?.addEventListener('click', () => {
    lastSharePayload = { title: result.verdict, subtitle: result.meaning, badge: sourceLabel(source), chips: result.flags.slice(0,3), footer: settings.blurNames ? t('misc.names_yes') : t('misc.names_no'), type: 'decode' };
    downloadShareCard('bys_decode_share.png');
  });
}

function renderSendResult(result, text) {
  const target = $('#sendResult');
  target.classList.remove('hidden');
  currentRewriteMode = 'clear';
  target.innerHTML = `
    <section class="card stack-sm">
      <div class="score-wrap">
        <div class="score-ring" style="--deg:${Math.max(2, Math.min(100, result.score)) * 3.6}deg">
          <div class="center"><div class="score-num">${result.score}</div><div class="score-lbl">/100</div></div>
        </div>
        <div class="stack-sm" style="flex:1;">
          <span class="pill">${escapeHTML(result.label)}</span>
          <div class="issue-card"><strong>${escapeHTML(t('results.issue'))}</strong><p class="muted">${escapeHTML(result.issue)}</p></div>
        </div>
      </div>
      <div class="metric-list">${metricRows(result.breakdown)}</div>
    </section>
    <section class="card stack-sm">
      <strong>${escapeHTML(t('results.versions'))}</strong>
      <div class="rewrite-tabs">${['clear','warm','firm','short'].map((key)=>`<button class="rewrite-tab ${key==='clear'?'active':''}" data-rewrite="${key}">${escapeHTML(displayLabel(key))}</button>`).join('')}</div>
      <div id="rewriteOutput" class="output-box">${escapeHTML(result.rewrites.clear)}</div>
      <div class="download-row">
        <button class="ghost" id="copyRewriteBtn">${escapeHTML(t('results.copyThis'))}</button>
        <button class="primary" id="shareSendBtn">${escapeHTML(t('results.download'))}</button>
      </div>
    </section>`;
  $$('.rewrite-tab', target).forEach((btn) => btn.addEventListener('click', () => {
    currentRewriteMode = btn.dataset.rewrite;
    $$('.rewrite-tab', target).forEach((b) => b.classList.toggle('active', b === btn));
    $('#rewriteOutput').textContent = result.rewrites[currentRewriteMode];
  }));
  $('#copyRewriteBtn')?.addEventListener('click', async () => { await copyText(result.rewrites[currentRewriteMode]); });
  $('#shareSendBtn')?.addEventListener('click', () => {
    lastSharePayload = { title: t('share.score', { score: result.score }), subtitle: result.issue, badge: result.label, chips: topSendChips(result), footer: settings.blurNames ? t('misc.names_yes') : t('misc.names_no'), type: 'send' };
    downloadShareCard('bys_sendscore_share.png');
  });
}

const LABEL_KEYS = {
  clear: 'rewrite.clear', warm: 'rewrite.warm', firm: 'rewrite.firm', short: 'rewrite.short',
  frustration: 'metric.frustration', tension: 'metric.frustration', clarity: 'metric.clarity', warmth: 'metric.warmth', pressure: 'metric.pressure', pressure_perceived: 'metric.pressure', interest: 'metric.interest', involvement: 'metric.interest', respect: 'metric.respect', urgency: 'metric.urgency'
};
function displayLabel(key) { return t(LABEL_KEYS[key] || key); }
function topSendChips(result) { return Object.entries(result.breakdown).sort((a,b)=>b[1]-a[1]).slice(0,3).map(([k,v])=>`${displayLabel(k)} ${v}`); }
function metricRows(obj) { return Object.entries(obj).map(([key, value]) => `<div class="metric-row"><div class="label"><span>${escapeHTML(displayLabel(key))}</span><strong>${value}</strong></div><div class="bar"><div class="fill" style="width:${value}%"></div></div></div>`).join(''); }

function saveHistory(entry) { historyLog.unshift(entry); historyLog = historyLog.slice(0, 30); persistHistory(); renderHistory(); renderStats(); }
function renderHistory() {
  const target = $('#historyList');
  if (!historyLog.length) { target.innerHTML = `<div class="card empty-state">${escapeHTML(t('history.empty'))}</div>`; return; }
  target.innerHTML = historyLog.map((item, idx) => `<article class="history-card"><div class="meta">${formatDate(item.createdAt)} · ${escapeHTML(item.subtitle)}</div><h3>${escapeHTML(item.title)}</h3><p>${item.type === 'decode' ? escapeHTML(item.payload.meaning) : escapeHTML(item.payload.issue)}</p><div class="actions" style="margin-top:10px;"><button class="ghost small" data-history-open="${idx}">${escapeHTML(t('history.open'))}</button><button class="ghost small" data-history-share="${idx}">${escapeHTML(t('history.share'))}</button></div></article>`).join('');
  $$('[data-history-open]', target).forEach((btn) => btn.addEventListener('click', () => openFromHistory(Number(btn.dataset.historyOpen))));
  $$('[data-history-share]', target).forEach((btn) => btn.addEventListener('click', () => shareFromHistory(Number(btn.dataset.historyShare))));
}
function openFromHistory(index) { const item = historyLog[index]; if (!item) return; if (item.type === 'decode') { setDecodeMode(item.payload.source || 'text'); renderDecodeResult(item.payload, item.payload.source || 'text', item.payload.input || ''); openScreen('screen-decode'); } else { renderSendResult(item.payload, item.payload.input || ''); openScreen('screen-send'); } }
function shareFromHistory(index) {
  const item = historyLog[index]; if (!item) return;
  if (item.type === 'decode') { lastSharePayload = { title:item.payload.verdict, subtitle:item.payload.meaning, badge:sourceLabel(item.payload.source || 'text'), chips:item.payload.flags.slice(0,3), footer: settings.blurNames ? t('misc.names_yes') : t('misc.names_no'), type:'decode' }; downloadShareCard('bys_decode_share.png'); }
  else { lastSharePayload = { title:t('share.score',{score:item.payload.score}), subtitle:item.payload.issue, badge:item.payload.label, chips:topSendChips(item.payload), footer: settings.blurNames ? t('misc.names_yes') : t('misc.names_no'), type:'send' }; downloadShareCard('bys_sendscore_share.png'); }
}
function renderStats() { $('#statAnalyses').textContent = historyLog.length; $('#statLastMode').textContent = historyLog[0] ? (historyLog[0].type === 'decode' ? t('misc.received') : t('misc.send')) : '—'; }

function downloadShareCard(filename) {
  if (!lastSharePayload) return;
  const canvas = $('#shareCanvas'); const ctx = canvas.getContext('2d'); ctx.clearRect(0,0,canvas.width,canvas.height);
  const gradient = ctx.createLinearGradient(0,0,0,canvas.height); gradient.addColorStop(0,'#14203f'); gradient.addColorStop(1,'#0a1020'); ctx.fillStyle = gradient; ctx.fillRect(0,0,canvas.width,canvas.height);
  const glow = ctx.createRadialGradient(canvas.width*0.2, canvas.height*0.15, 40, canvas.width*0.2, canvas.height*0.15, 520); glow.addColorStop(0,'rgba(138, 162, 255, 0.45)'); glow.addColorStop(1,'rgba(138, 162, 255, 0)'); ctx.fillStyle = glow; ctx.fillRect(0,0,canvas.width,canvas.height);
  roundRect(ctx,56,80,canvas.width-112,canvas.height-160,42,'rgba(255,255,255,0.06)','rgba(255,255,255,0.10)');
  ctx.fillStyle = '#c3d0ff'; ctx.font = '600 40px Inter, Arial'; ctx.fillText(String(lastSharePayload.badge).toUpperCase(),96,160);
  ctx.fillStyle = '#ffffff'; ctx.font = '800 88px Inter, Arial'; wrapText(ctx,lastSharePayload.title,96,260,canvas.width-192,98);
  ctx.fillStyle = '#b8c3da'; ctx.font = '500 42px Inter, Arial'; wrapText(ctx,lastSharePayload.subtitle,96,500,canvas.width-192,54);
  let y = 720; lastSharePayload.chips.forEach((ch)=>{ roundRect(ctx,96,y-36,520,76,38,'rgba(138,162,255,0.16)','rgba(138,162,255,0.24)'); ctx.fillStyle='#eff3ff'; ctx.font='600 34px Inter, Arial'; ctx.fillText(ch,126,y+10); y += 98; });
  roundRect(ctx,96,1440,canvas.width-192,220,36,'rgba(255,255,255,0.04)','rgba(255,255,255,0.10)');
  ctx.fillStyle='#ffffff'; ctx.font='800 56px Inter, Arial'; ctx.fillText(t('app.name'),132,1525);
  ctx.fillStyle='#b8c3da'; ctx.font='500 34px Inter, Arial'; wrapText(ctx,t('share.decode'),132,1590,canvas.width-264,44);
  ctx.fillStyle='#8ea4ff'; ctx.font='600 28px Inter, Arial'; ctx.fillText(lastSharePayload.footer,132,1662);
  const link = document.createElement('a'); link.download = filename; link.href = canvas.toDataURL('image/png'); link.click();
}

function roundRect(ctx, x, y, w, h, r, fill, stroke) { ctx.beginPath(); ctx.moveTo(x+r,y); ctx.arcTo(x+w,y,x+w,y+h,r); ctx.arcTo(x+w,y+h,x,y+h,r); ctx.arcTo(x,y+h,x,y,r); ctx.arcTo(x,y,x+w,y,r); ctx.closePath(); if (fill){ctx.fillStyle=fill;ctx.fill();} if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=2;ctx.stroke();}}
function wrapText(ctx, text, x, y, maxWidth, lineHeight) { const words = String(text).split(' '); let line=''; for (let n=0;n<words.length;n++){const testLine=line+words[n]+' '; const metrics=ctx.measureText(testLine); if(metrics.width>maxWidth && n>0){ctx.fillText(line,x,y); line=words[n]+' '; y += lineHeight;} else { line=testLine; }} ctx.fillText(line,x,y); }
function sourceLabel(source) { return t(`source.${source}`); }
function persistHistory(){ localStorage.setItem(STORE.history, JSON.stringify(historyLog)); }
function persistSettings(){ localStorage.setItem(STORE.settings, JSON.stringify(settings)); hydrateSettingsUI(); }
function hydrateSettingsUI(){ const btn = $('#toggleBlur'); if(btn){ btn.classList.toggle('on', settings.blurNames); btn.textContent = settings.blurNames ? 'ON' : 'OFF'; } }
function hardReset(){ localStorage.removeItem(STORE.onboarding); localStorage.removeItem(STORE.history); localStorage.removeItem(STORE.settings); localStorage.removeItem(STORE.lang); historyLog=[]; settings={...defaultSettings}; currentLang = detectBrowserLang(); hydrateSettingsUI(); applyTranslations(); renderHistory(); renderStats(); openScreen('screen-onboarding-1'); }
function loadJSON(key, fallback){ try{ return JSON.parse(localStorage.getItem(key)||'null') ?? fallback; }catch{ return fallback; } }
function formatDate(iso){ try { return new Date(iso).toLocaleString(currentLang === 'it' ? 'it-IT' : currentLang === 'es' ? 'es-ES' : 'en-US', { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit' }); } catch { return iso; } }
async function copyText(text){ try{ await navigator.clipboard.writeText(text); } catch { const ta=document.createElement('textarea'); ta.value=text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); } }
function escapeHTML(str){ return String(str ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;'); }
