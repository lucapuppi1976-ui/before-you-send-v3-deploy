
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const STORE = {
  onboarding: 'bys_onboarding_done_liveai_v2',
  history: 'bys_history_liveai_v2',
  settings: 'bys_settings_liveai_v2',
  lang: 'bys_lang_liveai_v2',
};

const DEFAULT_ACCESS_PAGE = 'https://bb1studio.com/before-you-send/';
const CONTACT_EMAIL = 'hola@bb1studio.com';
// Languages of the app: the same as the site (bb1studio.com).
const LANGS = ['it', 'en', 'es', 'fr', 'de', 'ru'];
const DATE_LOCALES = { it: 'it-IT', en: 'en-US', es: 'es-ES', fr: 'fr-FR', de: 'de-DE', ru: 'ru-RU' };
const UPLOAD_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];
const MAX_IMAGE_EDGE = 1600;
const JPEG_QUALITY = 0.85;

let historyLog = loadJSON(STORE.history, []);
let currentDecodeMode = 'text';
let currentRewriteMode = 'clear';
let lastSharePayload = null;
let latestSendResult = null;
let latestDecodeResult = null;
let apiHealthy = false;
let locales = {};
let currentLang = localStorage.getItem(STORE.lang) || detectBrowserLang();
let gateState = {
  enabled: false,
  unlocked: true,
  accessPage: DEFAULT_ACCESS_PAGE,
  statusError: false,
};
let capabilities = { text: true, voice: true, image: true };
let remainingAnalyses = null;

// Demo examples in Spanish (the app is presented first in the Canary Islands): the reading follows the app
// language, the ready-to-send replies stay in Spanish, the language of the message.
const samples = {
  decodeText: 'No te preocupes, ya lo vemos más adelante :)',
  voiceTranscript: 'Hola, perdona que no te haya contestado antes, esta semana voy fatal de trabajo. Lo del sábado mejor lo dejamos para otro día, ¿vale? Ya te digo algo.',
  screenshotText: 'Uf, esta semana voy fatal de trabajo… No te preocupes, ya lo vemos más adelante :)',
  sendText: 'Vale, pues ya me dirás tú, porque nunca entiendo lo que quieres',
};

init();

// Link of the code email (#code=…&lang=…): the language of the site page and the code, so the app opens in
// that language and already unlocked. The fragment never reaches the server, and it leaves the address bar
// (and the browser history) straight away.
function readEmailLink() {
  const params = new URLSearchParams(location.hash.slice(1));
  const link = { code: (params.get('code') || '').trim(), lang: (params.get('lang') || '').trim().toLowerCase() };
  if (link.code || link.lang) history.replaceState(null, '', location.pathname + location.search);
  return link;
}

async function init() {
  const link = readEmailLink();
  await loadLocales();
  if (LANGS.includes(link.lang)) {
    currentLang = link.lang;
    localStorage.setItem(STORE.lang, currentLang);
  }
  bindNavigation();
  bindOnboarding();
  bindHome();
  bindDecode();
  bindSendScore();
  bindSettings();
  bindGate();
  bindLanguageSelectors();
  registerInstall();
  registerSW();
  applyTranslations();
  renderHistory();
  renderStats();

  await refreshGateStatus(false);
  if (gateState.enabled && !gateState.unlocked) {
    showGateScreen(gateState.statusError ? t('gate.error.status') : '');
    if (link.code && !gateState.statusError) {
      $('#gateCodeInput').value = link.code;
      await unlockGate();
    }
    return;
  }

  checkApiHealth();
  setTimeout(openInitialScreen, 350);
}


function openInitialScreen() {
  const done = localStorage.getItem(STORE.onboarding) === '1';
  openScreen(done ? 'screen-home' : 'screen-onboarding-1');
}

function accessPageUrl() {
  // The server provides the access page (BYS_ACCESS_PAGE_URL) in /api/access/status. On bb1studio.com that page
  // exists in every language of the app: Spanish without a prefix, the others under /<lang>/.
  const raw = String(gateState.accessPage || '').trim();
  const url = new URL(/^https?:\/\//i.test(raw) ? raw : DEFAULT_ACCESS_PAGE);
  if (url.hostname === 'bb1studio.com' && url.pathname === '/before-you-send/' && currentLang !== 'es') {
    url.pathname = `/${currentLang}${url.pathname}`;
  }
  return url.href;
}

function openAccessPage() {
  window.open(accessPageUrl(), '_blank', 'noopener');
}

function openContact() {
  window.location.href = `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent('Before You Send')}`;
}

function setGateError(message = '') {
  const target = $('#gateError');
  if (!target) return;
  if (!message) {
    target.textContent = '';
    target.classList.add('hidden');
    return;
  }
  target.textContent = message;
  target.classList.remove('hidden');
}

function syncGateUI() {
  const gateSel = $('#langSelectGate');
  if (gateSel) gateSel.value = currentLang;
  const accessCard = $('#accessSessionCard');
  if (accessCard) accessCard.classList.toggle('hidden', !gateState.enabled);
}

function showGateScreen(message = '') {
  syncGateUI();
  setGateError(message);
  openScreen('screen-gate', { scrollTop: true, restoreScroll: false });
  requestAnimationFrame(() => $('#gateCodeInput')?.focus());
}

function forceGateLock(message = t('gate.error.required')) {
  gateState.enabled = true;
  gateState.unlocked = false;
  showGateScreen(message);
}

async function refreshGateStatus(showError = true) {
  try {
    const res = await fetch('/api/access/status');
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(extractErrorMessage(data, t('gate.error.status')));
    gateState = { ...gateState, ...data, statusError: false };
    syncGateUI();
    return true;
  } catch (error) {
    gateState.enabled = true;
    gateState.unlocked = false;
    gateState.statusError = true;
    syncGateUI();
    if (showError) setGateError(error.message || t('gate.error.status'));
    return false;
  }
}

async function unlockGate() {
  const input = $('#gateCodeInput');
  const button = $('#unlockAccessBtn');
  const code = input?.value?.trim() || '';
  if (!code) {
    setGateError(t('gate.error.empty'));
    input?.focus();
    return;
  }
  setGateError('');
  if (button) {
    button.disabled = true;
    button.textContent = t('gate.unlocking');
  }
  try {
    let res;
    try {
      res = await fetch('/api/access/unlock', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, lang: currentLang }),
      });
    } catch {
      throw new Error(t('error.network'));
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = errorDetail(data);
      if (detail && detail.code === 'trial_ended') {
        if (detail.accessPage) gateState.accessPage = detail.accessPage;
        throw new Error(detail.message || t('error.trial_ended'));
      }
      if (res.status === 429) throw new Error(t('error.rate_limited'));
      if (res.status === 403 || res.status === 422) throw new Error(t('gate.error.invalid'));
      throw new Error(t('gate.error.status'));
    }
    gateState = { ...gateState, ...data, statusError: false };
    if (input) input.value = '';
    setGateError('');
    syncGateUI();
    checkApiHealth();
    setTimeout(openInitialScreen, 120);
  } catch (error) {
    setGateError(error.message || t('gate.error.invalid'));
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = t('gate.unlock');
    }
  }
}

async function lockGate() {
  try {
    await fetch('/api/access/logout', { method: 'POST' });
  } catch {}
  gateState.unlocked = false;
  showGateScreen('');
}

function bindGate() {
  $('#unlockAccessBtn')?.addEventListener('click', unlockGate);
  $('#gateCodeInput')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      unlockGate();
    }
  });
  $('#openAccessPageBtn')?.addEventListener('click', openAccessPage);
}

function errorDetail(data) {
  return data && data.detail && typeof data.detail === 'object' ? data.detail : null;
}

function extractErrorMessage(data, fallback = 'API error') {
  if (!data || typeof data !== 'object') return fallback;
  if (typeof data.detail === 'string' && data.detail) return data.detail;
  if (data.detail && typeof data.detail === 'object') {
    if (typeof data.detail.message === 'string' && data.detail.message) return data.detail.message;
    if (typeof data.detail.code === 'string' && data.detail.code) return data.detail.code;
  }
  if (typeof data.message === 'string' && data.message) return data.message;
  return fallback;
}

async function loadLocales() {
  const langs = LANGS;
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
  return LANGS.includes(raw) ? raw : 'it';
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
  const gateSel = $('#langSelectGate');
  if (homeSel) homeSel.value = currentLang;
  if (settingsSel) settingsSel.value = currentLang;
  if (gateSel) gateSel.value = currentLang;
  syncGateUI();
  renderQuota();
  renderHistory();
  renderStats();
  if (latestDecodeResult) renderDecodeResult(latestDecodeResult, latestDecodeResult.source || 'text', latestDecodeResult.input || '');
  if (latestSendResult) renderSendResult(latestSendResult, latestSendResult.input || '');
}

function bindLanguageSelectors() {
  ['#langSelectHome', '#langSelectSettings', '#langSelectGate'].forEach((sel) => {
    $(sel)?.addEventListener('change', (e) => setLanguage(e.target.value));
  });
}

function setLanguage(lang) {
  currentLang = LANGS.includes(lang) ? lang : 'it';
  localStorage.setItem(STORE.lang, currentLang);
  applyTranslations();
  if (!gateState.enabled || gateState.unlocked) checkApiHealth();
}

function bindNavigation() {
  $$('[data-open]:not(.nav-btn)').forEach((btn) => btn.addEventListener('click', () => openScreen(btn.dataset.open)));
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
    openScreen('screen-decode', { scrollTop: false, restoreScroll: false });
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

  // Demo: the transcript of a Spanish voice note. The old demo recording was in Italian; a Spanish one can come
  // back here when there is one. A file chosen before is cleared, so the demo analyzes the transcript.
  $('#useDemoVoice')?.addEventListener('click', () => {
    $('#voiceTranscript').value = samples.voiceTranscript;
    const upload = $('#voiceUpload');
    if (upload) upload.value = '';
    const player = $('#voicePlayer');
    player.pause();
    player.removeAttribute('src');
    player.classList.add('hidden');
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
  $('#lockAccessBtn')?.addEventListener('click', lockGate);
}

async function checkApiHealth() {
  if (gateState.enabled && !gateState.unlocked) return;
  const badge = $('#apiStatusBadge');
  const detail = $('#apiStatusDetail');
  if (badge) {
    badge.textContent = t('home.health.checking.badge');
    badge.className = 'badge waiting';
  }
  if (detail) detail.textContent = t('home.health.checking.detail');
  try {
    // /api/health only says the server is up; the gated config tells what is available.
    const res = await fetch('/api/config');
    if (res.status === 401) {
      forceGateLock();
      return;
    }
    if (!res.ok) throw new Error('config unavailable');
    const data = await res.json();
    applyConfig(data);
    apiHealthy = capabilities.text;
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

function applyConfig(data) {
  const supports = (data && data.supports) || {};
  capabilities = { text: supports.text !== false, voice: supports.voice !== false, image: supports.image !== false };
  setRemaining(data && data.access ? data.access.remaining : null);
  applyCapabilities();
}

function applyCapabilities() {
  // Without a transcription key the voice tab keeps working with a pasted transcript.
  const voiceOff = !capabilities.voice;
  const upload = $('#voiceUpload');
  if (upload) {
    upload.classList.toggle('hidden', voiceOff);
    upload.disabled = voiceOff;
    if (voiceOff) upload.value = '';
  }
  $('#voiceUnavailableNote')?.classList.toggle('hidden', !voiceOff);
}

function setRemaining(value) {
  const number = typeof value === 'string' ? Number(value) : value;
  remainingAnalyses = Number.isInteger(number) && number >= 0 ? number : null;
  renderQuota();
}

function updateRemainingFromResponse(res) {
  const raw = res.headers.get('X-BYS-Remaining');
  if (raw !== null && /^\d+$/.test(raw)) setRemaining(Number(raw));
}

function renderQuota() {
  const target = $('#quotaInfo');
  if (!target) return;
  if (remainingAnalyses === null) {
    target.textContent = '';
    target.classList.add('hidden');
    return;
  }
  const n = remainingAnalyses;
  target.textContent = n === 0 ? t('home.quota.none') : n === 1 ? t('home.quota.one') : t('home.quota.left', { n });
  target.classList.remove('hidden');
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
  if (!('serviceWorker' in navigator)) return;
  const register = () => navigator.serviceWorker.register('./sw.js').catch(console.warn);
  // init() awaits the locales first, so the load event has often fired already.
  if (document.readyState === 'complete') register();
  else window.addEventListener('load', register, { once: true });
}

function finishOnboarding() {
  localStorage.setItem(STORE.onboarding, '1');
  openScreen('screen-home');
}

function saveActiveScreenScroll() {
  const active = $('.screen.active');
  if (!active?.id) return;
  active.dataset.savedScroll = String(Math.max(0, Math.round(window.scrollY || 0)));
}

function getSavedScreenScroll(id) {
  const screen = $('#' + id);
  if (!screen) return null;
  const value = Number(screen.dataset.savedScroll);
  return Number.isFinite(value) ? value : null;
}

function openScreen(id, options = {}) {
  const { scrollTop = true, behavior = 'auto', restoreScroll = true, scrollTarget = null } = options;
  saveActiveScreenScroll();
  $$('.screen').forEach((screen) => screen.classList.remove('active'));
  const nextScreen = $('#' + id);
  nextScreen?.classList.add('active');
  $$('.nav-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.open === id));
  const showNav = ['screen-home', 'screen-history', 'screen-settings'].includes(id);
  $('.bottom-nav')?.classList.toggle('hidden', !showNav);
  requestAnimationFrame(() => {
    if (scrollTarget) {
      scrollSectionIntoView(scrollTarget);
      return;
    }
    const savedScroll = restoreScroll ? getSavedScreenScroll(id) : null;
    if (savedScroll !== null) {
      window.scrollTo({ top: savedScroll, behavior });
      return;
    }
    if (scrollTop) {
      window.scrollTo({ top: 0, behavior });
    }
  });
}

function scrollSectionIntoView(target) {
  const el = typeof target === 'string' ? $(target) : target;
  if (!el) return;
  const parentScreen = el.closest('.screen');
  if (parentScreen && !parentScreen.classList.contains('active')) return;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
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
    showDecodeError(error);
  }
}

async function runDecodeVoice() {
  showLoadingDecode(t('results.loading.voice'));
  try {
    const form = new FormData();
    const file = capabilities.voice ? $('#voiceUpload')?.files?.[0] : null;
    const transcript = $('#voiceTranscript')?.value?.trim();
    if (file) form.append('file', file);
    if (transcript) form.append('transcript', transcript);
    form.append('lang', currentLang);
    if (!file && !transcript) throw new Error(t('error.voice_missing'));
    const result = await apiForm('/api/decode/audio', form);
    latestDecodeResult = result;
    renderDecodeResult(result, 'voice', result.transcript || transcript || '');
    saveHistory({ type:'decode', title: result.verdict, subtitle: sourceLabel('voice'), createdAt: new Date().toISOString(), payload: { ...result, source:'voice', input: result.transcript || transcript || '' } });
  } catch (error) { showDecodeError(error); }
}

async function runDecodeImage() {
  showLoadingDecode(t('results.loading.image'));
  try {
    const form = new FormData();
    const file = $('#imageUpload')?.files?.[0];
    const extractedText = $('#imageExtractedText')?.value?.trim();
    if (!file && !extractedText) throw new Error(t('error.image_missing'));
    if (file) {
      const upload = await prepareImageUpload(file);
      form.append('file', upload, upload.name || 'screenshot.jpg');
    }
    if (extractedText) form.append('extracted_text', extractedText);
    form.append('lang', currentLang);
    const result = await apiForm('/api/decode/image', form);
    latestDecodeResult = result;
    renderDecodeResult(result, 'image', result.extracted_text || extractedText || '');
    saveHistory({ type:'decode', title: result.verdict, subtitle: sourceLabel('image'), createdAt: new Date().toISOString(), payload: { ...result, source:'image', input: result.extracted_text || extractedText || '' } });
  } catch (error) { showDecodeError(error); }
}

async function runSendAnalysis(text) {
  showLoadingSend(t('results.loading.send'));
  try {
    const result = await apiJson('/api/score/text', { text, lang: currentLang });
    latestSendResult = result;
    renderSendResult(result, text);
    saveHistory({ type:'send', title:`${result.score}/100 — ${result.label}`, subtitle:t('send.title'), createdAt:new Date().toISOString(), payload: result });
  } catch (error) { showSendError(error); }
}

// Screenshots are downscaled before upload (long edge 1600 px, JPEG ~0.85): faster and cheaper.
async function prepareImageUpload(file) {
  let image;
  try {
    image = await loadImageSource(file);
  } catch {
    return file; // the browser cannot decode it; the server decides whether the format is supported
  }
  try {
    if (!image.width || !image.height) return file;
    const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(image.width, image.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(image.width * scale));
    canvas.height = Math.max(1, Math.round(image.height * scale));
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff'; // JPEG has no transparency
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(image.source, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', JPEG_QUALITY));
    if (!blob) return file;
    if (scale === 1 && blob.size >= file.size && UPLOAD_IMAGE_TYPES.includes(file.type)) return file;
    return new File([blob], 'screenshot.jpg', { type: 'image/jpeg' });
  } finally {
    image.release();
  }
}

async function loadImageSource(file) {
  if (typeof createImageBitmap === 'function') {
    try {
      const bitmap = await createImageBitmap(file);
      return { source: bitmap, width: bitmap.width, height: bitmap.height, release: () => bitmap.close?.() };
    } catch {}
  }
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.src = url;
  try {
    await img.decode();
  } catch (error) {
    URL.revokeObjectURL(url);
    throw error;
  }
  return { source: img, width: img.naturalWidth, height: img.naturalHeight, release: () => URL.revokeObjectURL(url) };
}

async function apiRequest(url, init) {
  let res;
  try {
    res = await fetch(url, init);
  } catch {
    throw new Error(t('error.network'));
  }
  const data = await res.json().catch(() => ({}));
  updateRemainingFromResponse(res);
  if (res.status === 401) {
    forceGateLock();
    throw new Error(t('gate.error.required'));
  }
  if (!res.ok) throw apiError(res.status, data);
  return data;
}

function apiError(status, data) {
  const detail = errorDetail(data);
  if (detail && detail.code === 'trial_ended') {
    if (detail.accessPage) gateState.accessPage = detail.accessPage;
    const error = new Error(detail.message || t('error.trial_ended'));
    error.showAccess = true;
    return error;
  }
  // Errors raised by the endpoints carry a message localized by the server.
  if (detail && typeof detail.message === 'string' && detail.message) return new Error(detail.message);
  if (status === 413) return new Error(t('error.too_large'));
  if (status === 429) return new Error(t('error.rate_limited'));
  return new Error(t('error.generic'));
}

function apiJson(url, body) {
  return apiRequest(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
}

function apiForm(url, formData) {
  return apiRequest(url, { method:'POST', body: formData });
}

function showLoadingDecode(message) {
  const target = $('#decodeResult');
  target.classList.remove('hidden');
  target.innerHTML = `<section class="card loading-card"><div class="spinner"></div><p>${escapeHTML(message)}</p></section>`;
  scrollSectionIntoView(target);
}
function showLoadingSend(message) {
  const target = $('#sendResult');
  target.classList.remove('hidden');
  target.innerHTML = `<section class="card loading-card"><div class="spinner"></div><p>${escapeHTML(message)}</p></section>`;
  scrollSectionIntoView(target);
}
function showDecodeError(error) { renderErrorCard($('#decodeResult'), t('error.decode'), error); }
function showSendError(error) { renderErrorCard($('#sendResult'), t('error.send'), error); }
function renderErrorCard(target, title, error) {
  const message = typeof error === 'string' ? error : (error?.message || t('error.generic'));
  // Trial ended (one code per email): the way to continue is to write to us.
  const contactButton = error?.showAccess ? `<button class="ghost" data-contact>${escapeHTML(t('error.contact'))}</button>` : '';
  target.classList.remove('hidden');
  target.innerHTML = `<section class="card error-card"><strong>${escapeHTML(title)}</strong><p>${escapeHTML(message)}</p>${contactButton}</section>`;
  target.querySelector('[data-contact]')?.addEventListener('click', openContact);
  scrollSectionIntoView(target);
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
    lastSharePayload = { title: result.verdict, subtitle: result.meaning, badge: sourceLabel(source), chips: result.flags.slice(0,3), footer: t('share.footer'), type: 'decode' };
    downloadShareCard('bys_decode_share.png');
  });
  scrollSectionIntoView(target);
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
    lastSharePayload = { title: t('share.score', { score: result.score }), subtitle: result.issue, badge: result.label, chips: topSendChips(result), footer: t('share.footer'), type: 'send' };
    downloadShareCard('bys_sendscore_share.png');
  });
  scrollSectionIntoView(target);
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
function openFromHistory(index) { const item = historyLog[index]; if (!item) return; if (item.type === 'decode') { setDecodeMode(item.payload.source || 'text'); openScreen('screen-decode', { scrollTop: false, restoreScroll: false }); renderDecodeResult(item.payload, item.payload.source || 'text', item.payload.input || ''); } else { openScreen('screen-send', { scrollTop: false, restoreScroll: false }); renderSendResult(item.payload, item.payload.input || ''); } }
function shareFromHistory(index) {
  const item = historyLog[index]; if (!item) return;
  if (item.type === 'decode') { lastSharePayload = { title:item.payload.verdict, subtitle:item.payload.meaning, badge:sourceLabel(item.payload.source || 'text'), chips:item.payload.flags.slice(0,3), footer: t('share.footer'), type:'decode' }; downloadShareCard('bys_decode_share.png'); }
  else { lastSharePayload = { title:t('share.score',{score:item.payload.score}), subtitle:item.payload.issue, badge:item.payload.label, chips:topSendChips(item.payload), footer: t('share.footer'), type:'send' }; downloadShareCard('bys_sendscore_share.png'); }
}
function renderStats() { $('#statAnalyses').textContent = historyLog.length; $('#statLastMode').textContent = historyLog[0] ? (historyLog[0].type === 'decode' ? t('misc.received') : t('misc.send')) : '—'; }

function downloadShareCard(filename) {
  if (!lastSharePayload) return;
  const canvas = $('#shareCanvas'); const ctx = canvas.getContext('2d'); ctx.clearRect(0,0,canvas.width,canvas.height);
  const gradient = ctx.createLinearGradient(0,0,0,canvas.height); gradient.addColorStop(0,'#14203f'); gradient.addColorStop(1,'#0a1020'); ctx.fillStyle = gradient; ctx.fillRect(0,0,canvas.width,canvas.height);
  const glow = ctx.createRadialGradient(canvas.width*0.2, canvas.height*0.15, 40, canvas.width*0.2, canvas.height*0.15, 520); glow.addColorStop(0,'rgba(138, 162, 255, 0.45)'); glow.addColorStop(1,'rgba(138, 162, 255, 0)'); ctx.fillStyle = glow; ctx.fillRect(0,0,canvas.width,canvas.height);
  roundRect(ctx,56,80,canvas.width-112,canvas.height-160,42,'rgba(255,255,255,0.06)','rgba(255,255,255,0.10)');

  ctx.fillStyle = '#c3d0ff';
  ctx.font = '600 40px Inter, Arial';
  ctx.fillText(String(lastSharePayload.badge).toUpperCase(),96,160);

  const titleBottom = fitWrappedText(ctx, lastSharePayload.title, 96, 260, canvas.width-192, {
    color: '#ffffff',
    weight: 800,
    maxFontSize: 88,
    minFontSize: 56,
    lineHeightFactor: 1.02,
    maxLines: 4
  });

  const subtitleBottom = fitWrappedText(ctx, lastSharePayload.subtitle, 96, Math.max(460, titleBottom + 72), canvas.width-192, {
    color: '#b8c3da',
    weight: 500,
    maxFontSize: 42,
    minFontSize: 30,
    lineHeightFactor: 1.15,
    maxLines: 4
  });

  let y = Math.max(720, subtitleBottom + 120);
  lastSharePayload.chips.forEach((ch) => {
    roundRect(ctx,96,y-36,520,76,38,'rgba(138,162,255,0.16)','rgba(138,162,255,0.24)');
    ctx.fillStyle='#eff3ff';
    ctx.font='600 34px Inter, Arial';
    ctx.fillText(ch,126,y+10);
    y += 98;
  });

  roundRect(ctx,96,1440,canvas.width-192,220,36,'rgba(255,255,255,0.04)','rgba(255,255,255,0.10)');
  ctx.fillStyle='#ffffff'; ctx.font='800 56px Inter, Arial'; ctx.fillText(t('app.name'),132,1525);
  fitWrappedText(ctx, t('share.decode'), 132, 1590, canvas.width-264, {
    color: '#b8c3da',
    weight: 500,
    maxFontSize: 34,
    minFontSize: 28,
    lineHeightFactor: 1.1,
    maxLines: 2
  });
  ctx.fillStyle='#8ea4ff'; ctx.font='600 28px Inter, Arial'; ctx.fillText(lastSharePayload.footer,132,1662);

  const link = document.createElement('a'); link.download = filename; link.href = canvas.toDataURL('image/png'); link.click();
}

function roundRect(ctx, x, y, w, h, r, fill, stroke) { ctx.beginPath(); ctx.moveTo(x+r,y); ctx.arcTo(x+w,y,x+w,y+h,r); ctx.arcTo(x+w,y+h,x,y+h,r); ctx.arcTo(x,y+h,x,y,r); ctx.arcTo(x,y,x+w,y,r); ctx.closePath(); if (fill){ctx.fillStyle=fill;ctx.fill();} if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=2;ctx.stroke();}}
function wrapTextLines(ctx, text, maxWidth) {
  const words = String(text ?? '').trim().split(/\s+/).filter(Boolean);
  if (!words.length) return [''];
  const lines = [];
  let line = '';
  for (let i = 0; i < words.length; i++) {
    const testLine = line ? `${line} ${words[i]}` : words[i];
    if (ctx.measureText(testLine).width > maxWidth && line) {
      lines.push(line);
      line = words[i];
    } else {
      line = testLine;
    }
  }
  if (line) lines.push(line);
  return lines;
}
function clampTextToWidth(ctx, text, maxWidth) {
  let out = String(text ?? '').trim();
  if (ctx.measureText(out).width <= maxWidth) return out;
  while (out.length > 1 && ctx.measureText(`${out}…`).width > maxWidth) out = out.slice(0, -1).trim();
  return `${out}…`;
}
function fitWrappedText(ctx, text, x, y, maxWidth, options = {}) {
  const { color = '#ffffff', weight = 800, maxFontSize = 88, minFontSize = 30, lineHeightFactor = 1.08, maxLines = 4 } = options;
  let fontSize = maxFontSize;
  let lines = [];
  while (fontSize >= minFontSize) {
    ctx.font = `${weight} ${fontSize}px Inter, Arial`;
    lines = wrapTextLines(ctx, text, maxWidth);
    if (lines.length <= maxLines) break;
    fontSize -= 4;
  }
  ctx.font = `${weight} ${fontSize}px Inter, Arial`;
  lines = wrapTextLines(ctx, text, maxWidth);
  if (lines.length > maxLines) {
    lines = lines.slice(0, maxLines);
    lines[maxLines - 1] = clampTextToWidth(ctx, lines[maxLines - 1], maxWidth);
  }
  const lineHeight = Math.round(fontSize * lineHeightFactor);
  ctx.fillStyle = color;
  lines.forEach((line, idx) => ctx.fillText(line, x, y + (idx * lineHeight)));
  return y + ((lines.length - 1) * lineHeight);
}
function wrapText(ctx, text, x, y, maxWidth, lineHeight) { const lines = wrapTextLines(ctx, text, maxWidth); lines.forEach((line, idx) => ctx.fillText(line, x, y + (idx * lineHeight))); }
function sourceLabel(source) { return t(`source.${source}`); }
function persistHistory(){ localStorage.setItem(STORE.history, JSON.stringify(historyLog)); }
function hardReset(){ localStorage.removeItem(STORE.onboarding); localStorage.removeItem(STORE.history); localStorage.removeItem(STORE.settings); localStorage.removeItem(STORE.lang); historyLog=[]; currentLang = detectBrowserLang(); applyTranslations(); renderHistory(); renderStats(); if (gateState.enabled && !gateState.unlocked) { showGateScreen(''); return; } openScreen('screen-onboarding-1'); }
function loadJSON(key, fallback){ try{ return JSON.parse(localStorage.getItem(key)||'null') ?? fallback; }catch{ return fallback; } }
function formatDate(iso){ try { return new Date(iso).toLocaleString(DATE_LOCALES[currentLang] || 'en-US', { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit' }); } catch { return iso; } }
async function copyText(text){ try{ await navigator.clipboard.writeText(text); } catch { const ta=document.createElement('textarea'); ta.value=text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); } }
function escapeHTML(str){ return String(str ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;'); }
