
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

// Free examples (samples/<lang>.json, built by tools/build_samples.py): Spanish messages (the app is presented
// first in the Canary Islands) with results already analyzed in each app language. They are shown without calling
// the server, so an example never uses up an analysis; analyzing an example's text unchanged shows it again.
let examples = null; // { lang, data }
// Recording for the voice demo, if any: a 16 kHz WAV plays in every browser. None for now: the voice example
// shows the transcript of a Spanish voice note.
const DEMO_VOICE = '';
// From this many analyses left, the Home and each new result say how many remain.
const LOW_QUOTA = 5;
// One analysis at a time: the buttons are locked while one runs, so a double tap does not use up two analyses.
let analysisBusy = false;
const ANALYSIS_BUTTONS = [
  '#decodeTextAnalyze', '#decodeVoiceAnalyze', '#decodeImageAnalyze', '#sendAnalyze',
  '#decodeTextSample', '#useDemoVoice', '#useDemoScreenshot', '#sendSample', '#runQuickDemo',
];
// The voice note and the screenshot to analyze: chosen, recorded or shared from another app. When one is there it
// is analyzed, not the pasted text.
let voiceFile = null;
let imageFile = null;
// «Record»: a voice note played on the loudspeaker, through the microphone, at most 5 minutes.
const MAX_RECORDING_SECONDS = 300;
let recorder = null;
let recordTimer = null;
// «Share → Before You Send» (Android, installed app): sw.js keeps the shared file or text in this cache and opens
// ./?shared=1; the app takes it once, after the code screen if needed.
const SHARE_CACHE = 'bys-share';
let pendingShare = null;

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
  pendingShare = await takeSharedContent();
  if (LANGS.includes(link.lang)) {
    currentLang = link.lang;
    localStorage.setItem(STORE.lang, currentLang);
  }
  try {
    await loadLocales([currentLang]);
  } catch (error) {
    // The texts stay hidden until they are translated (no Italian flash); without the locales show the page as is.
    document.documentElement.classList.remove('i18n-pending');
    throw error;
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
  if (pendingShare) {
    const share = pendingShare;
    pendingShare = null;
    if (applySharedContent(share)) return;
  }
  const done = localStorage.getItem(STORE.onboarding) === '1';
  openScreen(done ? 'screen-home' : 'screen-onboarding-1');
}

async function takeSharedContent() {
  if (!new URLSearchParams(location.search).has('shared')) return null;
  history.replaceState(null, '', location.pathname);
  if (!('caches' in window)) return null;
  try {
    const cache = await caches.open(SHARE_CACHE);
    const meta = await cache.match('/__shared/meta');
    if (!meta) return null;
    const info = await meta.json();
    const stored = await cache.match('/__shared/file');
    const file = stored ? new File([await stored.blob()], info.name || 'shared', { type: info.type || '' }) : null;
    await caches.delete(SHARE_CACHE);
    return { file, text: String(info.text || '') };
  } catch {
    return null;
  }
}

// What was shared goes in the right tab, ready: the person taps «Analyze» (an analysis is never used up on its own).
function applySharedContent({ file, text }) {
  if (file && isAudioFile(file)) {
    setDecodeMode('voice');
    setVoiceFile(file, file.name);
  } else if (file && (file.type || '').startsWith('image/')) {
    setDecodeMode('image');
    setImageFile(file);
  } else if (text.trim()) {
    setDecodeMode('text');
    $('#decodeTextInput').value = text.trim().slice(0, 6000);
  } else {
    return false;
  }
  openScreen('screen-decode', { scrollTop: true, restoreScroll: false });
  const target = $('#decodeResult');
  target.innerHTML = `<p class="result-notice" role="status">${escapeHTML(t('decode.sharedReady'))}</p>`;
  target.classList.remove('hidden');
  return true;
}

function isAudioFile(file) {
  return (file.type || '').startsWith('audio/') || /\.(opus|ogg|oga|m4a|mp3|wav|webm|aac)$/i.test(file.name || '');
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

function setGateError(message = '', { contact = false } = {}) {
  const target = $('#gateError');
  $('#gateContactBtn')?.classList.toggle('hidden', !(message && contact));
  if (!target) return;
  if (!message) {
    target.textContent = '';
    target.classList.add('hidden');
    return;
  }
  target.textContent = message;
  target.classList.remove('hidden');
}

// Code used up or no longer active: detail.reason from the server is "exhausted", "expired" or "revoked".
function trialEndedTitle(reason) {
  return t(reason === 'exhausted' ? 'trial.used_up.title' : 'trial.inactive.title');
}

function trialEndedMessage(detail) {
  if (!detail.reason) return detail.message || t('error.trial_ended');
  return `${trialEndedTitle(detail.reason)}. ${t('trial.next')}`;
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
        const ended = new Error(trialEndedMessage(detail));
        ended.showAccess = true;
        throw ended;
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
    setGateError(error.message || t('gate.error.invalid'), { contact: Boolean(error.showAccess) });
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = t('gate.unlock');
    }
  }
}

async function lockGate() {
  // Without the code (it came by email) there is no way back in: ask first.
  if (!window.confirm(t('confirm.lock'))) return;
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
  $('#gateContactBtn')?.addEventListener('click', openContact);
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

// Only the texts in use (plus English, the fallback for a missing key): one or two small files when the app opens
// instead of six; another language is loaded when it is chosen.
async function loadLocales(langs) {
  const wanted = [...new Set([...langs, 'en'])].filter((lang) => LANGS.includes(lang) && !locales[lang]);
  const loaded = await Promise.all(wanted.map(async (lang) => {
    const res = await fetch(`locales/${lang}.json`);
    if (!res.ok) throw new Error(`locales/${lang}.json: ${res.status}`);
    return [lang, await res.json()];
  }));
  loaded.forEach(([lang, data]) => { locales[lang] = data; });
  if (!locales[currentLang]) currentLang = 'en';
}

// The phone's language when the app has it; otherwise Spanish, the language of its first market (Luca, 27/10).
const FALLBACK_LANG = 'es';

function detectBrowserLang() {
  const raw = (navigator.language || FALLBACK_LANG).slice(0, 2).toLowerCase();
  return LANGS.includes(raw) ? raw : FALLBACK_LANG;
}

function t(key, vars = {}) {
  const dict = locales[currentLang] || locales[FALLBACK_LANG] || {};
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
  $$('[data-i18n-aria-label]').forEach((el) => {
    el.setAttribute('aria-label', t(el.dataset.i18nAriaLabel));
  });
  // The contact address inside a text becomes a link (the text itself is set as plain text above).
  $$('[data-link-email]').forEach((el) => {
    const parts = el.textContent.split(CONTACT_EMAIL);
    if (parts.length < 2) return;
    el.replaceChildren(...parts.flatMap((part, i) => {
      if (i === 0) return [part];
      const link = document.createElement('a');
      link.href = `mailto:${CONTACT_EMAIL}`;
      link.textContent = CONTACT_EMAIL;
      return [link, part];
    }));
  });
  document.documentElement.classList.remove('i18n-pending');
  const homeSel = $('#langSelectHome');
  const settingsSel = $('#langSelectSettings');
  const gateSel = $('#langSelectGate');
  if (homeSel) homeSel.value = currentLang;
  if (settingsSel) settingsSel.value = currentLang;
  if (gateSel) gateSel.value = currentLang;
  syncGateUI();
  renderQuota();
  renderHistory();
  renderShareTip();
  $$('.field-hint').forEach((hint) => hint.classList.add('hidden'));
  // An example on screen comes back in the new language from setLanguage().
  if (latestDecodeResult && !latestDecodeResult.example) renderDecodeResult(latestDecodeResult, latestDecodeResult.source || 'text', latestDecodeResult.input || '');
  if (latestSendResult && !latestSendResult.example) renderSendResult(latestSendResult, latestSendResult.input || '');
}

function bindLanguageSelectors() {
  ['#langSelectHome', '#langSelectSettings', '#langSelectGate'].forEach((sel) => {
    $(sel)?.addEventListener('change', (e) => setLanguage(e.target.value));
  });
}

async function setLanguage(lang) {
  const next = LANGS.includes(lang) ? lang : FALLBACK_LANG;
  try {
    await loadLocales([next]);
  } catch {
    // Texts not reachable (no network): stay in the current language, and say so in the selectors.
    ['#langSelectHome', '#langSelectSettings', '#langSelectGate'].forEach((sel) => { const el = $(sel); if (el) el.value = currentLang; });
    return;
  }
  currentLang = next;
  localStorage.setItem(STORE.lang, currentLang);
  applyTranslations();
  if (!gateState.enabled || gateState.unlocked) checkApiHealth();
  const shown = [latestDecodeResult, latestSendResult].filter((result) => result?.example);
  if (shown.length && await useExamples()) shown.forEach((result) => showExample(result.example));
}

function bindNavigation() {
  $$('[data-open]:not(.nav-btn)').forEach((btn) => btn.addEventListener('click', () => openScreen(btn.dataset.open)));
  $$('[data-back]').forEach((btn) => btn.addEventListener('click', () => openScreen(btn.dataset.back)));
  $$('.nav-btn').forEach((btn) => btn.addEventListener('click', () => openScreen(btn.dataset.open)));
}

function bindOnboarding() {
  $$('[data-next-onboarding]').forEach((btn) => btn.addEventListener('click', () => openScreen(`screen-onboarding-${btn.dataset.nextOnboarding}`)));
  $$('[data-prev-onboarding]').forEach((btn) => btn.addEventListener('click', () => openScreen(`screen-onboarding-${btn.dataset.prevOnboarding}`)));
  $$('[data-skip-onboarding]').forEach((btn) => btn.addEventListener('click', finishOnboarding));
  $('#finishOnboarding')?.addEventListener('click', finishOnboarding);
}

function bindHome() {
  $('#runQuickDemo')?.addEventListener('click', () => withAnalysisLock(async () => {
    setDecodeMode('text');
    openScreen('screen-decode', { scrollTop: false, restoreScroll: false });
    await fillExample('decodeText', '#decodeTextInput');
  }));
}

function bindDecode() {
  $$('.tab').forEach((tab) => tab.addEventListener('click', () => setDecodeMode(tab.dataset.decodeMode)));
  $('#decodeTextSample')?.addEventListener('click', () => withAnalysisLock(() => fillExample('decodeText', '#decodeTextInput')));
  $('#decodeTextAnalyze')?.addEventListener('click', () => withAnalysisLock(async () => {
    const field = $('#decodeTextInput');
    const text = field.value.trim();
    if (!text) return showFieldHint(field, t('hint.decode_empty'));
    if (isExampleInput('decodeText', text)) return showExample('decodeText');
    await runDecodeText(text);
  }));

  // Voice example: the transcript of a Spanish voice note (and the recording, if DEMO_VOICE is set) with its ready
  // result. A voice note chosen before is cleared, so the example's text is what counts.
  $('#useDemoVoice')?.addEventListener('click', () => withAnalysisLock(async () => {
    if (!await fillExample('voice', '#voiceTranscript')) return;
    clearVoiceFile();
    $('#voiceTextBox').open = true;
    if (DEMO_VOICE) {
      const player = $('#voicePlayer');
      player.src = DEMO_VOICE;
      player.classList.remove('hidden');
    }
  }));

  $('#voiceUpload')?.addEventListener('change', (e) => {
    const file = e.target.files?.[0];
    if (file) setVoiceFile(file, file.name);
  });
  $('#voiceFileRemove')?.addEventListener('click', clearVoiceFile);
  $('#voiceRecordBtn')?.addEventListener('click', startRecording);
  $('#voiceStopBtn')?.addEventListener('click', stopRecording);

  $('#decodeVoiceAnalyze')?.addEventListener('click', () => withAnalysisLock(runDecodeVoice));

  $('#useDemoScreenshot')?.addEventListener('click', () => withAnalysisLock(async () => {
    if (!await fillExample('image', '#imageExtractedText')) return;
    clearImageFile();
    $('#imageTextBox').open = true;
    $('#imagePreview').src = 'assets/chat_demo_final.png';
    $('#imagePreview').classList.remove('hidden');
  }));

  $('#imageUpload')?.addEventListener('change', (e) => {
    const file = e.target.files?.[0];
    if (file) setImageFile(file);
  });
  $('#imageFileRemove')?.addEventListener('click', clearImageFile);

  $('#decodeImageAnalyze')?.addEventListener('click', () => withAnalysisLock(runDecodeImage));
}

function bindSendScore() {
  // The example has no message it replies to: that field is emptied and closed.
  $('#sendSample')?.addEventListener('click', () => withAnalysisLock(async () => {
    if (!await fillExample('send', '#sendInput')) return;
    $('#sendReplyTo').value = '';
    $('#sendReplyToBox').open = false;
  }));
  $('#sendAnalyze')?.addEventListener('click', () => withAnalysisLock(async () => {
    const field = $('#sendInput');
    const text = field.value.trim();
    const replyTo = $('#sendReplyTo').value.trim();
    if (!text) return showFieldHint(field, t('hint.send_empty'));
    if (!replyTo && isExampleInput('send', text)) return showExample('send');
    await runSendAnalysis(text, replyTo);
  }));
}

function bindSettings() {
  $('#rerunOnboarding')?.addEventListener('click', () => {
    localStorage.removeItem(STORE.onboarding);
    openScreen('screen-onboarding-1');
  });
  $('#hardResetBtn')?.addEventListener('click', () => {
    if (window.confirm(t('confirm.reset'))) hardReset();
  });
  $('#clearHistoryBtn')?.addEventListener('click', () => {
    if (!historyLog.length || !window.confirm(t('confirm.clearHistory'))) return;
    historyLog = [];
    persistHistory();
    renderHistory();
  });
  $('#refreshApiHealth')?.addEventListener('click', checkApiHealth);
  $('#lockAccessBtn')?.addEventListener('click', lockGate);
  $('#settingsContactBtn')?.addEventListener('click', openContact);
  $('#trialContactBtn')?.addEventListener('click', openContact);
}

// --- free examples and the one-analysis-at-a-time lock -----------------------------------------------------------

async function withAnalysisLock(task) {
  if (analysisBusy) return;
  analysisBusy = true;
  ANALYSIS_BUTTONS.forEach((sel) => { const btn = $(sel); if (btn) btn.disabled = true; });
  try {
    await task();
  } finally {
    analysisBusy = false;
    ANALYSIS_BUTTONS.forEach((sel) => { const btn = $(sel); if (btn) btn.disabled = false; });
  }
}

// The examples of the current language, loaded once; null (with the error shown) when they cannot be loaded.
async function useExamples(errorTarget = null) {
  if (examples?.lang === currentLang) return examples.data;
  const lang = currentLang;
  try {
    const res = await fetch(`samples/${lang}.json`);
    if (!res.ok) throw new Error(String(res.status));
    examples = { lang, data: await res.json() };
    return examples.data;
  } catch {
    if (errorTarget) renderErrorCard(errorTarget, t('error.decode'), t('error.network'));
    return null;
  }
}

// Puts an example's text in the field and shows its ready result.
async function fillExample(kind, fieldSel) {
  const data = await useExamples($(kind === 'send' ? '#sendResult' : '#decodeResult'));
  if (!data?.[kind]) return false;
  const field = $(fieldSel);
  field.value = data[kind].input;
  clearFieldHint(field);
  showExample(kind);
  return true;
}

function isExampleInput(kind, value) {
  return Boolean(value) && examples?.data?.[kind]?.input === value;
}

function showExample(kind) {
  const example = examples?.data?.[kind];
  if (!example) return;
  const result = { ...example.result, example: kind };
  if (kind === 'send') {
    latestSendResult = result;
    renderSendResult(result, example.input);
  } else {
    latestDecodeResult = result;
    renderDecodeResult(result, result.source || 'text', example.input);
  }
}

// A short hint under an empty field, instead of an analysis.
function showFieldHint(field, message) {
  if (!field) return;
  let hint = field.nextElementSibling;
  if (!hint?.classList.contains('field-hint')) {
    hint = document.createElement('p');
    hint.className = 'field-hint';
    hint.setAttribute('role', 'alert');
    field.insertAdjacentElement('afterend', hint);
    field.addEventListener('input', () => clearFieldHint(field));
  }
  hint.textContent = message;
  hint.classList.remove('hidden');
  field.focus();
}

function clearFieldHint(field) {
  const hint = field?.nextElementSibling;
  if (hint?.classList.contains('field-hint')) hint.classList.add('hidden');
}

// --- voice note and screenshot: chosen, recorded or shared -------------------------------------------------------

function setVoiceFile(file, label) {
  voiceFile = file;
  $('#voiceFileName').textContent = label || file.name || t('decode.voice.recorded');
  $('#voiceFileChip').classList.remove('hidden');
  const player = $('#voicePlayer');
  if (player.src.startsWith('blob:')) URL.revokeObjectURL(player.src);
  player.src = URL.createObjectURL(file);
  player.classList.remove('hidden');
  clearFieldHint($('#voicePickRow'));
}

function clearVoiceFile() {
  voiceFile = null;
  $('#voiceUpload').value = '';
  $('#voiceFileChip').classList.add('hidden');
  const player = $('#voicePlayer');
  player.pause();
  if (player.src.startsWith('blob:')) URL.revokeObjectURL(player.src);
  player.removeAttribute('src');
  player.classList.add('hidden');
}

function setImageFile(file) {
  imageFile = file;
  $('#imageFileName').textContent = file.name || t('source.image');
  $('#imageFileChip').classList.remove('hidden');
  const preview = $('#imagePreview');
  if (preview.src.startsWith('blob:')) URL.revokeObjectURL(preview.src);
  preview.src = URL.createObjectURL(file);
  preview.classList.remove('hidden');
  clearFieldHint($('#imagePickRow'));
}

function clearImageFile() {
  imageFile = null;
  $('#imageUpload').value = '';
  $('#imageFileChip').classList.add('hidden');
  const preview = $('#imagePreview');
  if (preview.src.startsWith('blob:')) URL.revokeObjectURL(preview.src);
  preview.classList.add('hidden');
}

function canRecord() {
  return Boolean(window.MediaRecorder && navigator.mediaDevices?.getUserMedia);
}

async function startRecording() {
  if (recorder || !canRecord()) return;
  const pick = $('#voicePickRow');
  clearFieldHint(pick);
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    showFieldHint(pick, t(error?.name === 'NotAllowedError' ? 'decode.voice.micDenied' : 'decode.voice.micMissing'));
    return;
  }
  // Opus in WebM (Chrome, Android) or AAC in MP4 (Safari, iPhone): both are read by the transcription.
  const mimeType = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus', 'audio/webm']
    .find((type) => MediaRecorder.isTypeSupported?.(type));
  const chunks = [];
  const started = Date.now();
  const current = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  recorder = current;
  current.addEventListener('dataavailable', (event) => { if (event.data?.size) chunks.push(event.data); });
  current.addEventListener('stop', () => {
    stream.getTracks().forEach((track) => track.stop());
    clearInterval(recordTimer);
    recorder = null;
    showRecording(false);
    if (!chunks.length) return;
    const type = (current.mimeType || mimeType || 'audio/webm').split(';')[0];
    const extension = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm';
    const seconds = Math.round((Date.now() - started) / 1000);
    setVoiceFile(new File(chunks, `recording.${extension}`, { type }), `${t('decode.voice.recorded')} · ${formatDuration(seconds)}`);
  });
  clearVoiceFile();
  current.start(1000);
  showRecording(true);
  const tick = () => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    $('#voiceRecordingTime').textContent = formatDuration(seconds);
    if (seconds >= MAX_RECORDING_SECONDS) stopRecording();
  };
  tick();
  recordTimer = setInterval(tick, 500);
}

// Stops the recording; the promise resolves once the recorded file is ready.
function stopRecording() {
  if (!recorder || recorder.state === 'inactive') return Promise.resolve();
  const done = new Promise((resolve) => recorder.addEventListener('stop', () => setTimeout(resolve, 0), { once: true }));
  recorder.stop();
  return done;
}

function showRecording(on) {
  $('#voiceRecording').classList.toggle('hidden', !on);
  $('#voicePickRow').classList.toggle('hidden', on);
}

function formatDuration(seconds) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

// Android: sharing from WhatsApp works once the app is installed; the tip says so (iPhone web apps cannot receive
// shares, so nothing is shown there).
function renderShareTip() {
  const android = /Android/i.test(navigator.userAgent);
  const installed = window.matchMedia?.('(display-mode: standalone)').matches;
  $$('[data-share-tip]').forEach((tip) => {
    tip.textContent = android ? t(installed ? 'decode.shareTip.installed' : 'decode.shareTip.install') : '';
    tip.classList.toggle('hidden', !android);
  });
}

// The Home says something about the service only when there is a problem, with a way to try again.
async function checkApiHealth() {
  if (gateState.enabled && !gateState.unlocked) return;
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
    showServiceNotice(apiHealthy ? '' : t('home.service.unavailable'));
  } catch (error) {
    apiHealthy = false;
    showServiceNotice(t('home.service.offline'));
  }
}

function showServiceNotice(message) {
  const notice = $('#serviceNotice');
  if (!notice) return;
  $('#serviceNoticeText').textContent = message;
  notice.classList.toggle('hidden', !message);
}

function applyConfig(data) {
  const supports = (data && data.supports) || {};
  capabilities = { text: supports.text !== false, voice: supports.voice !== false, image: supports.image !== false };
  // "Free trial" only on trial codes: not for paid codes or the shared tester code.
  $('#homeEyebrow')?.classList.toggle('hidden', (data && data.access && data.access.plan) !== 'trial');
  setRemaining(data && data.access ? data.access.remaining : null);
  applyCapabilities();
}

function applyCapabilities() {
  // Without a transcription key the voice tab keeps working with a pasted transcript.
  const voiceOff = !capabilities.voice;
  $('#voicePickRow')?.classList.toggle('hidden', voiceOff || Boolean(recorder));
  $('#voiceRecordBtn')?.classList.toggle('hidden', voiceOff || !canRecord());
  const upload = $('#voiceUpload');
  if (upload) upload.disabled = voiceOff;
  if (voiceOff) {
    clearVoiceFile();
    $('#voiceTextBox').open = true;
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

// Analyses left, on the Home and on both analysis screens: a warning from LOW_QUOTA down; at 0 the Home shows the
// end-of-trial card and the analysis screens say the analyses are used up.
function renderQuota() {
  const n = remainingAnalyses;
  $('#trialEndedHome')?.classList.toggle('hidden', n !== 0);
  if (n === 0) $('#trialEndedTitle').textContent = trialEndedTitle('exhausted');
  const text = n === null ? '' : n === 0 ? trialEndedTitle('exhausted')
    : n === 1 ? t('home.quota.one') : n <= LOW_QUOTA ? t('home.quota.low', { n }) : t('home.quota.left', { n });
  const home = $('#quotaInfo');
  if (home) {
    home.textContent = n === 0 ? '' : text;
    home.classList.toggle('hidden', n === null || n === 0);
    home.classList.toggle('warn', n !== null && n <= LOW_QUOTA);
  }
  $$('.quota-mirror').forEach((pill) => {
    pill.textContent = text;
    pill.classList.toggle('hidden', n === null);
    pill.classList.toggle('warn', n !== null && n <= LOW_QUOTA);
  });
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
  if (id !== 'screen-decode') stopRecording(); // leaving the screen ends a recording (it is kept as the voice note)
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
  // Another kind of input: the result of the previous one goes (it stays in the history).
  if (mode !== currentDecodeMode) {
    if (mode !== 'voice') stopRecording();
    latestDecodeResult = null;
    hideResult('#decodeResult');
  }
  currentDecodeMode = mode;
  $$('.tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.decodeMode === mode);
    tab.setAttribute('aria-selected', String(tab.dataset.decodeMode === mode));
  });
  ['text', 'voice', 'image'].forEach((key) => {
    $('#decode-mode-' + key)?.classList.toggle('hidden', key !== mode);
  });
}

async function runDecodeText(text) {
  showLoadingDecode(t('results.loading.decode'));
  try {
    const result = await apiJson('/api/decode/text', { text, lang: currentLang });
    latestDecodeResult = result;
    renderDecodeResult(result, 'text', text, { fresh: true });
    saveHistory({
      type: 'decode', title: result.verdict, subtitle: sourceLabel('text'), createdAt: new Date().toISOString(),
      payload: { ...result, source: 'text', input: text },
    });
  } catch (error) {
    showDecodeError(error);
  }
}

async function runDecodeVoice() {
  await stopRecording(); // «Analyze» while recording: the recording so far is the voice note
  const pick = $('#voicePickRow');
  const file = capabilities.voice ? voiceFile : null;
  const transcript = $('#voiceTranscript').value.trim();
  clearFieldHint(pick);
  if (!file && !transcript) {
    if (!capabilities.voice) return showFieldHint($('#voiceTranscript'), t('error.voice_missing'));
    return showFieldHint(pick, t('error.voice_missing'));
  }
  if (!file && isExampleInput('voice', transcript)) return showExample('voice');
  showLoadingDecode(t('results.loading.voice'));
  try {
    // The voice note when there is one, otherwise the pasted text: the server analyzes a transcript before a file.
    const form = new FormData();
    if (file) form.append('file', file, file.name || 'voice');
    else form.append('transcript', transcript);
    form.append('lang', currentLang);
    const result = await apiForm('/api/decode/audio', form);
    latestDecodeResult = result;
    renderDecodeResult(result, 'voice', result.transcript || transcript || '', { fresh: true });
    saveHistory({ type:'decode', title: result.verdict, subtitle: sourceLabel('voice'), createdAt: new Date().toISOString(), payload: { ...result, source:'voice', input: result.transcript || transcript || '' } });
  } catch (error) { showDecodeError(error); }
}

async function runDecodeImage() {
  const pick = $('#imagePickRow');
  const file = imageFile;
  const extractedText = $('#imageExtractedText').value.trim();
  clearFieldHint(pick);
  if (!file && !extractedText) return showFieldHint(pick, t('error.image_missing'));
  if (!file && isExampleInput('image', extractedText)) return showExample('image');
  showLoadingDecode(t('results.loading.image'));
  try {
    const form = new FormData();
    if (file) {
      const upload = await prepareImageUpload(file);
      form.append('file', upload, upload.name || 'screenshot.jpg');
    } else {
      form.append('extracted_text', extractedText);
    }
    form.append('lang', currentLang);
    const result = await apiForm('/api/decode/image', form);
    latestDecodeResult = result;
    renderDecodeResult(result, 'image', result.extracted_text || extractedText || '', { fresh: true });
    saveHistory({ type:'decode', title: result.verdict, subtitle: sourceLabel('image'), createdAt: new Date().toISOString(), payload: { ...result, source:'image', input: result.extracted_text || extractedText || '' } });
  } catch (error) { showDecodeError(error); }
}

async function runSendAnalysis(text, replyTo = '') {
  showLoadingSend(t('results.loading.send'));
  try {
    const result = await apiJson('/api/score/text', { text, lang: currentLang, ...(replyTo ? { reply_to: replyTo } : {}) });
    latestSendResult = result;
    renderSendResult(result, text, { fresh: true });
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
    const error = new Error(trialEndedMessage(detail));
    error.showAccess = true;
    error.trialEnded = detail.reason || null;
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
  // Trial ended (one code per email): not an error, the way to continue is to write to us.
  if (error?.trialEnded) {
    target.classList.remove('hidden');
    target.innerHTML = `<section class="card trial-card" role="status"><strong>${escapeHTML(trialEndedTitle(error.trialEnded))}</strong><p>${escapeHTML(t('trial.next'))}</p><button class="primary" data-contact>${escapeHTML(t('error.contact'))}</button></section>`;
  } else {
    const contactButton = error?.showAccess ? `<button class="ghost" data-contact>${escapeHTML(t('error.contact'))}</button>` : '';
    target.classList.remove('hidden');
    target.innerHTML = `<section class="card error-card"><strong>${escapeHTML(title)}</strong><p>${escapeHTML(message)}</p>${contactButton}</section>`;
  }
  target.querySelector('[data-contact]')?.addEventListener('click', openContact);
  scrollSectionIntoView(target);
}

// Above a result: an example says it used no analysis; a new analysis says how many are left from LOW_QUOTA down.
function resultNotice(result, fresh) {
  let message = '';
  if (result.example) message = t('results.exampleNote');
  else if (fresh && remainingAnalyses !== null && remainingAnalyses <= LOW_QUOTA) {
    const n = remainingAnalyses;
    message = n === 0 ? t('quota.notice.last') : n === 1 ? t('quota.notice.one') : t('quota.notice.left', { n });
  }
  return message ? `<p class="result-notice${result.example ? '' : ' warn'}" role="status">${escapeHTML(message)}</p>` : '';
}

function renderDecodeResult(result, source, text, { fresh = false } = {}) {
  const target = $('#decodeResult');
  target.classList.remove('hidden');
  const heard = source === 'image' && result.extracted_text
    ? `<strong>${escapeHTML(t('results.extracted'))}</strong><p class="muted">${escapeHTML(result.extracted_text)}</p>`
    : source === 'voice' && result.transcript
    ? `<strong>${escapeHTML(t('results.transcript'))}</strong><p class="muted">${escapeHTML(result.transcript)}</p>`
    : '';
  // The message in the language of the app when it is written in another one (Luca, 29/10: a voice note in Slovak).
  const translated = result.message_translation
    ? `<div class="translation"><strong>${escapeHTML(t('results.translation'))}</strong><p>${escapeHTML(result.message_translation)}</p></div>`
    : '';
  const extractedBlock = heard || translated ? `<section class="card stack-sm">${heard}${translated}</section>` : '';
  const eyebrow = result.example ? `${t('results.example')} · ${sourceLabel(source)}` : sourceLabel(source);
  target.innerHTML = `
    ${resultNotice(result, fresh)}
    ${extractedBlock}
    <section class="card stack-sm">
      <div class="result-header">
        <p class="eyebrow">${escapeHTML(eyebrow)}</p>
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
      <div class="reply-grid">${result.replies.map((r, i) => copyCard(r.style, r.text, i, r.translation)).join('')}</div>
      <button class="ghost" id="shareDecodeBtn">${escapeHTML(t('results.share'))}</button>
    </section>
    <button class="primary again-btn" data-again="decode">${escapeHTML(t('results.againDecode'))}</button>`;
  bindCopyButtons(target, result.replies.map((r) => r.text));
  $('#shareDecodeBtn')?.addEventListener('click', () => {
    lastSharePayload = { title: result.verdict, subtitle: result.meaning, badge: sourceLabel(source), chips: result.flags.slice(0,3), footer: t('share.footer'), type: 'decode' };
    shareCard('before-you-send.png');
  });
  target.querySelector('[data-again]')?.addEventListener('click', () => startOver('decode'));
  scrollSectionIntoView(target);
}

// A ready-to-send text with its own «Copy» button (replies and rewritten versions). Under a reply in another language,
// what it says in the language of the app, so the user knows what they send; «Copy» still copies only the text to send.
function copyCard(title, text, index, translation = '') {
  const meaning = translation
    ? `<div class="reply-translation"><span class="result-kicker">${escapeHTML(t('results.replyTranslation'))}</span><p>${escapeHTML(translation)}</p></div>`
    : '';
  return `<div class="reply-card"><div class="reply-head"><h4>${escapeHTML(title)}</h4><button class="ghost small copy-btn" data-copy="${index}">${escapeHTML(t('results.copy'))}</button></div><p>${escapeHTML(text)}</p>${meaning}</div>`;
}

function bindCopyButtons(target, texts) {
  $$('[data-copy]', target).forEach((btn) => btn.addEventListener('click', async () => {
    await copyText(texts[Number(btn.dataset.copy)] || '');
    $$('[data-copy]', target).forEach((other) => { other.textContent = t('results.copy'); other.classList.remove('done'); });
    btn.textContent = t('results.copied');
    btn.classList.add('done');
    setTimeout(() => { btn.textContent = t('results.copy'); btn.classList.remove('done'); }, 2000);
  }));
}

// «Analyze another message»: empty fields and no result, ready for the next one.
function startOver(kind) {
  if (kind === 'send') {
    $('#sendInput').value = '';
    $('#sendReplyTo').value = '';
    $('#sendReplyToBox').open = false;
    latestSendResult = null;
    hideResult('#sendResult');
    window.scrollTo({ top: 0 });
    $('#sendInput').focus();
    return;
  }
  ['#decodeTextInput', '#voiceTranscript', '#imageExtractedText'].forEach((sel) => { $(sel).value = ''; });
  clearVoiceFile();
  clearImageFile();
  $('#voiceTextBox').open = false;
  $('#imageTextBox').open = false;
  latestDecodeResult = null;
  hideResult('#decodeResult');
  window.scrollTo({ top: 0 });
  // Text: the cursor in the field; voice note and screenshot: the big «Choose» button is the next step.
  if (currentDecodeMode === 'text') $('#decodeTextInput').focus();
}

function hideResult(sel) {
  const target = $(sel);
  target.innerHTML = '';
  target.classList.add('hidden');
}

function renderSendResult(result, text, { fresh = false } = {}) {
  const target = $('#sendResult');
  target.classList.remove('hidden');
  const versions = ['clear', 'warm', 'firm', 'short'];
  target.innerHTML = `
    ${resultNotice(result, fresh)}
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
      <div class="reply-grid">${versions.map((key, i) => copyCard(displayLabel(key), result.rewrites[key], i, result.rewrite_translations?.[key])).join('')}</div>
      <button class="ghost" id="shareSendBtn">${escapeHTML(t('results.share'))}</button>
    </section>
    <button class="primary again-btn" data-again="send">${escapeHTML(t('results.againSend'))}</button>`;
  bindCopyButtons(target, versions.map((key) => result.rewrites[key]));
  $('#shareSendBtn')?.addEventListener('click', () => {
    lastSharePayload = { title: t('share.score', { score: result.score }), subtitle: result.issue, badge: result.label, chips: topSendChips(result), footer: t('share.footer'), type: 'send' };
    shareCard('before-you-send.png');
  });
  target.querySelector('[data-again]')?.addEventListener('click', () => startOver('send'));
  scrollSectionIntoView(target);
}

const LABEL_KEYS = {
  clear: 'rewrite.clear', warm: 'rewrite.warm', firm: 'rewrite.firm', short: 'rewrite.short',
  frustration: 'metric.frustration', tension: 'metric.frustration', clarity: 'metric.clarity', warmth: 'metric.warmth', pressure: 'metric.pressure', pressure_perceived: 'metric.pressure', interest: 'metric.interest', involvement: 'metric.interest', respect: 'metric.respect', urgency: 'metric.urgency'
};
function displayLabel(key) { return t(LABEL_KEYS[key] || key); }
function topSendChips(result) { return Object.entries(result.breakdown).sort((a,b)=>b[1]-a[1]).slice(0,3).map(([k,v])=>`${displayLabel(k)} ${v}`); }
function metricRows(obj) { return Object.entries(obj).map(([key, value]) => `<div class="metric-row"><div class="label"><span>${escapeHTML(displayLabel(key))}</span><strong>${value}</strong></div><div class="bar"><div class="fill" style="width:${value}%"></div></div></div>`).join(''); }

function saveHistory(entry) { historyLog.unshift(entry); historyLog = historyLog.slice(0, 30); persistHistory(); renderHistory(); }
// Compact cards: the whole card opens the result; the beginning of the analyzed message shows; one entry can go.
function renderHistory() {
  const target = $('#historyList');
  if (!historyLog.length) { target.innerHTML = `<div class="card empty-state">${escapeHTML(t('history.empty'))}</div>`; return; }
  target.innerHTML = historyLog.map((item, idx) => {
    const kind = item.type === 'decode' ? sourceLabel(item.payload.source || 'text') : t('send.title');
    const input = item.payload.input ? `<span class="history-input">“${escapeHTML(item.payload.input)}”</span>` : '';
    const summary = item.type === 'decode' ? item.payload.meaning : item.payload.issue;
    return `<article class="history-card"><button class="history-open" data-history-open="${idx}"><span class="meta">${formatDate(item.createdAt)} · ${escapeHTML(kind)}</span><span class="history-title">${escapeHTML(item.title)}</span>${input}<span class="history-summary">${escapeHTML(summary)}</span></button><button class="history-delete" data-history-delete="${idx}" aria-label="${escapeHTML(t('history.delete'))}" title="${escapeHTML(t('history.delete'))}">×</button></article>`;
  }).join('');
  $$('[data-history-open]', target).forEach((btn) => btn.addEventListener('click', () => openFromHistory(Number(btn.dataset.historyOpen))));
  $$('[data-history-delete]', target).forEach((btn) => btn.addEventListener('click', () => {
    if (!window.confirm(t('confirm.deleteOne'))) return;
    historyLog.splice(Number(btn.dataset.historyDelete), 1);
    persistHistory();
    renderHistory();
  }));
}
function openFromHistory(index) { const item = historyLog[index]; if (!item) return; if (item.type === 'decode') { setDecodeMode(item.payload.source || 'text'); openScreen('screen-decode', { scrollTop: false, restoreScroll: false }); renderDecodeResult(item.payload, item.payload.source || 'text', item.payload.input || ''); } else { openScreen('screen-send', { scrollTop: false, restoreScroll: false }); renderSendResult(item.payload, item.payload.input || ''); } }

// «Share»: the result card as an image through the phone's share menu; where that is missing, it is downloaded.
async function shareCard(filename) {
  if (!lastSharePayload) return;
  const canvas = drawShareCard();
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
  if (!blob) return;
  const file = new File([blob], filename, { type: 'image/png' });
  if (navigator.canShare?.({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title: t('app.name') });
      return;
    } catch (error) {
      if (error?.name === 'AbortError') return; // the user closed the menu
    }
  }
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.download = filename;
  link.href = url;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function drawShareCard() {
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
  return canvas;
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
async function hardReset() {
  [STORE.onboarding, STORE.history, STORE.settings, STORE.lang].forEach((key) => localStorage.removeItem(key));
  historyLog = [];
  currentLang = detectBrowserLang();
  try { await loadLocales([currentLang]); } catch {}
  applyTranslations();
  renderHistory();
  if (gateState.enabled && !gateState.unlocked) { showGateScreen(''); return; }
  openScreen('screen-onboarding-1');
}
function loadJSON(key, fallback){ try{ return JSON.parse(localStorage.getItem(key)||'null') ?? fallback; }catch{ return fallback; } }
function formatDate(iso){ try { return new Date(iso).toLocaleString(DATE_LOCALES[currentLang] || 'en-US', { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit' }); } catch { return iso; } }
async function copyText(text){ try{ await navigator.clipboard.writeText(text); } catch { const ta=document.createElement('textarea'); ta.value=text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); } }
function escapeHTML(str){ return String(str ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;'); }
