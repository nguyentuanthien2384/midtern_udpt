/* ============================================
   ARS STORE — Main Application Logic
   ============================================ */

const API = "";
let activityLog = [];
let clusterNodes = [];

/* ─── Utilities ──────────────────────────── */
function esc(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

function escAttr(s) {
  return esc(s).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function safeToastType(type) {
  return ["success", "error", "info"].includes(type) ? type : "info";
}

function safeLogType(type) {
  return ["ok", "err", "info", "warn"].includes(type) ? type : "info";
}

function setBtnLoading(btnId, isLoading, text) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  if (isLoading) {
    btn.disabled = true;
    btn.dataset.origHtml = btn.innerHTML;
    btn.innerHTML =
      '<i class="fas fa-spinner fa-spin"></i> ' + esc(text || "Đang xử lý...");
  } else {
    btn.disabled = false;
    if (btn.dataset.origHtml) btn.innerHTML = btn.dataset.origHtml;
  }
}

// Biến thể nhận thẳng phần tử nút (dùng cho nút render động trong card).
function setBtnLoadingEl(btn, isLoading, text) {
  if (!btn) return;
  if (isLoading) {
    btn.disabled = true;
    btn.dataset.origHtml = btn.innerHTML;
    btn.innerHTML =
      '<i class="fas fa-spinner fa-spin"></i> ' + esc(text || "Đang xử lý...");
  } else {
    btn.disabled = false;
    if (btn.dataset.origHtml) btn.innerHTML = btn.dataset.origHtml;
  }
}

/* ─── Toast Notifications ────────────────── */
function showToast(msg, type) {
  type = safeToastType(type || "success");
  const container = document.getElementById("toastContainer");
  const icons = {
    success: "fa-check-circle",
    error: "fa-exclamation-circle",
    info: "fa-info-circle",
  };
  const toast = document.createElement("div");
  toast.className = "toast " + type;
  toast.innerHTML =
    '<i class="fas ' + (icons[type] || icons.info) + '"></i> ' + esc(msg);
  container.appendChild(toast);
  setTimeout(function () {
    toast.classList.add("hide");
    setTimeout(function () {
      toast.remove();
    }, 300);
  }, 3500);
}

/* ─── Navigation ─────────────────────────── */
var PAGE_NAMES = ["dashboard", "records", "add", "search", "servers", "logs"];

function showPage(name, el) {
  if (PAGE_NAMES.indexOf(name) === -1) name = "dashboard";

  document.querySelectorAll(".page").forEach(function (p) {
    p.classList.remove("active");
  });
  document.querySelectorAll(".sidebar-item").forEach(function (i) {
    i.classList.remove("active");
  });

  var page = document.getElementById("page-" + name);
  if (page) page.classList.add("active");

  // Khi gọi từ click sẽ có el; khi khôi phục từ URL (reload/back) thì tự tìm mục tương ứng.
  if (!el)
    el = document.querySelector('.sidebar-item[data-page="' + name + '"]');
  if (el) el.classList.add("active");

  var titles = {
    dashboard: "Tổng Quan",
    records: "Danh Sách Dữ Liệu",
    add: "Thêm Dữ Liệu",
    search: "Tìm Kiếm & Xóa",
    servers: "Trạng Thái Server",
    logs: "Nhật Ký Hoạt Động",
  };
  var titleEl = document.getElementById("pageTitle");
  if (titleEl) titleEl.textContent = titles[name] || "Tổng Quan";

  // Ghi trang hiện tại vào URL để reload giữ nguyên trang (và back/forward hoạt động).
  if (location.hash.slice(1) !== name) {
    location.hash = name;
  }

  if (name === "dashboard") loadDashboardStats();
  if (name === "records") loadAllData();
  if (name === "servers") {
    loadClusterStatus();
    loadRingInfo();
  }
  if (name === "logs") loadServerLogs();
}

// Đồng bộ khi người dùng bấm Back/Forward hoặc sửa hash trực tiếp.
window.addEventListener("hashchange", function () {
  var name = location.hash.slice(1);
  if (PAGE_NAMES.indexOf(name) !== -1) showPage(name);
});

/* ─── Clock ──────────────────────────────── */
function updateClock() {
  var el = document.getElementById("clockText");
  if (el) el.textContent = new Date().toLocaleTimeString("vi-VN");
}

/* ─── Logging ──────────────────────────────
   Log lấy từ server (/api/logs) là nguồn chuẩn, đồng nhất với log trên
   Docker (`docker logs udpt-manager`). addLog() chỉ echo tức thời ở client
   cho mượt, sẽ được thay bằng bản server ở lần đồng bộ kế tiếp. */
var lastLogId = 0;
var serverLogsSupported = true;

function addLog(msg, type) {
  var now = new Date().toLocaleTimeString("vi-VN");
  activityLog.unshift({ time: now, msg: msg, type: type || "ok" });
  if (activityLog.length > 200) activityLog.pop();
  renderLogs();
  // Kéo bản log chính thức từ server ngay sau thao tác.
  setTimeout(loadServerLogs, 350);
}

function renderLogs() {
  var box = document.getElementById("logBox");
  if (!box) return;
  if (!activityLog.length) {
    box.innerHTML =
      '<div class="log-line"><span class="log-info">Chưa có hoạt động nào.</span></div>';
    return;
  }
  box.innerHTML = activityLog
    .map(function (l) {
      var type = safeLogType(l.type);
      return (
        '<div class="log-line"><span class="log-time">[' +
        esc(l.time) +
        ']</span> <span class="log-' +
        type +
        '">' +
        esc(l.msg) +
        "</span></div>"
      );
    })
    .join("");
}

async function loadServerLogs() {
  if (!serverLogsSupported) return;
  try {
    var res = await fetch(API + "/api/logs");
    if (!res.ok) throw new Error("HTTP " + res.status);
    var data = await res.json();
    var logs = data.logs || [];
    // Server giữ thứ tự cũ -> mới; UI hiển thị mới -> cũ.
    activityLog = logs
      .slice()
      .reverse()
      .map(function (l) {
        return { time: l.time, msg: l.msg, type: l.level };
      });
    lastLogId = data.last_id || lastLogId;
    renderLogs();
  } catch (e) {
    serverLogsSupported = false;
  }
}

async function clearLogs() {
  try {
    await fetch(API + "/api/logs/clear", { method: "POST" });
  } catch (e) {
    /* vẫn xóa phía client */
  }
  activityLog = [];
  lastLogId = 0;
  renderLogs();
  showToast("Đã xóa nhật ký", "info");
}

/* ─── Dashboard Stats ────────────────────── */
async function loadDashboardStats() {
  try {
    var res = await fetch(API + "/api/dashboard/stats");
    var d = await res.json();

    setText("statTotalKeys", d.total_keys);
    setText("statOnlineNodes", d.online_nodes + "/" + d.total_nodes);
    setText("statTotalReplicas", d.total_replicas);
    setText("statClusterHealth", d.cluster_health + "%");

    // Cluster indicator in header
    var ind = document.getElementById("clusterIndicatorText");
    if (ind) ind.textContent = d.online_nodes + "/" + d.total_nodes + " Online";

    // Data distribution
    var distEl = document.getElementById("dataDist");
    if (distEl && d.nodes) {
      var maxKeys =
        Math.max.apply(
          null,
          d.nodes.map(function (n) {
            return n.primary_count + n.replica_count;
          }),
        ) || 1;
      distEl.innerHTML = d.nodes
        .map(function (n) {
          var total = n.primary_count + n.replica_count;
          var pct = Math.round((total / maxKeys) * 100);
          var statusClass =
            n.status === "ONLINE"
              ? "color: var(--color-success)"
              : "color: var(--color-danger)";
          return (
            '<div class="dist-row">' +
            '<span class="dist-label"><i class="fas fa-circle" style="font-size:8px;' +
            statusClass +
            '"></i> ' +
            esc(n.id || n.node_id || "Node " + n.port) +
            "</span>" +
            '<div class="dist-bar-bg"><div class="dist-bar" style="width:' +
            pct +
            '%"></div></div>' +
            '<span class="dist-val">' +
            total +
            " keys</span>" +
            "</div>"
          );
        })
        .join("");
    }
  } catch (e) {
    setText("statTotalKeys", "--");
    setText("statOnlineNodes", "--");
    setText("statTotalReplicas", "--");
    setText("statClusterHealth", "--");
  }
}

function setText(id, val) {
  var el = document.getElementById(id);
  if (el) el.textContent = val;
}

async function loadNodeOptions() {
  try {
    var res = await fetch(API + "/api/cluster/nodes");
    clusterNodes = await res.json();
    ["putPort", "searchPort"].forEach(function (selectId) {
      var select = document.getElementById(selectId);
      if (!select) return;
      select.innerHTML = clusterNodes
        .map(function (n) {
          return (
            '<option value="' +
            escAttr(n.id) +
            '">' +
            esc(n.label || n.id + " — " + n.host + ":" + n.port) +
            "</option>"
          );
        })
        .join("");
    });
  } catch (e) {
    console.warn("Không tải được danh sách node:", e);
  }
}

function getDefaultNodeId() {
  return clusterNodes.length ? clusterNodes[0].id : "node1";
}

/* ─── Cluster Status ─────────────────────── */
async function loadClusterStatus(silent) {
  // Auto-refresh chạy ngầm (silent=true) để không xoay nút "Đang tải..." liên tục.
  if (!silent) setBtnLoading("btnRefreshStatus", true, "Đang tải...");
  try {
    var res = await fetch(API + "/api/cluster/status");
    var nodes = await res.json();
    var grid = document.getElementById("nodesGrid");
    if (!grid) return;

    populateRemoveSelect(nodes);

    grid.innerHTML = nodes
      .map(function (n, i) {
        var isOnline = n.status === "ONLINE";
        var statusBadge = isOnline
          ? '<span class="badge badge-online"><span class="dot"></span> Online</span>'
          : '<span class="badge badge-offline"><span class="dot"></span> Offline</span>';

        var neighborsHtml = "";
        if (n.node_status) {
          var entries = Object.entries(n.node_status);
          neighborsHtml =
            '<div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--color-border)">' +
            '<div style="font-size:11px;font-weight:700;color:var(--color-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">Kết nối</div>' +
            entries
              .map(function (e) {
                var alive = e[1] === "ALIVE";
                return (
                  '<div style="display:flex;align-items:center;gap:6px;font-size:12px;margin-bottom:4px">' +
                  '<i class="fas fa-circle" style="font-size:6px;color:' +
                  (alive ? "var(--color-success)" : "var(--color-danger)") +
                  '"></i>' +
                  esc(e[0]) +
                  ' — <span style="color:' +
                  (alive ? "var(--color-success)" : "var(--color-danger)") +
                  '">' +
                  esc(e[1]) +
                  "</span></div>"
                );
              })
              .join("") +
            "</div>";
        }

        var nodeRef = n.id || n.node_id;
        var canControl = !!n.control_capable;
        var controlMode = n.control_mode || "none";
        var managedProc = !!n.managed_process;
        var actionsHtml = "";
        if (isOnline) {
          actionsHtml +=
            '<button class="btn btn-sm btn-outline" data-node-ref="' +
            escAttr(nodeRef) +
            '" onclick="syncNode(this.dataset.nodeRef, this)"><i class="fas fa-sync"></i> Đồng bộ</button>';
          if (canControl) {
            var stopLabel =
              controlMode === "docker" || controlMode === "rpc" || managedProc
                ? "Tắt node"
                : "Tắt (force theo port)";
            actionsHtml +=
              '<button class="btn btn-sm btn-danger" data-node-ref="' +
              escAttr(nodeRef) +
              '" onclick="stopNode(this.dataset.nodeRef, this)"><i class="fas fa-power-off"></i> ' +
              esc(stopLabel) +
              "</button>";
          }
        } else if (canControl) {
          actionsHtml =
            '<button class="btn btn-sm btn-success" data-node-ref="' +
            escAttr(nodeRef) +
            '" onclick="startNode(this.dataset.nodeRef, this)"><i class="fas fa-play"></i> Bật node</button>';
        } else {
          actionsHtml =
            '<span style="font-size:12px;color:var(--color-danger)"><i class="fas fa-exclamation-triangle"></i> Không phản hồi</span>';
        }

        // Nút xóa node khỏi cluster (membership động) — luôn hiển thị.
        actionsHtml +=
          '<button class="btn btn-sm btn-remove-node" data-node-ref="' +
          escAttr(nodeRef) +
          '" onclick="removeNode(this.dataset.nodeRef, this)"><i class="fas fa-trash"></i> Xóa node</button>';

        return (
          '<div class="node-card ' +
          (isOnline ? "online" : "offline") +
          '">' +
          '<div class="node-card-head"><div class="node-name"><i class="fas fa-server"></i> ' +
          esc(n.id || n.node_id || "Node " + (i + 1)) +
          "</div>" +
          statusBadge +
          "</div>" +
          '<div class="node-addr">' +
          esc((n.host || "localhost") + ":" + n.port) +
          "</div>" +
          '<div class="node-stats-row">' +
          '<div class="node-stat"><div class="node-stat-val">' +
          n.primary_count +
          '</div><div class="node-stat-label">Primary</div></div>' +
          '<div class="node-stat"><div class="node-stat-val">' +
          n.replica_count +
          '</div><div class="node-stat-label">Replica</div></div>' +
          "</div>" +
          neighborsHtml +
          '<div class="node-actions">' +
          actionsHtml +
          "</div></div>"
        );
      })
      .join("");

    setText("lastUpdate", new Date().toLocaleTimeString("vi-VN"));
  } catch (e) {
    var grid2 = document.getElementById("nodesGrid");
    if (grid2)
      grid2.innerHTML =
        '<div class="empty-state" style="grid-column:1/-1"><i class="fas fa-exclamation-triangle"></i><p>Không thể kết nối Management Server</p></div>';
  } finally {
    if (!silent) setBtnLoading("btnRefreshStatus", false);
  }
}

/* ─── Quản lý membership: thêm / xóa node động ───────── */
function populateRemoveSelect(nodes) {
  var sel = document.getElementById("removeNodeSelect");
  if (!sel) return;
  var keep = sel.value;
  sel.innerHTML = (nodes || [])
    .map(function (n) {
      var id = n.id || n.node_id;
      var st = n.status === "ONLINE" ? "online" : "offline";
      return (
        '<option value="' + esc(id) + '">' + esc(id) + " (" + st + ")</option>"
      );
    })
    .join("");
  if (keep) sel.value = keep;
}

function membershipMsg(text, kind) {
  var el = document.getElementById("membershipMsg");
  if (!el) return;
  el.textContent = text || "";
  el.className = "membership-msg" + (kind ? " membership-msg-" + kind : "");
}

function refreshServersView() {
  // Cập nhật lại lưới node + biểu đồ ring sau khi đổi membership.
  setTimeout(function () {
    loadClusterStatus(true);
  }, 300);
  setTimeout(loadRingInfo, 700);
  setTimeout(loadRemovedNodes, 500);
}

async function loadRemovedNodes() {
  try {
    var res = await fetch(API + "/api/cluster/removed-nodes");
    var data = await res.json();
    var removed = (data && data.removed) || [];
    var group = document.getElementById("restoreGroup");
    var sel = document.getElementById("restoreNodeSelect");
    if (!group || !sel) return;
    if (removed.length === 0) {
      group.style.display = "none";
      sel.innerHTML = "";
      return;
    }
    group.style.display = "flex";
    sel.innerHTML = removed
      .map(function (n) {
        return (
          '<option value="' +
          esc(n.id) +
          '">' +
          esc(n.id) +
          " (" +
          esc(String(n.host)) +
          ":" +
          esc(String(n.port)) +
          ")</option>"
        );
      })
      .join("");
  } catch (e) {
    /* im lặng nếu manager chưa sẵn sàng */
  }
}

async function restoreNode() {
  var sel = document.getElementById("restoreNodeSelect");
  var id = sel ? sel.value : "";
  if (!id) {
    membershipMsg("Chọn node để khôi phục.", "err");
    return;
  }

  setBtnLoading("btnRestoreNode", true, "Đang khôi phục...");
  membershipMsg(
    "Đang khôi phục " + id + " và lấy lại dữ liệu từ cụm...",
    "info",
  );
  try {
    var res = await fetch(API + "/api/cluster/restore-node", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: id }),
    });
    var data = await res.json();
    if (data.status === "ok") {
      membershipMsg(
        "Đã khôi phục " +
          id +
          " — lấy lại " +
          (data.keys_migrated || 0) +
          " key từ cụm (dữ liệu hiện hành).",
        "ok",
      );
      addLog("KHÔI PHỤC node " + id, "ok");
      refreshServersView();
    } else {
      membershipMsg(
        "Lỗi: " + (data.message || "không khôi phục được node"),
        "err",
      );
    }
  } catch (e) {
    membershipMsg("Lỗi kết nối: " + e.message, "err");
  } finally {
    setBtnLoading("btnRestoreNode", false);
  }
}

async function addNode() {
  var id = (document.getElementById("addNodeId").value || "").trim();
  var host = (
    document.getElementById("addNodeHost").value || "127.0.0.1"
  ).trim();
  var portStr = (document.getElementById("addNodePort").value || "").trim();
  var port = portStr ? parseInt(portStr, 10) : null;
  if (!id) {
    membershipMsg("Nhập id node (vd: node4).", "err");
    return;
  }

  setBtnLoading("btnAddNode", true, "Đang thêm...");
  membershipMsg("Đang thêm " + id + " và di trú dữ liệu...", "info");
  try {
    var body = { id: id, host: host };
    // Local: port là cổng node lắng nghe. Docker: port (nếu có) là cổng publish ra host.
    if (port) {
      body.port = port;
      body.host_port = port;
    }
    var res = await fetch(API + "/api/cluster/add-node", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    var data = await res.json();
    if (data.status === "ok") {
      membershipMsg(
        "Đã thêm " +
          id +
          " [" +
          (data.mode || "?") +
          "] — di trú " +
          (data.keys_migrated || 0) +
          " key sang chủ mới.",
        "ok",
      );
      addLog("THÊM node " + id, "ok");
      document.getElementById("addNodeId").value = "";
      document.getElementById("addNodePort").value = "";
      refreshServersView();
    } else {
      membershipMsg("Lỗi: " + (data.message || "không thêm được node"), "err");
    }
  } catch (e) {
    membershipMsg("Lỗi kết nối: " + e.message, "err");
  } finally {
    setBtnLoading("btnAddNode", false);
  }
}

async function removeNode(id, btn) {
  if (!id) {
    var sel = document.getElementById("removeNodeSelect");
    id = sel ? sel.value : "";
  }
  if (!id) {
    membershipMsg("Chọn node để xóa.", "err");
    return;
  }
  if (
    !confirm(
      "Xóa node '" +
        id +
        "' khỏi cluster? Dữ liệu sẽ được di trú sang node khác.",
    )
  )
    return;

  if (btn) setBtnLoadingEl(btn, true, "Đang xóa...");
  setBtnLoading("btnRemoveNode", true, "Đang xóa...");
  membershipMsg("Đang drain dữ liệu của " + id + " sang node khác...", "info");
  try {
    var res = await fetch(API + "/api/cluster/remove-node", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: id }),
    });
    var data = await res.json();
    if (data.status === "ok") {
      var dr = (data.drained && data.drained.migrated) || {};
      membershipMsg(
        "Đã xóa " +
          id +
          " — drain " +
          (dr.pushed || 0) +
          " key sang node còn lại.",
        "ok",
      );
      addLog("XÓA node " + id, "warn");
      refreshServersView();
    } else {
      membershipMsg("Lỗi: " + (data.message || "không xóa được node"), "err");
    }
  } catch (e) {
    membershipMsg("Lỗi kết nối: " + e.message, "err");
  } finally {
    setBtnLoading("btnRemoveNode", false);
  }
}

var RING_COLORS = [
  "#6366f1",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#06b6d4",
  "#a855f7",
  "#ec4899",
  "#84cc16",
];

// Panel so sánh: % key phải phân bố lại khi thêm/bớt 1 node —
// consistent hashing (hệ thống này) vs hash % N (cách ngây thơ).
function renderRemapCompare(remap) {
  if (!remap || !remap.remove || !remap.add) return "";
  function row(label, ch, mod) {
    var chW = Math.min(100, ch),
      modW = Math.min(100, mod);
    return (
      '<div class="cmp-row">' +
      '<div class="cmp-label">' +
      label +
      "</div>" +
      '<div class="cmp-bars">' +
      '<div class="cmp-line"><span class="cmp-tag cmp-tag-ch">Consistent hashing</span>' +
      '<div class="cmp-track"><div class="cmp-fill cmp-fill-ch" style="width:' +
      chW +
      '%"></div></div>' +
      '<span class="cmp-val cmp-val-ch">' +
      ch.toFixed(1) +
      "%</span></div>" +
      '<div class="cmp-line"><span class="cmp-tag cmp-tag-mod">hash % N</span>' +
      '<div class="cmp-track"><div class="cmp-fill cmp-fill-mod" style="width:' +
      modW +
      '%"></div></div>' +
      '<span class="cmp-val cmp-val-mod">' +
      mod.toFixed(1) +
      "%</span></div>" +
      "</div></div>"
    );
  }
  return (
    '<div class="cmp-box">' +
    '<div class="cmp-title"><i class="fas fa-right-left"></i> % dữ liệu phải phân bố lại khi đổi số node ' +
    '<span class="cmp-sub">(' +
    (remap.nodes || "?") +
    " node · mẫu " +
    (remap.sample || 0) +
    " key)</span></div>" +
    row("Bớt 1 node", remap.remove.consistent, remap.remove.modulo) +
    row("Thêm 1 node", remap.add.consistent, remap.add.modulo) +
    '<div class="cmp-note"><i class="fas fa-lightbulb"></i> Càng thấp càng tốt. Consistent hashing chỉ dời ~k/n key; ' +
    "hash % N phải dời gần hết — đây là lý do bài viết chọn consistent hashing.</div>" +
    "</div>"
  );
}

async function loadRingInfo() {
  var legend = document.getElementById("ringLegend");
  var canvas = document.getElementById("ringCanvas");
  if (!legend || !canvas) return;
  try {
    var res = await fetch(API + "/api/ring");
    var data = await res.json();
    if (data.status !== "ok")
      throw new Error(data.message || "Không lấy được hash ring");

    var dist = data.distribution || {};
    var nodes = dist.nodes || [];
    var anyDead = !!dist.any_dead;
    var colorOf = {};
    nodes.forEach(function (n, i) {
      colorOf[n.id] = RING_COLORS[i % RING_COLORS.length];
    });
    var DEAD_COLOR = "#cbd5e1";
    function dotColor(nodeId, alive) {
      return alive === false ? DEAD_COLOR : colorOf[nodeId] || "#888";
    }

    setText(
      "ringMeta",
      (dist.vnodes_per_node || "?") +
        " vnode/node · " +
        (dist.ring_points || 0) +
        " điểm trên ring" +
        (anyDead
          ? " · ⚠ " + (nodes.length - (dist.alive_count || 0)) + " node chết"
          : ""),
    );

    // Vẽ vòng băm + các virtual node theo góc (độ). Node chết tô xám.
    var cx = 160,
      cy = 160,
      R = 130;
    var svg =
      '<circle cx="' +
      cx +
      '" cy="' +
      cy +
      '" r="' +
      R +
      '" class="ring-circle"/>';
    var allPoints = data.points || [];
    var MAX_DOTS = 150;
    var step = Math.max(1, Math.ceil(allPoints.length / MAX_DOTS));
    var points = allPoints.filter(function (_, i) {
      return i % step === 0;
    });
    svg += points
      .map(function (p) {
        var rad = ((p.angle - 90) * Math.PI) / 180;
        var x = cx + R * Math.cos(rad);
        var y = cy + R * Math.sin(rad);
        var alive = p.alive !== false;
        return (
          '<circle cx="' +
          x.toFixed(1) +
          '" cy="' +
          y.toFixed(1) +
          '" r="3" fill="' +
          dotColor(p.node, alive) +
          '" opacity="' +
          (alive ? "1" : "0.45") +
          '"><title>' +
          esc(p.node) +
          (alive ? "" : " (chết)") +
          " @ " +
          p.angle.toFixed(1) +
          "°</title></circle>"
        );
      })
      .join("");
    svg +=
      '<text x="' +
      cx +
      '" y="' +
      (cy - 6) +
      '" text-anchor="middle" class="ring-center-main">' +
      (dist.alive_count != null ? dist.alive_count : nodes.length) +
      "/" +
      nodes.length +
      " node</text>";
    svg +=
      '<text x="' +
      cx +
      '" y="' +
      (cy + 14) +
      '" text-anchor="middle" class="ring-center-sub">hash ring</text>';
    canvas.innerHTML = svg;

    // Bảng phân bố keyspace. Khi có node chết, hiện thêm % HIỆU DỤNG
    // (keyspace của node chết dồn sang node sống kế tiếp - kịch bản xoá server).
    var aliveCount =
      dist.alive_count ||
      nodes.filter(function (n) {
        return n.status !== "DEAD";
      }).length;
    var ideal = aliveCount ? 100 / aliveCount : 0;
    legend.innerHTML =
      nodes
        .map(function (n) {
          var dead = n.status === "DEAD";
          var pct = n.keyspace_percent || 0;
          var eff = n.effective_percent;
          var barPct = dead ? pct : anyDead && eff != null ? eff : pct;
          var width = Math.min(100, barPct);
          var color = dead ? DEAD_COLOR : colorOf[n.id] || "#888";
          var actual =
            n.actual_keys != null
              ? " · " +
                n.actual_keys +
                " key primary" +
                (n.replica_keys ? " / " + n.replica_keys + " replica" : "")
              : "";
          var statusBadge = dead
            ? '<span class="ring-status ring-status-dead">CHẾT</span>'
            : '<span class="ring-status ring-status-alive">SỐNG</span>';
          var pctText = dead
            ? '<span class="ring-node-pct ring-dead-text">offline</span>'
            : anyDead && eff != null
              ? '<span class="ring-node-pct">' +
                eff.toFixed(1) +
                '% <s class="ring-old-pct">' +
                pct.toFixed(1) +
                "%</s></span>"
              : '<span class="ring-node-pct">' + pct.toFixed(1) + "%</span>";
          return (
            '<div class="ring-legend-item' +
            (dead ? " ring-item-dead" : "") +
            '">' +
            '<div class="ring-legend-top">' +
            '<span class="ring-dot" style="background:' +
            color +
            '"></span>' +
            '<span class="ring-node-name">' +
            esc(n.id) +
            "</span>" +
            statusBadge +
            pctText +
            "</div>" +
            '<div class="ring-bar"><div class="ring-bar-fill" style="width:' +
            width +
            "%;background:" +
            color +
            '"></div></div>' +
            '<div class="ring-node-sub">' +
            (n.vnodes || 0) +
            " virtual node" +
            actual +
            "</div>" +
            "</div>"
          );
        })
        .join("") +
      '<div class="ring-ideal"><i class="fas fa-info-circle"></i> ' +
      (anyDead
        ? "Có node chết: cột hiển thị % HIỆU DỤNG (keyspace dồn sang node sống kế tiếp), số mờ gạch là % lúc đủ node."
        : "Phân bố lý tưởng: ~" +
          ideal.toFixed(1) +
          "%/node. Càng sát mức này, tải càng cân bằng.") +
      "</div>" +
      renderRemapCompare(data.remap);
  } catch (e) {
    legend.innerHTML =
      '<div class="empty-state"><i class="fas fa-exclamation-triangle"></i><p>Không tải được hash ring</p></div>';
    if (canvas) canvas.innerHTML = "";
    setText("ringMeta", "--");
  }
}

async function syncNode(nodeRef, btn) {
  var origHtml = "";
  if (btn) {
    btn.disabled = true;
    origHtml = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Đồng bộ...';
  }
  try {
    var res = await fetch(API + "/api/sync/" + encodeURIComponent(nodeRef), {
      method: "POST",
    });
    var data = await res.json();
    if (!res.ok || data.status !== "ok")
      throw new Error(data.message || "Đồng bộ thất bại");
    showToast(
      "Đồng bộ " +
        nodeRef +
        " thành công! Primary: " +
        data.primary +
        ", Replica: " +
        data.replica,
    );
    addLog(
      "Đồng bộ " +
        nodeRef +
        ": primary=" +
        data.primary +
        ", replica=" +
        data.replica,
    );
    loadClusterStatus();
  } catch (e) {
    showToast("Lỗi đồng bộ: " + e, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
}

async function runFailoverDemo() {
  var key = "demo_failover_" + Date.now();
  var value = "value_" + Date.now();
  setBtnLoading("btnFailoverDemo", true, "Đang demo...");
  try {
    var res = await fetch(API + "/api/demo/failover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: key, value: value }),
    });
    var data = await res.json();
    if (!res.ok || data.status !== "ok") {
      throw new Error(
        data && data.message ? data.message : "Demo failover thất bại",
      );
    }
    showToast("Demo failover thành công! Key=" + key);
    addLog(
      "DEMO FAILOVER key=" +
        key +
        " | primary=" +
        data.stopped_primary +
        " | put=" +
        data.put_result.status +
        " | get=" +
        data.get_result.status +
        " | delete=" +
        data.delete_result.status,
    );
    setTimeout(loadClusterStatus, 400);
    setTimeout(loadRingInfo, 900);
    setTimeout(loadAllData, 500);
  } catch (e) {
    showToast("Demo failover lỗi: " + e, "error");
    addLog("DEMO FAILOVER FAIL: " + e, "err");
  } finally {
    setBtnLoading("btnFailoverDemo", false);
  }
}

async function startNode(nodeRef, btn) {
  var origHtml = "";
  if (btn) {
    btn.disabled = true;
    origHtml = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Đang bật...';
  }
  try {
    var res = await fetch(
      API + "/api/node/" + encodeURIComponent(nodeRef) + "/start",
      { method: "POST" },
    );
    var data = await res.json();
    if (!res.ok || data.status !== "ok")
      throw new Error(data.message || "Bật node thất bại");
    showToast("Bật " + nodeRef + " thành công!");
    addLog("START " + nodeRef + " -> OK");
    setTimeout(loadClusterStatus, 400);
    setTimeout(loadRingInfo, 900);
  } catch (e) {
    showToast("Lỗi bật node: " + e, "error");
    addLog("START " + nodeRef + " -> FAIL: " + e, "err");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
}

async function stopNode(nodeRef, btn) {
  if (!confirm('Tắt node "' + nodeRef + '"?')) return;
  var origHtml = "";
  if (btn) {
    btn.disabled = true;
    origHtml = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Đang tắt...';
  }
  try {
    var res = await fetch(
      API + "/api/node/" + encodeURIComponent(nodeRef) + "/stop",
      { method: "POST" },
    );
    var data = await res.json();
    if (!res.ok || data.status !== "ok")
      throw new Error(data.message || "Tắt node thất bại");
    showToast("Đã tắt " + nodeRef + " thành công!");
    addLog("STOP " + nodeRef + " -> OK");
    setTimeout(loadClusterStatus, 400);
    setTimeout(loadRingInfo, 900);
  } catch (e) {
    showToast("Lỗi tắt node: " + e, "error");
    addLog("STOP " + nodeRef + " -> FAIL: " + e, "err");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
}

/* ─── All Data ───────────────────────────── */
async function loadAllData() {
  setBtnLoading("btnRefreshData", true, "Đang tải...");
  var tbody = document.getElementById("dataTable");
  if (!tbody) return;
  tbody.innerHTML =
    '<tr><td colspan="6" class="loading-state"><i class="fas fa-spinner fa-spin"></i> Đang tải dữ liệu...</td></tr>';
  try {
    var res = await fetch(API + "/api/all-data");
    var data = await res.json();
    if (!data.length) {
      tbody.innerHTML =
        '<tr><td colspan="6" class="empty-state"><i class="fas fa-inbox"></i><p>Chưa có dữ liệu nào trong cluster</p></td></tr>';
      return;
    }
    tbody.innerHTML = data
      .map(function (d, idx) {
        var replicas =
          (d.replica_nodes || [])
            .map(function (p) {
              return (
                '<span class="node-tag"><i class="fas fa-copy"></i> ' +
                esc(p) +
                "</span>"
              );
            })
            .join(" ") || '<span style="color:var(--color-muted)">—</span>';

        // Node gốc: nếu đang offline thì cảnh báo và ghi rõ dữ liệu phục vụ từ bản sao.
        var primaryHtml;
        if (d.primary_node && d.primary_offline) {
          primaryHtml =
            '<span class="node-tag" style="border-color:var(--color-danger);color:var(--color-danger)">' +
            '<i class="fas fa-server"></i> ' +
            esc(d.primary_node) +
            " (offline)</span>" +
            '<div style="font-size:11px;color:var(--color-muted);margin-top:3px">' +
            '<i class="fas fa-shield-alt"></i> đang phục vụ từ bản sao</div>';
        } else if (d.primary_node) {
          primaryHtml =
            '<span class="node-tag"><i class="fas fa-server"></i> ' +
            esc(d.primary_node) +
            "</span>";
        } else {
          primaryHtml =
            '<span style="color:var(--color-muted)">không rõ</span>';
        }

        return (
          "<tr>" +
          '<td style="color:var(--color-muted);font-size:12px;width:40px">' +
          (idx + 1) +
          "</td>" +
          '<td class="td-key">' +
          esc(d.key) +
          "</td>" +
          "<td>" +
          esc(d.value) +
          "</td>" +
          "<td>" +
          primaryHtml +
          "</td>" +
          "<td>" +
          replicas +
          "</td>" +
          '<td><button class="btn btn-danger btn-sm" data-key="' +
          escAttr(d.key) +
          '" onclick="deleteKey(this.dataset.key, this)"><i class="fas fa-trash"></i> Xóa</button></td>' +
          "</tr>"
        );
      })
      .join("");
  } catch (e) {
    tbody.innerHTML =
      '<tr><td colspan="6" class="empty-state"><i class="fas fa-exclamation-triangle"></i><p>Lỗi kết nối server</p></td></tr>';
  } finally {
    setBtnLoading("btnRefreshData", false);
  }
}

function filterTable(q) {
  q = q.toLowerCase();
  document.querySelectorAll("#dataTable tr").forEach(function (r) {
    r.style.display = r.textContent.toLowerCase().includes(q) ? "" : "none";
  });
}

/* ─── PUT ─────────────────────────────────── */
async function doPut() {
  var key = document.getElementById("putKey").value.trim();
  var value = document.getElementById("putValue").value.trim();
  var port = document.getElementById("putPort").value;
  if (!key) {
    showToast("Key không được để trống!", "error");
    return;
  }
  setBtnLoading("btnPut", true, "Đang lưu...");
  try {
    var res = await fetch(API + "/api/put", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: key, value: value, node_id: port }),
    });
    var data = await res.json();
    if (data.status === "ok") {
      showToast(
        'Lưu thành công! Key="' +
          key +
          '" → Node ' +
          data.node +
          " (" +
          data.role +
          ")",
      );
      addLog(
        'PUT "' +
          key +
          '" = "' +
          value +
          '" → Node ' +
          data.node +
          " [" +
          data.role +
          "]",
      );
      document.getElementById("putKey").value = "";
      document.getElementById("putValue").value = "";
    } else {
      showToast("Lỗi: " + (data.message || "Unknown"), "error");
      addLog('PUT "' + key + '" THẤT BẠI: ' + data.message, "err");
    }
  } catch (e) {
    showToast("Lỗi kết nối: " + e, "error");
  } finally {
    setBtnLoading("btnPut", false);
  }
}

/* ─── GET ─────────────────────────────────── */
async function doGet() {
  var key = document.getElementById("searchKey").value.trim();
  var port = document.getElementById("searchPort").value;
  if (!key) {
    showToast("Nhập key cần tìm!", "error");
    return;
  }
  setBtnLoading("btnGet", true, "Đang tìm...");
  try {
    var res = await fetch(API + "/api/get", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: key, node_id: port }),
    });
    var data = await res.json();
    var box = document.getElementById("searchResult");
    box.classList.add("show");
    if (data.status === "ok") {
      var replicaText = Array.isArray(data.replicas)
        ? data.replicas.join(", ")
        : data.replicas || data.replica || "—";
      box.innerHTML =
        '<div style="margin-bottom:8px"><i class="fas fa-check-circle" style="color:var(--color-success)"></i> <strong>Tìm thấy!</strong></div>' +
        "<div><strong>Key:</strong> " +
        esc(key) +
        "</div>" +
        '<div><strong>Value:</strong> <span style="color:var(--color-accent);font-weight:600">' +
        esc(data.value) +
        "</span></div>" +
        '<div><strong>Node xử lý:</strong> <span class="node-tag">' +
        esc(data.node) +
        "</span> (" +
        esc(data.role) +
        ")</div>" +
        "<div><strong>Primary:</strong> " +
        esc(data.primary) +
        " | <strong>Replica:</strong> " +
        esc(replicaText) +
        "</div>";
      addLog('GET "' + key + '" → "' + data.value + '" từ Node ' + data.node);
    } else {
      box.innerHTML =
        '<div><i class="fas fa-times-circle" style="color:var(--color-danger)"></i> <strong>Không tìm thấy</strong> key "<strong>' +
        esc(key) +
        '</strong>"</div>';
      addLog('GET "' + key + '" → NOT FOUND', "err");
    }
  } catch (e) {
    showToast("Lỗi: " + e, "error");
  } finally {
    setBtnLoading("btnGet", false);
  }
}

/* ─── DELETE ──────────────────────────────── */
async function doDelete() {
  var key = document.getElementById("searchKey").value.trim();
  var port = document.getElementById("searchPort").value;
  if (!key) {
    showToast("Nhập key cần xóa!", "error");
    return;
  }
  if (!confirm('Xóa key "' + key + '" khỏi toàn bộ cluster?')) return;
  setBtnLoading("btnDelete", true, "Đang xóa...");
  try {
    await deleteKeyViaPort(key, port);
  } finally {
    setBtnLoading("btnDelete", false);
  }
}

async function deleteKey(key, btn) {
  if (!confirm('Xóa key "' + key + '"?')) return;
  var origHtml = "";
  if (btn) {
    btn.disabled = true;
    origHtml = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
  }
  try {
    await deleteKeyViaPort(key, getDefaultNodeId());
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
}

async function deleteKeyViaPort(key, port) {
  try {
    var res = await fetch(API + "/api/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: key, node_id: port }),
    });
    var data = await res.json();
    if (data.status === "ok") {
      showToast('Đã xóa "' + key + '" thành công!');
      addLog('DELETE "' + key + '" → Node ' + data.node);
      loadAllData();
    } else if (data.status === "not_found") {
      showToast('Key "' + key + '" không tồn tại', "info");
    } else {
      showToast("Lỗi: " + (data.message || ""), "error");
    }
  } catch (e) {
    showToast("Lỗi: " + e, "error");
  }
}

/* ─── Init ───────────────────────────────── */
document.addEventListener("DOMContentLoaded", function () {
  updateClock();
  loadNodeOptions();
  setInterval(updateClock, 1000);
  loadDashboardStats();
  setTimeout(function () {
    loadClusterStatus(true);
  }, 500);
  setTimeout(loadRingInfo, 800);
  setTimeout(loadRemovedNodes, 900);
  setInterval(function () {
    loadClusterStatus(true);
  }, 15000);
  setInterval(loadDashboardStats, 15000);
  setInterval(loadRingInfo, 8000);
  setInterval(loadRemovedNodes, 8000);
  loadServerLogs();
  setInterval(loadServerLogs, 5000);

  // Khôi phục trang theo URL (reload không bị về trang chủ).
  var initial = location.hash.slice(1);
  if (PAGE_NAMES.indexOf(initial) === -1) initial = "dashboard";
  showPage(initial);
});
