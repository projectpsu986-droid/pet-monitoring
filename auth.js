// auth.js - Login/Register/Forgot/Reset
function qs(sel) { return document.querySelector(sel); }

function showAlert(msg, type) {
  const el = qs('#authAlert');
  if (!msg) {
    el.classList.add('hidden');
    el.textContent = '';
    el.classList.remove('error', 'success');
    return;
  }
  el.classList.remove('hidden');
  el.classList.remove('error', 'success');
  if (type) el.classList.add(type);
  el.textContent = msg;
}

function setSubtitle(text) {
  const el = qs('#authSubtitle');
  if (el) el.textContent = text;
}

function hideAllSections() {
  document.querySelectorAll('.auth-section').forEach(s => s.classList.add('hidden'));
}

function showSection(sectionId) {
  hideAllSections();
  showAlert('', '');
  qs(sectionId).classList.remove('hidden');
}

async function apiPost(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(body || {})
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

// Hash routing: 
//  #login
//  #register
//  #forgot
//  #reset?email=...&token=...
function parseHash() {
  const h = window.location.hash || '#login';
  const [path, query] = h.replace('#', '').split('?');
  const params = new URLSearchParams(query || '');
  return { path: path || 'login', params };
}

function go(path) {
  window.location.hash = '#' + path;
}

function applyRoute() {
  const { path, params } = parseHash();

  if (path === 'register') {
    setSubtitle('สร้างบัญชีด้วยอีเมล (ต้องรอแอดมินอนุมัติ)');
    showSection('#registerForm');
    return;
  }

  if (path === 'forgot') {
    setSubtitle('กรอกอีเมลเพื่อรับลิงก์รีเซ็ตรหัสผ่าน');
    showSection('#forgotForm');
    return;
  }

  if (path === 'reset') {
    const email = params.get('email') || '';
    const token = params.get('token') || '';
    if (!email || !token) {
      setSubtitle('ลิงก์รีเซ็ตไม่ถูกต้อง');
      showSection('#loginForm');
      showAlert('ลิงก์รีเซ็ตรหัสผ่านไม่ถูกต้องหรือไม่ครบถ้วน', 'error');
      return;
    }
    qs('#resetEmail').value = email;
    // store token in form dataset
    qs('#resetForm').dataset.token = token;
    setSubtitle('ตั้งรหัสผ่านใหม่');
    showSection('#resetForm');
    return;
  }

  // default login
  setSubtitle('ลงชื่อเข้าใช้เพื่อใช้งานระบบ');
  showSection('#loginForm');
}

window.addEventListener('hashchange', applyRoute);
document.addEventListener('DOMContentLoaded', () => {
  // If already logged in, go to app
  fetch('/api/auth/me', { credentials: 'include' })
    .then(r => r.json())
    .then(d => {
      if (d && d.logged_in) window.location.href = '/';
    })
    .catch(() => {});
  applyRoute();

  // nav buttons
  qs('#toRegisterBtn').addEventListener('click', () => go('register'));
  qs('#toForgotBtn').addEventListener('click', () => go('forgot'));
  qs('#toLoginBtn1').addEventListener('click', () => go('login'));
  qs('#toLoginBtn2').addEventListener('click', () => go('login'));
  qs('#toLoginBtn3').addEventListener('click', () => go('login'));

  // login
  qs('#loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    showAlert('', '');
    const email = qs('#loginEmail').value.trim();
    const password = qs('#loginPassword').value;
    const { ok, data } = await apiPost('/api/auth/login', { email, password });
    if (!ok || !data.ok) {
      if (data.error === 'not_approved') {
        showAlert('บัญชีนี้ยังไม่ได้รับการอนุมัติจากแอดมิน', 'error');
      } else {
        showAlert('อีเมลหรือรหัสผ่านไม่ถูกต้อง', 'error');
      }
      return;
    }
    window.location.href = '/';
  });

  // register
  qs('#registerForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    showAlert('', '');
    const name = qs('#regName').value.trim();
    const email = qs('#regEmail').value.trim();
    const password = qs('#regPassword').value;
    const { ok, data } = await apiPost('/api/auth/register', { name, email, password });
    if (!ok || !data.ok) {
      if (data.error === 'email_exists') {
        showAlert('อีเมลนี้ถูกใช้งานแล้ว', 'error');
      } else {
        showAlert('สมัครไม่สำเร็จ กรุณาลองใหม่', 'error');
      }
      return;
    }
    showAlert('สมัครสำเร็จ! บัญชีของคุณอยู่ระหว่างรออนุมัติจากแอดมิน', 'success');
    // optional: go back to login
    setTimeout(() => go('login'), 800);
  });

  // forgot
  qs('#forgotForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    showAlert('', '');
    const email = qs('#forgotEmail').value.trim();
    const { ok, data } = await apiPost('/api/auth/forgot', { email });
    if (!ok || !data.ok) {
      if (data.error === 'email_send_failed') {
        showAlert('ส่งอีเมลไม่สำเร็จ (ตรวจสอบการตั้งค่า SMTP ที่เซิร์ฟเวอร์)', 'error');
      } else {
        showAlert('ทำรายการไม่สำเร็จ กรุณาลองใหม่', 'error');
      }
      return;
    }
    showAlert('ถ้ามีบัญชีนี้อยู่ ระบบจะส่งลิงก์รีเซ็ตรหัสผ่านไปทางอีเมล', 'success');
  });

  // reset
  qs('#resetForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    showAlert('', '');
    const email = qs('#resetEmail').value.trim();
    const token = qs('#resetForm').dataset.token || '';
    const new_password = qs('#resetPassword').value;
    const { ok, data } = await apiPost('/api/auth/reset', { email, token, new_password });
    if (!ok || !data.ok) {
      if (data.error === 'token_expired') showAlert('ลิงก์หมดอายุ กรุณากดลืมรหัสผ่านใหม่', 'error');
      else showAlert('รีเซ็ตรหัสผ่านไม่สำเร็จ กรุณาตรวจสอบลิงก์และลองใหม่', 'error');
      return;
    }
    showAlert('ตั้งรหัสผ่านใหม่สำเร็จ! กำลังกลับไปหน้า Login...', 'success');
    setTimeout(() => go('login'), 800);
  });
});
