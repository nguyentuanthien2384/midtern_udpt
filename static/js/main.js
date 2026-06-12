/* ============================================
   ARS STORE — Main Application Logic
   ============================================ */

const API = '';
let activityLog = [];
let clusterNodes = [];

/* ─── Utilities ──────────────────────────── */
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

function escAttr(s) {
  return esc(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function safeToastType(type) {
  return ['success', 'error', 'info'].includes(type) ? type : 'info';
}

function safeLogType(type) {
  return ['ok', 'err', 'info'].includes(type) ? type : 'info';
}

function setBtnLoading(btnId, isLoading, text) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  if (isLoading) {
    btn.disabled = true;
    btn.dataset.origHtml = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ' + esc(text || 'Đang xử lý...');
  } else {
    btn.disabled = false;
    if (btn.dataset.origHtml) btn.innerHTML = btn.dataset.origHtml;
  }
}

/* ─── Toast Notifications ────────────────── */
function showToast(msg, type) {
  type = safeToastType(type || 'success');
  const container = document.getElementById('toastContainer');
  const icons = { success: 'fa-check-circle', error: 'fa-exclamation-circle', info: 'fa-info-circle' };
  const toast = document.createElement('div');
  toast.className = 'toast ' + type;
  toast.innerHTML = '<i class="fas ' + (icons[type] || icons.info) + '"></i> ' + esc(msg);
  container.appendChild(toast);
  setTimeout(function() {
    toast.classList.add('hide');
    setTimeout(function() { toast.remove(); }, 300);
  }, 3500);
}

/* ─── Navigation ─────────────────────────── */
function showPage(name, el) {
  document.querySelectorAll('.page').forEach(function(p) { p.classList.remove('active'); });
  document.querySelectorAll('.sidebar-item').forEach(function(i) { i.classList.remove('active'); });

  var page = document.getElementById('page-' + name);
  if (page) page.classList.add('active');
  if (el) el.classList.add('active');

  var titles = {
    dashboard: 'Tổng Quan',
    records: 'Danh Sách Dữ Liệu',
    add: 'Thêm Dữ Liệu',
    search: 'Tìm Kiếm & Xóa',
    servers: 'Trạng Thái Server',
    logs: 'Nhật Ký Hoạt Động'
  };
  var titleEl = document.getElementById('pageTitle');
  if (titleEl) titleEl.textContent = titles[name] || 'Tổng Quan';

  if (name === 'dashboard') loadDashboardStats();
  if (name === 'records') loadAllData();
  if (name === 'servers') loadClusterStatus();
}

/* ─── Clock ──────────────────────────────── */
function updateClock() {
  var el = document.getElementById('clockText');
  if (el) el.textContent = new Date().toLocaleTimeString('vi-VN');
}

/* ─── Logging ────────────────────────────── */
function addLog(msg, type) {
  var now = new Date().toLocaleTimeString('vi-VN');
  activityLog.unshift({ time: now, msg: msg, type: type || 'ok' });
  if (activityLog.length > 100) activityLog.pop();
  renderLogs();
}

function renderLogs() {
  var box = document.getElementById('logBox');
  if (!box) return;
  if (!activityLog.length) {
    box.innerHTML = '<div class="log-line"><span class="log-info">Chưa có hoạt động nào.</span></div>';
    return;
  }
  box.innerHTML = activityLog.map(function(l) {
    var type = safeLogType(l.type);
    return '<div class="log-line"><span class="log-time">[' + esc(l.time) + ']</span> <span class="log-' + type + '">' + esc(l.msg) + '</span></div>';
  }).join('');
}

function clearLogs() {
  activityLog = [];
  renderLogs();
  showToast('Đã xóa nhật ký', 'info');
}

/* ─── Dashboard Stats ────────────────────── */
async function loadDashboardStats() {
  try {
    var res = await fetch(API + '/api/dashboard/stats');
    var d = await res.json();

    setText('statTotalKeys', d.total_keys);
    setText('statOnlineNodes', d.online_nodes + '/' + d.total_nodes);
    setText('statTotalReplicas', d.total_replicas);
    setText('statClusterHealth', d.cluster_health + '%');

    // Cluster indicator in header
    var ind = document.getElementById('clusterIndicatorText');
    if (ind) ind.textContent = d.online_nodes + '/' + d.total_nodes + ' Online';

    // Data distribution
    var distEl = document.getElementById('dataDist');
    if (distEl && d.nodes) {
      var maxKeys = Math.max.apply(null, d.nodes.map(function(n) { return n.primary_count + n.replica_count; })) || 1;
      distEl.innerHTML = d.nodes.map(function(n) {
        var total = n.primary_count + n.replica_count;
        var pct = Math.round((total / maxKeys) * 100);
        var statusClass = n.status === 'ONLINE' ? 'color: var(--color-success)' : 'color: var(--color-danger)';
        return '<div class="dist-row">' +
          '<span class="dist-label"><i class="fas fa-circle" style="font-size:8px;' + statusClass + '"></i> ' + esc(n.id || n.node_id || ('Node ' + n.port)) + '</span>' +
          '<div class="dist-bar-bg"><div class="dist-bar" style="width:' + pct + '%"></div></div>' +
          '<span class="dist-val">' + total + ' keys</span>' +
          '</div>';
      }).join('');
    }
  } catch (e) {
    setText('statTotalKeys', '--');
    setText('statOnlineNodes', '--');
    setText('statTotalReplicas', '--');
    setText('statClusterHealth', '--');
  }
}

function setText(id, val) {
  var el = document.getElementById(id);
  if (el) el.textContent = val;
}

async function loadNodeOptions() {
  try {
    var res = await fetch(API + '/api/cluster/nodes');
    clusterNodes = await res.json();
    ['putPort', 'searchPort'].forEach(function(selectId) {
      var select = document.getElementById(selectId);
      if (!select) return;
      select.innerHTML = clusterNodes.map(function(n) {
        return '<option value="' + escAttr(n.id) + '">' + esc(n.label || (n.id + ' — ' + n.host + ':' + n.port)) + '</option>';
      }).join('');
    });
  } catch (e) {
    console.warn('Không tải được danh sách node:', e);
  }
}

function getDefaultNodeId() {
  return clusterNodes.length ? clusterNodes[0].id : 'node1';
}

/* ─── Cluster Status ─────────────────────── */
async function loadClusterStatus() {
  setBtnLoading('btnRefreshStatus', true, 'Đang tải...');
  try {
    var res = await fetch(API + '/api/cluster/status');
    var nodes = await res.json();
    var grid = document.getElementById('nodesGrid');
    if (!grid) return;

    grid.innerHTML = nodes.map(function(n, i) {
      var isOnline = n.status === 'ONLINE';
      var statusBadge = isOnline
        ? '<span class="badge badge-online"><span class="dot"></span> Online</span>'
        : '<span class="badge badge-offline"><span class="dot"></span> Offline</span>';

      var neighborsHtml = '';
      if (n.node_status) {
        var entries = Object.entries(n.node_status);
        neighborsHtml = '<div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--color-border)">' +
          '<div style="font-size:11px;font-weight:700;color:var(--color-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">Kết nối</div>' +
          entries.map(function(e) {
            var alive = e[1] === 'ALIVE';
            return '<div style="display:flex;align-items:center;gap:6px;font-size:12px;margin-bottom:4px">' +
              '<i class="fas fa-circle" style="font-size:6px;color:' + (alive ? 'var(--color-success)' : 'var(--color-danger)') + '"></i>' +
              esc(e[0]) + ' — <span style="color:' + (alive ? 'var(--color-success)' : 'var(--color-danger)') + '">' + esc(e[1]) + '</span></div>';
          }).join('') +
          '</div>';
      }

      var actionsHtml = isOnline
        ? '<button class="btn btn-sm btn-outline" data-node-ref="' + escAttr(n.id || n.node_id) + '" onclick="syncNode(this.dataset.nodeRef, this)"><i class="fas fa-sync"></i> Đồng bộ</button>'
        : '<span style="font-size:12px;color:var(--color-danger)"><i class="fas fa-exclamation-triangle"></i> Không phản hồi</span>';

      return '<div class="node-card ' + (isOnline ? 'online' : 'offline') + '">' +
        '<div class="node-card-head"><div class="node-name"><i class="fas fa-server"></i> ' + esc(n.id || n.node_id || ('Node ' + (i + 1))) + '</div>' + statusBadge + '</div>' +
        '<div class="node-addr">' + esc((n.host || 'localhost') + ':' + n.port) + '</div>' +
        '<div class="node-stats-row">' +
          '<div class="node-stat"><div class="node-stat-val">' + n.primary_count + '</div><div class="node-stat-label">Primary</div></div>' +
          '<div class="node-stat"><div class="node-stat-val">' + n.replica_count + '</div><div class="node-stat-label">Replica</div></div>' +
        '</div>' +
        neighborsHtml +
        '<div class="node-actions">' + actionsHtml + '</div></div>';
    }).join('');

    setText('lastUpdate', new Date().toLocaleTimeString('vi-VN'));
  } catch (e) {
    var grid2 = document.getElementById('nodesGrid');
    if (grid2) grid2.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><i class="fas fa-exclamation-triangle"></i><p>Không thể kết nối Management Server</p></div>';
  } finally {
    setBtnLoading('btnRefreshStatus', false);
  }
}

async function syncNode(nodeRef, btn) {
  var origHtml = '';
  if (btn) { btn.disabled = true; origHtml = btn.innerHTML; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Đồng bộ...'; }
  try {
    var res = await fetch(API + '/api/sync/' + encodeURIComponent(nodeRef), { method: 'POST' });
    var data = await res.json();
    if (!res.ok || data.status !== 'ok') throw new Error(data.message || 'Đồng bộ thất bại');
    showToast('Đồng bộ ' + nodeRef + ' thành công! Primary: ' + data.primary + ', Replica: ' + data.replica);
    addLog('Đồng bộ ' + nodeRef + ': primary=' + data.primary + ', replica=' + data.replica);
    loadClusterStatus();
  } catch (e) {
    showToast('Lỗi đồng bộ: ' + e, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = origHtml; }
  }
}

/* ─── All Data ───────────────────────────── */
async function loadAllData() {
  setBtnLoading('btnRefreshData', true, 'Đang tải...');
  var tbody = document.getElementById('dataTable');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="6" class="loading-state"><i class="fas fa-spinner fa-spin"></i> Đang tải dữ liệu...</td></tr>';
  try {
    var res = await fetch(API + '/api/all-data');
    var data = await res.json();
    if (!data.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty-state"><i class="fas fa-inbox"></i><p>Chưa có dữ liệu nào trong cluster</p></td></tr>';
      return;
    }
    tbody.innerHTML = data.map(function(d, idx) {
      var replicas = (d.replica_nodes || []).map(function(p) {
        return '<span class="node-tag"><i class="fas fa-copy"></i> ' + esc(p) + '</span>';
      }).join(' ') || '<span style="color:var(--color-muted)">—</span>';
      return '<tr>' +
        '<td style="color:var(--color-muted);font-size:12px;width:40px">' + (idx + 1) + '</td>' +
        '<td class="td-key">' + esc(d.key) + '</td>' +
        '<td>' + esc(d.value) + '</td>' +
        '<td><span class="node-tag"><i class="fas fa-server"></i> ' + esc(d.primary_node || '?') + '</span></td>' +
        '<td>' + replicas + '</td>' +
        '<td><button class="btn btn-danger btn-sm" data-key="' + escAttr(d.key) + '" onclick="deleteKey(this.dataset.key, this)"><i class="fas fa-trash"></i> Xóa</button></td>' +
        '</tr>';
    }).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state"><i class="fas fa-exclamation-triangle"></i><p>Lỗi kết nối server</p></td></tr>';
  } finally {
    setBtnLoading('btnRefreshData', false);
  }
}

function filterTable(q) {
  q = q.toLowerCase();
  document.querySelectorAll('#dataTable tr').forEach(function(r) {
    r.style.display = r.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}

/* ─── PUT ─────────────────────────────────── */
async function doPut() {
  var key = document.getElementById('putKey').value.trim();
  var value = document.getElementById('putValue').value.trim();
  var port = document.getElementById('putPort').value;
  if (!key) { showToast('Key không được để trống!', 'error'); return; }
  setBtnLoading('btnPut', true, 'Đang lưu...');
  try {
    var res = await fetch(API + '/api/put', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: key, value: value, node_id: port })
    });
    var data = await res.json();
    if (data.status === 'ok') {
      showToast('Lưu thành công! Key="' + key + '" → Node ' + data.node + ' (' + data.role + ')');
      addLog('PUT "' + key + '" = "' + value + '" → Node ' + data.node + ' [' + data.role + ']');
      document.getElementById('putKey').value = '';
      document.getElementById('putValue').value = '';
    } else {
      showToast('Lỗi: ' + (data.message || 'Unknown'), 'error');
      addLog('PUT "' + key + '" THẤT BẠI: ' + data.message, 'err');
    }
  } catch (e) {
    showToast('Lỗi kết nối: ' + e, 'error');
  } finally {
    setBtnLoading('btnPut', false);
  }
}

/* ─── GET ─────────────────────────────────── */
async function doGet() {
  var key = document.getElementById('searchKey').value.trim();
  var port = document.getElementById('searchPort').value;
  if (!key) { showToast('Nhập key cần tìm!', 'error'); return; }
  setBtnLoading('btnGet', true, 'Đang tìm...');
  try {
    var res = await fetch(API + '/api/get', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: key, node_id: port })
    });
    var data = await res.json();
    var box = document.getElementById('searchResult');
    box.classList.add('show');
    if (data.status === 'ok') {
      var replicaText = Array.isArray(data.replicas) ? data.replicas.join(', ') : (data.replicas || data.replica || '—');
      box.innerHTML = '<div style="margin-bottom:8px"><i class="fas fa-check-circle" style="color:var(--color-success)"></i> <strong>Tìm thấy!</strong></div>' +
        '<div><strong>Key:</strong> ' + esc(key) + '</div>' +
        '<div><strong>Value:</strong> <span style="color:var(--color-accent);font-weight:600">' + esc(data.value) + '</span></div>' +
        '<div><strong>Node xử lý:</strong> <span class="node-tag">' + esc(data.node) + '</span> (' + esc(data.role) + ')</div>' +
        '<div><strong>Primary:</strong> ' + esc(data.primary) + ' | <strong>Replica:</strong> ' + esc(replicaText) + '</div>';
      addLog('GET "' + key + '" → "' + data.value + '" từ Node ' + data.node);
    } else {
      box.innerHTML = '<div><i class="fas fa-times-circle" style="color:var(--color-danger)"></i> <strong>Không tìm thấy</strong> key "<strong>' + esc(key) + '</strong>"</div>';
      addLog('GET "' + key + '" → NOT FOUND', 'err');
    }
  } catch (e) {
    showToast('Lỗi: ' + e, 'error');
  } finally {
    setBtnLoading('btnGet', false);
  }
}

/* ─── DELETE ──────────────────────────────── */
async function doDelete() {
  var key = document.getElementById('searchKey').value.trim();
  var port = document.getElementById('searchPort').value;
  if (!key) { showToast('Nhập key cần xóa!', 'error'); return; }
  if (!confirm('Xóa key "' + key + '" khỏi toàn bộ cluster?')) return;
  setBtnLoading('btnDelete', true, 'Đang xóa...');
  try { await deleteKeyViaPort(key, port); }
  finally { setBtnLoading('btnDelete', false); }
}

async function deleteKey(key, btn) {
  if (!confirm('Xóa key "' + key + '"?')) return;
  var origHtml = '';
  if (btn) { btn.disabled = true; origHtml = btn.innerHTML; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>'; }
  try { await deleteKeyViaPort(key, getDefaultNodeId()); }
  finally { if (btn) { btn.disabled = false; btn.innerHTML = origHtml; } }
}

async function deleteKeyViaPort(key, port) {
  try {
    var res = await fetch(API + '/api/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: key, node_id: port })
    });
    var data = await res.json();
    if (data.status === 'ok') {
      showToast('Đã xóa "' + key + '" thành công!');
      addLog('DELETE "' + key + '" → Node ' + data.node);
      loadAllData();
    } else if (data.status === 'not_found') {
      showToast('Key "' + key + '" không tồn tại', 'info');
    } else {
      showToast('Lỗi: ' + (data.message || ''), 'error');
    }
  } catch (e) {
    showToast('Lỗi: ' + e, 'error');
  }
}

/* ─── Init ───────────────────────────────── */
document.addEventListener('DOMContentLoaded', function() {
  updateClock();
  loadNodeOptions();
  setInterval(updateClock, 1000);
  loadDashboardStats();
  setTimeout(loadClusterStatus, 500);
  setInterval(loadClusterStatus, 15000);
  setInterval(loadDashboardStats, 15000);
});
