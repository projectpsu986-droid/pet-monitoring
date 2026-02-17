// admin.js - Admin panel (approve/reject/set role)
function qs(sel) { return document.querySelector(sel); }

function showAlert(msg, type) {
  const el = qs('#adminAlert');
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

async function apiGet(url) {
  const res = await fetch(url, { credentials: 'include' });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
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

function escapeHtml(s) {
  return String(s ?? '')
    .replaceAll('&','&amp;')
    .replaceAll('<','&lt;')
    .replaceAll('>','&gt;')
    .replaceAll('"','&quot;')
    .replaceAll("'",'&#039;');
}

function roleSelectHtml(currentRole, userId, selectIdPrefix) {
  const rid = `${selectIdPrefix}${userId}`;
  const r = (currentRole || 'user').toLowerCase();
  return `
    <select class="admin-select" id="${rid}">
      <option value="user" ${r === 'user' ? 'selected' : ''}>user</option>
      <option value="admin" ${r === 'admin' ? 'selected' : ''}>admin</option>
    </select>
  `;
}

function renderPending(rows) {
  const tbody = qs('#pendingTbody');
  if (!tbody) return;

  if (!Array.isArray(rows) || rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="admin-muted">ไม่มีรายการรออนุมัติ</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map(u => `
    <tr>
      <td>${escapeHtml(u.id)}</td>
      <td>${escapeHtml(u.name)}</td>
      <td>${escapeHtml(u.email)}</td>
      <td>${roleSelectHtml(u.role, u.id, 'pendingRole_')}</td>
      <td>
        <button class="admin-btn small" data-action="approve" data-id="${escapeHtml(u.id)}">Approve</button>
        <button class="admin-btn small danger" data-action="reject" data-id="${escapeHtml(u.id)}">Reject</button>
        <button class="admin-btn small secondary" data-action="setrole_pending" data-id="${escapeHtml(u.id)}">Set role</button>
      </td>
    </tr>
  `).join('');

  // bind
  tbody.querySelectorAll('button[data-action]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const act = btn.dataset.action;
      const id = Number(btn.dataset.id);
      if (!Number.isFinite(id)) return;

      showAlert('', '');

      if (act === 'approve') {
        if (!confirm('ยืนยันอนุมัติผู้ใช้ ID ' + id + ' ?')) return;
        const { ok, data } = await apiPost('/api/admin/approve', { user_id: id });
        if (!ok || !data.ok) return showAlert('Approve ไม่สำเร็จ', 'error');
        showAlert('อนุมัติแล้ว', 'success');
        await reloadAll();
        return;
      }

      if (act === 'reject') {
        if (!confirm('ยืนยัน Reject และลบบัญชีที่ยังไม่อนุมัติ (ID ' + id + ') ?')) return;
        const { ok, data } = await apiPost('/api/admin/reject', { user_id: id });
        if (!ok || !data.ok) return showAlert('Reject ไม่สำเร็จ', 'error');
        showAlert('ลบรายการที่รออนุมัติแล้ว', 'success');
        await reloadAll();
        return;
      }

      if (act === 'setrole_pending') {
        const sel = qs('#pendingRole_' + id);
        const role = (sel?.value || '').toLowerCase();
        const { ok, data } = await apiPost('/api/admin/set_role', { user_id: id, role });
        if (!ok || !data.ok) return showAlert('ตั้ง role ไม่สำเร็จ', 'error');
        showAlert('ตั้ง role แล้ว', 'success');
        await reloadAll();
        return;
      }
    });
  });
}

let _allUsersCache = [];

function filterUsersAndRender() {
  const q = (qs('#userSearch')?.value || '').trim().toLowerCase();
  const rows = !q ? _allUsersCache : _allUsersCache.filter(u => {
    const name = String(u.name || '').toLowerCase();
    const email = String(u.email || '').toLowerCase();
    return name.includes(q) || email.includes(q);
  });

  const tbody = qs('#usersTbody');
  if (!tbody) return;

  if (!Array.isArray(rows) || rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="admin-muted">ไม่พบผู้ใช้</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map(u => {
    const approved = Number(u.is_approved) === 1;
    const badge = approved ? '<span class="badge ok">approved</span>' : '<span class="badge warn">pending</span>';
    return `
      <tr>
        <td>${escapeHtml(u.id)}</td>
        <td>${escapeHtml(u.name)}</td>
        <td>${escapeHtml(u.email)}</td>
        <td>${badge}</td>
        <td>${roleSelectHtml(u.role, u.id, 'userRole_')}</td>
        <td>
          <button class="admin-btn small secondary" data-action="setrole_user" data-id="${escapeHtml(u.id)}">Set role</button>
        </td>
      </tr>
    `;
  }).join('');

  tbody.querySelectorAll('button[data-action="setrole_user"]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = Number(btn.dataset.id);
      if (!Number.isFinite(id)) return;
      showAlert('', '');
      const sel = qs('#userRole_' + id);
      const role = (sel?.value || '').toLowerCase();
      const { ok, data } = await apiPost('/api/admin/set_role', { user_id: id, role });
      if (!ok || !data.ok) return showAlert('ตั้ง role ไม่สำเร็จ', 'error');
      showAlert('ตั้ง role แล้ว', 'success');
      await reloadAll();
    });
  });
}

async function reloadAll() {
  // pending
  const p = await apiGet('/api/admin/pending');
  if (!p.ok || !p.data.ok) {
    if (p.status === 401 || p.status === 403) {
      showAlert('ไม่มีสิทธิ์เข้าถึง (ต้องเป็นแอดมิน)', 'error');
      return;
    }
    showAlert('โหลดรายการ pending ไม่สำเร็จ', 'error');
  } else {
    renderPending(p.data.pending || []);
  }

  // users
  const u = await apiGet('/api/admin/users?include_pending=1');
  if (!u.ok || !u.data.ok) {
    showAlert('โหลดรายชื่อผู้ใช้ไม่สำเร็จ', 'error');
  } else {
    _allUsersCache = Array.isArray(u.data.users) ? u.data.users : [];
    filterUsersAndRender();
  }
}

async function bootstrap() {
  showAlert('', '');
  const meText = qs('#adminMeText');
  const me = await apiGet('/api/auth/me');

  if (!me.ok || !me.data || !me.data.logged_in) {
    window.location.href = '/login.html#login';
    return;
  }

  const role = (me.data.user?.role || '').toLowerCase();
  const email = me.data.user?.email || '';
  const name = me.data.user?.name || '';

  if (meText) meText.textContent = `ผู้ใช้: ${name} (${email}) • role=${role}`;

  if (role !== 'admin') {
    showAlert('บัญชีนี้ไม่ใช่แอดมิน จึงไม่สามารถเข้า Admin Panel ได้', 'error');
    qs('#pendingTbody').innerHTML = `<tr><td colspan="5" class="admin-muted">ต้องเป็น admin เท่านั้น</td></tr>`;
    qs('#usersTbody').innerHTML = `<tr><td colspan="6" class="admin-muted">ต้องเป็น admin เท่านั้น</td></tr>`;
    return;
  }

  await reloadAll();
}

document.addEventListener('DOMContentLoaded', () => {
  qs('#reloadBtn')?.addEventListener('click', () => reloadAll());
  qs('#userSearch')?.addEventListener('input', () => filterUsersAndRender());

  qs('#logoutBtn')?.addEventListener('click', async () => {
    await apiPost('/api/auth/logout', {});
    window.location.href = '/login.html#login';
  });

  bootstrap();
});
