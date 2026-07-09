// Smart-ProfiHub — мінімальний SPA-клієнт поверх REST API.

const API = "/api";
let state = { token: null, refresh: null, user: null };

// ---------- HTTP-хелпер ----------
async function api(path, { method = "GET", body, auth = true, signal } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && state.token) headers["Authorization"] = `Bearer ${state.token}`;
  const res = await fetch(API + path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });
  let data = null;
  try { data = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) {
    // Прострочений/недійсний токен на авторизованому запиті — на сторінку входу.
    if (res.status === 401 && auth && state.token) {
      sessionExpired();
    }
    throw new Error((data && data.message) || `Помилка ${res.status}`);
  }
  return data;
}

// Викидає користувача на логін через протермінований/недійсний токен.
let _expiredNotified = false;
function sessionExpired() {
  if (_expiredNotified) return;  // не спамимо тостами при кількох паралельних запитах
  _expiredNotified = true;
  toast("Сесія завершилася. Увійдіть знову.", "err");
  logout();
  setTimeout(() => { _expiredNotified = false; }, 1500);
}

// ---------- Утиліти ----------
const $ = (sel) => document.querySelector(sel);
const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
function toast(msg, type = "ok") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast ${type}`;
  setTimeout(() => t.classList.add("hidden"), 3000);
}
function hasRole(code) { return state.user && state.user.roles.includes(code); }
function esc(s) { return (s == null ? "" : String(s)).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function fmtSize(n) { if (n < 1024) return `${n} Б`; if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} КБ`; return `${(n / 1024 / 1024).toFixed(1)} МБ`; }
// Гроші (USD): дрібні суми показуємо з більшою точністю.
function fmtMoney(v) {
  v = Number(v) || 0;
  const a = Math.abs(v);
  if (a === 0) return "$0.00";
  if (a < 0.01) return "$" + v.toFixed(4);
  if (a < 1) return "$" + v.toFixed(3);
  return "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtInt(n) { return (Number(n) || 0).toLocaleString("uk-UA"); }
let usageSubTab = "tokens";  // активна вкладка «Квоти»: tokens | money
function openUsageTab(sub) { usageSubTab = sub || "tokens"; openTab("usage"); }

// ---------- Мінімальний Markdown → HTML (вхід вже екранований) ----------
function mdInline(s) {
  s = s.replace(/`([^`]+)`/g, (m, c) => `<code>${c}</code>`);
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
    (m, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`);
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  return s;
}
function _mdRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
}
function _mdTableSep(l) { return l && /\|/.test(l) && /^[\s:|-]+$/.test(l.trim()) && /-/.test(l); }
function _mdBlockStart(l) { return /^\s*(#{1,6}\s|[-*+]\s|\d+\.\s|>|```)/.test(l) || /^\s*---+\s*$/.test(l); }
function renderMarkdown(md) {
  const lines = esc(md || "").split(/\r?\n/);
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*```(\w*)\s*$/);
    if (fence) {
      const buf = []; i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++;
      out.push(`<pre class="md-pre"><code>${buf.join("\n")}</code></pre>`);
      continue;
    }
    if (/\|/.test(line) && _mdTableSep(lines[i + 1])) {
      const header = _mdRow(line); i += 2;
      const rows = [];
      while (i < lines.length && /\|/.test(lines[i]) && lines[i].trim() !== "") { rows.push(_mdRow(lines[i])); i++; }
      let t = '<table class="md-table"><thead><tr>' + header.map(h => `<th>${mdInline(h)}</th>`).join("") + "</tr></thead><tbody>";
      rows.forEach(r => { t += "<tr>" + r.map(c => `<td>${mdInline(c)}</td>`).join("") + "</tr>"; });
      out.push(t + "</tbody></table>");
      continue;
    }
    const h = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (h) { const lvl = Math.min(h[1].length + 1, 4); out.push(`<h${lvl} class="md-h">${mdInline(h[2])}</h${lvl}>`); i++; continue; }
    if (/^\s*---+\s*$/.test(line)) { out.push("<hr class='md-hr'>"); i++; continue; }
    if (/^\s*[-*+]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*+]\s+/, "")); i++; }
      out.push('<ul class="md-ul">' + items.map(it => `<li>${mdInline(it)}</li>`).join("") + "</ul>");
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*\d+\.\s+/, "")); i++; }
      out.push('<ol class="md-ol">' + items.map(it => `<li>${mdInline(it)}</li>`).join("") + "</ol>");
      continue;
    }
    if (line.trim() === "") { i++; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() !== "" && !_mdBlockStart(lines[i]) &&
           !(/\|/.test(lines[i]) && _mdTableSep(lines[i + 1]))) {
      para.push(lines[i]); i++;
    }
    if (para.length) out.push(`<p class="md-p">${para.map(mdInline).join("<br>")}</p>`);
    else i++;
  }
  return out.join("");
}

// Завантаження файлу через fetch з JWT (звичайне посилання не передало б заголовок).
async function downloadFile(id, name) {
  try {
    const res = await fetch(`${API}/files/${id}/download`, {
      headers: { "Authorization": `Bearer ${state.token}` },
    });
    if (!res.ok) throw new Error("Не вдалося завантажити файл");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name || "file";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (err) { toast(err.message, "err"); }
}

// Повне клікабельне посилання на файл (хост — поточний).
function fileUrl(f) { return location.origin + f.url; }

// Рендерить блок із клікабельними посиланнями на створені файли.
function renderFileLinks(files) {
  if (!files || !files.length) return null;
  const box = el(`<div class="file-links"><div class="muted">Створені файли:</div></div>`);
  files.forEach(f => {
    const url = fileUrl(f);
    const a = el(`<a class="file-link" href="${url}" target="_blank" rel="noopener">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 20h14v-2H5v2ZM12 4v9l3.5-3.5 1.4 1.4L12 16l-4.9-5.1 1.4-1.4L12 13V4h0Z"/></svg>
      <span>${esc(url)}</span></a>`);
    box.appendChild(a);
  });
  return box;
}

// ---------- Автентифікація ----------
function saveSession() {
  localStorage.setItem("sph", JSON.stringify(state));
}
function loadSession() {
  try {
    const s = JSON.parse(localStorage.getItem("sph"));
    if (s && s.token) state = s;
  } catch (e) { /* ignore */ }
}
function logout() {
  state = { token: null, refresh: null, user: null };
  localStorage.removeItem("sph");
  showLogin();
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  try {
    const data = await api("/auth/login", {
      method: "POST", auth: false,
      body: { username: $("#login-username").value, password: $("#login-password").value },
    });
    state.token = data.access_token;
    state.refresh = data.refresh_token;
    state.user = data.user;
    saveSession();
    showApp();
  } catch (err) {
    $("#login-error").textContent = err.message;
  }
});
$("#logout-btn").addEventListener("click", logout);
// Клік по індикатору квоти (внизу зліва) → «Квота» → вкладка «Гроші».
$("#quota").addEventListener("click", () => openUsageTab("money"));

// ---------- Модель у верхній панелі (перемикач + автовибір) ----------
// Завантажує доступні користувачу моделі та типову; малює перемикач.
async function loadModelPicker() {
  try {
    const res = await api("/models/mine");
    chatState.models = res.models || [];
    const ids = new Set(chatState.models.map(m => m.id));
    if (chatState.activeModelId == null || !ids.has(chatState.activeModelId)) {
      chatState.activeModelId = res.default_id;
    }
  } catch (e) { chatState.models = []; }
  renderModelPicker();
}

function renderModelPicker() {
  const box = $("#model-picker");
  if (!box) return;
  const models = chatState.models || [];
  if (!models.length) {
    box.innerHTML = `<span class="mp-none">Немає доступних моделей</span>`;
    return;
  }
  if (models.length === 1) {
    box.innerHTML = `<span class="mp-label">Модель:</span>` +
      `<span class="mp-single">${esc(models[0].name)}</span>`;
    return;
  }
  const cur = chatState.activeModelId;
  const opts = models.map(m =>
    `<option value="${m.id}" ${m.id === cur ? "selected" : ""}>${esc(m.name)}${m.is_system ? " (системна)" : ""}</option>`).join("");
  box.innerHTML = `<span class="mp-label">Модель:</span>` +
    `<select id="mp-select" class="mp-select" title="Модель для чату">${opts}</select>`;
  $("#mp-select").addEventListener("change", () => pickModel(Number($("#mp-select").value)));
}

// Вибір моделі: застосовуємо до відкритого чату (як і до наступних нових).
async function pickModel(id) {
  chatState.activeModelId = id;
  if (!chatState.sessionId) return;
  try {
    await api(`/chat/sessions/${chatState.sessionId}`, { method: "PATCH", body: { model_id: id } });
    const nm = (chatState.models.find(m => m.id === id) || {}).name || "";
    const label = $("#chat-model");
    if (label) label.textContent = "· " + nm;
    refreshSessionList();
  } catch (err) { toast(err.message, "err"); renderModelPicker(); }
}

// «Новий чат» — модель обирається автоматично (поточна активна / типова).
async function createNewChat() {
  try {
    const body = chatState.activeModelId ? { model_id: chatState.activeModelId } : {};
    const s = await api("/chat/sessions", { method: "POST", body });
    chatState.selectedSkill = null;  // новий чат — відтискаємо обрану навичку
    chatState.openAfter = s.id;
    if (document.querySelector("#nav .nav-item.active")?.dataset.tab === "chat") {
      await viewChat();
    } else {
      openTab("chat");
    }
  } catch (err) { toast(err.message, "err"); }
}

$("#new-chat-btn").addEventListener("click", createNewChat);

// ---------- Тема (Темна glass / Світла / Material), лише всередині застосунку ----------
const T_MOON = '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/>';
const T_SUN = '<path d="M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10Zm0-13v2m0 16v2M4 12H2m20 0h-2M5.6 5.6 4.2 4.2m15.6 1.4 1.4-1.4M5.6 18.4l-1.4 1.4m15.6-1.4 1.4 1.4" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>';
const T_GRID = '<path d="M4 4h7v7H4V4Zm9 0h7v7h-7V4ZM4 13h7v7H4v-7Zm9 0h7v7h-7v-7Z"/>';
const THEMES = [
  { id: "dark", label: "Темна", icon: T_MOON },
  { id: "light", label: "Світла", icon: T_SUN },
  { id: "material", label: "Material", icon: T_GRID },
];
function themePref() {
  const t = localStorage.getItem("theme");
  return ["dark", "light", "material"].includes(t) ? t : "dark";
}
function applyTheme() {
  const t = themePref();
  document.body.classList.toggle("theme-light", t === "light");
  document.body.classList.toggle("theme-material", t === "material");
  document.querySelectorAll("#theme-seg button").forEach(b =>
    b.classList.toggle("active", b.dataset.theme === t));
}
function buildThemeSeg() {
  const seg = $("#theme-seg");
  if (!seg) return;
  seg.innerHTML = THEMES.map(t =>
    `<button data-theme="${t.id}" title="${t.label} тема"><svg viewBox="0 0 24 24" aria-hidden="true">${t.icon}</svg></button>`).join("");
  seg.querySelectorAll("button").forEach(b =>
    b.addEventListener("click", () => { localStorage.setItem("theme", b.dataset.theme); applyTheme(); }));
}
buildThemeSeg();

// Перемикач показу пароля.
const pwToggle = $("#pw-toggle");
if (pwToggle) pwToggle.addEventListener("click", () => {
  const inp = $("#login-password");
  const show = inp.type === "password";
  inp.type = show ? "text" : "password";
  pwToggle.classList.toggle("active", show);
});

function showLogin() {
  document.body.classList.remove("theme-light", "theme-material");  // логін завжди темний
  $("#login-screen").classList.remove("hidden");
  $("#app").classList.add("hidden");
}

async function showApp() {
  $("#login-screen").classList.add("hidden");
  $("#app").classList.remove("hidden");
  applyTheme();  // застосовуємо тему користувача (лише в застосунку)
  const display = state.user.full_name || state.user.username;
  $("#user-name").textContent = display;
  $("#user-roles").textContent = state.user.roles.length ? state.user.roles.join(", ") : "member";
  $("#user-avatar").textContent = (display[0] || "?").toUpperCase();
  buildNav();
  refreshQuota();
  loadModelPicker();
}

async function refreshQuota() {
  try {
    const u = await api("/usage/me");
    const q = u.quota || { used: 0, limit: 0, percent: 0 };
    $("#quota-used").textContent = fmtMoney(q.used);
    $("#quota-limit").textContent = q.limit ? fmtMoney(q.limit) : "∞";
    const pct = Math.max(0, Math.min(100, q.percent || 0));
    const fill = $("#quota-fill");
    fill.style.width = pct + "%";
    // Колір: зелений → бурштиновий → червоний.
    const color = pct >= 90 ? "var(--danger)" : pct >= 70 ? "#f59e0b" : "var(--ok)";
    fill.style.background = color;
    $("#quota").classList.toggle("quota-full", pct >= 100);
    const resets = q.resets_at ? new Date(q.resets_at).toLocaleString("uk-UA") : "";
    $("#quota").title =
      `Тижнева квота: ${fmtMoney(q.used)} / ${q.limit ? fmtMoney(q.limit) : "∞"} (${pct}%)` +
      (q.custom ? " · персональна" : " · системна") +
      (resets ? `\nСкидання: ${resets}` : "") + "\n(клік — відкрити «Квота» → «Гроші»)";
  } catch (e) { /* ignore */ }
}

// ---------- Навігація (за ролями) ----------
const NAV_ICONS = {
  chat: '<path d="M4 4h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-5 4V6a2 2 0 0 1 2-2Z"/>',
  skills: '<path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"/>',
  catalog: '<path d="M4 4h7v7H4V4Zm9 0h7v7h-7V4ZM4 13h7v7H4v-7Zm9 0h7v7h-7v-7Z"/>',
  files: '<path d="M4 5a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5Z"/>',
  "manage-skills": '<path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm9 4-2-1 1-2-2-2-2 1-1-2h-2l-1 2-2-1-2 2 1 2-2 1v2l2 1-1 2 2 2 2-1 1 2h2l1-2 2 1 2-2-1-2 2-1v-2Z"/>',
  models: '<path d="M9 3h6v2h3a1 1 0 0 1 1 1v3h2v6h-2v3a1 1 0 0 1-1 1h-3v2H9v-2H6a1 1 0 0 1-1-1v-3H3V9h2V6a1 1 0 0 1 1-1h3V3Zm0 6v6h6V9H9Z"/>',
  groups: '<path d="M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm8 0a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm-8 2c-3 0-6 1.5-6 4v2h8v-2c0-1 .4-1.9 1-2.7A9 9 0 0 0 8 13Zm8 0c-.7 0-1.4.1-2 .3.6.8 1 1.7 1 2.7v2h7v-2c0-2.5-3-4-6-4Z"/>',
  users: '<path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm0 2c-4 0-7 2-7 5v1h14v-1c0-3-3-5-7-5Z"/>',
  usage: '<path d="M5 13h3v7H5v-7Zm5.5-6h3v13h-3V7ZM16 10h3v10h-3V10Z"/>',
};

const TABS = [
  { id: "chat", label: "Чат", view: viewChat },
  { id: "catalog", label: "Каталог", view: viewCatalog },
  { id: "files", label: "Мої файли", view: viewFiles },
  { id: "manage-skills", label: "Управління навичками", view: viewManageSkills, roles: ["admin", "skill_manager"] },
  { id: "models", label: "Моделі", view: viewModels, roles: ["admin"] },
  { id: "groups", label: "Групи", view: viewGroups },
  { id: "users", label: "Користувачі", view: viewUsers, roles: ["admin"] },
  { id: "usage", label: "Квота", view: viewUsage },
];

function visibleTabs() {
  return TABS.filter(t => !t.roles || t.roles.some(r => hasRole(r)));
}

function buildNav() {
  const nav = $("#nav");
  nav.innerHTML = "";
  visibleTabs().forEach(tab => {
    const b = el(`<button class="nav-item">
      <svg viewBox="0 0 24 24" aria-hidden="true">${NAV_ICONS[tab.id] || ""}</svg>
      <span>${esc(tab.label)}</span>
    </button>`);
    b.dataset.tab = tab.id;
    b.addEventListener("click", () => openTab(tab.id));
    nav.appendChild(b);
  });
  openTab(visibleTabs()[0].id);
}

async function openTab(id) {
  document.querySelectorAll("#nav .nav-item").forEach(b =>
    b.classList.toggle("active", b.dataset.tab === id));
  const tab = TABS.find(t => t.id === id);
  $("#page-title").textContent = tab ? tab.label : "";
  $("#view").classList.toggle("flush", id === "chat");  // чат — без центрування, ближче до меню
  $("#view").innerHTML = `<p class="muted">Завантаження…</p>`;
  try { await tab.view(); }
  catch (err) { $("#view").innerHTML = `<div class="card"><p class="error">${esc(err.message)}</p></div>`; }
  refreshQuota();
}

// ---------- Чат з обраною моделлю ----------
let chatState = { sessionId: null, skills: [], openAfter: null, selectedSkill: null,
                  models: [], activeModelId: null };

function sessionItemHtml(s, activeId) {
  // Без кольорового маркування за вендором — усі чати одним системним кольором.
  return `<div class="session-item vendor-default ${s.id === activeId ? "active" : ""}" data-sid="${s.id}">
      <span class="session-title">${esc(s.title || "Без назви")}</span>
      <button class="session-del small ghost" data-del="${s.id}" title="Видалити">×</button>
    </div>`;
}

async function viewChat() {
  const [skills, sessions] = await Promise.all([
    api("/skills/mine"), api("/chat/sessions"),
  ]);
  chatState.skills = skills;
  loadModelPicker();  // оновлюємо перелік моделей (доступи могли змінитись)
  const view = $("#view");

  const sessionItems = sessions.map(s => sessionItemHtml(s, chatState.sessionId)).join("")
    || `<p class="muted">Чатів ще немає. Натисніть «Новий чат» угорі.</p>`;

  view.innerHTML = `
    <div class="chat-layout">
      <aside class="chat-sidebar">
        <div class="card chat-history">
          <h3>Мої чати</h3>
          <div id="session-list">${sessionItems}</div>
        </div>
      </aside>
      <section class="chat-main card">
        <div id="chat-header" class="chat-header muted">Оберіть чат або створіть новий («Новий чат» угорі).</div>
        <div id="chat-log" class="chat-log"></div>
        <div id="chat-input-box" class="hidden">
          <div class="row">
            <textarea id="chat-text" placeholder="Введіть повідомлення…" style="flex:1; min-height:60px"></textarea>
            <button id="chat-send">Надіслати</button>
          </div>
        </div>
      </section>
      <aside class="skill-panel">
        <div class="ribbon-title">Навички</div>
        <div class="skill-ribbon" id="skill-ribbon">${renderSkillRibbon(skills)}</div>
      </aside>
    </div>`;

  bindSessionListEvents();
  bindRibbon();

  // Відкриваємо новостворений чат, інакше — останній наявний.
  const toOpen = chatState.openAfter || (sessions[0] && sessions[0].id);
  chatState.openAfter = null;
  if (toOpen) openChatSession(toOpen);
}

function renderSkillRibbon(skills) {
  const skillIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"/></svg>';
  const plusIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" fill="none"/></svg>';
  // Плитка «додати навичку» (Каталог) — не виділена, з великим плюсом.
  const tiles = [`<button class="ribbon-tile ribbon-add" data-catalog="1" title="Додати навичку з Каталогу">
      ${plusIcon}<span class="rt-name">Додати</span></button>`];
  (skills || []).forEach(s => {
    const pressed = s.id === chatState.selectedSkill ? "pressed" : "";
    tiles.push(`<button class="ribbon-tile ${pressed}" data-skill="${s.id}" title="${esc(s.description || s.name)}">
      ${skillIcon}<span class="rt-name">${esc(s.name)}</span></button>`);
  });
  return tiles.join("");
}

function bindRibbon() {
  document.querySelectorAll(".ribbon-tile[data-catalog]").forEach(b =>
    b.addEventListener("click", () => openTab("catalog")));
  document.querySelectorAll(".ribbon-tile[data-skill]").forEach(b =>
    b.addEventListener("click", () => {
      const id = Number(b.dataset.skill);
      chatState.selectedSkill = (chatState.selectedSkill === id) ? null : id;
      document.querySelectorAll(".ribbon-tile[data-skill]").forEach(t =>
        t.classList.toggle("pressed", Number(t.dataset.skill) === chatState.selectedSkill));
    }));
}

function bindSessionListEvents() {
  document.querySelectorAll(".session-item").forEach(item => {
    item.addEventListener("click", (e) => {
      if (e.target.dataset.del) return;
      openChatSession(Number(item.dataset.sid));
    });
  });
  document.querySelectorAll("[data-del]").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        await api(`/chat/sessions/${btn.dataset.del}`, { method: "DELETE" });
        if (chatState.sessionId === Number(btn.dataset.del)) chatState.sessionId = null;
        toast("Сесію видалено");
        await refreshSessionList();
        if (!chatState.sessionId) {
          $("#chat-header").textContent = "Оберіть або створіть чат.";
          $("#chat-log").innerHTML = "";
          $("#chat-input-box").classList.add("hidden");
        }
      } catch (err) { toast(err.message, "err"); }
    });
  });
}

async function refreshSessionList() {
  const sessions = await api("/chat/sessions");
  const list = $("#session-list");
  if (!list) return;
  list.innerHTML = sessions.map(s => sessionItemHtml(s, chatState.sessionId)).join("")
    || `<p class="muted">Чатів ще немає.</p>`;
  bindSessionListEvents();
}

// Непомітне число токенів збоку; відповіді ШІ — з Markdown-розміткою.
function renderChatMessage(m) {
  const log = $("#chat-log");
  const n = m.role === "assistant" ? m.completion_tokens : m.prompt_tokens;
  const body = m.role === "assistant"
    ? `<div class="md-body">${renderMarkdown(m.content)}</div>`
    : esc(m.content);
  const bubble = el(`<div class="msg ${m.role}">${body}<span class="msg-tok"></span></div>`);
  if (n != null && n !== "") bubble.querySelector(".msg-tok").textContent = n;
  log.appendChild(bubble);
  // Робочі посилання на створені файли (зберігаються з повідомленням — переживають
  // перевідкриття чату).
  if (m.files && m.files.length) {
    const fl = renderFileLinks(m.files);
    if (fl) log.appendChild(fl);
  }
  log.scrollTop = log.scrollHeight;
  return bubble;
}

function showChatLoading() {
  const log = $("#chat-log");
  const el2 = el(`<div class="chat-loading"><span class="cl-dot"></span><span>ШІ генерує відповідь…</span><div class="cl-bar"><div></div></div></div>`);
  log.appendChild(el2);
  log.scrollTop = log.scrollHeight;
  return el2;
}
function renderChatError(msg) {
  const log = $("#chat-log");
  log.appendChild(el(`<div class="chat-error">⚠️ <b>Якась чортівня!</b> Трапилася помилка: ${esc(msg)}</div>`));
  log.scrollTop = log.scrollHeight;
}
function renderChatNote(text) {
  const log = $("#chat-log");
  log.appendChild(el(`<div class="chat-note">${esc(text)}</div>`));
  log.scrollTop = log.scrollHeight;
}

async function openChatSession(sessionId) {
  chatState.sessionId = sessionId;
  const session = await api(`/chat/sessions/${sessionId}`);
  // Перемикач у верхній панелі відображає модель відкритого чату.
  if (session.model_id) { chatState.activeModelId = session.model_id; renderModelPicker(); }
  $("#chat-header").innerHTML =
    `<strong>${esc(session.title || "Чат")}</strong> <span class="muted" id="chat-model">· ${esc(session.model_name || "—")}</span>`;
  const log = $("#chat-log");
  log.innerHTML = "";
  session.messages.forEach(renderChatMessage);
  $("#chat-input-box").classList.remove("hidden");
  document.querySelectorAll(".session-item").forEach(i =>
    i.classList.toggle("active", Number(i.dataset.sid) === sessionId));

  const sendBtn = $("#chat-send");

  function setSending(on) {
    sendBtn.classList.toggle("stop", on);
    sendBtn.textContent = on ? "■ Стоп" : "Надіслати";
  }

  async function doSend() {
    const text = $("#chat-text").value.trim();
    if (!text) return;
    const skillId = chatState.selectedSkill || null;
    const userEl = renderChatMessage({ role: "user", content: text });
    $("#chat-text").value = "";
    const loading = showChatLoading();
    const controller = new AbortController();
    chatState.controller = controller;
    setSending(true);
    try {
      const res = await api(`/chat/sessions/${sessionId}/messages`, {
        method: "POST", body: { content: text, skill_id: skillId }, signal: controller.signal });
      loading.remove();
      userEl.querySelector(".msg-tok").textContent = res.usage.prompt_tokens;
      // Файли рендеряться як частина повідомлення (той самий шлях, що й при
      // перевідкритті чату) — жодного окремого блоку, що губиться.
      renderChatMessage({ role: "assistant", content: res.content,
        completion_tokens: res.usage.completion_tokens, files: res.files });
      refreshSessionList();
      refreshQuota();
    } catch (err) {
      loading.remove();
      if (err.name === "AbortError") renderChatNote("⏹ Запит зупинено.");
      else renderChatError(err.message);
    } finally {
      chatState.controller = null;
      setSending(false);
    }
  }

  // Кнопка працює як «Надіслати», а під час запиту — як «Стоп».
  sendBtn.onclick = () => {
    if (chatState.controller) { chatState.controller.abort(); return; }
    doSend();
  };
  // Enter — надіслати; Shift+Enter — новий рядок.
  $("#chat-text").onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!chatState.controller) doSend();
    }
  };
}

// ---------- Каталог (встановлення / вилучення навичок) ----------

// Стандартна іконка навички (коли власну не задано) — фірмова «блискавка».
const DEFAULT_SKILL_ICON =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"/></svg>';
const DESC_PREVIEW_LEN = 120;  // скільки символів опису показувати згорнутим

let catalogState = { skills: [], mineIds: new Set(), query: "", expanded: new Set() };

// Дата публікації (fallback — дата створення) для сортування «новіші зверху».
function pubTime(s) {
  const d = s.published_at || s.created_at;
  return d ? new Date(d).getTime() : 0;
}

async function viewCatalog() {
  const [all, mine] = await Promise.all([api("/skills"), api("/skills/mine")]);
  const published = all
    .filter(s => s.status === "published")
    // 1) за популярністю (активації), 2) за новизною (дата публікації).
    .sort((a, b) => (b.activations_count - a.activations_count) || (pubTime(b) - pubTime(a)));
  catalogState.skills = published;
  catalogState.mineIds = new Set(mine.map(s => s.id));
  catalogState.expanded = new Set();

  const view = $("#view");
  view.innerHTML = `
    <div class="catalog">
      <div class="catalog-search">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 4a6 6 0 1 0 3.9 10.6l4.3 4.3 1.4-1.4-4.3-4.3A6 6 0 0 0 10 4Zm0 2a4 4 0 1 1 0 8 4 4 0 0 1 0-8Z"/></svg>
        <input id="cat-q" type="text" placeholder="Пошук навичок…" autocomplete="off" value="${esc(catalogState.query)}">
      </div>
      <div class="cat-grid" id="cat-grid"></div>
    </div>`;

  const q = $("#cat-q");
  q.addEventListener("input", () => { catalogState.query = q.value; renderCatalogGrid(); });
  renderCatalogGrid();
  q.focus();
  // Курсор — у кінець уже введеного тексту (перерендер зберігає запит).
  q.setSelectionRange(q.value.length, q.value.length);
}

function renderCatalogGrid() {
  const grid = $("#cat-grid");
  if (!grid) return;
  const query = catalogState.query.trim().toLowerCase();
  const list = catalogState.skills.filter(s => {
    if (!query) return true;
    return [s.name, s.description, s.category, s.author]
      .some(v => (v || "").toLowerCase().includes(query));
  });

  grid.innerHTML = "";
  if (!catalogState.skills.length) {
    grid.innerHTML = `<p class="muted cat-empty">Немає опублікованих навичок.</p>`;
    return;
  }
  if (!list.length) {
    grid.innerHTML = `<p class="muted cat-empty">Нічого не знайдено за запитом «${esc(catalogState.query)}».</p>`;
    return;
  }
  list.forEach(s => grid.appendChild(catalogCard(s)));
}

function skillLogo(s, cls) {
  return s.has_icon
    ? `<img class="${cls}-img" src="${esc(s.icon_url)}" alt="${esc(s.name)}">`
    : `<span class="${cls}-default">${DEFAULT_SKILL_ICON}</span>`;
}

// Виконує встановлення/вилучення; оновлює стан і повертає новий статус.
async function toggleInstall(s) {
  const installed = catalogState.mineIds.has(s.id);
  const action = installed ? "deactivate" : "activate";
  const res = await api(`/skills/${s.id}/${action}`, { method: "POST" });
  if (installed) catalogState.mineIds.delete(s.id);
  else catalogState.mineIds.add(s.id);
  const local = catalogState.skills.find(x => x.id === s.id);
  if (local && res && typeof res.activations_count === "number") {
    local.activations_count = res.activations_count;
  }
  return !installed;
}

function installBtnHtml(installed) {
  return installed
    ? `Вилучити`
    : `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v10.6l3.3-3.3 1.4 1.4L12 17.4 6.3 11.7l1.4-1.4L11 13.6V3h1Zm-7 15h14v2H5v-2Z"/></svg>Встановити`;
}

function catalogCard(s) {
  const installed = catalogState.mineIds.has(s.id);
  const desc = s.description || "";
  const shortDesc = desc.length > DESC_PREVIEW_LEN
    ? desc.slice(0, DESC_PREVIEW_LEN).trimEnd() + "…" : desc;

  const card = el(`<div class="cat-card" tabindex="0" role="button" title="Детальніше про навичку">
    <div class="cat-logo">${skillLogo(s, "cat-logo")}</div>
    <h3 class="cat-name">${esc(s.name)}</h3>
    <div class="cat-meta">
      <span class="badge cat-cat">${esc(s.category || "Загальне")}</span>
      <div class="cat-va">
        <span>Версія: <b>${esc(s.version)}</b></span>
        <span>Автор: <b>${esc(s.author || "—")}</b></span>
      </div>
    </div>
    <div class="cat-desc">${esc(shortDesc)}</div>
    <button class="cat-btn ${installed ? "cat-remove" : "cat-install"}">${installBtnHtml(installed)}</button>
  </div>`);

  const btn = card.querySelector(".cat-btn");
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();  // не відкривати модалку
    try {
      const nowInstalled = await toggleInstall(s);
      toast(nowInstalled ? "Навичку встановлено" : "Навичку вилучено");
      renderCatalogGrid();
    } catch (err) { toast(err.message, "err"); }
  });
  // Клік по плитці (не по кнопці) — відкриває детальну картку.
  card.addEventListener("click", () => openSkillModal(s.id));
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openSkillModal(s.id); }
  });
  return card;
}

// Детальна картка навички: усі атрибути + форма зворотного зв'язку.
async function openSkillModal(skillId) {
  let s;
  try { s = await api(`/skills/${skillId}`); }
  catch (err) { toast(err.message, "err"); return; }
  const installed = catalogState.mineIds.has(s.id);

  const attr = (label, value) => value && value.trim()
    ? `<div class="sm-attr"><div class="sm-attr-k">${esc(label)}</div><div class="sm-attr-v">${esc(value)}</div></div>`
    : "";
  const inputsList = (s.inputs && s.inputs.length)
    ? `<div class="sm-attr"><div class="sm-attr-k">Параметри</div><div class="sm-attr-v">${
        s.inputs.map(i => `<span class="badge">${esc(i.label || i.name)}${i.is_required ? " *" : ""}</span>`).join(" ")
      }</div></div>` : "";

  const overlay = el(`<div class="modal-overlay" id="skill-modal">
    <div class="modal sm-modal">
      <div class="modal-head sm-head">
        <div class="sm-title">
          <span class="sm-logo">${skillLogo(s, "sm-logo")}</span>
          <div>
            <h2>${esc(s.name)}</h2>
            <div class="sm-meta">
              <span class="badge">${esc(s.category || "Загальне")}</span>
              <span class="muted">Версія: <b>${esc(s.version)}</b> · Автор: <b>${esc(s.author || "—")}</b></span>
              <span class="muted">Активацій: ${s.activations_count}</span>
            </div>
          </div>
        </div>
        <button class="modal-x" id="sm-close" aria-label="Закрити">×</button>
      </div>
      <div class="sm-body">
        <p class="sm-desc">${esc(s.description || "")}</p>
        ${attr("Вхідні дані", s.input_spec)}
        ${attr("Результат роботи", s.output_spec)}
        ${attr("Стартовий промпт", s.starter_prompt)}
        ${inputsList}
      </div>
      <div class="sm-actions">
        <button class="cat-btn ${installed ? "cat-remove" : "cat-install"}" id="sm-install">${installBtnHtml(installed)}</button>
      </div>
      <hr class="md-hr">
      <div class="sm-feedback">
        <h3>Зворотний зв'язок</h3>
        <p class="muted">Знайшли проблему чи маєте пропозицію? Напишіть — повідомлення отримає команда навичок.</p>
        <textarea id="sm-fb-text" placeholder="Ваше повідомлення…" rows="3"></textarea>
        <div class="row" style="margin-top:8px">
          <button id="sm-fb-send">Надіслати</button>
        </div>
      </div>
    </div>
  </div>`);
  document.body.appendChild(overlay);

  const close = () => { overlay.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  overlay.querySelector("#sm-close").addEventListener("click", close);

  const installBtn = overlay.querySelector("#sm-install");
  installBtn.addEventListener("click", async () => {
    try {
      const nowInstalled = await toggleInstall(s);
      toast(nowInstalled ? "Навичку встановлено" : "Навичку вилучено");
      installBtn.className = `cat-btn ${nowInstalled ? "cat-remove" : "cat-install"}`;
      installBtn.innerHTML = installBtnHtml(nowInstalled);
      renderCatalogGrid();
    } catch (err) { toast(err.message, "err"); }
  });

  const sendBtn = overlay.querySelector("#sm-fb-send");
  sendBtn.addEventListener("click", async () => {
    const text = overlay.querySelector("#sm-fb-text").value.trim();
    if (!text) { toast("Введіть повідомлення", "err"); return; }
    sendBtn.disabled = true;
    try {
      await api(`/skills/${s.id}/feedback`, { method: "POST", body: { message: text } });
      toast("Дякуємо! Повідомлення надіслано.");
      close();
    } catch (err) { toast(err.message, "err"); sendBtn.disabled = false; }
  });
}

// ---------- Файли користувача ----------
async function viewFiles() {
  const files = await api("/files");
  const view = $("#view");
  view.innerHTML = `
    <div class="card">
      <h2>Завантажити файл</h2>
      <div class="row">
        <input type="file" id="f-file">
        <button id="f-upload">Завантажити</button>
      </div>
      <p class="muted" style="margin:8px 0 0">Ваші файли зберігаються в особистому захищеному сховищі та доступні лише вам.</p>
    </div>
    <div class="card">
      <h2>Мої файли</h2>
      <table><thead><tr><th>Назва</th><th>Джерело</th><th>Розмір</th><th>Створено</th><th>Дії</th></tr></thead><tbody id="f-body"></tbody></table>
    </div>`;

  $("#f-upload").addEventListener("click", async () => {
    const file = $("#f-file").files[0];
    if (!file) { toast("Оберіть файл", "err"); return; }
    const form = new FormData();
    form.append("file", file);
    $("#f-upload").disabled = true;
    try {
      const res = await fetch(API + "/files", {
        method: "POST",
        headers: { "Authorization": `Bearer ${state.token}` },
        body: form,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || "Помилка завантаження");
      toast("Файл завантажено"); openTab("files");
    } catch (err) { toast(err.message, "err"); }
    finally { $("#f-upload").disabled = false; }
  });

  const body = $("#f-body");
  if (!files.length) body.innerHTML = `<tr><td colspan="5" class="muted">Файлів ще немає.</td></tr>`;
  files.forEach(f => {
    const src = f.source === "skill_run" ? "навичка" : "завантажено";
    const date = f.created_at ? f.created_at.replace("T", " ").slice(0, 16) : "";
    const tr = el(`<tr>
      <td><a class="file-link inline" href="${fileUrl(f)}" target="_blank" rel="noopener">${esc(f.filename)}</a></td>
      <td><span class="badge">${src}</span></td>
      <td>${fmtSize(f.size)}</td><td>${esc(date)}</td>
      <td class="actions">
        <button class="small danger" data-act="del">Видалити</button>
      </td>
    </tr>`);
    tr.querySelector("[data-act='del']").addEventListener("click", async () => {
      if (!confirm(`Видалити файл «${f.filename}»?`)) return;
      try { await api(`/files/${f.id}`, { method: "DELETE" }); toast("Файл видалено"); openTab("files"); }
      catch (err) { toast(err.message, "err"); }
    });
    body.appendChild(tr);
  });
}

// ---------- Управління навичками (каталог) ----------

// Завантаження пакета: без skillId — НОВА навичка; зі skillId — нова ВЕРСІЯ наявної.
async function uploadSkillPackage(file, skillId) {
  const form = new FormData();
  form.append("file", file);
  const url = skillId ? `${API}/skills/${skillId}/upload` : `${API}/skills/upload`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Authorization": `Bearer ${state.token}` },
    body: form,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.message || "Помилка завантаження");
  return data;
}

async function downloadSkillPackage(s) {
  try {
    const res = await fetch(`${API}/skills/${s.id}/download`, {
      headers: { "Authorization": `Bearer ${state.token}` },
    });
    if (!res.ok) throw new Error("Не вдалося завантажити пакет");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${s.name}-${s.version}.zip`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (err) { toast(err.message, "err"); }
}

const STATUS_LABEL = { published: "Опублікована" };
function statusBadge(s) {
  return s.status === "published"
    ? `<span class="badge published">Опублікована</span>`
    : `<span class="badge draft">Не опублікована</span>`;
}

// Керування категоріями (довідник для вибору при редагуванні навички).
async function renderCategoryChips() {
  const box = $("#cat-chips");
  if (!box) return;
  try {
    const cats = await api("/categories");
    if (!cats.length) { box.innerHTML = `<span class="muted">Категорій ще немає.</span>`; return; }
    box.innerHTML = "";
    cats.forEach(c => {
      const chip = el(`<span class="cat-chip">${esc(c.name)}<button title="Видалити категорію" data-id="${c.id}">×</button></span>`);
      chip.querySelector("button").addEventListener("click", async () => {
        if (!confirm(`Видалити категорію «${c.name}»?\n\nНавички, у яких вона вказана, збережуть свою назву категорії.`)) return;
        try { await api(`/categories/${c.id}`, { method: "DELETE" }); toast("Категорію видалено"); renderCategoryChips(); }
        catch (err) { toast(err.message, "err"); }
      });
      box.appendChild(chip);
    });
  } catch (err) { box.innerHTML = `<span class="error">${esc(err.message)}</span>`; }
}

async function addCategory() {
  const input = $("#cat-new");
  const name = input.value.trim();
  if (!name) { toast("Вкажіть назву категорії", "err"); return; }
  try {
    await api("/categories", { method: "POST", body: { name } });
    input.value = "";
    toast("Категорію додано");
    renderCategoryChips();
  } catch (err) { toast(err.message, "err"); }
}

// Зворотний зв'язок: формат дати DD-MM-YYYY HH:MM.
let manageFbFilter = "new";
function fmtFeedbackDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}-${p(d.getMonth() + 1)}-${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

async function renderFeedback() {
  const listEl = $("#fb-list");
  if (!listEl) return;
  let data;
  try { data = await api(`/skills/feedback?filter=${manageFbFilter}`); }
  catch (err) { listEl.innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }

  const badge = $("#fb-unread");
  badge.textContent = data.unread || "";
  badge.classList.toggle("hidden", !data.unread);
  document.querySelectorAll(".fb-tab").forEach(b =>
    b.classList.toggle("active", b.dataset.f === manageFbFilter));

  if (!data.items.length) {
    listEl.innerHTML = `<p class="muted">${manageFbFilter === "new"
      ? "Нових повідомлень немає." : "Повідомлень ще немає."}</p>`;
    return;
  }
  listEl.innerHTML = "";
  data.items.forEach(f => {
    const item = el(`<div class="fb-item ${f.is_read ? "" : "fb-new"}">
      <div class="fb-line-top">
        <span class="fb-dt">${esc(fmtFeedbackDate(f.created_at))}</span>
        ${f.is_read ? "" : `<span class="fb-tag">Нове</span>`}
      </div>
      <div class="fb-sub">Навичка: <b>${esc(f.skill_name)}</b>. Версія: ${esc(f.skill_version || "—")}</div>
      <div class="fb-sub">Користувач: ${esc(f.username || "—")}</div>
      <div class="fb-text">${esc(f.message)}</div>
      ${f.is_read ? "" : `<div class="fb-item-actions"><button class="small ghost fb-mark">Позначити прочитаним</button></div>`}
    </div>`);
    const mark = item.querySelector(".fb-mark");
    if (mark) mark.addEventListener("click", async () => {
      try { await api(`/skills/feedback/${f.id}/read`, { method: "POST" }); renderFeedback(); }
      catch (err) { toast(err.message, "err"); }
    });
    listEl.appendChild(item);
  });
}

async function viewManageSkills() {
  const skills = await api("/skills");
  const view = $("#view");
  view.innerHTML = `
    <div class="card" id="fb-card">
      <div class="fb-head">
        <h2>Зворотний зв'язок <span id="fb-unread" class="fb-badge hidden"></span></h2>
        <div class="fb-controls">
          <div class="fb-tabs">
            <button class="fb-tab" data-f="new">Нові</button>
            <button class="fb-tab" data-f="all">Усі</button>
          </div>
          <button class="small ghost" id="fb-read-all">Позначити всі прочитаними</button>
        </div>
      </div>
      <div id="fb-list" class="fb-list"></div>
    </div>
    <div class="card">
      <h2>Завантажити нову навичку</h2>
      <p class="muted">Архів <code>.zip</code> або <code>.skill</code> зі <code>skill.md</code> (назва, версія, автор, опис, категорія) та файлами. Нова навичка отримує статус <b>«Не опублікована»</b>.</p>
      <div class="row">
        <input type="file" id="sk-file" accept=".zip,.skill">
        <button id="sk-upload">Завантажити</button>
      </div>
    </div>
    <div class="card">
      <h2>Категорії</h2>
      <p class="muted">Керуйте списком категорій. Обрати категорію можна при редагуванні навички.</p>
      <div class="row" style="margin-bottom:12px">
        <input id="cat-new" placeholder="Назва нової категорії">
        <button id="cat-add">Додати</button>
      </div>
      <div id="cat-chips" class="cat-chips"></div>
    </div>
    <div id="sk-edit"></div>
    <div class="card"><h2>Усі навички</h2><table><thead><tr><th>Назва</th><th>Версія</th><th>Автор</th><th>Категорія</th><th>Статус</th><th>Активацій</th><th>Дії</th></tr></thead><tbody id="sk-body"></tbody></table></div>`;

  renderCategoryChips();
  $("#cat-add").addEventListener("click", addCategory);
  $("#cat-new").addEventListener("keydown", (e) => { if (e.key === "Enter") addCategory(); });

  // Зворотний зв'язок від користувачів.
  document.querySelectorAll(".fb-tab").forEach(b =>
    b.addEventListener("click", () => { manageFbFilter = b.dataset.f; renderFeedback(); }));
  $("#fb-read-all").addEventListener("click", async () => {
    try { await api("/skills/feedback/read-all", { method: "POST" }); renderFeedback(); }
    catch (err) { toast(err.message, "err"); }
  });
  renderFeedback();

  $("#sk-upload").addEventListener("click", async () => {
    const file = $("#sk-file").files[0];
    if (!file) { toast("Оберіть файл архіву", "err"); return; }
    $("#sk-upload").disabled = true;
    try {
      const data = await uploadSkillPackage(file, null);
      toast(`Навичку «${data.name}» завантажено (не опублікована)`);
      openTab("manage-skills");
    } catch (err) { toast(err.message, "err"); }
    finally { $("#sk-upload").disabled = false; }
  });

  const body = $("#sk-body");
  skills.forEach(s => {
    const pubBtn = s.status === "published"
      ? `<button class="small ghost" data-act="unpub">Зняти з публікації</button>`
      : `<button class="small" data-act="pub">Опублікувати</button>`;
    const tr = el(`<tr>
      <td title="${esc(s.package_filename || "")}">${esc(s.name)}</td>
      <td>${esc(s.version)}</td>
      <td>${esc(s.author || "—")}</td>
      <td>${esc(s.category || "—")}</td>
      <td>${statusBadge(s)}</td>
      <td>${s.activations_count}</td>
      <td class="actions">
        ${pubBtn}
        <button class="small ghost" data-act="edit">Редагувати</button>
        ${s.has_package ? `<button class="small ghost" data-act="dl">Скачати</button>` : ""}
        <button class="small danger" data-act="del">Видалити</button>
      </td>
    </tr>`);
    const setStatus = async (status) => {
      try { await api(`/skills/${s.id}/status`, { method: "POST", body: { status } });
        toast(status === "published" ? "Навичку опубліковано" : "Публікацію скасовано");
        openTab("manage-skills"); }
      catch (err) { toast(err.message, "err"); }
    };
    const pb = tr.querySelector("[data-act='pub']");
    if (pb) pb.addEventListener("click", () => setStatus("published"));
    const upb = tr.querySelector("[data-act='unpub']");
    if (upb) upb.addEventListener("click", () => setStatus("draft"));
    tr.querySelector("[data-act='edit']").addEventListener("click", () => openSkillEditor(s.id));
    const dlb = tr.querySelector("[data-act='dl']");
    if (dlb) dlb.addEventListener("click", () => downloadSkillPackage(s));
    tr.querySelector("[data-act='del']").addEventListener("click", async () => {
      if (!confirm(`Видалити навичку «${s.name}»?\n\nБудуть видалені всі її зв'язки з групами та користувачами — після повторного встановлення її доведеться активувати заново.`)) return;
      try { await api(`/skills/${s.id}`, { method: "DELETE" });
        toast("Навичку та її зв'язки видалено"); openTab("manage-skills"); }
      catch (err) { toast(err.message, "err"); }
    });
    body.appendChild(tr);
  });
}

// Завантаження PNG-іконки навички.
async function uploadSkillIcon(file, skillId) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API}/skills/${skillId}/icon`, {
    method: "POST",
    headers: { "Authorization": `Bearer ${state.token}` },
    body: form,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.message || "Помилка завантаження іконки");
  return data;
}

// Контекст навички: редагування атрибутів + іконка + завантаження нової версії.
async function openSkillEditor(skillId) {
  const [s, cats] = await Promise.all([
    api(`/skills/${skillId}`), api("/categories").catch(() => []),
  ]);
  // Гарантуємо, що поточна категорія навички є серед варіантів (навіть якщо її
  // видалили з довідника).
  const names = cats.map(c => c.name);
  if (s.category && !names.includes(s.category)) names.unshift(s.category);
  const catOptions = [`<option value="">— без категорії —</option>`]
    .concat(names.map(n =>
      `<option value="${esc(n)}" ${n === s.category ? "selected" : ""}>${esc(n)}</option>`))
    .join("");

  const box = $("#sk-edit");
  box.innerHTML = `
    <div class="card">
      <h2>Навичка: ${esc(s.name)} <span class="muted">v${esc(s.version)}</span></h2>
      <div class="editor-top">
        <div class="editor-icon">
          <div class="editor-icon-preview" id="se-icon-preview"></div>
          <input type="file" id="se-icon-file" accept="image/png" hidden>
          <div class="editor-icon-actions">
            <button class="small ghost" id="se-icon-pick">Завантажити PNG</button>
            <button class="small ghost danger" id="se-icon-del" ${s.has_icon ? "" : "hidden"}>Прибрати</button>
          </div>
          <div class="muted editor-icon-hint">PNG, до 2 МБ. Якщо не задано — стандартна.</div>
        </div>
        <div class="editor-fields">
          <div class="row">
            <div class="field" style="flex:2"><label>Назва</label><input id="se-name" value="${esc(s.name)}"></div>
            <div class="field" style="flex:1"><label>Версія</label><input id="se-version" value="${esc(s.version)}"></div>
          </div>
          <div class="row">
            <div class="field" style="flex:1"><label>Автор</label><input id="se-author" value="${esc(s.author || "")}"></div>
            <div class="field" style="flex:1"><label>Категорія</label><select id="se-category">${catOptions}</select></div>
          </div>
        </div>
      </div>
      <div class="field"><label>Опис</label><textarea id="se-desc">${esc(s.description || "")}</textarea></div>
      <div class="field"><label>Вхідні дані</label><textarea id="se-input" placeholder="Що подавати на вхід навички">${esc(s.input_spec || "")}</textarea></div>
      <div class="field"><label>Результат роботи</label><textarea id="se-output" placeholder="Що навичка повертає на виході">${esc(s.output_spec || "")}</textarea></div>
      <div class="field"><label>Стартовий промпт</label><textarea id="se-starter" placeholder="Приклад стартового промпту для користувача">${esc(s.starter_prompt || "")}</textarea></div>
      <div class="row">
        <button id="se-save">Зберегти</button>
        <button id="se-close" class="ghost">Закрити</button>
      </div>
      <hr class="md-hr">
      <h3>Нова версія</h3>
      <p class="muted">Завантаження пакета тут оновить <b>цю саму</b> навичку (нова версія): активації та призначення групам збережуться.</p>
      <div class="row">
        <input type="file" id="se-file" accept=".zip,.skill">
        <button id="se-upload">Завантажити нову версію</button>
      </div>
      <div id="se-files" class="muted" style="margin-top:10px"></div>
    </div>`;
  box.scrollIntoView({ behavior: "smooth", block: "start" });

  // Прев'ю іконки: власна (якщо є) або стандартна.
  function paintIcon(skill) {
    const p = $("#se-icon-preview");
    if (skill.has_icon) {
      p.innerHTML = `<img src="${esc(skill.icon_url)}" alt="іконка">`;
    } else {
      p.innerHTML = `<span class="editor-icon-default">${DEFAULT_SKILL_ICON}</span>`;
    }
    $("#se-icon-del").hidden = !skill.has_icon;
  }
  paintIcon(s);

  if (s.has_package) {
    api(`/skills/${s.id}/files`).then(files => {
      $("#se-files").textContent = "Файли пакета: " + files.map(f => f.name).join(", ");
    }).catch(() => {});
  }

  $("#se-icon-pick").addEventListener("click", () => $("#se-icon-file").click());
  $("#se-icon-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const updated = await uploadSkillIcon(file, s.id);
      s.has_icon = updated.has_icon; s.icon_url = updated.icon_url;
      paintIcon(updated);
      toast("Іконку оновлено");
    } catch (err) { toast(err.message, "err"); }
    finally { e.target.value = ""; }
  });
  $("#se-icon-del").addEventListener("click", async () => {
    try {
      const updated = await api(`/skills/${s.id}/icon`, { method: "DELETE" });
      s.has_icon = false; s.icon_url = null;
      paintIcon(updated);
      toast("Іконку прибрано");
    } catch (err) { toast(err.message, "err"); }
  });

  $("#se-save").addEventListener("click", async () => {
    try {
      await api(`/skills/${s.id}`, { method: "PATCH", body: {
        name: $("#se-name").value, version: $("#se-version").value,
        author: $("#se-author").value, category: $("#se-category").value,
        description: $("#se-desc").value,
        input_spec: $("#se-input").value, output_spec: $("#se-output").value,
        starter_prompt: $("#se-starter").value,
      }});
      toast("Атрибути навички збережено"); openTab("manage-skills");
    } catch (err) { toast(err.message, "err"); }
  });
  $("#se-close").addEventListener("click", () => { box.innerHTML = ""; });
  $("#se-upload").addEventListener("click", async () => {
    const file = $("#se-file").files[0];
    if (!file) { toast("Оберіть файл архіву", "err"); return; }
    $("#se-upload").disabled = true;
    try {
      const data = await uploadSkillPackage(file, s.id);
      toast(`Нову версію «${data.name}» v${data.version} завантажено`);
      openTab("manage-skills");
    } catch (err) { toast(err.message, "err"); }
    finally { $("#se-upload").disabled = false; }
  });
}

// ---------- Моделі (Admin) ----------
const PROVIDER_LABELS = {
  azure_ai_foundry: "Azure OpenAI",
  local: "Локальна (Ollama/LM Studio)",
};
const LOCAL_DEFAULT_BASE = "http://localhost:11434/v1";

let modelsSubTab = "config";  // активна вкладка «Моделі»: config | access

async function viewModels() {
  const view = $("#view");
  view.innerHTML = `
    <div class="usage-tabs">
      <button class="u-tab" data-mt="config">Конфіг</button>
      <button class="u-tab" data-mt="access">Доступи</button>
    </div>
    <div id="models-panel"></div>`;
  document.querySelectorAll(".u-tab").forEach(b =>
    b.addEventListener("click", () => { modelsSubTab = b.dataset.mt; paintModelsSub(); }));
  paintModelsSub();
}

function paintModelsSub() {
  document.querySelectorAll(".u-tab").forEach(b =>
    b.classList.toggle("active", b.dataset.mt === modelsSubTab));
  if (modelsSubTab === "access") renderModelAccess();
  else renderModelConfig();
}

async function renderModelConfig() {
  const [models, providers] = await Promise.all([api("/models"), api("/models/providers")]);
  const panel = $("#models-panel");
  const provOpts = providers.map(p =>
    `<option value="${p}">${esc(PROVIDER_LABELS[p] || p)}</option>`).join("");
  panel.innerHTML = `
    <div class="card">
      <h2>Підключити модель</h2>
      <div class="row">
        <input id="m-name" placeholder="Назва">
        <select id="m-provider">${provOpts}</select>
      </div>
      <div class="row" style="margin-top:12px">
        <input id="m-base" autocomplete="off"
               placeholder="Базовий URL — для локальних Ollama/LM Studio (напр. http://localhost:11434/v1)">
      </div>
      <div class="row" style="margin-top:12px">
        <input id="m-dep" list="m-dep-list" autocomplete="off"
               placeholder="Оберіть модель зі списку або введіть вручну">
        <datalist id="m-dep-list"></datalist>
        <button id="m-refresh" class="ghost" title="Оновити список моделей">↻ Оновити</button>
      </div>
      <div class="row" style="margin-top:12px">
        <input id="m-pin" type="text" inputmode="decimal" placeholder="Вартість $ за 1М вхідних токенів (напр. 10.58)">
        <input id="m-pout" type="text" inputmode="decimal" placeholder="Вартість $ за 1М вихідних токенів">
        <button id="m-add">Додати</button>
      </div>
      <p class="muted" id="m-dep-hint" style="margin:8px 0 0"></p>
    </div>
    <div class="card"><h2>Реєстр моделей</h2><p class="muted">Лише <strong>активні</strong> моделі доступні користувачам у чаті. <strong>Системна</strong> модель — для службових задач (іменування чатів). Ціни задаються у USD за 1 мільйон токенів і використовуються для обліку витрат.</p><table><thead><tr><th>Назва</th><th>Провайдер</th><th>Модель / базовий URL</th><th>Ціна $/1М (вх · вих)</th><th>Статус</th><th>Системна</th><th>Дії</th></tr></thead><tbody id="m-body"></tbody></table></div>`;

  const isLocal = () => $("#m-provider").value === "local";

  // Динамічний список доступних моделей у обраного провайдера.
  async function loadAvailableModels() {
    const dl = $("#m-dep-list");
    const hint = $("#m-dep-hint");
    const provider = $("#m-provider").value;
    const baseUrl = $("#m-base").value.trim();
    hint.textContent = "Завантаження списку моделей…";
    dl.innerHTML = "";
    let url = `/models/available?provider=${encodeURIComponent(provider)}`;
    if (baseUrl) url += `&base_url=${encodeURIComponent(baseUrl)}`;
    try {
      const res = await api(url);
      dl.innerHTML = (res.models || []).map(m => `<option value="${esc(m)}"></option>`).join("");
      const label = PROVIDER_LABELS[res.provider] || res.provider;
      if (!res.models || !res.models.length) {
        hint.textContent = "Список порожній — введіть назву моделі вручну.";
      } else if (res.source === "fallback") {
        hint.textContent = provider === "local"
          ? `${label}: показано типові назви. Вкажіть Базовий URL і натисніть «Оновити», щоб отримати реальний список із сервера.`
          : `${label}: показано типові моделі (демо-режим або не задано ключ). Можна ввести й вручну.`;
      } else {
        hint.textContent = `${label}: знайдено ${res.models.length} — оберіть зі списку або введіть вручну.`;
      }
    } catch (err) {
      hint.textContent = `Не вдалося отримати список (${err.message}). Введіть назву вручну.`;
    }
  }
  $("#m-provider").addEventListener("change", () => {
    // Для локального провайдера підставляємо типовий Ollama-URL, якщо поле порожнє.
    if (isLocal() && !$("#m-base").value.trim()) $("#m-base").value = LOCAL_DEFAULT_BASE;
    loadAvailableModels();
  });
  $("#m-base").addEventListener("change", loadAvailableModels);
  $("#m-refresh").addEventListener("click", loadAvailableModels);
  // Автопідстановка назви за обраною моделлю, якщо поле назви порожнє.
  $("#m-dep").addEventListener("change", () => {
    const name = $("#m-name");
    const dep = $("#m-dep").value.trim();
    if (dep && !name.value.trim()) {
      const label = PROVIDER_LABELS[$("#m-provider").value] || $("#m-provider").value;
      name.value = `${dep} (${label})`;
    }
  });
  loadAvailableModels();

  $("#m-add").addEventListener("click", async () => {
    try {
      await api("/models", { method: "POST", body: {
        name: $("#m-name").value, provider: $("#m-provider").value,
        deployment_name: $("#m-dep").value, base_url: $("#m-base").value.trim() || null,
        price_in: $("#m-pin").value.trim() || null,
        price_out: $("#m-pout").value.trim() || null }});
      toast("Модель додано"); renderModelConfig();
    } catch (err) { toast(err.message, "err"); }
  });
  const body = $("#m-body");
  models.forEach(m => {
    const status = m.is_active
      ? `<span class="badge published">активна</span>`
      : `<span class="badge delisted">неактивна</span>`;
    const sysCell = m.is_system
      ? `<span class="badge published">системна</span>`
      : `<button class="small ghost" data-act="sys">Зробити системною</button>`;
    const target = m.base_url
      ? `${esc(m.deployment_name)} <span class="muted">· ${esc(m.base_url)}</span>`
      : esc(m.deployment_name);
    const priceCell = (m.price_in != null || m.price_out != null)
      ? `${m.price_in != null ? fmtMoney(m.price_in) : "—"} · ${m.price_out != null ? fmtMoney(m.price_out) : "—"}`
      : `<span class="muted">не задано</span>`;
    const tr = el(`<tr>
      <td>${esc(m.name)}</td>
      <td><span class="badge">${esc(PROVIDER_LABELS[m.provider] || m.provider)}</span></td>
      <td>${target}</td>
      <td>${priceCell}</td>
      <td>${status}</td>
      <td>${sysCell}</td>
      <td class="actions">
        <button class="small ghost" data-act="toggle" data-id="${m.id}" data-active="${m.is_active}">${m.is_active ? "Деактивувати" : "Активувати"}</button>
        <button class="small danger" data-act="del" data-id="${m.id}">Видалити</button>
      </td>
    </tr>`);
    const sysBtn = tr.querySelector("[data-act='sys']");
    if (sysBtn) sysBtn.addEventListener("click", async () => {
      try { await api(`/models/${m.id}/system`, { method: "POST", body: { is_system: true } });
        toast(`«${m.name}» — системна модель`); renderModelConfig(); }
      catch (err) { toast(err.message, "err"); }
    });
    tr.querySelector("[data-act='toggle']").addEventListener("click", async () => {
      try { await api(`/models/${m.id}/activate`, { method: "POST", body: { is_active: !m.is_active } });
        toast("Статус оновлено"); renderModelConfig(); }
      catch (err) { toast(err.message, "err"); }
    });
    tr.querySelector("[data-act='del']").addEventListener("click", async () => {
      if (!confirm(`Видалити модель «${m.name}»?`)) return;
      try { await api(`/models/${m.id}`, { method: "DELETE" }); toast("Модель видалено"); renderModelConfig(); }
      catch (err) { toast(err.message, "err"); }
    });
    body.appendChild(tr);
  });
}

// Матриця доступів: групи (рядки) × моделі (колонки), чекбокси у комірках.
async function renderModelAccess() {
  const panel = $("#models-panel");
  panel.innerHTML = `<div class="card"><p class="muted">Завантаження…</p></div>`;
  const mx = await api("/models/access-matrix");
  if (!mx.models.length) {
    panel.innerHTML = `<div class="card"><p class="muted">Спершу додайте моделі у вкладці «Конфіг».</p></div>`;
    return;
  }
  if (!mx.groups.length) {
    panel.innerHTML = `<div class="card"><p class="muted">Спершу створіть групи у вкладці «Групи», щоб надавати їм доступ до моделей.</p></div>`;
    return;
  }
  const grants = new Set(mx.grants);
  const head = mx.models.map(m => {
    const tags = (m.is_system ? `<span class="am-tag">системна</span>` : "")
      + (m.is_active ? "" : `<span class="am-tag am-off">неактивна</span>`);
    return `<th class="am-col${m.is_system ? " am-sys" : ""}"><span class="am-mname">${esc(m.name)}</span>${tags}</th>`;
  }).join("");
  const rows = mx.groups.map(g => {
    const cells = mx.models.map(m => {
      if (m.is_system) {
        return `<td class="am-cell"><input type="checkbox" checked disabled title="Системна модель доступна всім"></td>`;
      }
      const on = grants.has(`${g.id}:${m.id}`);
      return `<td class="am-cell"><input type="checkbox" data-g="${g.id}" data-m="${m.id}"${on ? " checked" : ""}></td>`;
    }).join("");
    return `<tr><th class="am-row" scope="row">${esc(g.name)}</th>${cells}</tr>`;
  }).join("");
  panel.innerHTML = `
    <div class="card">
      <h2>Матриця доступів</h2>
      <p class="muted">Позначте, які <strong>групи</strong> мають доступ до яких <strong>моделей</strong>. Користувач бачить у чаті моделі своїх груп. <strong>Системна</strong> модель доступна всім і не потребує призначення.</p>
      <div class="am-wrap">
        <table class="am-table">
          <thead><tr><th class="am-corner">Група \\ Модель</th>${head}</tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>`;
  panel.querySelectorAll(".am-cell input[data-g]").forEach(cb =>
    cb.addEventListener("change", async () => {
      cb.disabled = true;
      try {
        await api("/models/access", { method: "POST", body: {
          group_id: Number(cb.dataset.g), model_id: Number(cb.dataset.m), granted: cb.checked } });
      } catch (err) {
        cb.checked = !cb.checked;  // відкат на помилці
        toast(err.message, "err");
      } finally { cb.disabled = false; }
    }));
}

// ---------- Групи ----------

// Живий пошук користувачів (за іменем/логіном/email) з випадним списком.
function attachUserSearch(input, resultsEl, { exclude = new Set(), onPick }) {
  let timer = null;
  const doSearch = async () => {
    const q = input.value.trim();
    try {
      const users = await api(`/users/search?q=${encodeURIComponent(q)}`);
      const list = users.filter(u => !exclude.has(u.id));
      resultsEl.innerHTML = "";
      if (!list.length) { resultsEl.innerHTML = `<div class="us-empty muted">Нічого не знайдено</div>`; return; }
      list.forEach(u => {
        const item = el(`<button class="us-item" type="button">
          <span class="us-name">${esc(u.full_name || u.username)}</span>
          <span class="us-sub muted">${esc(u.email || u.username)}</span></button>`);
        item.addEventListener("click", () => onPick(u));
        resultsEl.appendChild(item);
      });
    } catch (err) { resultsEl.innerHTML = `<div class="us-empty error">${esc(err.message)}</div>`; }
  };
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(doSearch, 220); });
  input.addEventListener("focus", doSearch);
}

async function viewGroups() {
  const groups = await api("/groups");
  const view = $("#view");
  const admin = hasRole("admin");
  const createCard = admin ? `<div class="card"><h2>Нова група</h2><div class="row">
      <input id="g-name" placeholder="Назва групи">
      <input id="g-desc" placeholder="Опис (необов'язково)">
      <button id="g-add">Створити</button></div></div>` : "";
  view.innerHTML = createCard + `<div class="card"><h2>Групи</h2><div id="g-list"></div></div>`;

  if (admin) $("#g-add").addEventListener("click", async () => {
    const name = $("#g-name").value.trim();
    if (!name) { toast("Вкажіть назву групи", "err"); return; }
    try { await api("/groups", { method: "POST", body: { name, description: $("#g-desc").value }});
      toast("Групу створено"); openTab("groups"); }
    catch (err) { toast(err.message, "err"); }
  });

  const list = $("#g-list");
  if (!groups.length) { list.innerHTML = `<p class="muted">Немає груп.</p>`; return; }
  for (const g of groups) {
    const actions = admin ? `<div class="g-actions">
        <button class="small ghost g-rename">Перейменувати</button>
        <button class="small danger g-del">Видалити</button></div>` : "";
    const wrap = el(`<div class="card g-card">
      <div class="g-head">
        <div class="g-head-info">
          <h3 class="g-title">${esc(g.name)}</h3>
          <p class="muted g-desc">${esc(g.description || "")}</p>
        </div>
        ${actions}
      </div>
      <div id="g-detail-${g.id}"></div>
    </div>`);
    if (admin) {
      wrap.querySelector(".g-rename").addEventListener("click", async () => {
        const name = prompt("Нова назва групи:", g.name);
        if (name === null) return;
        const description = prompt("Опис (необов'язково):", g.description || "");
        try { await api(`/groups/${g.id}`, { method: "PATCH",
            body: { name, description: description == null ? "" : description }});
          toast("Групу оновлено"); openTab("groups"); }
        catch (err) { toast(err.message, "err"); }
      });
      wrap.querySelector(".g-del").addEventListener("click", async () => {
        if (!confirm(`Видалити групу «${g.name}»?\n\nУчасники втратять доступ до навичок, наданих цією групою.`)) return;
        try { await api(`/groups/${g.id}`, { method: "DELETE" }); toast("Групу видалено"); openTab("groups"); }
        catch (err) { toast(err.message, "err"); }
      });
    }
    list.appendChild(wrap);
    renderGroupDetail(g.id);
  }
}

async function renderGroupDetail(groupId) {
  const detail = await api(`/groups/${groupId}`);
  const container = $(`#g-detail-${groupId}`);
  if (!container) return;
  const admin = hasRole("admin");
  const members = detail.members || [];
  const memberIds = new Set(members.map(m => m.user_id));

  container.innerHTML = `
    <div class="g-members-head">
      <strong>Учасники (${members.length})</strong>
      <input class="g-msearch" placeholder="Пошук учасника…" autocomplete="off">
    </div>
    <div class="g-members"></div>
    ${admin ? `
    <div class="g-add">
      <div class="muted g-add-label">Додати учасника:</div>
      <div class="us-box">
        <input class="us-input" placeholder="Пошук за іменем або email…" autocomplete="off">
        <div class="us-results"></div>
      </div>
    </div>` : ""}`;

  const listEl = container.querySelector(".g-members");
  function paintMembers(filter = "") {
    if (!members.length) { listEl.innerHTML = `<div class="muted g-empty">У групі немає учасників.</div>`; return; }
    const f = filter.trim().toLowerCase();
    const shown = members.filter(m => !f ||
      [m.full_name, m.username, m.email].some(v => (v || "").toLowerCase().includes(f)));
    if (!shown.length) { listEl.innerHTML = `<div class="muted g-empty">Нічого не знайдено.</div>`; return; }
    listEl.innerHTML = "";
    shown.forEach(m => {
      const row = el(`<div class="g-member">
        <div class="g-member-info">
          <span class="g-member-name">${esc(m.full_name || m.username)}</span>
          <span class="g-member-sub muted">${esc(m.email || m.username)}</span>
        </div>
        ${admin ? `<button class="small danger g-rm">Вилучити</button>` : ""}
      </div>`);
      const rm = row.querySelector(".g-rm");
      if (rm) rm.addEventListener("click", async () => {
        try { await api(`/groups/${groupId}/members/${m.user_id}`, { method: "DELETE" });
          toast("Учасника вилучено"); renderGroupDetail(groupId); }
        catch (err) { toast(err.message, "err"); }
      });
      listEl.appendChild(row);
    });
  }
  paintMembers();
  container.querySelector(".g-msearch").addEventListener("input",
    (e) => paintMembers(e.target.value));

  if (admin) {
    attachUserSearch(container.querySelector(".us-input"),
      container.querySelector(".us-results"), {
        exclude: memberIds,
        onPick: async (u) => {
          try { await api(`/groups/${groupId}/members`, { method: "POST", body: { user_id: u.id }});
            toast(`Додано: ${u.full_name || u.username}`); renderGroupDetail(groupId); }
          catch (err) { toast(err.message, "err"); }
        },
      });
  }
}

// ---------- Користувачі (Admin) ----------
const USER_ROLE_OPTS = [
  { v: "", label: "member (звичайний)" },
  { v: "skill_manager", label: "skill_manager" },
  { v: "admin", label: "admin" },
];
function primaryRole(u) {
  if (u.roles.includes("admin")) return "admin";
  if (u.roles.includes("skill_manager")) return "skill_manager";
  return "";
}

async function viewUsers() {
  const [users, def] = await Promise.all([api("/users"), api("/usage/default-limit")]);
  const view = $("#view");
  const roleOpts = (sel) => USER_ROLE_OPTS.map(o =>
    `<option value="${o.v}" ${o.v === sel ? "selected" : ""}>${esc(o.label)}</option>`).join("");
  view.innerHTML = `
    <div class="card">
      <h2>Системна тижнева квота ($)</h2>
      <p class="muted">Тижневий ліміт витрат у доларах для всіх користувачів без персональної квоти. Скидання — щопонеділка 00:05 UTC.</p>
      <div class="row">
        <input id="sys-limit" type="number" min="0" step="0.01" value="${def.limit}">
        <button id="sys-save">Зберегти</button>
      </div>
    </div>
    <div class="card">
      <h2>Новий користувач</h2>
      <div class="row">
        <input id="u-username" placeholder="Логін">
        <input id="u-name" placeholder="Повне ім'я">
        <input id="u-email" placeholder="Email" type="email">
        <input id="u-pass" placeholder="Пароль" type="text">
        <select id="u-role">${roleOpts("")}</select>
        <button id="u-add">Створити</button>
      </div>
    </div>
    <div id="u-detail"></div>
    <div class="card"><h2>Користувачі</h2><table><thead><tr><th>ID</th><th>Логін</th><th>Ім'я</th><th>Email</th><th>Ролі</th><th>Тижнева квота $ (витрачено / ліміт)</th><th>Активний</th><th>Дії</th></tr></thead><tbody id="u-body"></tbody></table></div>`;

  $("#sys-save").addEventListener("click", async () => {
    try { await api("/usage/default-limit", { method: "POST", body: { limit: Number($("#sys-limit").value) }});
      toast("Системну квоту оновлено"); openTab("users"); }
    catch (err) { toast(err.message, "err"); }
  });
  $("#u-add").addEventListener("click", async () => {
    const roles = $("#u-role").value ? [$("#u-role").value] : [];
    try { await api("/users", { method: "POST", body: {
      username: $("#u-username").value, full_name: $("#u-name").value,
      email: $("#u-email").value || null, password: $("#u-pass").value, roles }});
      toast("Користувача створено"); openTab("users"); }
    catch (err) { toast(err.message, "err"); }
  });

  const body = $("#u-body");
  users.forEach(u => {
    const used = fmtMoney(u.used_cost || 0);
    const eff = fmtMoney(u.effective_limit || 0);
    const custom = u.custom_limit != null
      ? `<span class="badge published" title="Персональна квота">власна</span>`
      : `<span class="badge" title="Системна квота">системна</span>`;
    const delQuota = u.custom_limit != null
      ? `<button class="small danger" data-act="limit-del">✕ квота</button>` : "";
    const protectedUser = u.is_system_admin || u.id === (state.user && state.user.id);
    const tr = el(`<tr>
      <td>${u.id}</td><td>${esc(u.username)}</td><td>${esc(u.full_name || "")}</td>
      <td>${esc(u.email || "—")}</td>
      <td>${u.roles.join(", ") || "member"}</td>
      <td><span class="muted">${used} /</span> ${eff} ${custom}
        <button class="small ghost" data-act="limit">${u.custom_limit != null ? "Змінити" : "Задати"}</button>
        ${delQuota}</td>
      <td>${u.is_active ? "✓" : "—"}</td>
      <td class="actions">
        <button class="small ghost" data-act="edit">Редагувати</button>
        <button class="small" data-act="reset">Пароль</button>
        <button class="small danger" data-act="del" ${protectedUser ? "disabled title='Захищений обліковий запис'" : ""}>Видалити</button>
      </td>
    </tr>`);
    tr.querySelector("[data-act='edit']").addEventListener("click", () => openUserEditor(u.id));
    tr.querySelector("[data-act='limit']").addEventListener("click", async () => {
      const v = prompt(`Персональна тижнева квота для «${u.username}» ($, напр. 1 або 0.5):`,
                       u.custom_limit != null ? u.custom_limit : u.effective_limit);
      if (v === null) return;
      try { await api(`/users/${u.id}/token-limit`, { method: "POST", body: { limit: Number(v) }});
        toast("Персональну квоту встановлено"); openTab("users"); }
      catch (err) { toast(err.message, "err"); }
    });
    const limDel = tr.querySelector("[data-act='limit-del']");
    if (limDel) limDel.addEventListener("click", async () => {
      if (!confirm(`Видалити персональну квоту «${u.username}»? Діятиме системна.`)) return;
      try { await api(`/users/${u.id}/token-limit`, { method: "DELETE" });
        toast("Персональну квоту видалено"); openTab("users"); }
      catch (err) { toast(err.message, "err"); }
    });
    tr.querySelector("[data-act='reset']").addEventListener("click", async () => {
      const p = prompt("Новий пароль (мін. 6 символів):");
      if (!p) return;
      try { await api(`/users/${u.id}/reset-password`, { method: "POST", body: { password: p }}); toast("Пароль оновлено"); }
      catch (err) { toast(err.message, "err"); }
    });
    const delBtn = tr.querySelector("[data-act='del']");
    if (delBtn && !protectedUser) delBtn.addEventListener("click", async () => {
      if (!confirm(`Видалити користувача «${u.username}»?\n\nБудуть видалені його чати, файли, членства та вся історія. Дію не можна скасувати.`)) return;
      try { await api(`/users/${u.id}`, { method: "DELETE" }); toast("Користувача видалено"); openTab("users"); }
      catch (err) { toast(err.message, "err"); }
    });
    body.appendChild(tr);
  });
}

// Редактор користувача: атрибути + список груп із можливістю вилучення.
async function openUserEditor(userId) {
  const [u, groups] = await Promise.all([
    api(`/users/${userId}`), api(`/users/${userId}/groups`).catch(() => []),
  ]);
  const roleOpts = USER_ROLE_OPTS.map(o =>
    `<option value="${o.v}" ${o.v === primaryRole(u) ? "selected" : ""}>${esc(o.label)}</option>`).join("");
  const box = $("#u-detail");
  box.innerHTML = `
    <div class="card">
      <h2>Користувач: ${esc(u.username)}</h2>
      <div class="row">
        <div class="field" style="flex:1"><label>Повне ім'я</label><input id="ue-name" value="${esc(u.full_name || "")}"></div>
        <div class="field" style="flex:1"><label>Email</label><input id="ue-email" value="${esc(u.email || "")}"></div>
      </div>
      <div class="row">
        <div class="field" style="flex:1"><label>Роль</label><select id="ue-role">${roleOpts}</select></div>
        <div class="field" style="flex:1"><label>Статус</label>
          <select id="ue-active"><option value="1" ${u.is_active ? "selected" : ""}>Активний</option><option value="0" ${!u.is_active ? "selected" : ""}>Деактивований</option></select></div>
      </div>
      <div class="row">
        <button id="ue-save">Зберегти</button>
        <button id="ue-close" class="ghost">Закрити</button>
      </div>
      <hr class="md-hr">
      <h3>Групи користувача</h3>
      <div id="ue-groups" class="ue-groups"></div>
    </div>`;
  box.scrollIntoView({ behavior: "smooth", block: "start" });

  function paintGroups(list) {
    const g = $("#ue-groups");
    if (!list.length) { g.innerHTML = `<span class="muted">Користувач не входить у жодну групу.</span>`; return; }
    g.innerHTML = "";
    list.forEach(gr => {
      const chip = el(`<span class="cat-chip">${esc(gr.name)}<button title="Вилучити з групи" data-gid="${gr.id}">×</button></span>`);
      chip.querySelector("button").addEventListener("click", async () => {
        if (!confirm(`Вилучити «${u.username}» з групи «${gr.name}»?`)) return;
        try {
          await api(`/users/${userId}/groups/${gr.id}`, { method: "DELETE" });
          toast("Вилучено з групи");
          paintGroups(await api(`/users/${userId}/groups`));
        } catch (err) { toast(err.message, "err"); }
      });
      g.appendChild(chip);
    });
  }
  paintGroups(groups);

  $("#ue-close").addEventListener("click", () => { box.innerHTML = ""; });
  $("#ue-save").addEventListener("click", async () => {
    const roles = $("#ue-role").value ? [$("#ue-role").value] : [];
    try {
      await api(`/users/${userId}`, { method: "PATCH", body: {
        full_name: $("#ue-name").value, email: $("#ue-email").value || null,
        is_active: $("#ue-active").value === "1", roles }});
      toast("Користувача оновлено"); openTab("users");
    } catch (err) { toast(err.message, "err"); }
  });
}

// ---------- Токени ----------
// ---------- Квота: вкладки «Токени» / «Гроші» ----------
let moneyRange = { from: "", to: "" };
// Область перегляду «Гроші» (лише для Admin): свій/інший користувач або група.
let moneyScope = { mode: "user", userId: "", groupId: "" };
let scopeOptions = null;  // кеш списків користувачів/груп для перемикача

async function viewUsage() {
  const view = $("#view");
  view.innerHTML = `
    <div class="usage-tabs">
      <button class="u-tab" data-u="tokens">Токени</button>
      <button class="u-tab" data-u="money">Гроші</button>
    </div>
    <div id="usage-panel"></div>`;
  document.querySelectorAll(".u-tab").forEach(b =>
    b.addEventListener("click", () => { usageSubTab = b.dataset.u; paintUsageSub(); }));
  paintUsageSub();
}

function paintUsageSub() {
  document.querySelectorAll(".u-tab").forEach(b =>
    b.classList.toggle("active", b.dataset.u === usageSubTab));
  if (usageSubTab === "money") renderMoneyPanel();
  else renderTokensPanel();
}

async function renderTokensPanel() {
  const panel = $("#usage-panel");
  panel.innerHTML = `<p class="muted">Завантаження…</p>`;
  const [mine, tl] = await Promise.all([api("/usage/me"), api("/usage/timeline")]);
  let html = `<div class="card"><h2>Мої токени</h2><div class="stat-grid">
      <div class="stat"><div class="num">${fmtInt(mine.total_tokens)}</div><div class="label">Усього токенів</div></div>
      <div class="stat"><div class="num">${fmtInt(mine.requests)}</div><div class="label">Запитів</div></div>
      <div class="stat"><div class="num">${fmtInt(mine.prompt_tokens)}</div><div class="label">Вхідні</div></div>
      <div class="stat"><div class="num">${fmtInt(mine.completion_tokens)}</div><div class="label">Вихідні</div></div>
    </div></div>
    <div class="card">
      <h2>Використання токенів за часом</h2>
      <div class="ch-legend"><span class="ch-key"><i style="background:var(--chart-in)"></i>Вхідні</span><span class="ch-key"><i style="background:var(--chart-out)"></i>Вихідні</span></div>
      <div id="tok-chart"></div>
    </div>`;
  panel.innerHTML = html;
  renderTokenChart($("#tok-chart"), tl);

  if (hasRole("admin")) {
    const [g, sys] = await Promise.all([api("/usage/global"), api("/usage/system")]);
    const rows = g.by_user.map(u => `<tr><td>${esc(u.username)}</td><td>${fmtInt(u.total_tokens)}</td><td>${fmtMoney(u.cost_total)}</td><td>${u.requests}</td></tr>`).join("");
    const sysRows = (sys.breakdown || []).map(b =>
      `<tr><td>${esc(b.model)}</td><td><span class="badge">${esc(b.feature)}</span></td><td>${fmtInt(b.prompt_tokens)}</td><td>${fmtInt(b.completion_tokens)}</td><td>${fmtInt(b.total_tokens)}</td><td>${b.requests}</td></tr>`).join("")
      || `<tr><td colspan="6" class="muted">Системного використання ще не було.</td></tr>`;
    panel.insertAdjacentHTML("beforeend", `
      <div class="card"><h2>Глобальне споживання (Admin)</h2>
        <table><thead><tr><th>Користувач</th><th>Токенів</th><th>Витрачено</th><th>Запитів</th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="card"><h2>Системне використання (Admin)</h2>
        <p class="muted">Окремий облік токенів платформи (поза квотами користувачів).</p>
        <table><thead><tr><th>Модель</th><th>Фіча</th><th>Вхідні</th><th>Вихідні</th><th>Усього</th><th>Запитів</th></tr></thead><tbody>${sysRows}</tbody></table></div>`);
  }
}

async function renderMoneyPanel() {
  const panel = $("#usage-panel");
  const admin = hasRole("admin");
  panel.innerHTML = `
    ${admin ? `<div class="card scope-card" id="scope-card"><p class="muted">Завантаження…</p></div>` : ""}
    <div class="card">
      <div class="money-head">
        <h2>Витрати</h2>
        <div class="money-range">
          <label>Від <input type="date" id="mr-from" value="${esc(moneyRange.from)}"></label>
          <label>До <input type="date" id="mr-to" value="${esc(moneyRange.to)}"></label>
          <button class="small ghost" id="mr-reset">За весь час</button>
        </div>
      </div>
      <div id="money-total"><p class="muted">Завантаження…</p></div>
    </div>
    <div class="card">
      <h2>Витрати за моделями</h2>
      <div class="money-split"><div id="money-pie"></div><div id="money-list"></div></div>
    </div>
    <div class="card">
      <h2>Використання Навичок</h2>
      <div id="skill-usage"></div>
    </div>`;
  $("#mr-from").addEventListener("change", () => { moneyRange.from = $("#mr-from").value; loadMoney(); });
  $("#mr-to").addEventListener("change", () => { moneyRange.to = $("#mr-to").value; loadMoney(); });
  $("#mr-reset").addEventListener("click", () => {
    moneyRange = { from: "", to: "" }; $("#mr-from").value = ""; $("#mr-to").value = ""; loadMoney();
  });
  if (admin) await renderScopeSelector();
  loadMoney();
}

// Перемикач області (Admin): користувач ↔ група.
async function renderScopeSelector() {
  if (!scopeOptions) scopeOptions = await api("/usage/scope-options");
  const card = $("#scope-card");
  if (!card) return;
  const isGroup = moneyScope.mode === "group";
  const count = ((isGroup ? scopeOptions.groups : scopeOptions.users) || []).length;
  const size = Math.min(7, Math.max(3, count));
  card.innerHTML = `
    <div class="scope-head">
      <h2>Статистика для</h2>
      <div class="seg scope-seg">
        <button type="button" class="${isGroup ? "" : "active"}" data-mode="user">Користувача</button>
        <button type="button" class="${isGroup ? "active" : ""}" data-mode="group">Групи</button>
      </div>
    </div>
    <div class="scope-search-wrap">
      <svg class="scope-search-ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M10 4a6 6 0 1 0 3.9 10.6l4.3 4.3 1.4-1.4-4.3-4.3A6 6 0 0 0 10 4Zm0 2a4 4 0 1 1 0 8 4 4 0 0 1 0-8Z"/></svg>
      <input type="text" id="scope-search" class="scope-search" autocomplete="off"
             placeholder="${isGroup ? "Пошук групи…" : "Пошук користувача…"}">
    </div>
    <select id="scope-target" class="scope-select" size="${size}">${scopeTargetOptions()}</select>
    <div id="scope-info" class="muted scope-info"></div>`;
  // Синхронізуємо стан із фактично обраним у списку значенням.
  const sel = $("#scope-target");
  if (isGroup) moneyScope.groupId = sel.value;
  else moneyScope.userId = sel.value;

  card.querySelectorAll(".scope-seg button").forEach(b =>
    b.addEventListener("click", () => {
      if (moneyScope.mode === b.dataset.mode) return;
      moneyScope.mode = b.dataset.mode;
      renderScopeSelector().then(loadMoney);
    }));
  $("#scope-search").addEventListener("input", (e) => {
    sel.innerHTML = scopeTargetOptions(e.target.value);
  });
  sel.addEventListener("change", () => {
    if (!sel.value) return;  // рядок-заглушка «нічого не знайдено»
    if (moneyScope.mode === "group") moneyScope.groupId = sel.value;
    else moneyScope.userId = sel.value;
    loadMoney();
  });
}

function scopeTargetOptions(filter = "") {
  const f = filter.trim().toLowerCase();
  if (moneyScope.mode === "group") {
    let groups = (scopeOptions && scopeOptions.groups) || [];
    const total = groups.length;
    if (f) groups = groups.filter(g => (g.name || "").toLowerCase().includes(f));
    if (!groups.length)
      return `<option value="" disabled>${total ? "— нічого не знайдено —" : "— груп немає —"}</option>`;
    const cur = moneyScope.groupId || String(groups[0].id);
    return groups.map(g =>
      `<option value="${g.id}" ${String(g.id) === String(cur) ? "selected" : ""}>${esc(g.name)}</option>`).join("");
  }
  const me = state.user && state.user.id;
  let users = (scopeOptions && scopeOptions.users) || [];
  if (f) users = users.filter(u =>
    ((u.full_name || "") + " " + (u.username || "")).toLowerCase().includes(f));
  if (!users.length) return `<option value="" disabled>— нічого не знайдено —</option>`;
  const cur = moneyScope.userId || String(me);
  return users.map(u => {
    const label = (u.full_name ? u.full_name + " · " : "") + u.username + (u.id === me ? " (я)" : "");
    return `<option value="${u.id}" ${String(u.id) === String(cur) ? "selected" : ""}>${esc(label)}</option>`;
  }).join("");
}

async function loadMoney() {
  const qs = [];
  if (moneyRange.from) qs.push("from=" + encodeURIComponent(moneyRange.from));
  if (moneyRange.to) qs.push("to=" + encodeURIComponent(moneyRange.to));
  if (hasRole("admin")) {
    if (moneyScope.mode === "group" && moneyScope.groupId)
      qs.push("group_id=" + encodeURIComponent(moneyScope.groupId));
    else if (moneyScope.userId)
      qs.push("user_id=" + encodeURIComponent(moneyScope.userId));
  }
  const m = await api("/usage/money" + (qs.length ? "?" + qs.join("&") : ""));
  const si = $("#scope-info");
  if (si && m.scope) {
    si.textContent = m.scope.type === "group"
      ? `Група «${m.scope.group}» · учасників: ${fmtInt(m.scope.members)}`
      : `Користувач: ${m.scope.username}`;
  }
  // Порожній період — за замовчуванням підставляємо межі всієї активності.
  if (!moneyRange.from && m.activity_from) $("#mr-from").value = m.activity_from.slice(0, 10);
  if (!moneyRange.to && m.activity_to) $("#mr-to").value = m.activity_to.slice(0, 10);
  $("#money-total").innerHTML = `<div class="stat-grid">
      <div class="stat"><div class="num">${fmtMoney(m.cost_total)}</div><div class="label">Усього витрачено</div></div>
      <div class="stat"><div class="num">${fmtMoney(m.cost_in)}</div><div class="label">За вхідні токени</div></div>
      <div class="stat"><div class="num">${fmtMoney(m.cost_out)}</div><div class="label">За вихідні токени</div></div>
      <div class="stat"><div class="num">${fmtInt(m.requests)}</div><div class="label">Запитів</div></div>
    </div>`;
  renderMoneyPie($("#money-pie"), $("#money-list"), m.by_model);
  renderSkillUsage($("#skill-usage"), m.by_skill);
}

// Розподіл вартості по навичках: загальна та середня за запуск.
function renderSkillUsage(box, bySkill) {
  const items = (bySkill || []).filter(s => s.runs > 0);
  if (!items.length) {
    box.innerHTML = `<p class="muted">За обраний період навички не запускались.</p>`;
    return;
  }
  box.innerHTML = `<table class="su-table">
    <thead><tr>
      <th class="su-name">Навичка</th>
      <th class="su-num">Запусків</th>
      <th class="su-num">Загальна вартість</th>
      <th class="su-num">Середня / запуск</th>
    </tr></thead>
    <tbody>${items.map(s => `<tr>
      <td class="su-name">${esc(s.skill)}</td>
      <td class="su-num">${fmtInt(s.runs)}</td>
      <td class="su-num">${fmtMoney(s.cost)}</td>
      <td class="su-num">${fmtMoney(s.avg)}</td>
    </tr>`).join("")}</tbody>
  </table>`;
}

// ---------- Діаграми (inline SVG, тема-залежні) ----------
function fmtBucketLabel(iso, gran) {
  const d = new Date(iso);
  const p = n => String(n).padStart(2, "0");
  if (gran === "hour") return `${p(d.getDate())}.${p(d.getMonth() + 1)} ${p(d.getHours())}:00`;
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}`;
}

function renderTokenChart(container, tl) {
  const buckets = tl.buckets || [];
  if (!buckets.length) { container.innerHTML = `<p class="muted">Немає використання за цей період.</p>`; return; }
  const W = 820, H = 280, padL = 56, padR = 12, padT = 12, padB = 38;
  const plotW = W - padL - padR, plotH = H - padT - padB, baseY = padT + plotH;
  const n = buckets.length, slot = plotW / n, gap = 2;
  const bw = Math.max(1, slot - gap);
  const maxV = Math.max(1, ...buckets.map(b => (b.prompt_tokens || 0) + (b.completion_tokens || 0)));
  const h = v => (v / maxV) * plotH;

  let grid = "";
  [0, maxV / 2, maxV].forEach(v => {
    const y = baseY - h(v);
    grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" class="ch-grid"/>`;
    grid += `<text x="${padL - 8}" y="${(y + 4).toFixed(1)}" class="ch-axis" text-anchor="end">${fmtInt(Math.round(v))}</text>`;
  });

  const every = Math.ceil(n / 7);
  let bars = "", labels = "";
  buckets.forEach((b, i) => {
    const x = padL + i * slot + gap / 2;
    const pin = b.prompt_tokens || 0, pout = b.completion_tokens || 0;
    const hIn = h(pin), hOut = h(pout);
    const title = esc(`${fmtBucketLabel(b.t, tl.granularity)} · вхідні ${fmtInt(pin)} · вихідні ${fmtInt(pout)}`);
    let g = `<g class="ch-bar"><title>${title}</title>`;
    if (hIn > 0.5) g += `<rect x="${x.toFixed(1)}" y="${(baseY - hIn).toFixed(1)}" width="${bw.toFixed(1)}" height="${hIn.toFixed(1)}" rx="2" fill="var(--chart-in)"/>`;
    if (hOut > 0.5) {
      const yOut = baseY - hIn - (hIn > 0 ? gap : 0) - hOut;
      g += `<rect x="${x.toFixed(1)}" y="${Math.max(padT, yOut).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, hOut).toFixed(1)}" rx="2" fill="var(--chart-out)"/>`;
    }
    bars += g + `</g>`;
    if (i % every === 0 || i === n - 1) {
      const cx = padL + i * slot + slot / 2;
      labels += `<text x="${cx.toFixed(1)}" y="${(baseY + 16).toFixed(1)}" class="ch-axis" text-anchor="middle">${esc(fmtBucketLabel(b.t, tl.granularity))}</text>`;
    }
  });
  container.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" class="chart-svg" preserveAspectRatio="xMidYMid meet" role="img">
       ${grid}<line x1="${padL}" y1="${baseY}" x2="${W - padR}" y2="${baseY}" class="ch-axis-line"/>${bars}${labels}</svg>`;
}

const PIE_HUES = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)", "var(--c7)", "var(--c8)"];
function _polar(cx, cy, r, a) { return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; }
function _arc(cx, cy, r, rin, a1, a2) {
  const large = (a2 - a1) > Math.PI ? 1 : 0;
  const [x1, y1] = _polar(cx, cy, r, a1), [x2, y2] = _polar(cx, cy, r, a2);
  const [x3, y3] = _polar(cx, cy, rin, a2), [x4, y4] = _polar(cx, cy, rin, a1);
  return `M${x1.toFixed(2)} ${y1.toFixed(2)} A${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)} `
    + `L${x3.toFixed(2)} ${y3.toFixed(2)} A${rin} ${rin} 0 ${large} 0 ${x4.toFixed(2)} ${y4.toFixed(2)} Z`;
}

function renderMoneyPie(pieEl, listEl, byModel) {
  const items = (byModel || []).filter(m => m.cost > 0);
  if (!items.length) {
    pieEl.innerHTML = `<p class="muted">Немає витрат за обраний період.</p>`;
    listEl.innerHTML = ""; return;
  }
  // Понад 8 моделей — решту згортаємо в «Інші».
  let data = items;
  if (items.length > 8) {
    const other = items.slice(7).reduce((s, m) => s + m.cost, 0);
    data = items.slice(0, 7).concat([{ model: "Інші", cost: other, _other: true }]);
  }
  const total = data.reduce((s, m) => s + m.cost, 0);
  const colorOf = (m, i) => m._other ? "var(--muted)" : PIE_HUES[i % 8];
  const cx = 110, cy = 110, r = 96, rin = 58;

  let segs = "", ang = -Math.PI / 2;
  if (data.length === 1) {
    segs = `<circle cx="${cx}" cy="${cy}" r="${(r + rin) / 2}" fill="none" stroke="${colorOf(data[0], 0)}" stroke-width="${r - rin}"><title>${esc(data[0].model)}: ${fmtMoney(data[0].cost)} (100%)</title></circle>`;
  } else {
    data.forEach((m, i) => {
      const a2 = ang + (m.cost / total) * 2 * Math.PI;
      segs += `<path d="${_arc(cx, cy, r, rin, ang, a2)}" fill="${colorOf(m, i)}" class="pie-seg"><title>${esc(m.model)}: ${fmtMoney(m.cost)} (${(m.cost / total * 100).toFixed(1)}%)</title></path>`;
      ang = a2;
    });
  }
  pieEl.innerHTML = `<svg viewBox="0 0 220 220" class="pie-svg" role="img">${segs}
     <text x="110" y="105" text-anchor="middle" class="pie-total-l">Разом</text>
     <text x="110" y="128" text-anchor="middle" class="pie-total-v">${fmtMoney(total)}</text></svg>`;
  listEl.innerHTML = data.map((m, i) =>
    `<div class="mm-row"><span class="mm-sw" style="background:${colorOf(m, i)}"></span>
       <span class="mm-name">${esc(m.model)}</span>
       <span class="mm-val">${fmtMoney(m.cost)}</span>
       <span class="mm-pct muted">${(m.cost / total * 100).toFixed(1)}%</span></div>`).join("");
}

// ---------- Старт ----------
loadSession();
if (state.token && state.user) {
  // Перевіряємо валідність токена; якщо протух — на логін.
  api("/auth/me").then(u => { state.user = u; showApp(); }).catch(() => logout());
} else {
  showLogin();
}
