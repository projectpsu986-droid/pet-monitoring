
// ============================
// Auth guard (redirect to login.html if not logged in)
// ============================
async function authGuard() {
  try {
    const res = await fetch('/api/auth/me', { credentials: 'include' });
    const data = await res.json();
    if (!data || !data.logged_in) {
      window.location.href = '/login.html';
      return;
    }
    // Optionally show user name somewhere in UI later
    window.__CURRENT_USER__ = data.user;

    // Admin-only menu button (Slide Menu)
    try { updateAdminMenuVisibility(); } catch (e) {}
  } catch (e) {
    window.location.href = '/login.html';
  }
}

// ============================
// Admin Panel menu visibility
// - Show "Admin Panel" in slide menu only when role=admin
// ============================
function isAdminUser(u) {
  const role = String(u?.role || '').trim().toLowerCase();
  return role === 'admin';
}

function updateAdminMenuVisibility() {
  const btn = document.getElementById('adminPanelBtn');
  if (!btn) return;
  const u = window.__CURRENT_USER__;
  if (isAdminUser(u)) btn.classList.remove('hidden');
  else btn.classList.add('hidden');
}

function goAdminPanel() {
  window.location.href = '/admin.html';
}

async function logout() {
  try {
    await fetch('/api/auth/logout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include' });
  } catch (e) {}
  window.location.href = '/login.html';
}


// Refresh latest camera JPEG (bridge mode)
// Use chained setTimeout (not setInterval) to avoid request pile-up when network is slow.
let cameraImgRefreshTimer = null;
function startCameraImgRefresh(intervalMs = 300) {
  if (cameraImgRefreshTimer) return;

  const tick = () => {
    const img = document.getElementById("cameraImg");
    if (!img) {
      cameraImgRefreshTimer = setTimeout(tick, intervalMs);
      return;
    }
    const base = img.getAttribute("data-base");
    if (!base) {
      cameraImgRefreshTimer = setTimeout(tick, intervalMs);
      return;
    }

    // Cache-bust every request
    const nextSrc = `${base}?t=${Date.now()}`;

    // Only schedule next refresh after current image finishes (prevents backlog)
    const scheduleNext = () => {
      img.onload = null;
      img.onerror = null;
      cameraImgRefreshTimer = setTimeout(tick, intervalMs);
    };

    img.onload = scheduleNext;
    img.onerror = scheduleNext;
    img.src = nextSrc;
  };

  cameraImgRefreshTimer = setTimeout(tick, intervalMs);
}

document.addEventListener('DOMContentLoaded', authGuard);
document.addEventListener('DOMContentLoaded', () => startCameraImgRefresh(300));

// In case other scripts run before authGuard finishes, update once on load too.
document.addEventListener('DOMContentLoaded', () => {
  try { updateAdminMenuVisibility(); } catch (e) {}
});




/* =========================
 * 1) CONFIG
 * ========================= */
const API_BASE = ""; // same-origin (required for Service Worker / Web Push)
const ENDPOINTS = {
  cats: `${API_BASE}/api/cats`,
  catsDisplayStatus: `${API_BASE}/api/cats/display_status`,
  catsUpdate: `${API_BASE}/api/cats/update`,
  catsUploadImage: `${API_BASE}/api/cats/upload_image`,
  alerts: `${API_BASE}/api/alerts`,
  alertsMarkRead: `${API_BASE}/api/alerts/mark_read`,
  alertsMarkAllRead: `${API_BASE}/api/alerts/mark_all_read`,
  systemConfig: `${API_BASE}/api/system_config`,
  systemConfigSummaries: `${API_BASE}/api/system_config/summaries`,
  systemConfigApplySummary: `${API_BASE}/api/system_config/apply_summary`,
  rooms: `${API_BASE}/api/rooms`,
timeline: `${API_BASE}/api/timeline`,
  timelineTable: `${API_BASE}/api/timeline_table`,
};
const REFRESH_INTERVAL = 5000;

/* =========================
 * 1B) WEB PUSH NOTIFICATIONS
 * - Requires HTTPS or localhost
 * - Must be triggered by user gesture (button click)
 * ========================= */
const PUSH_ENDPOINTS = {
  vapidPublicKey: `${API_BASE}/api/push/vapid_public_key`,
  subscribe: `${API_BASE}/api/push/subscribe`,
  unsubscribe: `${API_BASE}/api/push/unsubscribe`,
  test: `${API_BASE}/api/push/test`,
};

function _setPushStatus(text) {
  const ids = ["pushStatusSettings"];
  ids.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  });
}

function _urlBase64ToUint8Array(base64String) {
  // base64url -> Uint8Array
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) outputArray[i] = rawData.charCodeAt(i);
  return outputArray;
}

async function _registerServiceWorker() {
  if (!('serviceWorker' in navigator)) {
    throw new Error('Browser นี้ไม่รองรับ Service Worker');
  }
  // Important: sw.js must be served from the same origin
  const reg = await navigator.serviceWorker.register('/sw.js');
  return reg;
}

async function _getVapidPublicKey() {
  const res = await fetch(PUSH_ENDPOINTS.vapidPublicKey);
  if (!res.ok) throw new Error('ไม่สามารถโหลด VAPID public key');
  const data = await res.json();
  return data.publicKey;
}

async function _postJSON(url, obj) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(obj || {}),
  });
  const text = await res.text();
  let json = null;
  try { json = text ? JSON.parse(text) : null; } catch { json = null; }
  return { ok: res.ok, status: res.status, text, json };
}



// ตัวอย่างการแจ้งเตือนจากระบบ (ใช้บนหน้าทดสอบ)
const PUSH_TEMPLATES = [
  {
    id: 'no_cat',
    title: 'Pet Monitoring',
    body: '⚠️ ไม่พบแมวเป็นเวลานานกว่าที่ตั้งค่าไว้',
    url: '/#alerts',
    note: 'ตัวอย่าง: No Cat (เกินชั่วโมงที่กำหนด)'
  },
  {
    id: 'no_eat',
    title: 'Pet Monitoring',
    body: '🍽️ วันนี้แมวยังกินอาหารไม่ครบตามจำนวนครั้งที่ตั้งค่าไว้',
    url: '/#alerts',
    note: 'ตัวอย่าง: No Eating (ต่ำกว่าค่าเกณฑ์)'
  },
  {
    id: 'excrete_low',
    title: 'Pet Monitoring',
    body: '🚽 การขับถ่ายวันนี้น้อยกว่าค่าขั้นต่ำที่ตั้งไว้',
    url: '/#alerts',
    note: 'ตัวอย่าง: Excretion ต่ำกว่าค่า min'
  },
  {
    id: 'excrete_high',
    title: 'Pet Monitoring',
    body: '🚽 การขับถ่ายวันนี้มากกว่าค่าสูงสุดที่ตั้งไว้',
    url: '/#alerts',
    note: 'ตัวอย่าง: Excretion เกินค่า max'
  },
  {
    id: 'system',
    title: 'Pet Monitoring',
    body: '✅ ระบบทำงานปกติ (แจ้งเตือนทดสอบจากระบบ)',
    url: '/#notifications',
    note: 'ตัวอย่าง: แจ้งเตือนสถานะระบบ'
  },
];

function _getPushFormEls() {
  return {
    templateList: document.getElementById('pushTemplateList'),
    titleInput: document.getElementById('pushTitleInput'),
    bodyInput: document.getElementById('pushBodyInput'),
    urlInput: document.getElementById('pushUrlInput'),
    preview: document.getElementById('pushPreviewBox'),
    sendBtn: document.getElementById('pushSendBtn'),
  };
}

function _setPushPreview(title, body, url, note) {
  const { preview } = _getPushFormEls();
  if (!preview) return;
  const safe = (v) => String(v ?? '');
  preview.innerHTML = `
    <div class="push-preview-title">${safe(title)}</div>
    <div class="push-preview-body">${safe(body)}</div>
    <div class="push-preview-url">เปิดไปที่: ${safe(url)}</div>
    ${note ? `<div class="push-preview-note">${safe(note)}</div>` : ''}
  `;
}

function applyPushTemplate(tpl) {
  const { titleInput, bodyInput, urlInput } = _getPushFormEls();
  if (titleInput) titleInput.value = tpl.title || '';
  if (bodyInput) bodyInput.value = tpl.body || '';
  if (urlInput) urlInput.value = tpl.url || '/';
  _setPushPreview(tpl.title, tpl.body, tpl.url, tpl.note);
}

function renderPushTemplates() {
  const { templateList, sendBtn } = _getPushFormEls();
  if (!templateList) return;

  templateList.innerHTML = PUSH_TEMPLATES.map((t) => {
    const label = (t.note || t.id || 'template');
    return `<button type="button" class="push-template-btn" data-template-id="${t.id}">${label}</button>`;
  }).join('');

  templateList.querySelectorAll('button[data-template-id]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.getAttribute('data-template-id');
      const tpl = PUSH_TEMPLATES.find((x) => x.id === id) || PUSH_TEMPLATES[0];
      applyPushTemplate(tpl);
    });
  });

  // default select first template
  if (PUSH_TEMPLATES[0]) applyPushTemplate(PUSH_TEMPLATES[0]);

  if (sendBtn && !sendBtn.dataset.bound) {
    sendBtn.dataset.bound = '1';
    sendBtn.addEventListener('click', () => testPushNotification());
  }
}

async function _getJSON(url){
  const res = await fetch(url, { method: 'GET', credentials: 'include' });
  let data = null;
  try{ data = await res.json(); }catch(e){}
  if(!res.ok){
    const msg = (data && data.error) ? data.error : ('HTTP ' + res.status);
    throw new Error(msg);
  }
  return data;
}

async function refreshPushStatus() {
  try {
    if (!('Notification' in window)) {
      _setPushStatus('สถานะ: Browser ไม่รองรับ Notification');
      return;
    }
    const perm = Notification.permission;
    if (perm === 'denied') {
      _setPushStatus('สถานะ: ถูกบล็อก (denied) — ไปที่ Site settings > Notifications แล้วเปลี่ยนเป็น Allow');
      return;
    }
    const reg = await _registerServiceWorker();
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      _setPushStatus('สถานะ: เปิดใช้งานแล้ว ✅');
    } else {
      _setPushStatus(`สถานะ: ยังไม่ได้ subscribe (permission=${perm})`);
    }
  } catch (e) {
    _setPushStatus('สถานะ: ตรวจสอบไม่ได้ (' + (e?.message || e) + ')');
  }
}

// Called by button click in Notification Settings page
async function enablePushNotifications() {
  try {
    if (!('Notification' in window)) {
      alert('Browser นี้ไม่รองรับ Notification');
      return;
    }

    // Must be triggered by user gesture; this is called from onclick
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
      _setPushStatus('สถานะ: ยังไม่ได้อนุญาต (permission=' + permission + ')');
      alert('ต้องกด Allow เพื่อเปิด Push');
      return;
    }

    const reg = await _registerServiceWorker();
    const existing = await reg.pushManager.getSubscription();
    if (existing) {
      // Upsert to server (important after login/session changes)
      await _postJSON(PUSH_ENDPOINTS.subscribe, existing);
      _setPushStatus('สถานะ: เปิดใช้งานแล้ว ✅');
      alert('เปิด Push สำเร็จ');
      return;
    }

    const publicKey = await _getVapidPublicKey();
    const applicationServerKey = _urlBase64ToUint8Array(publicKey);

    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey,
    });

    const out = await _postJSON(PUSH_ENDPOINTS.subscribe, sub);
    if (!out.ok) throw new Error(out.text || 'subscribe failed');

    _setPushStatus('สถานะ: เปิดใช้งานแล้ว ✅');
    alert('เปิด Push สำเร็จ');
  } catch (e) {
    console.error(e);
    _setPushStatus('สถานะ: เปิดไม่ได้ (' + (e?.message || e) + ')');
    alert('เปิด Push ไม่สำเร็จ: ' + (e?.message || e));
  }
}

async function disablePushNotifications() {
  try {
    const reg = await _registerServiceWorker();
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      // remove on server
      await _postJSON(PUSH_ENDPOINTS.unsubscribe, { endpoint: sub.endpoint });
      await sub.unsubscribe();
    }
    _setPushStatus('สถานะ: ปิดแล้ว');
    alert('ปิด Push แล้ว');
  } catch (e) {
    console.error(e);
    alert('ปิด Push ไม่สำเร็จ: ' + (e?.message || e));
  }
}

async function testPushNotification() {
  try {
    const { titleInput, bodyInput, urlInput } = _getPushFormEls();
    const title = (titleInput?.value || 'Pet Monitoring').trim() || 'Pet Monitoring';
    const body = (bodyInput?.value || 'ทดสอบแจ้งเตือนจากระบบ').trim() || 'ทดสอบแจ้งเตือนจากระบบ';
    const url = (urlInput?.value || '/#notifications').trim() || '/#notifications';

    const out = await _postJSON(PUSH_ENDPOINTS.test, { title, body, url });
    if (!out.ok) throw new Error(out.text || 'test failed');
    alert('ส่งคำสั่งทดสอบแล้ว (ถ้า subscribe ไว้จะเด้ง)');
  } catch (e) {
    console.error(e);
    alert('ทดสอบไม่สำเร็จ: ' + (e?.message || e));
  }
}


/* =========================
 * 2) STATE
 * ========================= */
let cats = [];
let dateRangePicker = null;
let selectedCatId = null;        // ชื่อแมวที่เลือก (จำมาจากหน้า Cat/Detail)
let refreshTimer = null;

// Cat edit modal state
let catEditPreviewObjectURL = null;

// =========================
// Active cats (from DB: display_status=1) used across dropdowns/pages
// =========================
function getActiveCats() {
  return (cats || []).filter((c) => Number(c?.display_status) === 1);
}

function ensureSelectedCatIsActive() {
  if (!selectedCatId) return;
  const active = getActiveCats();
  if (active.some((c) => c?.name === selectedCatId)) return;
  selectedCatId = active.length ? active[0].name : null;
}

function refreshStatisticsCatSelectIfVisible() {
  const statsPage = document.getElementById("statisticsPage");
  if (!statsPage || statsPage.classList.contains("hidden")) return;

  const catSel = document.getElementById("catSelect");
  const titleEl = document.getElementById("statisticsTitle");
  if (!catSel) return;

  ensureSelectedCatIsActive();
  const active = getActiveCats();

  catSel.innerHTML = "";
  if (active.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "ไม่มีแมวที่ถูกเลือก";
    catSel.appendChild(opt);
    catSel.disabled = true;
    if (titleEl) titleEl.textContent = "Statistics";
    return;
  }

  catSel.disabled = false;
  active.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c.name;
    opt.textContent = c.name;
    if (c.name === selectedCatId) opt.selected = true;
    catSel.appendChild(opt);
  });

  if (titleEl && selectedCatId) titleEl.textContent = `${selectedCatId}'s Statistics`;
}

function refreshSystemConfigSummaryIfVisible() {
  const scPage = document.getElementById("systemConfigPage");
  if (!scPage || scPage.classList.contains("hidden")) return;
  // dropdown อ้างอิง display_status อยู่แล้ว
  populateConfigCatSelect();
  loadSystemConfigSummaries();
}

function refreshNotificationsIfVisible() {
  const nPage = document.getElementById("notificationsPage");
  if (!nPage || nPage.classList.contains("hidden")) return;
  loadNotificationsList();
}


// =========================
// Cat visibility (Cat page only)
// =========================
const CAT_VIS_STORAGE_KEY = "visibleCats_v1";

function _loadVisibleCatsArray() {
  try {
    const raw = localStorage.getItem(CAT_VIS_STORAGE_KEY);
    if (!raw) return null; // null = show all
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr)) return null;
    return arr.map((x) => String(x)).filter((x) => x.trim() !== "");
  } catch (_) {
    return null;
  }
}

function _saveVisibleCatsArray(arr) {
  try {
    localStorage.setItem(CAT_VIS_STORAGE_KEY, JSON.stringify(arr || []));
  } catch (_) {}
}

function getVisibleCatsSet() {
  const arr = _loadVisibleCatsArray();
  if (!arr) return null;
  return new Set(arr);
}

function normalizeVisibleCatsWithCurrentList() {
  // ถ้ามีการบันทึกไว้แล้ว: เพิ่มแมวใหม่ให้เป็น visible โดย default
  const arr = _loadVisibleCatsArray();
  if (!arr) return;

  const set = new Set(arr);
  let changed = false;
  (cats || []).forEach((c) => {
    const nm = String(c?.name || "").trim();
    if (!nm) return;
    if (!set.has(nm)) {
      set.add(nm);
      changed = true;
    }
  });
  if (changed) _saveVisibleCatsArray(Array.from(set));
}

function isCatVisibleByName(catName) {
  const set = getVisibleCatsSet();
  if (!set) return true; // default show all
  return set.has(String(catName));
}


let rooms = [];                  // [{name, cameras:[{label,index}]}]
let currentRoomIndex = null;
let currentCameraIndex = 0;
let cameraTimestampTimer = null;

// navigation: จำหน้าเดิมก่อนเปิด Alerts
let lastPageId = null;

// Alerts states
let selectedAlertIds = new Set();
let lastAlertsRaw = [];          // รายการดิบจาก API (ของแมวที่เลือก)

// โฟกัสแถว alert เฉพาะเมื่อมาจากหน้า Notifications
let _focusAlertId = null;

function focusAlertIfNeeded() {
  if (!_focusAlertId) return;
  const el = document.querySelector(`.alert-item[data-id="${_focusAlertId}"]`);
  if (el) {
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("pulse-highlight");
    setTimeout(() => el.classList.remove("pulse-highlight"), 2500);
  }
  _focusAlertId = null;
}

/* =========================
 * 3) STARTUP
 * ========================= */

function _handleInitialRoute() {
  const h = (location.hash || "").replace(/^#/, "").trim().toLowerCase();
  if (!h) return;
  if (h === "notifications") {
    showNotificationsPage();
    return;
  }
  if (h === "notification-settings" || h === "notificationsettings") {
    showNotificationSettings();
    return;
  }
}
document.addEventListener("DOMContentLoaded", () => {
  try { _handleInitialRoute(); } catch (e) {}
  // NOTE: ไม่ต้องเรียก refreshPushStatus ตอนโหลดหน้าแรกเสมอ
  // เพราะจะไป register service worker ทุกครั้ง และบาง browser อาจมีอาการรีเฟรชหน้า/กระตุก
  // เราจะเรียก refreshPushStatus เฉพาะตอนเข้า "Notification Settings" เท่านั้น

    initDateRangePicker();
bindSystemConfigSummaryApply();
  fetchCatDataFromAPI();
  refreshTimer = setInterval(updateCatData, REFRESH_INTERVAL);
  loadSystemConfig();
  loadRoomsAndRender();
  // Fullscreen (double click + button)
  try { _bindCameraFullscreenHandlersOnce(); } catch (e) {}
});

/* =========================
 * Utils
 * ========================= */
function getVisiblePageId() {
  const ids = [
    "homePage", "cameraPage", "catPage", "profilePage",
    "catDetailPage", "systemConfigPage", "notificationsPage",
    "notificationSettingsPage",
    "alertsPage", "statisticsPage", "timelinePage"
  ];
  for (const id of ids) {
    const el = document.getElementById(id);
    if (el && !el.classList.contains("hidden")) return id;
  }
  return null;
}

function capitalize(s) {
  if (!s) return "";
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = String(text ?? "");
}

// =========================
// Cat Image Helpers
// =========================
// Display order: real_image_url -> image_url
function getCatDisplayImage(cat) {
  const realUrl = String(cat?.real_image_url || "").trim();
  const baseUrl = String(cat?.image_url || "").trim();
  const chosen = realUrl || baseUrl || "";
  // If API returns a relative assets path, serve it from API_BASE.
  if (chosen.startsWith("/assets/")) {
    return `${API_BASE}${chosen}`;
  }
  return chosen;
}


// ===== Summary Card Helpers =====
function safeDiv(n, d) { return d ? (n / d) : 0; }
function formatAvg(value) {
  // show as integer when close to integer, else 2 decimals
  if (Number.isFinite(value) && Math.abs(value - Math.round(value)) < 1e-9) return String(Math.round(value));
  return (Number.isFinite(value) ? value.toFixed(2) : "0");
}
function unitLabel(period) {
  if (period === "monthly") return "เดือน";
  if (period === "yearly")  return "ปี";
  // daily + range => per day
  return "วัน";
}
function countNonNullBuckets(arr) {
  // Count buckets that actually have data (non-null numeric)
  return (arr || []).reduce((acc, v) => acc + (Number.isFinite(Number(v)) ? 1 : 0), 0);
}

function updateSummaryCardsFromSeries(period, eatArr, excArr) {
  const totalEat = sum(eatArr);
  const totalExc = sum(excArr);

  // IMPORTANT: average should be computed only from days/months/years that have data,
  // not from the total calendar bucket count.
  const denomEat = countNonNullBuckets(eatArr);
  const denomExc = countNonNullBuckets(excArr);

  const avgEat = safeDiv(totalEat, denomEat);
  const avgExc = safeDiv(totalExc, denomExc);

  const unit = unitLabel(period);

  // Display average prominently; keep total + number of days used for reference
  setText(
    "eatTime",
    `เฉลี่ย ${formatAvg(avgEat)} ครั้ง/${unit} (รวม ${totalEat} ครั้ง, คิดจาก ${denomEat || 0} ${unit})`
  );
  setText(
    "excreteTime",
    `เฉลี่ย ${formatAvg(avgExc)} ครั้ง/${unit} (รวม ${totalExc} ครั้ง, คิดจาก ${denomExc || 0} ${unit})`
  );
}

function handleFetchError(err) {
  console.error("❌ API Error:", err);
  if (!document.body.dataset.alerted) {
    alert("ไม่สามารถเชื่อมต่อ API ได้ กรุณาตรวจสอบว่า Flask ทำงานอยู่หรือไม่");
    document.body.dataset.alerted = "true";
  }
}

function fmtDateTime(s) {
  const d = new Date(s);
  return isNaN(d) ? "" : d.toLocaleString();
}

function priorityClass(type) {
  switch (type) {
    case "no_cat":
    case "no_eating":
      return "high-priority";
    case "low_excrete":
    case "high_excrete":
      return "medium-priority";
    default:
      return "";
  }
}

/* =========================
 * 4) CATS: โหลด/เรนเดอร์
 * ========================= */
function fetchCatDataFromAPI() {
  fetch(ENDPOINTS.cats)
    .then(res => res.json())
    .then(data => {
      cats = Array.isArray(data) ? data : [];
      renderCatCards(cats);
      ensureSelectedCatIsActive();
      populateConfigCatSelect();
      refreshStatisticsCatSelectIfVisible();
      refreshSystemConfigSummaryIfVisible();
      refreshNotificationsIfVisible();
    })
    .catch(handleFetchError);
}

function updateCatData() {
  fetch(ENDPOINTS.cats)
    .then(res => res.json())
    .then(data => {
      cats = Array.isArray(data) ? data : [];
      renderCatCards(cats);
      updateOpenCatDetail();

      ensureSelectedCatIsActive();
      populateConfigCatSelect();
      refreshStatisticsCatSelectIfVisible();
      refreshSystemConfigSummaryIfVisible();
      refreshNotificationsIfVisible();
    })
    .catch(handleFetchError);
}

function renderCatCards(catList) {
  const container = document.querySelector(".cat-grid");
  if (!container) return;
  container.innerHTML = "";

  const seen = new Set();

  (catList || []).forEach((cat) => {
    const name = cat?.name;
    if (!name) return;
    if (seen.has(name)) return;
    seen.add(name);

    // แสดงเฉพาะแมวที่ถูกเลือกจาก DB (display_status = 1)
    if (Number(cat?.display_status) !== 1) return;

    const card = document.createElement("div");
    card.className = "cat-card";
    card.onclick = () => selectCat(name);

    const imgUrl = getCatDisplayImage(cat);
    card.innerHTML = `
      <img src="${escapeAttr(imgUrl)}" alt="${escapeAttr(name)}" class="cat-image">
      <h3>${escapeHtml(name)}</h3>
    `;
    container.appendChild(card);
  });

  if (container.children.length === 0) {
    container.innerHTML = `
      <div class="cat-settings-empty" style="grid-column:1/-1; text-align:center;">
        ไม่มีแมวที่ถูกเลือกให้แสดง (กด "ตั้งค่าแมว" เพื่อเลือก)
      </div>
    `;
  }
}/* =========================
 * 5) CAT DETAIL
 * ========================= */
function selectCat(catName) {
  const cat = cats.find((c) => c.name === catName);
  if (!cat) return;

  selectedCatId = catName; // จำชื่อแมวไว้ใช้ที่ Alerts/Statistics
  document.getElementById("catDetailName").textContent = cat.name;
  document.getElementById("catProfileName").textContent = `Name ${cat.name}`;
  document.getElementById("catDetailImage").src = getCatDisplayImage(cat);
  document.getElementById("catLocation").textContent = cat.current_room || "Unknown";
  document.getElementById("catPage").classList.add("hidden");
  document.getElementById("profilePage").classList.add("hidden");
  document.getElementById("systemConfigPage").classList.add("hidden");
  document.getElementById("catDetailPage").classList.remove("hidden");
}

function goBackToCatGallery() {
  selectedCatId = null;
  document.getElementById("catDetailPage").classList.add("hidden");
  document.getElementById("catPage").classList.remove("hidden");
  document.getElementById("profilePage").classList.add("hidden");
  document.getElementById("systemConfigPage").classList.add("hidden");
}


/* =========================
 * 5.1) CAT EDIT (name + real_image_url)
 * ========================= */
function openCatEditModal() {
  // Must be on Cat Detail page
  if (!selectedCatId) {
    alert("กรุณาเลือกแมวก่อน");
    return;
  }
  const cat = cats.find((c) => c.name === selectedCatId);
  if (!cat) return;

  const overlay = document.getElementById("catEditOverlay");
	const modalEl = document.getElementById("catEditModal");
	if (!overlay || !modalEl) return;

  // Prefill
  const nameInput = document.getElementById("catEditNameInput");
  if (nameInput) nameInput.value = cat.name || "";

  const fileInput = document.getElementById("catEditFileInput");
  if (fileInput) fileInput.value = "";

	// reset flag
	modalEl.dataset.resetRealImage = "0";

  // Preview
  updateCatEditPreview();

	overlay.classList.remove("hidden");
	modalEl.classList.remove("hidden");
}

function closeCatEditModal() {
  const overlay = document.getElementById("catEditOverlay");
  const modal = document.getElementById("catEditModal");
  if (overlay) overlay.classList.add("hidden");
  if (modal) modal.classList.add("hidden");

  // cleanup object URL
  if (catEditPreviewObjectURL) {
    URL.revokeObjectURL(catEditPreviewObjectURL);
    catEditPreviewObjectURL = null;
  }
}

function updateCatEditPreview() {
  const cat = cats.find((c) => c.name === selectedCatId);
  if (!cat) return;

  const previewImg = document.getElementById("catEditPreviewImg");
  const previewName = document.getElementById("catEditPreviewName");
  const nameInput = document.getElementById("catEditNameInput");
  const fileInput = document.getElementById("catEditFileInput");

  const modal = document.getElementById("catEditModal");
  const resetReal = String(modal?.dataset?.resetRealImage || "0") === "1";

  const nm = String(nameInput?.value || cat.name || "").trim();
  const baseUrl = String(cat.image_url || "").trim();

  // Pick preview image priority:
  // 1) if user selected file -> local preview
  // 2) if user pressed reset -> base image_url
  // 3) else use current real_image_url -> image_url
  let img = baseUrl;

  const file = fileInput?.files?.[0] || null;
  if (file) {
    if (catEditPreviewObjectURL) {
      URL.revokeObjectURL(catEditPreviewObjectURL);
      catEditPreviewObjectURL = null;
    }
    catEditPreviewObjectURL = URL.createObjectURL(file);
    img = catEditPreviewObjectURL;
  } else if (resetReal) {
    img = baseUrl;
  } else {
    img = String(cat.real_image_url || "").trim() || baseUrl;
  }

  if (previewImg) previewImg.src = img;
  if (previewName) previewName.textContent = nm || "-";
}

function resetCatRealImage() {
  const modal = document.getElementById("catEditModal");
  if (modal) modal.dataset.resetRealImage = "1";

  const fileInput = document.getElementById("catEditFileInput");
  if (fileInput) fileInput.value = "";

  if (catEditPreviewObjectURL) {
    URL.revokeObjectURL(catEditPreviewObjectURL);
    catEditPreviewObjectURL = null;
  }
  updateCatEditPreview();
}

function saveCatEdit() {
  const cat = cats.find((c) => c.name === selectedCatId);
  if (!cat) return;

  const nameInput = document.getElementById("catEditNameInput");
  const fileInput = document.getElementById("catEditFileInput");

  const modal = document.getElementById("catEditModal");
  const resetReal = String(modal?.dataset?.resetRealImage || "0") === "1";

  const newName = String(nameInput?.value || "").trim();
  const file = fileInput?.files?.[0] || null;

  if (!newName) {
    alert("ชื่อแมวต้องไม่ว่าง");
    return;
  }

  const payload = {
    oldName: cat.name,
    newName,
  };

  // Only reset when user explicitly pressed the reset button.
  if (resetReal && !file) {
    payload.reset_image = true;
  }


  // 1) Update name/reset flags first
  fetch(ENDPOINTS.catsUpdate, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
    .then(async (r) => {
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j?.message || `HTTP ${r.status}`);
      }
      return r.json();
    })
    .then(async () => {
      // 2) If user selected a file, upload it
      if (!file) return;
      const fd = new FormData();
      fd.append("catName", newName);
      fd.append("file", file);
      const r = await fetch(ENDPOINTS.catsUploadImage, { method: "POST", body: fd });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j?.message || `Upload failed (HTTP ${r.status})`);
      }
      const j = await r.json().catch(() => ({}));
      if (!j?.ok) throw new Error(j?.message || "Upload failed");
    })
    .then(() => {
      selectedCatId = newName;
      closeCatEditModal();
      updateCatData();
    })
    .catch((e) => {
      console.error(e);
      alert(`บันทึกไม่สำเร็จ: ${e.message || e}`);
    });
}

function updateOpenCatDetail() {
  if (selectedCatId && !document.getElementById("catDetailPage").classList.contains("hidden")) {
    const cat = cats.find((c) => c.name === selectedCatId);
    if (cat) {
      document.getElementById("catLocation").textContent = cat.current_room || "Unknown";
      document.getElementById("catDetailName").textContent = cat.name;
      document.getElementById("catProfileName").textContent = `Name ${cat.name}`;
      document.getElementById("catDetailImage").src = getCatDisplayImage(cat);
    }
  }
}

/* =========================
 * 6) ROOMS & CAMERA
 * ========================= */
function loadRoomsAndRender() {
  fetch(ENDPOINTS.rooms)
    .then(res => res.json())
    .then(data => {
      rooms = Array.isArray(data) ? data : [];
      renderRoomCards(rooms);
    })
    .catch(err => console.error("❌ โหลดผังห้อง/กล้องล้มเหลว:", err));
}

function renderRoomCards(roomList) {
  const grid = document.querySelector(".room-grid");
  if (!grid) return;
  grid.innerHTML = "";
  roomList.forEach((room, idx) => {
    const card = document.createElement("div");
    card.className = "room-card";
    card.onclick = () => selectRoom(idx);
    card.innerHTML = `
      <div class="room-preview">
        <div class="live-preview">
          <div class="camera-placeholder">📹</div>
        </div>
      </div>
      <h3>${capitalize(room.name || "Room")}</h3>
      <button class="select-btn">เลือกห้อง</button>
    `;
    grid.appendChild(card);
  });
}

function selectRoom(index) {
  if (index < 0 || index >= rooms.length) return;
  currentRoomIndex = index;
  currentCameraIndex = 0;

  document.getElementById("homePage").classList.add("hidden");
  document.getElementById("cameraPage").classList.remove("hidden");
  document.getElementById("profilePage").classList.add("hidden");
  document.getElementById("systemConfigPage").classList.add("hidden");

  updateCameraUI();

  if (cameraTimestampTimer) clearInterval(cameraTimestampTimer);
  cameraTimestampTimer = setInterval(() => {
    document.getElementById("timestamp").textContent = new Date().toLocaleString();
  }, 1000);
}

function updateCameraUI() {
  if (currentRoomIndex === null) return;

  const room = rooms[currentRoomIndex] || {};
  const cams = room.cameras || [];
  const cam = cams[currentCameraIndex];

  document.getElementById("currentRoomName").textContent = capitalize(room.name || "Room");
  document.getElementById("cameraInfo").textContent = cams.length
    ? `กล้อง ${currentCameraIndex + 1} จาก ${cams.length}`
    : `ไม่มีกล้องในห้องนี้`;

  const feed = cam ? `${API_BASE}/camera_latest/${room.name}/${cam.index}.jpg` : "";
  document.getElementById("cameraFeed").innerHTML = cam
    ? `<img id="cameraImg" class="camera-img" data-base="${feed}" src="${feed}?t=${Date.now()}" alt="${cam.label}">`
    : `<div class="simulated-video"><div class="camera-placeholder large">📹</div><p>ไม่มีกล้อง</p></div>`;

  const prevBtn = document.querySelector(".camera-controls .nav-btn:first-child");
  const nextBtn = document.querySelector(".camera-controls .nav-btn:last-child");
  if (prevBtn) prevBtn.disabled = currentCameraIndex <= 0;
  if (nextBtn) nextBtn.disabled = currentCameraIndex >= cams.length - 1;

  document.getElementById("timestamp").textContent = new Date().toLocaleString();
}

// =========================
// Fullscreen (YouTube-like)
// - Button: #fullscreenBtn
// - Double click on video toggles fullscreen
// =========================
function _getCameraFullscreenTarget() {
  // Fullscreen the wrapper so the button stays visible
  return document.querySelector("#cameraPage .video-wrapper");
}

function _isFullscreenActive() {
  return !!(document.fullscreenElement || document.webkitFullscreenElement);
}

function _setFullscreenUI(isOn) {
  // Toggle class on <html> for CSS tweaks (hide navbar/header/controls)
  try { document.documentElement.classList.toggle("fs-active", !!isOn); } catch (e) {}

  const btn = document.getElementById("fullscreenBtn");
  if (btn) {
    // ⛶ = enter, ✕ = exit
    btn.textContent = isOn ? "✕" : "⛶";
    btn.setAttribute("aria-label", isOn ? "Exit fullscreen" : "Fullscreen");
    btn.title = isOn ? "ออกจากเต็มจอ" : "เต็มจอ";
  }
}

function toggleCameraFullscreen() {
  const el = _getCameraFullscreenTarget();
  if (!el) return;

  // Safari iOS uses webkit* APIs
  const active = _isFullscreenActive();
  if (active) {
    const exit = document.exitFullscreen || document.webkitExitFullscreen;
    if (exit) exit.call(document);
    return;
  }

  const req = el.requestFullscreen || el.webkitRequestFullscreen;
  if (!req) {
    alert("Browser นี้ไม่รองรับโหมดเต็มจอ");
    return;
  }
  try {
    req.call(el);
  } catch (e) {
    console.error(e);
    alert("เข้าเต็มจอไม่สำเร็จ: " + (e?.message || e));
  }
}

// Bind once
function _bindCameraFullscreenHandlersOnce() {
  if (window.__cameraFullscreenBound) return;
  window.__cameraFullscreenBound = true;

  // Double click anywhere on the video area
  const feed = document.getElementById("cameraFeed");
  if (feed) {
    feed.addEventListener("dblclick", (ev) => {
      // Prevent double-tap zoom on mobile where possible
      ev.preventDefault();
      toggleCameraFullscreen();
    });
  }

  // Keep UI state in sync
  document.addEventListener("fullscreenchange", () => _setFullscreenUI(_isFullscreenActive()));
  document.addEventListener("webkitfullscreenchange", () => _setFullscreenUI(_isFullscreenActive()));
}

function previousCamera() {
  if (currentRoomIndex === null) return;
  if (currentCameraIndex > 0) {
    currentCameraIndex--;
    updateCameraUI();
  }
}

function nextCamera() {
  if (currentRoomIndex === null) return;
  const cams = rooms[currentRoomIndex]?.cameras || [];
  if (currentCameraIndex < cams.length - 1) {
    currentCameraIndex++;
    updateCameraUI();
  }
}

function goBack() {
  currentRoomIndex = null;
  if (cameraTimestampTimer) {
    clearInterval(cameraTimestampTimer);
    cameraTimestampTimer = null;
  }
  document.getElementById("cameraPage").classList.add("hidden");
  document.getElementById("homePage").classList.remove("hidden");
  document.getElementById("profilePage").classList.add("hidden");
  document.getElementById("systemConfigPage").classList.add("hidden");
}

/* =========================
 * 7) PAGE NAV
 * ========================= */
function showHomePage() {
  selectedCatId = null;
  currentRoomIndex = null;
  if (cameraTimestampTimer) {
    clearInterval(cameraTimestampTimer);
    cameraTimestampTimer = null;
  }
  document.getElementById("homePage").classList.remove("hidden");
  document.getElementById("cameraPage").classList.add("hidden");
  document.getElementById("catPage").classList.add("hidden");
  document.getElementById("profilePage").classList.add("hidden");
  document.getElementById("catDetailPage").classList.add("hidden");
  document.getElementById("systemConfigPage").classList.add("hidden");
  document.getElementById("notificationsPage").classList.add("hidden");
  document.getElementById("alertsPage").classList.add("hidden");
  document.getElementById("statisticsPage").classList.add("hidden");
  document.getElementById("timelinePage").classList.add("hidden");
}

function showCatPage() {
  selectedCatId = null;
  currentRoomIndex = null;
  document.getElementById("homePage").classList.add("hidden");
  document.getElementById("cameraPage").classList.add("hidden");
  document.getElementById("catPage").classList.remove("hidden");
  document.getElementById("catDetailPage").classList.add("hidden");
  document.getElementById("profilePage").classList.add("hidden");
  document.getElementById("systemConfigPage").classList.add("hidden");
  document.getElementById("notificationsPage").classList.add("hidden");
  document.getElementById("alertsPage").classList.add("hidden");
  document.getElementById("statisticsPage").classList.add("hidden");
  document.getElementById("timelinePage").classList.add("hidden");
}

// =========================
// Cat Settings Modal (visibility on Cat page)
// =========================
function openCatSettings() {
  const overlay = document.getElementById("catSettingsOverlay");
  const modal = document.getElementById("catSettingsModal");
  const list = document.getElementById("catSettingsList");
  if (!overlay || !modal || !list) return;

  // build list from DB field: cats.display_status (1=show,0=hide)
  if (!Array.isArray(cats) || cats.length === 0) {
    list.innerHTML = `<div class="cat-settings-empty">ยังไม่มีข้อมูลแมว</div>`;
  } else {
    const sorted = cats.slice().sort((a, b) => String(a?.name || "").localeCompare(String(b?.name || "")));
    let html = "";
    sorted.forEach((c) => {
      const name = String(c?.name || "").trim();
      if (!name) return;
      const checked = Number(c?.display_status) === 1;
      const img = getCatDisplayImage(c);
      const id = `catVis_${name.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
      html += `
        <label class="cat-settings-item" for="${escapeAttr(id)}">
          <input type="checkbox" id="${escapeAttr(id)}" data-cat-name="${escapeAttr(name)}" ${checked ? "checked" : ""}>
          <img src="${escapeAttr(img)}" alt="${escapeAttr(name)}" class="cat-settings-thumb">
          <span class="cat-settings-name">${escapeHtml(name)}</span>
        </label>
      `;
    });
    list.innerHTML = html || `<div class="cat-settings-empty">ยังไม่มีข้อมูลแมว</div>`;
  }

  overlay.classList.remove("hidden");
  modal.classList.remove("hidden");
}function closeCatSettings() {
  const overlay = document.getElementById("catSettingsOverlay");
  const modal = document.getElementById("catSettingsModal");
  if (overlay) overlay.classList.add("hidden");
  if (modal) modal.classList.add("hidden");
}

function toggleAllCatSettings(isOn) {
  const list = document.getElementById("catSettingsList");
  if (!list) return;
  list.querySelectorAll('input[type="checkbox"][data-cat-name]').forEach((cb) => {
    cb.checked = !!isOn;
  });
}

function saveCatSettings() {
  const list = document.getElementById("catSettingsList");
  if (!list) return;

  const selected = [];
  list.querySelectorAll('input[type="checkbox"][data-cat-name]').forEach((cb) => {
    if (cb.checked) selected.push(String(cb.dataset.catName || cb.getAttribute("data-cat-name") || "").trim());
  });

  // บันทึกลง DB: cats.display_status (1=แสดง, 0=ซ่อน)
  fetch(ENDPOINTS.catsDisplayStatus, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected: selected.filter((x) => x) }),
  })
    .then((r) => r.json())
    .then(() => {
      closeCatSettings();
      // reload cats แล้วให้กระทบทุก dropdown + summary ทันที
      updateCatData();
    })
    .catch(handleFetchError);
}function showProfilePage() {
  document.getElementById("homePage").classList.add("hidden");
  document.getElementById("profilePage").classList.remove("hidden");
  document.getElementById("catDetailPage").classList.add("hidden");
  document.getElementById("systemConfigPage").classList.add("hidden");
  document.getElementById("notificationsPage").classList.add("hidden");
  document.getElementById("alertsPage").classList.add("hidden");
  document.getElementById("statisticsPage").classList.add("hidden");
}

function showSystemConfigPage() {
  document.getElementById("profilePage").classList.add("hidden");
  document.getElementById("systemConfigPage").classList.remove("hidden");
  loadSystemConfig();
  loadSystemConfigSummaries();
}

/* =========================
 * 7.1) ALERTS & NOTIFICATIONS
 * ========================= */

/**
 * แสดงหน้าเพียงหน้าเดียว (ซ่อนทุกหน้าอื่น)
 * ใช้กับ Alerts/Notifications เพื่อไม่รีเซ็ต state เหมือน showHomePage()
 */
function _showOnlyPage(pageId) {
  const ids = [
    "homePage", "cameraPage", "catPage", "profilePage",
    "catDetailPage", "systemConfigPage", "notificationsPage",
    "notificationSettingsPage",
    "alertsPage", "statisticsPage", "timelinePage"
  ];
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    if (id === pageId) el.classList.remove("hidden");
    else el.classList.add("hidden");
  });
}

/**
 * เปิดหน้า Alerts (ถ้ามี selectedCatId จะกรองเฉพาะแมวตัวนั้น)
 * lastPageId จะถูกจำไว้เพื่อย้อนกลับ
 */
function showAlertsPage() {
  lastPageId = getVisiblePageId();
  _showOnlyPage("alertsPage");
  loadAlertsList();
}

/**
 * เปิดหน้า Notifications (แสดงสรุปจำนวน Alerts ที่ยังไม่อ่าน)
 */
function showNotificationsPage() {
  _showOnlyPage("notificationsPage");
  loadNotificationsList();
}

/**
 * เปิดหน้า Notification Settings
 * - หน้านี้เอาไว้ตั้งค่า/ทดสอบ Push Notification
 */
function showNotificationSettings() {
  _showOnlyPage("notificationSettingsPage");
  renderPushTemplates();
  refreshPushStatus();
}

/**
 * ปุ่มย้อนกลับจากหน้า Alerts
 */
function goBackFromAlerts() {
  const target = lastPageId;
  lastPageId = null;

  if (!target) {
    showHomePage();
    return;
  }

  if (target === "homePage") return showHomePage();
  if (target === "catPage") return showCatPage();
  if (target === "profilePage") return showProfilePage();
  if (target === "systemConfigPage") return showSystemConfigPage();
  if (target === "notificationsPage") return showNotificationsPage();
  if (target === "notificationSettingsPage") return showNotificationSettings();
  if (target === "statisticsPage") return showCatStatisticsPage();
  if (target === "timelinePage") return showTimelinePage();

  // cat detail ต้องรักษา selectedCatId ไว้
  if (target === "catDetailPage") {
    _showOnlyPage("catDetailPage");
    return;
  }

  // fallback
  showHomePage();
}

/* ---------- Alerts data / render ---------- */

function _jsString(v) {
  // สำหรับใส่ใน onclick แบบปลอดภัย
  return JSON.stringify(String(v ?? ""));
}

function loadAlertsList() {
  const listEl = document.getElementById("alertsList");
  if (!listEl) return;

  listEl.innerHTML = `<div style="padding:10px; color:#666;">กำลังโหลด Alerts...</div>`;

  const qs = new URLSearchParams();
  qs.set("include_read", "1");
  qs.set("mode", "realtime");
  if (selectedCatId) qs.set("cat", selectedCatId);

  fetch(`${ENDPOINTS.alerts}?${qs.toString()}`)
    .then(r => r.json())
    .then(rows => {
      lastAlertsRaw = Array.isArray(rows) ? rows : [];
      renderAlertsList(lastAlertsRaw);
      // ถ้ามาจากหน้า Notifications และมี _focusAlertId ให้เลื่อนไปหา
      setTimeout(focusAlertIfNeeded, 50);
    })
    .catch(handleFetchError);
}

function renderAlertsList(rows) {
  const listEl = document.getElementById("alertsList");
  if (!listEl) return;

  const data = Array.isArray(rows) ? rows : [];
  if (data.length === 0) {
    listEl.innerHTML = `
      <div class="alert-item read" style="text-align:center;">
        ไม่มี Alerts ในขณะนี้
      </div>
    `;
    return;
  }

  // group by cat
  const byCat = new Map();
  for (const a of data) {
    const cat = a?.cat || a?.cat_name || "-";
    if (!byCat.has(cat)) byCat.set(cat, []);
    byCat.get(cat).push(a);
  }

  // toolbar
  const toolbar = `
    <div class="alerts-toolbar" style="display:flex; gap:10px; margin-bottom:12px; align-items:center;">
      <button class="apply-filter-btn" onclick="markAllAlertsRead()" style="padding:10px 14px; border-radius:10px; border:none; cursor:pointer;">
        อ่านทั้งหมด${selectedCatId ? ` (${escapeHtml(selectedCatId)})` : ""}
      </button>
      <button class="clear-filter-btn" onclick="loadAlertsList()" style="padding:10px 14px; border-radius:10px; border:none; cursor:pointer;">
        รีเฟรช
      </button>
      <div style="margin-left:auto; color:#666; font-size:0.9rem;">
        แสดง ${data.length} รายการ
      </div>
    </div>
  `;

  let html = toolbar;

  const catsSorted = Array.from(byCat.keys()).sort((a, b) => String(a).localeCompare(String(b)));
  for (const cat of catsSorted) {
    const items = byCat.get(cat) || [];
    html += `<div class="alert-cat-group">`;
    html += `<div class="alert-cat-title">${escapeHtml(cat)}</div>`;
    html += `<div class="alert-items">`;

    for (const a of items) {
      const id = a?.id;
      const type = a?.type || a?.alert_type || "-";
      const msg = a?.message || "-";
      const isRead = Number(a?.is_read) === 1;
      const createdAt = a?.created_at || "";
      const high = (type === "no_cat" || type === "no_eating");

      html += `
        <div class="alert-item ${isRead ? "read" : ""} ${high ? "high-priority" : ""}" data-id="${escapeHtml(id)}">
          <div class="alert-line">
            <span class="alert-type-tag">${escapeHtml(type)}</span>
            <span style="font-weight:700; color:#333;">${escapeHtml(msg)}</span>
          </div>
          <div style="margin-top:8px; color:#777; font-size:0.85rem;">
            ${escapeHtml(createdAt)}
          </div>
          ${!isRead ? `
            <div style="margin-top:12px;">
              <button onclick="markAlertRead(${Number(id)}, event)"
                style="padding:8px 12px; border-radius:10px; border:none; cursor:pointer;">
                ทำเครื่องหมายอ่านแล้ว
              </button>
            </div>
          ` : ``}
        </div>
      `;
    }

    html += `</div></div>`;
  }

  listEl.innerHTML = html;
}

function markAlertRead(id, ev) {
  if (ev) ev.stopPropagation();
  const alertId = Number(id);
  if (!Number.isFinite(alertId)) return;

  fetch(ENDPOINTS.alertsMarkRead, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: [alertId] }),
  })
    .then(r => r.json())
    .then(() => {
      loadAlertsList();
      // ถ้า user เปิด notifications แล้วกลับไป อาจอยากให้ count อัปเดตด้วย
      if (!document.getElementById("notificationsPage")?.classList.contains("hidden")) {
        loadNotificationsList();
      }
    })
    .catch(handleFetchError);
}

function markAllAlertsRead() {
  const qs = new URLSearchParams();
  if (selectedCatId) qs.set("cat", selectedCatId);

  const url = qs.toString()
    ? `${ENDPOINTS.alertsMarkAllRead}?${qs.toString()}`
    : ENDPOINTS.alertsMarkAllRead;

  fetch(url, { method: "PATCH" })
    .then(r => r.json())
    .then(() => {
      loadAlertsList();
      if (!document.getElementById("notificationsPage")?.classList.contains("hidden")) {
        loadNotificationsList();
      }
    })
    .catch(handleFetchError);
}

/* ---------- Notifications data / render ---------- */

function loadNotificationsList() {
  const listEl = document.getElementById("notificationsList");
  if (!listEl) return;

  listEl.innerHTML = `<div style="padding:10px; color:#666;">กำลังโหลด Notifications...</div>`;

  const qs = new URLSearchParams();
  qs.set("include_read", "0");
  qs.set("mode", "realtime");

  fetch(`${ENDPOINTS.alerts}?${qs.toString()}`)
    .then(r => r.json())
    .then(rows => {
      const data = Array.isArray(rows) ? rows : [];
      renderNotificationsList(data);
    })
    .catch(handleFetchError);
}

function renderNotificationsList(unreadAlerts) {
  const listEl = document.getElementById("notificationsList");
  if (!listEl) return;

  const data = Array.isArray(unreadAlerts) ? unreadAlerts : [];

  // Filter by "active cats" (cats.display_status = 1) so hidden cats won't appear in Notifications.
  // ถ้า cats ยังไม่ถูกโหลด (active.length===0) จะไม่ filter เพื่อกันหน้าเงียบผิดพลาดตอนโหลดครั้งแรก
  const active = getActiveCats();
  let filtered = data;
  if (active.length > 0) {
    const activeSet = new Set(active.map(c => String(c?.name || "").trim()).filter(x => x));
    filtered = data.filter(a => {
      const cat = String(a?.cat || a?.cat_name || "-").trim();
      return activeSet.has(cat);
    });
  }

  if (filtered.length === 0) {
    listEl.innerHTML = `
      <div class="notification-item" style="text-align:center;">
        ไม่มี Notifications (ยังไม่อ่าน) ในขณะนี้
      </div>
    `;
    return;
  }

  // group by cat + store latest id for focus
  const map = new Map(); // cat -> {count, latestId, latestAt}
  for (const a of filtered) {
    const cat = a?.cat || a?.cat_name || "-";
    const id = Number(a?.id);
    const at = String(a?.created_at || "");
    if (!map.has(cat)) {
      map.set(cat, { count: 0, latestId: id, latestAt: at });
    }
    const obj = map.get(cat);
    obj.count += 1;

    // update latest (string compare ok if ISO datetime)
    if (at && (!obj.latestAt || at > obj.latestAt)) {
      obj.latestAt = at;
      obj.latestId = id;
    }
  }

  const catsSorted = Array.from(map.keys()).sort((a, b) => String(a).localeCompare(String(b)));
  let html = "";
  for (const cat of catsSorted) {
    const { count, latestId, latestAt } = map.get(cat);
    // IMPORTANT:
    // อย่าใช้ onclick="..." พร้อมส่ง string ที่มี double quote (จาก JSON.stringify)
    // เพราะจะทำให้ HTML attribute แตก เช่น onclick="fn("Orange")" แล้วคลิกไม่ทำงาน
    // วิธีแก้ที่ง่ายที่สุดคือเปลี่ยน attribute ทั้งก้อนให้ใช้ single quote ครอบแทน
    html += `
      <div class="notification-item unread"
           onclick='openAlertsFromNotification(${_jsString(cat)}, ${Number(latestId)})'
           style="display:flex; align-items:center; justify-content:space-between; cursor:pointer;">
        <div>
          <div style="font-weight:800; color:#333;">🐱 ${escapeHtml(cat)}</div>
          <div style="margin-top:6px; color:#666;">
            มี Alerts ที่ยังไม่อ่าน ${count} รายการ
            ${latestAt ? ` • ล่าสุด: ${escapeHtml(latestAt)}` : ""}
          </div>
        </div>
        <div class="notification-badge">${count}</div>
      </div>
    `;
  }

  listEl.innerHTML = html;
}

function openAlertsFromNotification(catName, focusAlertId) {
  selectedCatId = catName;
  _focusAlertId = Number(focusAlertId) || null;
  showAlertsPage();
}


/* =========================
 * 8) HAMBURGER MENU
 * ========================= */
function toggleMenu() {
  const menu = document.getElementById("navMenu");
  const overlay = document.getElementById("menuOverlay");
  const hamburgerBtn = document.querySelector(".hamburger-btn");

  if (menu.classList.contains("hidden")) {
    menu.classList.remove("hidden");
    menu.classList.add("show");
    overlay.classList.add("show");
    hamburgerBtn.classList.add("active");
    hamburgerBtn.innerHTML = "X";
  } else {
    closeMenu();
  }
}

function closeMenu() {
  const menu = document.getElementById("navMenu");
  const overlay = document.getElementById("menuOverlay");
  const hamburgerBtn = document.querySelector(".hamburger-btn");

  menu.classList.remove("show");
  overlay.classList.remove("show");
  hamburgerBtn.classList.remove("active");
  hamburgerBtn.innerHTML = "☰";

  setTimeout(() => menu.classList.add("hidden"), 300);
}

/* =========================
 * 9) STATISTICS (NO SLEEP)
 * ========================= */
let statsChartInstance = null;
let availableYears = [];  // ปีทั้งหมดใน DB (ASC)

function showCatStatisticsPage() {
  ensureSelectedCatIsActive();
  if (!selectedCatId) { alert("กรุณาเลือกแมวก่อน"); return; }

  document.getElementById("catDetailPage")?.classList.add("hidden");
  document.getElementById("statisticsPage")?.classList.remove("hidden");

  const titleEl = document.getElementById("statisticsTitle");
  if (titleEl) titleEl.textContent = `${selectedCatId}'s Statistics`;

  const catSel = document.getElementById("catSelect");
  if (catSel) {
    catSel.innerHTML = "";
    getActiveCats().forEach(c => {
      const opt = document.createElement("option");
      opt.value = c.name; opt.textContent = c.name;
      if (c.name === selectedCatId) opt.selected = true;
      catSel.appendChild(opt);
    });
    catSel.onchange = () => {
      selectedCatId = catSel.value;
      if (titleEl) titleEl.textContent = `${selectedCatId}'s Statistics`;
      updateStatistics();
    };
  }

  const MONTHS = [
    ["01","ม.ค."],["02","ก.พ."],["03","มี.ค."],["04","เม.ย."],["05","พ.ค."],["06","มิ.ย."],
    ["07","ก.ค."],["08","ส.ค."],["09","ก.ย."],["10","ต.ค."],["11","พ.ย."],["12","ธ.ค."]
  ];
  const monthEl = document.getElementById("monthSelect");
  if (monthEl) {
    monthEl.innerHTML = "";
    MONTHS.forEach(([v,t]) => {
      const opt = document.createElement("option");
      opt.value = v; opt.textContent = t; monthEl.appendChild(opt);
    });
    const now = new Date();
    monthEl.value = String(now.getMonth()+1).padStart(2,"0");
  }

  fetch(`${API_BASE}/api/statistics/years`)
    .then(r => r.json())
    .then(({years}) => {
      availableYears = (years || []).slice();
      const startSel = document.getElementById("yearStartSelect");
      const endSel   = document.getElementById("yearSelect");
      [startSel, endSel].forEach(sel => {
        if (!sel) return;
        sel.innerHTML = "";
        availableYears.forEach(y => {
          const opt = document.createElement("option");
          opt.value = String(y); opt.textContent = String(y);
          sel.appendChild(opt);
        });
      });

      if (availableYears.length) {
        const minY = availableYears[0], maxY = availableYears[availableYears.length-1];
        if (startSel) startSel.value = String(minY);
        if (endSel)   endSel.value   = String(maxY);
      }

      const periodEl = document.getElementById("periodSelect");
      if (periodEl) {
        periodEl.value = "daily";
        periodEl.onchange = updateDateFilter;
      }
      updateDateFilter();
      updateStatistics();
    })
    .catch(handleFetchError);
}

function updateDateFilter() {
  const period = document.getElementById("periodSelect")?.value || "daily";

  // ซ่อนทุกตัวก่อน
  ["yearSelect","monthSelect","yearStartSelect","dateRangeWrapper"].forEach(hideEl);

  if (period === "daily") {
    showEl("yearSelect");
    showEl("monthSelect");
  } else if (period === "monthly") {
    showEl("yearSelect");
  } else if (period === "yearly") {
    showEl("yearStartSelect");
    showEl("yearSelect");
  } else if (period === "range") {
    showEl("dateRangeWrapper");
  }
}


function updateStatistics() {
  searchStatistics();
}

function searchStatistics() {
  if (!selectedCatId) return;
  const qs = new URLSearchParams();
  qs.set("cat", selectedCatId);

  const period = document.getElementById("periodSelect").value;
  qs.set("period", period);

  if (period === "range") {
    // 1) ถ้าเลือกผ่านปฏิทิน (flatpickr) จะได้ selectedDates 2 ค่า
    if (dateRangePicker && Array.isArray(dateRangePicker.selectedDates) && dateRangePicker.selectedDates.length === 2) {
      const [sDate, eDate] = dateRangePicker.selectedDates;
      const sISO = toISODateLocal(sDate);
      const eISO = toISODateLocal(eDate);

      if (!sISO || !eISO) {
        alert("กรุณาเลือกช่วงวันที่ให้ถูกต้อง (รูปแบบ dd/mm/yyyy - dd/mm/yyyy)");
        return;
      }
      if (sISO > eISO) {
        alert("วันเริ่มต้นต้องไม่มากกว่าวันสิ้นสุด");
        return;
      }

      qs.set("start_date", sISO);
      qs.set("end_date", eISO);
    } else {
      // 2) fallback: พิมพ์เอง dd/mm/yyyy - dd/mm/yyyy
      const raw = document.getElementById("dateRange")?.value || "";
      const parsed = parseRangeInputToISO(raw);

      if (!parsed) {
        alert("กรุณาเลือกช่วงวันที่ให้ถูกต้อง (รูปแบบ dd/mm/yyyy - dd/mm/yyyy)");
        return;
      }
      if (parsed.startISO > parsed.endISO) {
        alert("วันเริ่มต้นต้องไม่มากกว่าวันสิ้นสุด");
        return;
      }

      qs.set("start_date", parsed.startISO);
      qs.set("end_date", parsed.endISO);
    }
  } else if (period === "daily") {
    qs.set("year", document.getElementById("yearSelect").value);
    qs.set("month", document.getElementById("monthSelect").value);
  } else if (period === "monthly") {
    qs.set("year", document.getElementById("yearSelect").value);
  } else if (period === "yearly") {
    qs.set("start_year", document.getElementById("yearStartSelect").value);
    qs.set("end_year", document.getElementById("yearSelect").value);
  }

  fetch(`${API_BASE}/api/statistics?${qs.toString()}`)
    .then(r => r.json())
    .then(drawStatisticsAligned)
    .catch(handleFetchError);
}

/* ===== renderer รายวันเดียว ใช้ทั้ง daily & range (NO SLEEP) ===== */
function renderDailyLine(labelsYMD, raw) {
  const rawLabels = raw.labels || [];
  const series    = raw.series || {};

  const eat = alignSeries(labelsYMD, rawLabels, series.eatCount || []);
  const exc = alignSeries(labelsYMD, rawLabels, series.excreteCount || []);

  const ctx = document.getElementById("statsChart");
  if (statsChartInstance) { statsChartInstance.destroy(); statsChartInstance = null; }

  statsChartInstance = new Chart(ctx, {
    type: "line",
    data: {
      labels: labelsYMD,
      datasets: [
        { label: "Eat (count)",     data: eat, borderWidth: 2, tension: 0.2, pointRadius: 2 },
        { label: "Excrete (count)", data: exc, borderWidth: 2, tension: 0.2, pointRadius: 2 }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { title: { display: true, text: "Date" }, ticks: { autoSkip: true, autoSkipPadding: 16, maxRotation: 0 }, grid: { display: false } },
        y: { beginAtZero: true, title: { display: true, text: "Count" } }
      },
      plugins: { legend: { position: "bottom" }, tooltip: { mode: "index", intersect: false } }
    }
  });

  // Summary cards: average per selected unit
  // NOTE: average is computed only from buckets that have data (non-null)
  const curPeriod = document.getElementById("periodSelect")?.value || "daily";
  updateSummaryCardsFromSeries(curPeriod, eat, exc);
}

/* ===== วาดกราฟ + จัดช่วงให้ตรงสเปค ===== */
function drawStatisticsAligned(data) {
  if (!data) return;
  const period = document.getElementById("periodSelect")?.value || "daily";

  if (period === "range") {
    const parsed = getSelectedRangeISO();
    if (!parsed) return;
    if (parsed.startISO > parsed.endISO) return;
    const labels = buildDateListInclusive(parsed.startISO, parsed.endISO);
    renderDailyLine(labels, data);
    return;
  }

  if (period === "daily") {
    const year  = document.getElementById("yearSelect")?.value || String(new Date().getFullYear());
    const month = document.getElementById("monthSelect")?.value || String(new Date().getMonth()+1).padStart(2,"0");
    const end = lastDayOfYearMonth(year, month);
    const days = end.getDate();
    const labels = Array.from({ length: days }, (_, i) => `${year}-${month}-${String(i + 1).padStart(2,"0")}`);
    renderDailyLine(labels, data);
    return;
  }

  // monthly / yearly
  const year   = document.getElementById("yearSelect")?.value || "";
  const startY = document.getElementById("yearStartSelect")?.value || "";

  let targetLabels = [];
  if (period === "monthly") {
    targetLabels = [...Array(12)].map((_,i) => `${String(year).padStart(4,"0")}-${String(i+1).padStart(2,"0")}`);
  } else { // yearly
    const s = parseInt(startY || (availableYears[0] || new Date().getFullYear()), 10);
    const e = parseInt(year   || (availableYears[availableYears.length-1] || s), 10);
    targetLabels = rangeYears(Math.min(s,e), Math.max(s,e)).map(y => String(y));
  }

  const rawLabels = data.labels || [];
  const S = data.series || {};
  const eat = alignSeries(targetLabels, rawLabels, S.eatCount || []);
  const exc = alignSeries(targetLabels, rawLabels, S.excreteCount || []);

  const ctx = document.getElementById("statsChart");
  if (statsChartInstance) { statsChartInstance.destroy(); statsChartInstance = null; }

  statsChartInstance = new Chart(ctx, {
    type: "line",
    data: {
      labels: targetLabels,
      datasets: [
        { label: "Eat (count)",     data: eat, borderWidth: 2, tension: 0.25 },
        { label: "Excrete (count)", data: exc, borderWidth: 2, tension: 0.25 }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: { y: { beginAtZero: true, title: { display: true, text: "Count" } } },
      plugins: { legend: { position: "bottom" }, tooltip: { mode: "index", intersect: false } },
      interaction: { mode: "index", intersect: false }
    }
  });

  // Summary cards: average per selected unit
  // NOTE: average is computed only from buckets that have data (non-null)
  updateSummaryCardsFromSeries(period, eat, exc);
}

/* ===== Helpers ===== */
function alignSeries(targetLabels, rawLabels, rawSeries) {
  // ถ้าไม่มีข้อมูลของ label นั้น ให้คืนค่าเป็น null (Chart.js จะไม่ plot จุดนั้น)
  const m = new Map();
  rawLabels.forEach((lb, i) => {
    const v = rawSeries[i];
    if (v === null || v === undefined) return;
    const n = Number(v);
    if (Number.isFinite(n)) m.set(String(lb), n);
  });

  return targetLabels.map((lb) => {
    const key = String(lb);
    return m.has(key) ? Number(m.get(key)) : null;
  });
}

function sum(arr) {
  // รวมเฉพาะค่าที่เป็นตัวเลข (ข้าม null)
  return (arr || []).reduce((a, b) => a + (Number.isFinite(Number(b)) ? Number(b) : 0), 0);
}

function lastDayOfYearMonth(y, m) {
  const Y = parseInt(y || new Date().getFullYear(), 10);
  const M = parseInt(m || (new Date().getMonth()+1), 10);
  return new Date(Y, M, 0);
}
function lastNDates(endDate, N) {
  const out = [];
  const end = new Date(endDate.getFullYear(), endDate.getMonth(), endDate.getDate());
  for (let i = N-1; i >= 0; i--) {
    const d = new Date(end);
    d.setDate(end.getDate() - i);
    out.push(d);
  }
  return out;
}
function fmtYMD(d){
  const y=d.getFullYear();
  const m=String(d.getMonth()+1).padStart(2,"0");
  const dd=String(d.getDate()).padStart(2,"0");
  return `${y}-${m}-${dd}`;
}



function initDateRangePicker() {
  const el = document.getElementById("dateRange");
  if (!el) return;

  // ใช้ flatpickr (โหลดจาก CDN ใน index.html) เพื่อให้มี calendar popup และเลือกช่วง (range)
  if (window.flatpickr) {
    dateRangePicker = window.flatpickr(el, {
      mode: "range",
      dateFormat: "d/m/Y",
      allowInput: true,
      rangeSeparator: " - "
    });
  } else {
    // fallback: ยังพิมพ์เองได้ตาม placeholder
    dateRangePicker = null;
  }
}

function openDateRangePicker() {
  if (dateRangePicker && typeof dateRangePicker.open === "function") {
    dateRangePicker.open();
    return;
  }
  document.getElementById("dateRange")?.focus();
}

function toISODateLocal(d) {
  if (!(d instanceof Date) || isNaN(d.getTime())) return null;
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function parseRangeInputToISO(rangeStr) {
  const s = String(rangeStr || "").trim();
  if (!s) return null;

  const m = s.match(/^(\d{1,2}\/\d{1,2}\/\d{4})\s*-\s*(\d{1,2}\/\d{1,2}\/\d{4})$/);
  if (!m) return null;

  const startISO = parseDMYToISO(m[1]);
  const endISO = parseDMYToISO(m[2]);
  if (!startISO || !endISO) return null;

  return { startISO, endISO };
}

/**
 * ดึงช่วงวันที่ที่ user เลือกจาก UI (Statistics -> period=range)
 * รองรับทั้งเลือกจากปฏิทิน (flatpickr) และพิมพ์เองในช่อง dateRange
 * @returns {{startISO:string,endISO:string}|null}
 */
function getSelectedRangeISO() {
  // 1) ถ้าเลือกผ่านปฏิทิน (flatpickr) จะได้ selectedDates 2 ค่า
  if (dateRangePicker && Array.isArray(dateRangePicker.selectedDates) && dateRangePicker.selectedDates.length === 2) {
    const [sDate, eDate] = dateRangePicker.selectedDates;
    const startISO = toISODateLocal(sDate);
    const endISO = toISODateLocal(eDate);
    if (!startISO || !endISO) return null;
    return { startISO, endISO };
  }

  // 2) fallback: พิมพ์เอง dd/mm/yyyy - dd/mm/yyyy
  const raw = document.getElementById("dateRange")?.value || "";
  return parseRangeInputToISO(raw);
}


function parseDMYToISO(dmy) {
  // รับรูปแบบ dd/mm/yyyy แล้วคืนค่า yyyy-mm-dd (หรือ null ถ้าไม่ถูกต้อง)
  const s = String(dmy || "").trim();
  if (!s) return null;
  const m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (!m) return null;
  const dd = Number(m[1]);
  const mm = Number(m[2]);
  const yyyy = Number(m[3]);
  if (!Number.isFinite(dd) || !Number.isFinite(mm) || !Number.isFinite(yyyy)) return null;
  if (yyyy < 1900 || yyyy > 3000) return null;
  if (mm < 1 || mm > 12) return null;
  if (dd < 1 || dd > 31) return null;

  // validate real date
  const dt = new Date(yyyy, mm - 1, dd);
  if (dt.getFullYear() !== yyyy || (dt.getMonth() + 1) !== mm || dt.getDate() !== dd) return null;
  return fmtYMD(dt);
}
function rangeYears(s,e){ const out=[]; for(let y=s; y<=e; y++) out.push(y); return out; }
function buildDateListInclusive(startISO, endISO) {
  const res = [];
  let d = new Date(startISO);
  const end = new Date(endISO);
  d = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const endNorm = new Date(end.getFullYear(), end.getMonth(), end.getDate());
  while (d <= endNorm) {
    res.push(fmtYMD(d));
    d.setDate(d.getDate() + 1);
  }
  return res;
}

function goBackFromStatistics() {
  document.getElementById("statisticsPage")?.classList.add("hidden");
  document.getElementById("catDetailPage")?.classList.remove("hidden");
  document.getElementById("systemConfigPage")?.classList.add("hidden");
}


/* =========================
 * 9.2) TIMELINE (10s slots) - per-slot scroll
 * ========================= */

/* =====================
 * TIMELINE (10s / 1h)
 * ===================== */

function showEl(id){ const el=document.getElementById(id); if(el) el.classList.remove("hidden"); }
function hideEl(id){ const el=document.getElementById(id); if(el) el.classList.add("hidden"); }
function escapeHtml(s){
  const str = String(s ?? "");
  return str
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;")
    .replaceAll("'","&#39;");
}

function escapeAttr(s) {
  // ใช้สำหรับใส่ใน attribute HTML
  return escapeHtml(String(s ?? "")).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}


let timelineBefore = null;        // cursor (datetime string) to load older slots (10s view)
let timelineHasMore = true;
let timelineIsLoading = false;
let timelineAutoTimer = null;
const TIMELINE_PAGE_SIZE = 300;   // 300 slots = ~50 นาที (10s/slot)

function getTimelineGranularity() {
  const el = document.getElementById("timelineGranularity");
  return (el?.value || "10s");
}

function setTimelineGranularity(val) {
  const el = document.getElementById("timelineGranularity");
  if (el) el.value = val;
}

function showTimelinePage() {
  // hide others
  [
    "homePage","cameraPage","catPage","profilePage","catDetailPage",
    "systemConfigPage","notificationsPage","alertsPage","statisticsPage"
  ].forEach(id => document.getElementById(id)?.classList.add("hidden"));

  document.getElementById("timelinePage")?.classList.remove("hidden");

  // default date = today
  const dateEl = document.getElementById("timelineDate");
  if (dateEl && !dateEl.value) {
    const now = new Date();
    const yyyy = now.getFullYear();
    const mm = String(now.getMonth() + 1).padStart(2, "0");
    const dd = String(now.getDate()).padStart(2, "0");
    dateEl.value = `${yyyy}-${mm}-${dd}`;
  }

  // bind change granularity (กัน bind ซ้ำด้วย flag)
  const granEl = document.getElementById("timelineGranularity");
  if (granEl && !granEl.dataset.bound) {
    granEl.addEventListener("change", () => reloadTimeline());
    granEl.dataset.bound = "1";
  }

  updateTimelineTitle();
  reloadTimeline();
}

function updateTimelineTitle() {
  const titleEl = document.getElementById("timelineTitle");
  if (!titleEl) return;

  const g = getTimelineGranularity();
  const gLabel = (g === "1h") ? "1 ชั่วโมง" : "10 วินาที";
  const name = selectedCatId ? selectedCatId : "ทุกสี/ทุกตัว";
  titleEl.textContent = `Timeline: ${name} (${gLabel})`;
}

function reloadTimeline() {
  updateTimelineTitle();

  const g = getTimelineGranularity();

  // toggle view
  const listEl = document.getElementById("timelineList");
  const tableWrap = document.getElementById("timelineTableWrap");
  if (g === "1h") {
    // show table
    listEl?.classList.add("hidden");
    tableWrap?.classList.remove("hidden");
    hideEl("timelineEnd");
    stopTimelineAutoRefresh();
    loadTimelineTable();
    return;
  }

  // 10s view needs selected cat
  if (!selectedCatId) {
    alert("โหมด 10 วินาที ต้องเลือกแมวก่อน — จะสลับเป็นโหมด 1 ชั่วโมงให้");
    setTimelineGranularity("1h");
    reloadTimeline();
    return;
  }

  tableWrap?.classList.add("hidden");
  listEl?.classList.remove("hidden");

  // reset state
  timelineBefore = null;
  timelineHasMore = true;
  timelineIsLoading = false;
  if (listEl) listEl.innerHTML = "";
  hideEl("timelineEnd");
  loadTimelineChunk(true);

  const autoEl = document.getElementById("timelineAutoRefresh");
  if (autoEl?.checked) startTimelineAutoRefresh();
  else stopTimelineAutoRefresh();
}

function startTimelineAutoRefresh() {
  stopTimelineAutoRefresh();
  timelineAutoTimer = setInterval(() => {
    const list = document.getElementById("timelineList");
    if (!list) return;
    if (list.scrollTop <= 30) {
      // reload latest chunk (reset cursor)
      timelineBefore = null;
      timelineHasMore = true;
      list.innerHTML = "";
      hideEl("timelineEnd");
      loadTimelineChunk(true);
    }
  }, 8000);
}

function stopTimelineAutoRefresh() {
  if (timelineAutoTimer) clearInterval(timelineAutoTimer);
  timelineAutoTimer = null;
}

function goBackFromTimeline() {
  stopTimelineAutoRefresh();
  showCatPage();
}

/* ---------- 10s view ---------- */
function loadTimelineChunk(resetScrollTop) {
  if (timelineIsLoading || !timelineHasMore) return;
  timelineIsLoading = true;
  showEl("timelineLoading");

  const date = document.getElementById("timelineDate")?.value;
  const params = new URLSearchParams();
  params.set("cat", selectedCatId);
  if (date) params.set("date", date);
  params.set("limit", String(TIMELINE_PAGE_SIZE));
  if (timelineBefore) params.set("before", timelineBefore);

  const url = `${ENDPOINTS.timeline}?${params.toString()}`;
  fetch(url)
    .then(r => r.json())
    .then(payload => {
      const rows = payload?.rows || [];
      timelineHasMore = !!payload?.has_more;
      timelineBefore = payload?.next_before || null;

      renderTimelineListRows(rows);

      const meta = document.getElementById("timelineMeta");
      if (meta) {
        const day = payload?.date || date || "-";
        const loaded = document.querySelectorAll("#timelineList .timeline-row").length;
        meta.textContent = `วันที่: ${day} | โหลดแล้ว: ${loaded} แถว | แสดง: 10 วินาที`;
      }

      if (!timelineHasMore) showEl("timelineEnd");

      if (resetScrollTop) {
        const list = document.getElementById("timelineList");
        if (list) list.scrollTop = 0;
      }
    })
    .catch(err => {
      console.error("loadTimelineChunk error:", err);
      alert("โหลด timeline ไม่สำเร็จ");
    })
    .finally(() => {
      timelineIsLoading = false;
      hideEl("timelineLoading");
    });
}

function renderTimelineListRows(rows) {
  const list = document.getElementById("timelineList");
  if (!list) return;

  for (const r of rows) {
    const timeTxt = (r.date_slot || "-");
    const roomTxt = (r.room || "-");
    const actTxt = (r.activity || "-");
    const st = (r.status || "-").toUpperCase();

    const row = document.createElement("div");
    row.className = "timeline-row";

    const statusClass = (st === "F") ? "st-found" : (st === "NF") ? "st-notfound" : "st-unknown";

    row.innerHTML = `
      <div class="tl-time">${escapeHtml(timeTxt)}</div>
      <div class="tl-room">${escapeHtml(roomTxt)}</div>
      <div class="tl-activity">${escapeHtml(actTxt)}</div>
      <div class="tl-status ${statusClass}">${escapeHtml(st)}</div>
    `;
    list.appendChild(row);
  }
}

// infinite scroll for 10s view
document.addEventListener("DOMContentLoaded", () => {
  const list = document.getElementById("timelineList");
  if (!list) return;

  list.addEventListener("scroll", () => {
    // only when 10s view is active
    if (getTimelineGranularity() !== "10s") return;

    const nearBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 40;
    if (nearBottom) loadTimelineChunk(false);
  });
});

/* ---------- 1h view (table) ---------- */
function loadTimelineTable() {
  showEl("timelineLoading");

  const date = document.getElementById("timelineDate")?.value;
  const params = new URLSearchParams();
  if (date) params.set("date", date);

  const url = `${ENDPOINTS.timelineTable}?${params.toString()}`;
  fetch(url)
    .then(r => r.json())
    .then(payload => {
      const meta = document.getElementById("timelineMeta");
      if (meta) {
        meta.textContent = `วันที่: ${payload?.date || (date || "-")} | แสดง: รายชั่วโมง (00-23)`;
      }
      renderTimelineHourTable(payload);
    })
    .catch(err => {
      console.error("loadTimelineTable error:", err);
      alert("โหลดตาราง timeline ไม่สำเร็จ");
    })
    .finally(() => hideEl("timelineLoading"));
}

function renderTimelineHourTable(payload) {
  const table = document.getElementById("timelineTable");
  if (!table) return;

  const day = payload?.date || "-";
  const hours = payload?.hours || [];
  const rows = payload?.rows || [];

  // header (ไม่มีคอลัมน์ Activities)
  const thead = `<thead><tr>
    <th class="sticky-col col-date">Date</th>
    <th class="sticky-col-2 col-color">Color</th>
    <th class="sticky-col-3 col-cat">Cat_Name</th>
    ${hours.map(h => `<th class="hour-col">${h}.00</th>`).join("")}
  </tr></thead>`;

  // body
  let tbody = `<tbody>`;
  for (const r of rows) {
    const color = r.color || "-";
    const catName = r.cat_name || "-";
    const cells = r.cells || {};

    tbody += `<tr>
      <td class="sticky-col col-date">${day}</td>
      <td class="sticky-col-2 col-color">${escapeHtml(color)}</td>
      <td class="sticky-col-3 col-cat">${escapeHtml(catName)}</td>
      ${hours.map(h => {
        const val = cells[h] ?? "-";
        // val อาจมี <br> อยู่แล้ว (จาก backend) จึงห้าม escape ทั้งก้อน
        if (val === "-" || val === "" || val == null) {
          return `<td class="movement-empty">-</td>`;
        }
        if (String(val).toLowerCase().includes("not found")) {
          return `<td class="movement-nf">${escapeHtml(val)}</td>`;
        }
        return `<td class="movement-cell">${val}</td>`;
      }).join("")}
    </tr>`;
  }
  tbody += `</tbody>`;

  table.innerHTML = thead + tbody;
}


/* =========================
 * 3.1) SYSTEM CONFIG (Global / Per-cat by color)
 * ========================= */
function populateConfigCatSelect() {
  const sel = document.getElementById("configCatSelect");
  if (!sel) return;

  const current = sel.value || "__global__";
  // เคลียร์ให้เหลือ option Global ก่อน
  sel.innerHTML = `<option value="__global__">Global (ค่ารวมระบบ)</option>`;

  const activeCats = (cats || []).filter(c => String(c.display_status) === "1" || c.display_status === 1);
  for (const c of activeCats) {
    const name = c.name;
    if (!name) continue;
    const color = c.color ? ` (${c.color})` : "";
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = `${name}${color}`;
    sel.appendChild(opt);
  }

  // restore selection if possible
  const opts = Array.from(sel.options).map(o => o.value);
  sel.value = opts.includes(current) ? current : "__global__";
}

// hook: เรียกทุกครั้งที่โหลด cats แล้ว เพื่อให้ dropdown พร้อมใช้งาน
(function hookCatsLoadForConfigSelect() {
  const _oldFetchCatDataFromAPI = fetchCatDataFromAPI;
  fetchCatDataFromAPI = function () {
    return _oldFetchCatDataFromAPI.apply(this, arguments);
  };

  // หลัง renderCatCards เสร็จ (ซึ่งอยู่ใน .then) เราไม่มี callback ตรง ๆ
  // เลยใช้ observer แบบเบา: เรียก populate ใน updateCatData + DOMContentLoaded ด้วย
})();

function getSelectedConfigScope() {
  const sel = document.getElementById("configCatSelect");
  if (!sel) return { scope: "global" };
  const v = sel.value || "__global__";
  if (v === "__global__") return { scope: "global" };
  const catName = v;

  // หา color เพื่อส่งให้ backend (backend จะ fallback ให้ถ้าไม่ส่ง)
  const cat = (cats || []).find(x => x && x.name === catName);
  const catColor = cat && cat.color ? cat.color : undefined;

  return { scope: "cat", catName, catColor };
}

function loadSystemConfig() {
  // ensure dropdown ready (ถ้า cats มาถึงแล้ว)
  populateConfigCatSelect();

  const scopeInfo = getSelectedConfigScope();
  let url = ENDPOINTS.systemConfig;
  if (scopeInfo.scope === "cat" && scopeInfo.catName) {
    url = `${ENDPOINTS.systemConfig}?cat=${encodeURIComponent(scopeInfo.catName)}`;
  }

  fetch(url)
    .then(res => res.json())
    .then(cfg => {
      if (!cfg) return;

      // เติมค่าให้ฟอร์ม (camelCase จาก backend)
      const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el && val !== undefined && val !== null) el.value = val;
      };

      setVal("alertNoCat", cfg.alertNoCat);
      setVal("alertNoEating", cfg.alertNoEating);
      setVal("minExcretion", cfg.minExcretion);
      setVal("maxExcretion", cfg.maxExcretion);
      setVal("maxCats", cfg.maxCats);

      // รีเฟรช Summary ให้สอดคล้องกับแมวที่เลือก
      loadSystemConfigSummaries();
    })
    .catch(handleFetchError);
}


function loadSystemConfigSummaries() {
  const wrap = document.getElementById("systemConfigSummaryList");
  if (!wrap) return;

  wrap.innerHTML = `<div class="summary-empty">กำลังโหลด...</div>`;

  const scopeInfo = getSelectedConfigScope();
  const qs = new URLSearchParams();
  if (scopeInfo.scope === "cat" && scopeInfo.catName) qs.set("cat", scopeInfo.catName);

  const url = qs.toString()
    ? `${ENDPOINTS.systemConfigSummaries}?${qs.toString()}`
    : ENDPOINTS.systemConfigSummaries;

  fetch(url)
    .then(r => r.json())
    .then(rows => {
      const data = Array.isArray(rows) ? rows : [];
      if (data.length === 0) {
        wrap.innerHTML = `<div class="summary-empty">ยังไม่มีค่าสรุปที่ซ้ำกัน ≥ 3 เดือน</div>`;
        return;
      }

      let html = `<table class="summary-table">
        <thead>
          <tr>
            <th>Cat</th>
            <th>Color</th>
            <th>alert_no_eat</th>
            <th>alert_no_excrete_max</th>
            <th>Months</th>
            <th>Latest</th>
            <th></th>
          </tr>
        </thead>
        <tbody>`;

      for (const r of data) {
        const catName = r?.catName || "-";
        const catColor = r?.catColor || "-";
        const alertNoEating = r?.alertNoEating ?? "-";
        const maxExcretion = r?.maxExcretion ?? "-";
        const months = r?.monthsCount ?? "-";
        const latest = r?.latestMonth ?? "-";

        html += `<tr>
          <td>${escapeHtml(catName)}</td>
          <td>${escapeHtml(catColor)}</td>
          <td>${escapeHtml(String(alertNoEating))}</td>
          <td>${escapeHtml(String(maxExcretion))}</td>
          <td>${escapeHtml(String(months))}</td>
          <td>${escapeHtml(String(latest))}</td>
          <td>
            <button class="summary-apply-btn"
              type="button"
              data-cat-color="${escapeAttr(catColor)}"
              data-cat-name="${escapeAttr(catName)}"
              data-alert-no-eating="${escapeAttr(String(alertNoEating))}"
              data-max-excretion="${escapeAttr(String(maxExcretion))}">
              แอดข้อมูลจากค่าสรุป
            </button>
          </td>
          </td>
        </tr>`;
      }

      html += `</tbody></table>`;
      wrap.innerHTML = html;
    })
    .catch(err => {
      console.error("loadSystemConfigSummaries error:", err);
      wrap.innerHTML = `<div class="summary-empty">โหลดค่าสรุปไม่สำเร็จ</div>`;
    });
}



function bindSystemConfigSummaryApply() {
  const wrap = document.getElementById("systemConfigSummaryList");
  if (!wrap || wrap.dataset.boundApply === "true") return;

  wrap.addEventListener("click", (e) => {
    const btn = e.target.closest && e.target.closest(".summary-apply-btn");
    if (!btn) return;

    const catColor = btn.dataset.catColor || "";
    const catName = btn.dataset.catName || "";
    const alertNoEating = Number(btn.dataset.alertNoEating);
    const maxExcretion = Number(btn.dataset.maxExcretion);

    applySummaryConfig(catColor, catName, alertNoEating, maxExcretion);
  });

  wrap.dataset.boundApply = "true";
}

function showSummaryMessage(msg, kind = "info") {
  const wrap = document.getElementById("systemConfigSummaryList");
  if (!wrap) return;

  let box = document.getElementById("systemConfigSummaryMessage");
  if (!box) {
    box = document.createElement("div");
    box.id = "systemConfigSummaryMessage";
    box.className = "summary-msg";
    wrap.parentElement?.insertBefore(box, wrap);
  }

  box.classList.remove("ok", "err", "info");
  box.classList.add(kind);
  box.textContent = msg;
  box.style.display = "block";

  // auto hide after a while
  window.clearTimeout(showSummaryMessage._t);
  showSummaryMessage._t = window.setTimeout(() => {
    box.style.display = "none";
  }, 4000);
}
function applySummaryConfig(catColor, catName, alertNoEating, maxExcretion) {
  if (!catColor) {
    showSummaryMessage("ไม่พบสีแมว (catColor) สำหรับการแอดค่า", "err");
    return;
  }

  fetch(ENDPOINTS.systemConfigApplySummary, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ catColor, catName, alertNoEating, maxExcretion }),
  })
    .then(async (r) => {
      let data = null;
      try {
        data = await r.json();
      } catch {
        // ignore
      }

      if (!r.ok) {
        const msg = data?.message || `แอดค่าจากค่าสรุปไม่สำเร็จ (HTTP ${r.status})`;
        showSummaryMessage(msg, "err");
        return;
      }

      showSummaryMessage("✅ แอดข้อมูลจากค่าสรุปเรียบร้อย", "ok");

      // reload current config + summaries
      loadSystemConfig();
      loadSystemConfigSummaries();
    })
    .catch((err) => {
      console.error("applySummaryConfig error:", err);
      showSummaryMessage("ไม่สามารถเชื่อมต่อ API ได้ กรุณาตรวจสอบว่า Flask ทำงานอยู่หรือไม่", "err");
    });
}

function saveSystemConfig() {
  const scopeInfo = getSelectedConfigScope();

  const getInt = (id) => {
    const el = document.getElementById(id);
    if (!el) return undefined;
    const v = parseInt(el.value, 10);
    return Number.isFinite(v) ? v : undefined;
  };

  const payload = {
    alertNoCat: getInt("alertNoCat"),
    alertNoEating: getInt("alertNoEating"),
    minExcretion: getInt("minExcretion"),
    maxExcretion: getInt("maxExcretion"),
    maxCats: getInt("maxCats"),
  };

  if (scopeInfo.scope === "cat") {
    payload.catName = scopeInfo.catName;
    if (scopeInfo.catColor) payload.catColor = scopeInfo.catColor;
  }

  fetch(ENDPOINTS.systemConfig, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
    .then(res => res.json())
    .then(result => {
      if (result && result.message) {
        alert("✅ บันทึกการตั้งค่าเรียบร้อย");
      } else {
        alert("⚠️ บันทึกการตั้งค่าไม่สำเร็จ");
      }
      loadSystemConfig();
      loadSystemConfigSummaries();
    })
    .catch(handleFetchError);
}

function resetSystemConfig() {
  const scopeInfo = getSelectedConfigScope();
  let url = `${ENDPOINTS.systemConfig}/reset`;
  if (scopeInfo.scope === "cat" && scopeInfo.catName) {
    url += `?cat=${encodeURIComponent(scopeInfo.catName)}`;
  }

  fetch(url, { method: "POST" })
    .then(res => res.json())
    .then(() => {
      alert("✅ รีเซ็ตการตั้งค่าเรียบร้อย");
      loadSystemConfig();
      loadSystemConfigSummaries();
    })
    .catch(handleFetchError);
}

// เรียก populate dropdown อีกครั้งทุก ๆ รอบ updateCatData เพื่อให้รายการแมวไม่ stale
const _oldUpdateCatData = updateCatData;
updateCatData = function () {
  return fetch(ENDPOINTS.cats)
    .then(res => res.json())
    .then(data => {
      cats = Array.isArray(data) ? data : [];
      renderCatCards(cats);
      updateOpenCatDetail();
      populateConfigCatSelect();
    })
    .catch(handleFetchError);
};

/* ADD THIS BLOCK TO script.js */

async function enablePush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    alert("Browser ไม่รองรับ Push Notification");
    return;
  }

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    alert("ต้องอนุญาตการแจ้งเตือนก่อน");
    return;
  }

  const reg = await navigator.serviceWorker.register("/sw.js");

  const keyResp = await fetch("/api/push/vapid_public_key");
  const { publicKey } = await keyResp.json();

  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey),
  });

  await fetch("/api/push/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sub),
  });

  alert("สมัครรับ Push สำเร็จ");
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding)
    .replace(/-/g, "+")
    .replace(/_/g, "/");

  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);

  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}


// ------------------------------
// LINE linking (Messaging API)
// ------------------------------
async function refreshLineStatus(){
  const el = document.getElementById('lineStatus');
  if(!el) return;
  el.textContent = 'สถานะ LINE: กำลังตรวจสอบ...';
  try{
    const data = await _getJSON('/api/line/status');
    el.textContent = data.linked ? 'สถานะ LINE: ✅ เชื่อมต่อแล้ว' : 'สถานะ LINE: ❌ ยังไม่ได้เชื่อมต่อ';
  }catch(e){
    el.textContent = 'สถานะ LINE: ตรวจสอบไม่ได้ (' + (e && e.message ? e.message : 'error') + ')';
  }
}

async function generateLineLinkCode(){
  const box = document.getElementById('lineLinkCodeBox');
  const el = document.getElementById('lineStatus');
  if(box) box.textContent = 'กำลังสร้างโค้ด...';
  if(el) el.textContent = 'สถานะ LINE: (รอเชื่อมต่อ)';
  try{
    // _postJSON() คืนค่าเป็น { ok, status, text, json }
    // โค้ดจริงอยู่ใน data.json.code
    const data = await _postJSON('/api/line/link_code', {});
    if(!data.ok){
      const msg = (data.json && data.json.error) ? data.json.error : (data.text || ('HTTP ' + data.status));
      throw new Error(msg);
    }
    const code = data.json ? data.json.code : null;
    if(box) box.textContent = code || '—';
  }catch(e){
    if(box) box.textContent = '—';
    alert('สร้างโค้ดไม่สำเร็จ: ' + (e && e.message ? e.message : e));
  }
}

// auto refresh when entering Notification Settings
const _origShowNotificationSettingsPage = window.showNotificationSettingsPage;
window.showNotificationSettingsPage = function(){
  if(typeof _origShowNotificationSettingsPage === 'function'){
    _origShowNotificationSettingsPage();
  }
  setTimeout(()=>{ refreshLineStatus(); }, 250);
};
