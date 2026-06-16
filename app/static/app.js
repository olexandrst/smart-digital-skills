// Smart-ProfiHub — мінімальний SPA-клієнт поверх REST API.

const API = "/api";
let state = { token: null, refresh: null, user: null };

// ---------- HTTP-хелпер ----------
async function api(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && state.token) headers["Authorization"] = `Bearer ${state.token}`;
  const res = await fetch(API + path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) {
    throw new Error((data && data.message) || `Помилка ${res.status}`);
  }
  return data;
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

// ---------- Вендор моделі → колір ----------
const VENDOR_LABELS = {
  openai: "OpenAI", azure: "Azure OpenAI", gemini: "Gemini",
  claude: "Claude", default: "Модель",
};
function vendorOf(model) {
  const p = (model.provider || model.model_provider || "").toLowerCase();
  const nm = ((model.name || model.deployment_name || model.model_name || "") + "").toLowerCase();
  if (nm.includes("claude") || p === "anthropic") return "claude";
  if (p === "azure_ai_foundry" || p === "azure_openai" || p === "azure") return "azure";
  if (p === "openai") return "openai";
  if (p === "gemini" || p === "google") return "gemini";
  return "default";
}

// ---------- Модальне вікно вибору моделі ----------
async function openModelModal() {
  const overlay = $("#model-modal");
  const tiles = $("#model-tiles");
  tiles.innerHTML = `<p class="muted">Завантаження…</p>`;
  overlay.classList.remove("hidden");
  try {
    const models = (await api("/models?active=1")).filter(m => m.model_type === "llm");
    if (!models.length) {
      tiles.innerHTML = `<p class="muted">Немає активних LLM-моделей. Зверніться до адміністратора.</p>`;
      return;
    }
    tiles.innerHTML = "";
    models.forEach(m => {
      const v = vendorOf(m);
      const tile = el(`<button class="model-tile vendor-${v}">
        <span class="tile-vendor">${esc(VENDOR_LABELS[v])}</span>
        <span class="tile-name">${esc(m.name)}</span>
        <span class="tile-dep">${esc(m.deployment_name || "")}</span>
      </button>`);
      tile.addEventListener("click", () => createChatWithModel(m.id));
      tiles.appendChild(tile);
    });
  } catch (err) { tiles.innerHTML = `<p class="error">${esc(err.message)}</p>`; }
}
function closeModelModal() { $("#model-modal").classList.add("hidden"); }

async function createChatWithModel(modelId) {
  try {
    const s = await api("/chat/sessions", { method: "POST", body: { model_id: Number(modelId) } });
    closeModelModal();
    chatState.openAfter = s.id;
    if (document.querySelector("#nav .nav-item.active")?.dataset.tab === "chat") {
      await viewChat();
    } else {
      openTab("chat");
    }
  } catch (err) { toast(err.message, "err"); }
}

$("#new-chat-btn").addEventListener("click", openModelModal);
$("#model-modal-close").addEventListener("click", closeModelModal);
$("#model-modal").addEventListener("click", (e) => {
  if (e.target.id === "model-modal") closeModelModal();
});

// Перемикач показу пароля.
const pwToggle = $("#pw-toggle");
if (pwToggle) pwToggle.addEventListener("click", () => {
  const inp = $("#login-password");
  const show = inp.type === "password";
  inp.type = show ? "text" : "password";
  pwToggle.classList.toggle("active", show);
});

function showLogin() {
  $("#login-screen").classList.remove("hidden");
  $("#app").classList.add("hidden");
}

async function showApp() {
  $("#login-screen").classList.add("hidden");
  $("#app").classList.remove("hidden");
  const display = state.user.full_name || state.user.username;
  $("#user-name").textContent = display;
  $("#user-roles").textContent = state.user.roles.length ? state.user.roles.join(", ") : "member";
  $("#user-avatar").textContent = (display[0] || "?").toUpperCase();
  buildNav();
  refreshQuota();
}

async function refreshQuota() {
  try {
    const u = await api("/usage/me");
    const q = u.quota || { used: u.total_tokens || 0, limit: 0, percent: 0 };
    const fmt = (n) => (n || 0).toLocaleString("uk-UA");
    $("#quota-used").textContent = fmt(q.used);
    $("#quota-limit").textContent = q.limit ? fmt(q.limit) : "∞";
    const pct = Math.max(0, Math.min(100, q.percent || 0));
    const fill = $("#quota-fill");
    fill.style.width = pct + "%";
    // Колір: зелений → бурштиновий → червоний.
    const color = pct >= 90 ? "var(--danger)" : pct >= 70 ? "#f59e0b" : "var(--ok)";
    fill.style.background = color;
    $("#quota").classList.toggle("quota-full", pct >= 100);
    const resets = q.resets_at ? new Date(q.resets_at).toLocaleString("uk-UA") : "";
    $("#quota").title =
      `Тижнева квота: ${fmt(q.used)} / ${q.limit ? fmt(q.limit) : "∞"} токенів (${pct}%)` +
      (q.custom ? " · персональна" : " · системна") +
      (resets ? `\nСкидання: ${resets}` : "");
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
  { id: "skills", label: "Мої скіли", view: viewMySkills },
  { id: "catalog", label: "Каталог", view: viewCatalog },
  { id: "files", label: "Файли", view: viewFiles },
  { id: "manage-skills", label: "Управління скілами", view: viewManageSkills, roles: ["admin", "skill_manager"] },
  { id: "models", label: "Моделі", view: viewModels, roles: ["admin"] },
  { id: "groups", label: "Групи", view: viewGroups },
  { id: "users", label: "Користувачі", view: viewUsers, roles: ["admin"] },
  { id: "usage", label: "Токени", view: viewUsage },
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
let chatState = { sessionId: null, skills: [], openAfter: null, selectedSkill: null };

function sessionItemHtml(s, activeId) {
  const v = vendorOf(s);
  return `<div class="session-item vendor-${v} ${s.id === activeId ? "active" : ""}" data-sid="${s.id}">
      <span class="session-title">${esc(s.title || "Без назви")}</span>
      <span class="session-meta">${esc(s.model_name || "")} · ${s.total_tokens} тк</span>
      <button class="session-del small ghost" data-del="${s.id}" title="Видалити">×</button>
    </div>`;
}

async function viewChat() {
  const [skills, sessions] = await Promise.all([
    api("/skills/mine"), api("/chat/sessions"),
  ]);
  chatState.skills = skills;
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
      <aside class="skill-ribbon" id="skill-ribbon">${renderSkillRibbon(skills)}</aside>
    </div>`;

  bindSessionListEvents();
  bindRibbon();

  // Відкриваємо новостворений чат, інакше — останній наявний.
  const toOpen = chatState.openAfter || (sessions[0] && sessions[0].id);
  chatState.openAfter = null;
  if (toOpen) openChatSession(toOpen);
}

function renderSkillRibbon(skills) {
  const tiles = [`<button class="ribbon-tile ribbon-catalog" data-catalog="1" title="Перейти у Каталог навичок">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5a2 2 0 0 1 2-2h5v16H6a2 2 0 0 0-2 2V5Zm16 0a2 2 0 0 0-2-2h-5v16h5a2 2 0 0 1 2 2V5Z"/></svg>
      <span>Каталог навичок</span></button>`];
  (skills || []).forEach(s => {
    const pressed = s.id === chatState.selectedSkill ? "pressed" : "";
    tiles.push(`<button class="ribbon-tile ${pressed}" data-skill="${s.id}" title="${esc(s.description || s.name)}">
      <span class="rt-name">${esc(s.name)}</span></button>`);
  });
  if (!skills || !skills.length) {
    tiles.push(`<p class="muted ribbon-empty">Немає активованих скілів. Відкрийте Каталог.</p>`);
  }
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

// Непомітне число токенів збоку під повідомленням.
function renderChatMessage(m) {
  const log = $("#chat-log");
  const n = m.role === "assistant" ? m.completion_tokens : m.prompt_tokens;
  const bubble = el(`<div class="msg ${m.role}">${esc(m.content)}<span class="msg-tok"></span></div>`);
  if (n != null && n !== "") bubble.querySelector(".msg-tok").textContent = n;
  log.appendChild(bubble);
  log.scrollTop = log.scrollHeight;
  return bubble;
}

async function openChatSession(sessionId) {
  chatState.sessionId = sessionId;
  const session = await api(`/chat/sessions/${sessionId}`);
  $("#chat-header").innerHTML =
    `<strong>${esc(session.title || "Чат")}</strong> <span class="muted">· ${esc(session.model_name || "—")}</span>`;
  const log = $("#chat-log");
  log.innerHTML = "";
  session.messages.forEach(renderChatMessage);
  $("#chat-input-box").classList.remove("hidden");
  document.querySelectorAll(".session-item").forEach(i =>
    i.classList.toggle("active", Number(i.dataset.sid) === sessionId));

  const sendBtn = $("#chat-send");
  sendBtn.onclick = async () => {
    const text = $("#chat-text").value.trim();
    if (!text) return;
    const skillId = chatState.selectedSkill || null;
    const userEl = renderChatMessage({ role: "user", content: text });
    $("#chat-text").value = "";
    sendBtn.disabled = true;
    try {
      const res = await api(`/chat/sessions/${sessionId}/messages`, {
        method: "POST", body: { content: text, skill_id: skillId } });
      userEl.querySelector(".msg-tok").textContent = res.usage.prompt_tokens;
      renderChatMessage({ role: "assistant", content: res.content,
        completion_tokens: res.usage.completion_tokens });
      const fl = renderFileLinks(res.files);
      if (fl) { $("#chat-log").appendChild(fl); $("#chat-log").scrollTop = $("#chat-log").scrollHeight; }
      refreshSessionList();
      refreshQuota();
    } catch (err) { toast(err.message, "err"); }
    finally { sendBtn.disabled = false; }
  };
}

// ---------- Мої скіли + запуск ----------
async function viewMySkills() {
  const skills = await api("/skills/mine");
  const view = $("#view");
  if (!skills.length) {
    view.innerHTML = `<div class="card"><h2>Мої скіли</h2><p class="muted">Поки немає активних скілів. Перейдіть у «Каталог», щоб активувати.</p></div>`;
    return;
  }
  view.innerHTML = `<div class="card"><h2>Мої скіли</h2><div class="grid" id="my-grid"></div></div><div id="runner"></div>`;
  const grid = $("#my-grid");
  skills.forEach(s => {
    const c = el(`<div class="skill-card">
      <h3>${esc(s.name)}</h3>
      <p>${esc(s.description)}</p>
      <button class="small" data-id="${s.id}">Запустити</button>
    </div>`);
    c.querySelector("button").addEventListener("click", () => openRunner(s.id));
    grid.appendChild(c);
  });
}

async function openRunner(skillId) {
  const skill = await api(`/skills/${skillId}`);
  const runner = $("#runner");
  const fields = skill.inputs.map(i => `
    <div class="field">
      <label>${esc(i.label || i.name)} ${i.is_required ? "*" : ""}</label>
      <textarea data-name="${esc(i.name)}" placeholder="${esc(i.description || "")}">${esc(i.default_value || "")}</textarea>
    </div>`).join("");
  runner.innerHTML = `<div class="card">
    <h2>Запуск: ${esc(skill.name)}</h2>
    <div id="chat-log" class="chat-log"></div>
    ${fields}
    <button id="run-btn">Виконати</button>
  </div>`;
  let sessionId = null;
  $("#run-btn").addEventListener("click", async () => {
    const inputs = {};
    runner.querySelectorAll("[data-name]").forEach(t => inputs[t.dataset.name] = t.value);
    const log = $("#chat-log");
    log.appendChild(el(`<div class="msg user">${esc(inputs.text || JSON.stringify(inputs))}</div>`));
    $("#run-btn").disabled = true;
    try {
      const res = await api(`/skills/${skillId}/run`, {
        method: "POST", body: { inputs, session_id: sessionId },
      });
      sessionId = res.session_id;
      log.appendChild(el(`<div class="msg assistant">${esc(res.content)}</div>`));
      const fl = renderFileLinks(res.files);
      if (fl) log.appendChild(fl);
      log.scrollTop = log.scrollHeight;
      refreshQuota();
      toast(`Токенів використано: ${res.usage.total_tokens}`);
    } catch (err) { toast(err.message, "err"); }
    finally { $("#run-btn").disabled = false; }
  });
}

// ---------- Каталог (самостійна активація) ----------
async function viewCatalog() {
  const [all, mine] = await Promise.all([api("/skills"), api("/skills/mine")]);
  const published = all.filter(s => s.status === "published");
  const mineIds = new Set(mine.map(s => s.id));
  const view = $("#view");
  view.innerHTML = `<div class="card"><h2>Каталог скілів</h2><div class="grid" id="cat-grid"></div></div>`;
  const grid = $("#cat-grid");
  if (!published.length) grid.innerHTML = `<p class="muted">Немає опублікованих скілів.</p>`;
  published.forEach(s => {
    const active = mineIds.has(s.id);
    const c = el(`<div class="skill-card">
      <h3>${esc(s.name)}</h3>
      <p>${esc(s.description)}</p>
      <span class="muted">Активацій: ${s.activations_count}</span><br><br>
      <button class="small" data-id="${s.id}" ${active ? "disabled" : ""}>${active ? "Активовано" : "Активувати"}</button>
    </div>`);
    if (!active) c.querySelector("button").addEventListener("click", async (e) => {
      try { await api(`/skills/${s.id}/activate`, { method: "POST" }); toast("Скіл активовано"); openTab("catalog"); }
      catch (err) { toast(err.message, "err"); }
    });
    grid.appendChild(c);
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
    const src = f.source === "skill_run" ? "скіл" : "завантажено";
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

// ---------- Управління скілами ----------
async function viewManageSkills() {
  const [skills, models] = await Promise.all([api("/skills"), api("/models")]);
  const view = $("#view");
  const modelOpts = models.map(m => `<option value="${m.id}">${esc(m.name)} (${m.model_type})</option>`).join("");
  view.innerHTML = `
    <div class="card">
      <h2>Завантажити скіл-пакет</h2>
      <p class="muted">Архів <code>.zip</code> або <code>.skill</code> зі <code>skill.md</code>, кодом та файлами/папками. Python-код виконується системою.</p>
      <div class="row">
        <input type="file" id="sk-file" accept=".zip,.skill">
        <button id="sk-upload">Завантажити</button>
      </div>
    </div>
    <div class="card">
      <h2>Новий LLM-скіл</h2>
      <div class="field"><label>Назва</label><input id="sk-name"></div>
      <div class="field"><label>Опис</label><input id="sk-desc"></div>
      <div class="field"><label>Модель</label><select id="sk-model">${modelOpts}</select></div>
      <div class="field"><label>Prompt-шаблон (плейсхолдери {text})</label><textarea id="sk-prompt">{text}</textarea></div>
      <div class="field"><label>Вхідні параметри (по одному в рядку: ім'я|обов'язковий 1/0)</label><textarea id="sk-inputs">text|1</textarea></div>
      <button id="sk-create">Створити</button>
    </div>
    <div class="card"><h2>Усі скіли</h2><table><thead><tr><th>Назва</th><th>Тип</th><th>Версія</th><th>Статус</th><th>Активацій</th><th>Дії</th></tr></thead><tbody id="sk-body"></tbody></table></div>`;

  $("#sk-upload").addEventListener("click", async () => {
    const file = $("#sk-file").files[0];
    if (!file) { toast("Оберіть файл архіву", "err"); return; }
    const form = new FormData();
    form.append("file", file);
    $("#sk-upload").disabled = true;
    try {
      const res = await fetch(API + "/skills/upload", {
        method: "POST",
        headers: { "Authorization": `Bearer ${state.token}` },
        body: form,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || "Помилка завантаження");
      toast(`Пакет «${data.name}» завантажено (чернетка)`); openTab("manage-skills");
    } catch (err) { toast(err.message, "err"); }
    finally { $("#sk-upload").disabled = false; }
  });

  $("#sk-create").addEventListener("click", async () => {
    const inputs = $("#sk-inputs").value.split("\n").map(l => l.trim()).filter(Boolean).map((l, i) => {
      const [name, req] = l.split("|");
      return { name: name.trim(), is_required: (req || "1").trim() === "1", position: i };
    });
    try {
      await api("/skills", { method: "POST", body: {
        name: $("#sk-name").value, description: $("#sk-desc").value,
        model_id: Number($("#sk-model").value), prompt_template: $("#sk-prompt").value, inputs,
      }});
      toast("Скіл створено"); openTab("manage-skills");
    } catch (err) { toast(err.message, "err"); }
  });

  const body = $("#sk-body");
  skills.forEach(s => {
    const next = { draft: "testing", testing: "published", published: "delisted", delisted: "published" };
    const kind = s.skill_kind === "package"
      ? `<span class="badge" title="${esc(s.package_filename || "")}">📦 пакет</span>`
      : `<span class="badge">💬 LLM</span>`;
    const tr = el(`<tr>
      <td>${esc(s.name)}</td><td>${kind}</td><td>${esc(s.version)}</td>
      <td><span class="badge ${s.status}">${s.status}</span></td>
      <td>${s.activations_count}</td>
      <td class="actions">
        <button class="small" data-act="status" data-next="${next[s.status]}">→ ${next[s.status]}</button>
        ${s.skill_kind === "package" ? `<button class="small ghost" data-act="files">Файли</button>` : ""}
        <button class="small danger" data-act="del">Видалити</button>
      </td>
    </tr>`);
    tr.querySelector("[data-act='status']").addEventListener("click", async (e) => {
      try { await api(`/skills/${s.id}/status`, { method: "POST", body: { status: e.target.dataset.next } });
        toast("Статус оновлено"); openTab("manage-skills"); }
      catch (err) { toast(err.message, "err"); }
    });
    const filesBtn = tr.querySelector("[data-act='files']");
    if (filesBtn) filesBtn.addEventListener("click", async () => {
      try {
        const files = await api(`/skills/${s.id}/files`);
        alert(`Файли пакета «${s.name}»:\n\n` + files.map(f => `${f.name} (${f.size} Б)`).join("\n"));
      } catch (err) { toast(err.message, "err"); }
    });
    tr.querySelector("[data-act='del']").addEventListener("click", async () => {
      if (!confirm(`Видалити скіл «${s.name}»?`)) return;
      try { await api(`/skills/${s.id}`, { method: "DELETE" }); toast("Скіл видалено"); openTab("manage-skills"); }
      catch (err) { toast(err.message, "err"); }
    });
    body.appendChild(tr);
  });
}

// ---------- Моделі (Admin) ----------
const PROVIDER_LABELS = {
  azure_ai_foundry: "Azure OpenAI",
  openai: "OpenAI",
  gemini: "Gemini",
};

async function viewModels() {
  const [models, providers] = await Promise.all([api("/models"), api("/models/providers")]);
  const view = $("#view");
  const provOpts = providers.map(p =>
    `<option value="${p}">${esc(PROVIDER_LABELS[p] || p)}</option>`).join("");
  view.innerHTML = `
    <div class="card">
      <h2>Підключити модель</h2>
      <div class="row">
        <input id="m-name" placeholder="Назва">
        <select id="m-provider">${provOpts}</select>
        <select id="m-type"><option value="llm">llm</option><option value="cv">cv</option></select>
        <input id="m-dep" placeholder="deployment / model name">
        <button id="m-add">Додати</button>
      </div>
      <p class="muted" style="margin:8px 0 0">deployment / model name — ідентифікатор моделі у провайдера
      (напр. <code>gpt-4o</code> для OpenAI/Azure, <code>gemini-1.5-pro</code> для Gemini).</p>
    </div>
    <div class="card"><h2>Реєстр моделей</h2><p class="muted">Лише <strong>активні</strong> моделі доступні користувачам у чаті. <strong>Системна</strong> модель використовується платформою для службових задач (напр. іменування чатів).</p><table><thead><tr><th>Назва</th><th>Провайдер</th><th>Тип</th><th>Деплоймент / модель</th><th>Статус</th><th>Системна</th><th>Дії</th></tr></thead><tbody id="m-body"></tbody></table></div>`;
  $("#m-add").addEventListener("click", async () => {
    try {
      await api("/models", { method: "POST", body: {
        name: $("#m-name").value, provider: $("#m-provider").value,
        model_type: $("#m-type").value, deployment_name: $("#m-dep").value }});
      toast("Модель додано"); openTab("models");
    } catch (err) { toast(err.message, "err"); }
  });
  const body = $("#m-body");
  models.forEach(m => {
    const status = m.is_active
      ? `<span class="badge published">активна</span>`
      : `<span class="badge delisted">неактивна</span>`;
    const sysCell = m.is_system
      ? `<span class="badge published">системна</span>`
      : (m.model_type === "llm"
          ? `<button class="small ghost" data-act="sys">Зробити системною</button>`
          : `<span class="muted">—</span>`);
    const tr = el(`<tr>
      <td>${esc(m.name)}</td>
      <td><span class="badge">${esc(PROVIDER_LABELS[m.provider] || m.provider)}</span></td>
      <td>${m.model_type}</td>
      <td>${esc(m.deployment_name)}</td>
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
        toast(`«${m.name}» — системна модель`); openTab("models"); }
      catch (err) { toast(err.message, "err"); }
    });
    tr.querySelector("[data-act='toggle']").addEventListener("click", async () => {
      try { await api(`/models/${m.id}/activate`, { method: "POST", body: { is_active: !m.is_active } });
        toast("Статус оновлено"); openTab("models"); }
      catch (err) { toast(err.message, "err"); }
    });
    tr.querySelector("[data-act='del']").addEventListener("click", async () => {
      if (!confirm(`Видалити модель «${m.name}»?`)) return;
      try { await api(`/models/${m.id}`, { method: "DELETE" }); toast("Модель видалено"); openTab("models"); }
      catch (err) { toast(err.message, "err"); }
    });
    body.appendChild(tr);
  });
}

// ---------- Групи ----------
async function viewGroups() {
  const groups = await api("/groups");
  const view = $("#view");
  let createCard = "";
  if (hasRole("admin")) {
    createCard = `<div class="card"><h2>Нова група</h2><div class="row">
      <input id="g-name" placeholder="Назва групи"><input id="g-desc" placeholder="Опис">
      <button id="g-add">Створити</button></div></div>`;
  }
  view.innerHTML = createCard + `<div class="card"><h2>Групи</h2><div id="g-list"></div></div>`;
  if (hasRole("admin")) $("#g-add").addEventListener("click", async () => {
    try { await api("/groups", { method: "POST", body: { name: $("#g-name").value, description: $("#g-desc").value }});
      toast("Групу створено"); openTab("groups"); }
    catch (err) { toast(err.message, "err"); }
  });
  const list = $("#g-list");
  if (!groups.length) list.innerHTML = `<p class="muted">Немає груп.</p>`;
  for (const g of groups) {
    const wrap = el(`<div class="card" style="background:var(--panel-2)"><h3>${esc(g.name)}</h3><p class="muted">${esc(g.description || "")}</p><div id="g-detail-${g.id}"></div></div>`);
    list.appendChild(wrap);
    renderGroupDetail(g.id);
  }
}

async function renderGroupDetail(groupId) {
  const [detail, gskills] = await Promise.all([api(`/groups/${groupId}`), api(`/groups/${groupId}/skills`)]);
  const container = $(`#g-detail-${groupId}`);
  const members = detail.members.map(m =>
    `<tr><td>${esc(m.full_name || m.username)}</td><td>${m.role}</td>
     <td class="actions"><button class="small danger" data-act="rm" data-uid="${m.user_id}">×</button></td></tr>`).join("");
  const skillsHtml = gskills.map(s => `<span class="badge">${esc(s.name)}</span>`).join(" ") || `<span class="muted">немає</span>`;

  container.innerHTML = `
    <table><thead><tr><th>Учасник</th><th>Роль</th><th></th></tr></thead><tbody>${members}</tbody></table>
    <p><strong>Скіли групи:</strong> ${skillsHtml}</p>
    <div class="row">
      <input placeholder="user_id" data-f="uid">
      <select data-f="role"><option value="member">member</option><option value="manager">manager</option></select>
      <button data-act="add-member">Додати учасника</button>
    </div>
    <div class="row" style="margin-top:8px">
      <input placeholder="skill_id" data-f="sid">
      <button data-act="add-skill">Призначити скіл</button>
    </div>`;

  const getF = (f) => container.querySelector(`[data-f="${f}"]`).value;
  container.querySelectorAll("[data-act='rm']").forEach(b => b.addEventListener("click", async () => {
    try { await api(`/groups/${groupId}/members/${b.dataset.uid}`, { method: "DELETE" }); toast("Видалено"); renderGroupDetail(groupId); }
    catch (err) { toast(err.message, "err"); }
  }));
  container.querySelector("[data-act='add-member']").addEventListener("click", async () => {
    try { await api(`/groups/${groupId}/members`, { method: "POST", body: { user_id: Number(getF("uid")), role: getF("role") }});
      toast("Учасника додано"); renderGroupDetail(groupId); }
    catch (err) { toast(err.message, "err"); }
  });
  container.querySelector("[data-act='add-skill']").addEventListener("click", async () => {
    try { await api(`/groups/${groupId}/skills`, { method: "POST", body: { skill_id: Number(getF("sid")) }});
      toast("Скіл призначено"); renderGroupDetail(groupId); }
    catch (err) { toast(err.message, "err"); }
  });
}

// ---------- Користувачі (Admin) ----------
async function viewUsers() {
  const [users, def] = await Promise.all([api("/users"), api("/usage/default-limit")]);
  const view = $("#view");
  view.innerHTML = `
    <div class="card">
      <h2>Системна тижнева квота</h2>
      <p class="muted">Діє для всіх користувачів без персональної квоти. Скидання — щопонеділка 00:05 UTC.</p>
      <div class="row">
        <input id="sys-limit" type="number" min="0" value="${def.limit}">
        <button id="sys-save">Зберегти</button>
      </div>
    </div>
    <div class="card">
      <h2>Новий користувач</h2>
      <div class="row">
        <input id="u-username" placeholder="Логін">
        <input id="u-name" placeholder="Повне ім'я">
        <input id="u-pass" placeholder="Пароль" type="text">
        <select id="u-role"><option value="">member</option><option value="skill_manager">skill_manager</option><option value="admin">admin</option></select>
        <button id="u-add">Створити</button>
      </div>
    </div>
    <div class="card"><h2>Користувачі</h2><table><thead><tr><th>ID</th><th>Логін</th><th>Ім'я</th><th>Ролі</th><th>Тижнева квота (використано / ліміт)</th><th>Активний</th><th>Дії</th></tr></thead><tbody id="u-body"></tbody></table></div>`;

  $("#sys-save").addEventListener("click", async () => {
    try { await api("/usage/default-limit", { method: "POST", body: { limit: Number($("#sys-limit").value) }});
      toast("Системну квоту оновлено"); openTab("users"); }
    catch (err) { toast(err.message, "err"); }
  });
  $("#u-add").addEventListener("click", async () => {
    const roles = $("#u-role").value ? [$("#u-role").value] : [];
    try { await api("/users", { method: "POST", body: {
      username: $("#u-username").value, full_name: $("#u-name").value, password: $("#u-pass").value, roles }});
      toast("Користувача створено"); openTab("users"); }
    catch (err) { toast(err.message, "err"); }
  });
  const body = $("#u-body");
  users.forEach(u => {
    const used = (u.token_used || 0).toLocaleString("uk-UA");
    const eff = (u.effective_limit || 0).toLocaleString("uk-UA");
    const custom = u.custom_limit != null
      ? `<span class="badge published" title="Персональна квота">власна</span>`
      : `<span class="badge" title="Системна квота">системна</span>`;
    const delBtn = u.custom_limit != null
      ? `<button class="small danger" data-act="limit-del">✕ квота</button>` : "";
    const tr = el(`<tr>
      <td>${u.id}</td><td>${esc(u.username)}</td><td>${esc(u.full_name || "")}</td>
      <td>${u.roles.join(", ") || "member"}</td>
      <td><span class="muted">${used} /</span> ${eff} ${custom}
        <button class="small ghost" data-act="limit">${u.custom_limit != null ? "Змінити" : "Задати"}</button>
        ${delBtn}</td>
      <td>${u.is_active ? "✓" : "—"}</td>
      <td class="actions"><button class="small" data-act="reset" data-id="${u.id}">Скинути пароль</button>
      <button class="small ghost" data-act="toggle" data-id="${u.id}" data-active="${u.is_active}">${u.is_active ? "Деактивувати" : "Активувати"}</button></td>
    </tr>`);
    tr.querySelector("[data-act='limit']").addEventListener("click", async () => {
      const v = prompt(`Персональна тижнева квота для «${u.username}» (токенів):`,
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
    tr.querySelector("[data-act='toggle']").addEventListener("click", async () => {
      try { await api(`/users/${u.id}`, { method: "PATCH", body: { is_active: !u.is_active }}); toast("Оновлено"); openTab("users"); }
      catch (err) { toast(err.message, "err"); }
    });
    body.appendChild(tr);
  });
}

// ---------- Токени ----------
async function viewUsage() {
  const view = $("#view");
  const mine = await api("/usage/me");
  let html = `<div class="card"><h2>Мої токени</h2><div class="stat-grid">
    <div class="stat"><div class="num">${mine.total_tokens}</div><div class="label">Усього токенів</div></div>
    <div class="stat"><div class="num">${mine.requests}</div><div class="label">Запитів</div></div>
    <div class="stat"><div class="num">${mine.prompt_tokens}</div><div class="label">Prompt</div></div>
    <div class="stat"><div class="num">${mine.completion_tokens}</div><div class="label">Completion</div></div>
  </div></div>`;

  if (hasRole("admin")) {
    const [g, sys] = await Promise.all([api("/usage/global"), api("/usage/system")]);
    const rows = g.by_user.map(u => `<tr><td>${esc(u.username)}</td><td>${u.total_tokens}</td><td>${u.requests}</td></tr>`).join("");
    html += `<div class="card"><h2>Глобальне споживання користувачів (Admin)</h2>
      <div class="stat-grid"><div class="stat"><div class="num">${g.total_tokens}</div><div class="label">Усього токенів</div></div>
      <div class="stat"><div class="num">${g.requests}</div><div class="label">Запитів</div></div></div>
      <table style="margin-top:16px"><thead><tr><th>Користувач</th><th>Токенів</th><th>Запитів</th></tr></thead><tbody>${rows}</tbody></table></div>`;

    const sysRows = (sys.breakdown || []).map(b =>
      `<tr><td>${esc(b.model)}</td><td><span class="badge">${esc(b.feature)}</span></td><td>${b.prompt_tokens}</td><td>${b.completion_tokens}</td><td>${b.total_tokens}</td><td>${b.requests}</td></tr>`).join("")
      || `<tr><td colspan="6" class="muted">Системного використання ще не було.</td></tr>`;
    html += `<div class="card"><h2>Системне використання (Admin)</h2>
      <p class="muted">Окремий облік токенів, які витрачає платформа (поза квотами користувачів).</p>
      <div class="stat-grid">
        <div class="stat"><div class="num">${sys.total_tokens}</div><div class="label">Усього (система)</div></div>
        <div class="stat"><div class="num">${sys.prompt_tokens}</div><div class="label">Вхідні</div></div>
        <div class="stat"><div class="num">${sys.completion_tokens}</div><div class="label">Вихідні</div></div>
        <div class="stat"><div class="num">${sys.requests}</div><div class="label">Запитів</div></div>
      </div>
      <table style="margin-top:16px"><thead><tr><th>Модель</th><th>Фіча</th><th>Вхідні</th><th>Вихідні</th><th>Усього</th><th>Запитів</th></tr></thead><tbody>${sysRows}</tbody></table></div>`;
  }
  view.innerHTML = html;
}

// ---------- Старт ----------
loadSession();
if (state.token && state.user) {
  // Перевіряємо валідність токена; якщо протух — на логін.
  api("/auth/me").then(u => { state.user = u; showApp(); }).catch(() => logout());
} else {
  showLogin();
}
