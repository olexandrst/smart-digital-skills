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

function showLogin() {
  $("#login-screen").classList.remove("hidden");
  $("#app").classList.add("hidden");
}

async function showApp() {
  $("#login-screen").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#user-name").textContent = state.user.full_name || state.user.username;
  $("#user-roles").textContent = state.user.roles.length ? `[${state.user.roles.join(", ")}]` : "[member]";
  buildNav();
}

// ---------- Навігація (за ролями) ----------
const TABS = [
  { id: "skills", label: "Мої скіли", view: viewMySkills },
  { id: "catalog", label: "Каталог", view: viewCatalog },
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
    const b = el(`<button>${tab.label}</button>`);
    b.dataset.tab = tab.id;
    b.addEventListener("click", () => openTab(tab.id));
    nav.appendChild(b);
  });
  openTab(visibleTabs()[0].id);
}

async function openTab(id) {
  document.querySelectorAll("#nav button").forEach(b =>
    b.classList.toggle("active", b.dataset.tab === id));
  const tab = TABS.find(t => t.id === id);
  $("#view").innerHTML = `<p class="muted">Завантаження…</p>`;
  try { await tab.view(); }
  catch (err) { $("#view").innerHTML = `<div class="card"><p class="error">${esc(err.message)}</p></div>`; }
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
      log.scrollTop = log.scrollHeight;
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

// ---------- Управління скілами ----------
async function viewManageSkills() {
  const [skills, models] = await Promise.all([api("/skills"), api("/models")]);
  const view = $("#view");
  const modelOpts = models.map(m => `<option value="${m.id}">${esc(m.name)} (${m.model_type})</option>`).join("");
  view.innerHTML = `
    <div class="card">
      <h2>Новий скіл</h2>
      <div class="field"><label>Назва</label><input id="sk-name"></div>
      <div class="field"><label>Опис</label><input id="sk-desc"></div>
      <div class="field"><label>Модель</label><select id="sk-model">${modelOpts}</select></div>
      <div class="field"><label>Prompt-шаблон (плейсхолдери {text})</label><textarea id="sk-prompt">{text}</textarea></div>
      <div class="field"><label>Вхідні параметри (по одному в рядку: ім'я|обов'язковий 1/0)</label><textarea id="sk-inputs">text|1</textarea></div>
      <button id="sk-create">Створити</button>
    </div>
    <div class="card"><h2>Усі скіли</h2><table><thead><tr><th>Назва</th><th>Версія</th><th>Статус</th><th>Активацій</th><th>Дії</th></tr></thead><tbody id="sk-body"></tbody></table></div>`;

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
    const tr = el(`<tr>
      <td>${esc(s.name)}</td><td>${esc(s.version)}</td>
      <td><span class="badge ${s.status}">${s.status}</span></td>
      <td>${s.activations_count}</td>
      <td class="actions"><button class="small" data-id="${s.id}" data-next="${next[s.status]}">→ ${next[s.status]}</button></td>
    </tr>`);
    tr.querySelector("button").addEventListener("click", async (e) => {
      try { await api(`/skills/${s.id}/status`, { method: "POST", body: { status: e.target.dataset.next } });
        toast("Статус оновлено"); openTab("manage-skills"); }
      catch (err) { toast(err.message, "err"); }
    });
    body.appendChild(tr);
  });
}

// ---------- Моделі (Admin) ----------
async function viewModels() {
  const models = await api("/models");
  const view = $("#view");
  view.innerHTML = `
    <div class="card">
      <h2>Підключити модель</h2>
      <div class="row">
        <input id="m-name" placeholder="Назва">
        <select id="m-type"><option value="llm">llm</option><option value="cv">cv</option></select>
        <input id="m-dep" placeholder="deployment_name">
        <button id="m-add">Додати</button>
      </div>
    </div>
    <div class="card"><h2>Реєстр моделей</h2><table><thead><tr><th>Назва</th><th>Тип</th><th>Деплоймент</th><th>Активна</th></tr></thead><tbody id="m-body"></tbody></table></div>`;
  $("#m-add").addEventListener("click", async () => {
    try {
      await api("/models", { method: "POST", body: {
        name: $("#m-name").value, model_type: $("#m-type").value, deployment_name: $("#m-dep").value }});
      toast("Модель додано"); openTab("models");
    } catch (err) { toast(err.message, "err"); }
  });
  const body = $("#m-body");
  models.forEach(m => body.appendChild(el(
    `<tr><td>${esc(m.name)}</td><td>${m.model_type}</td><td>${esc(m.deployment_name)}</td><td>${m.is_active ? "✓" : "—"}</td></tr>`)));
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
  const users = await api("/users");
  const view = $("#view");
  view.innerHTML = `
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
    <div class="card"><h2>Користувачі</h2><table><thead><tr><th>ID</th><th>Логін</th><th>Ім'я</th><th>Ролі</th><th>Активний</th><th>Дії</th></tr></thead><tbody id="u-body"></tbody></table></div>`;
  $("#u-add").addEventListener("click", async () => {
    const roles = $("#u-role").value ? [$("#u-role").value] : [];
    try { await api("/users", { method: "POST", body: {
      username: $("#u-username").value, full_name: $("#u-name").value, password: $("#u-pass").value, roles }});
      toast("Користувача створено"); openTab("users"); }
    catch (err) { toast(err.message, "err"); }
  });
  const body = $("#u-body");
  users.forEach(u => {
    const tr = el(`<tr>
      <td>${u.id}</td><td>${esc(u.username)}</td><td>${esc(u.full_name || "")}</td>
      <td>${u.roles.join(", ") || "member"}</td><td>${u.is_active ? "✓" : "—"}</td>
      <td class="actions"><button class="small" data-act="reset" data-id="${u.id}">Скинути пароль</button>
      <button class="small ghost" data-act="toggle" data-id="${u.id}" data-active="${u.is_active}">${u.is_active ? "Деактивувати" : "Активувати"}</button></td>
    </tr>`);
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
    const g = await api("/usage/global");
    const rows = g.by_user.map(u => `<tr><td>${esc(u.username)}</td><td>${u.total_tokens}</td><td>${u.requests}</td></tr>`).join("");
    html += `<div class="card"><h2>Глобальне споживання (Admin)</h2>
      <div class="stat-grid"><div class="stat"><div class="num">${g.total_tokens}</div><div class="label">Усього токенів</div></div>
      <div class="stat"><div class="num">${g.requests}</div><div class="label">Запитів</div></div></div>
      <table style="margin-top:16px"><thead><tr><th>Користувач</th><th>Токенів</th><th>Запитів</th></tr></thead><tbody>${rows}</tbody></table></div>`;
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
