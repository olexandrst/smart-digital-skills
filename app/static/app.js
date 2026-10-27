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

// Таблиці керування ширші за картку лише на вузькому екрані. Клас .is-scrollable
// вмикає роздільник перед закріпленою колонкою «Дії»: коли таблиця вміщається,
// вертикальна лінія була б єдиною в усьому інтерфейсі й виглядала б випадковою.
// Один спостерігач на документ — таблиці перемальовуються часто, тож вішати
// слухач на кожну було б витоком.
const _tableFit = new ResizeObserver(entries => {
  for (const e of entries) {
    e.target.classList.toggle("is-scrollable",
                              e.target.scrollWidth > e.target.clientWidth + 1);
  }
});
function watchTableFit(table) {
  if (!table) return;
  table.classList.toggle("is-scrollable", table.scrollWidth > table.clientWidth + 1);
  _tableFit.observe(table);
}
function esc(s) { return (s == null ? "" : String(s)).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function fmtSize(n) { if (n < 1024) return `${n} Б`; if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} КБ`; return `${(n / 1024 / 1024).toFixed(1)} МБ`; }

// ---------- Зображення в тексті матеріалу (фідбек, п. 3.2) ----------
// У тексті лежить постійне посилання на файл хабу, а <img> потребує
// підписаного (тег не передає JWT). Підписи приходять зі списку файлів
// картки або з відповіді на завантаження; hydrateImages підставляє їх.
const FILE_SIGS = new Map();

function registerFileSigs(list) {
  (list || []).forEach(f => {
    const url = f.preview_url || f.url;
    if (f && f.id && url) FILE_SIGS.set(Number(f.id), url);
  });
}

function hydrateImages(root) {
  if (!root) return;
  root.querySelectorAll("img[data-file]").forEach(img => {
    const url = FILE_SIGS.get(Number(img.dataset.file));
    if (url) { if (img.getAttribute("src") !== url) img.src = url; img.classList.remove("md-img-missing"); }
    else if (!img.getAttribute("src")) img.classList.add("md-img-missing");
  });
}

async function uploadInlineImage(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`${API}/catalog/images`, {
    method: "POST", headers: { Authorization: `Bearer ${state.token}` }, body: fd });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.message || "Не вдалося завантажити зображення");
  registerFileSigs([data]);
  return data;
}

// ---------- Санітизація HTML (Markdown + безпечний HTML у картках) ----------
const _HTML_ALLOWED = new Set(["a","abbr","b","blockquote","br","caption","code","col",
  "colgroup","del","div","em","h1","h2","h3","h4","h5","h6","hr","i","img","ins","kbd",
  "li","mark","ol","p","pre","s","samp","small","span","strong","sub","sup","table",
  "tbody","td","tfoot","th","thead","tr","u","ul"]);
const _HTML_DANGEROUS = new Set(["script","style","iframe","object","embed","form","input",
  "button","textarea","select","option","link","meta","base","svg","math","noscript"]);
const _HTML_ATTR = { a: ["href","title","target","rel"],
  img: ["src","alt","title","width","height","data-file"], "*": ["class"] };
function _safeUrl(name, val) {
  const v = (val || "").trim().toLowerCase();
  if (name === "src")
    return v.startsWith("https://") || v.startsWith("http://") ||
           v.startsWith("data:image/") || /^\/api\/catalog\/files\/\d+\/preview(\?|$)/.test(v);
  return v.startsWith("https://") || v.startsWith("http://") ||
         v.startsWith("mailto:") || v.startsWith("#");
}
function sanitizeHtml(html) {
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  const clean = (node) => {
    [...node.childNodes].forEach(clean);            // діти спершу (post-order)
    if (node.nodeType === 8) { node.remove(); return; }   // коментарі
    if (node.nodeType !== 1) return;                       // текст
    const tag = node.tagName.toLowerCase();
    if (_HTML_DANGEROUS.has(tag)) { node.remove(); return; }
    if (!_HTML_ALLOWED.has(tag)) {                         // невідомий тег — розгортаємо
      const frag = document.createDocumentFragment();
      while (node.firstChild) frag.appendChild(node.firstChild);
      node.replaceWith(frag);
      return;
    }
    [...node.attributes].forEach(a => {
      const nm = a.name.toLowerCase();
      const ok = !nm.startsWith("on") &&
        ((_HTML_ATTR[tag] || []).includes(nm) || (_HTML_ATTR["*"] || []).includes(nm));
      if (!ok) { node.removeAttribute(a.name); return; }
      if ((nm === "href" || nm === "src") && !_safeUrl(nm, a.value)) node.removeAttribute(a.name);
      if (nm === "data-file" && !/^\d+$/.test(a.value)) node.removeAttribute(a.name);
    });
    if (tag === "a") { node.setAttribute("target", "_blank"); node.setAttribute("rel", "noopener noreferrer"); }
  };
  clean(tpl.content);
  return tpl.innerHTML;
}

// ---------- Markdown (+ безпечний HTML) → HTML ----------
// opts.html === false — текст без HTML (промпти): «<документ>» лишається
// текстом, а не тегом, який санітайзер викинув би.
function mdInline(s, opts = {}) {
  // Екрановані символи (\*, \#, \`…) — літерали: їх ставить редактор, щоб
  // зірочка в тексті не стала курсивом.
  const escs = [];
  s = s.replace(/\\([\\`*_{}\[\]()#+\-.!|>~<])/g, (m, c) => { escs.push(c); return `\u0001${escs.length - 1}\u0001`; });
  // Інлайн-код виймаємо у плейсхолдери, щоб розмітка його не чіпала.
  const codes = [];
  s = s.replace(/`([^`]+)`/g, (m, c) => { codes.push(esc(c)); return `\u0000${codes.length - 1}\u0000`; });
  // Екрануємо «голі» & та < (сутності й HTML-теги лишаємо — їх санітизує sanitizeHtml).
  s = s.replace(/&(?![a-zA-Z]+;|#\d+;|#x[0-9a-fA-F]+;)/g, "&amp;")
       .replace(opts.html === false ? /</g : /<(?![a-zA-Z/!])/g, "&lt;");
  // Картинка: своя (файл хабу — показується через підписане посилання, див.
  // hydrateImages) або зовнішня за https. Готовий тег ховаємо в плейсхолдер,
  // щоб «__» чи «*» у посиланні не стали жирним або курсивом.
  const imgs = [];
  s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, alt, u) => {
    const a = alt.replace(/"/g, "&quot;");
    const own = u.match(/^\/api\/catalog\/files\/(\d+)\/preview$/);
    let tag = null;
    if (own) {
      const url = FILE_SIGS.get(Number(own[1]));
      tag = `<img class="md-img" data-file="${own[1]}"${url ? ` src="${url}"` : ""} alt="${a}">`;
    } else if (/^https?:\/\//i.test(u)) {
      tag = `<img class="md-img" src="${u}" alt="${a}">`;
    }
    if (!tag) return alt;
    imgs.push(tag);
    return `\u0002${imgs.length - 1}\u0002`;
  });
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
    (m, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`);
  s = s.replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>");
  // Усередині жирного може бути курсив: «**текст *курсив* текст**».
  s = s.replace(/\*\*(?=\S)((?:[^*]|\*(?!\*))+?)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  // Автопосилання «голих» URL (не всередині href="…" чи текст-посилання).
  s = s.replace(/(^|[^"'>=\](])((?:https?:\/\/)[^\s<>()"']+)/g,
    (m, pre, u) => `${pre}<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
  s = s.replace(/\u0002(\d+)\u0002/g, (m, idx) => imgs[+idx]);
  s = s.replace(/\u0000(\d+)\u0000/g, (m, idx) => `<code>${codes[+idx]}</code>`);
  s = s.replace(/\u0001(\d+)\u0001/g, (m, idx) => esc(escs[+idx]));
  return s;
}
function _mdRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
}
function _mdTableSep(l) { return l && /\|/.test(l) && /^[\s:|-]+$/.test(l.trim()) && /-/.test(l); }
function _mdBlockStart(l) { return /^\s*(#{1,6}\s|[-*+]\s|\d+\.\s|>|```)/.test(l) || /^\s*---+\s*$/.test(l); }
function renderMarkdown(md, opts = {}) {
  const inl = (t) => mdInline(t, opts);
  const lines = String(md || "").split(/\r?\n/);
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*```(\w*)\s*$/);
    if (fence) {
      const buf = []; i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++;
      out.push(`<pre class="md-pre"><code>${esc(buf.join("\n"))}</code></pre>`);
      continue;
    }
    if (/\|/.test(line) && _mdTableSep(lines[i + 1])) {
      const header = _mdRow(line); i += 2;
      const rows = [];
      while (i < lines.length && /\|/.test(lines[i]) && lines[i].trim() !== "") { rows.push(_mdRow(lines[i])); i++; }
      let t = '<table class="md-table"><thead><tr>' + header.map(h => `<th>${inl(h)}</th>`).join("") + "</tr></thead><tbody>";
      rows.forEach(r => { t += "<tr>" + r.map(c => `<td>${inl(c)}</td>`).join("") + "</tr>"; });
      out.push(t + "</tbody></table>");
      continue;
    }
    if (/^\s*>/.test(line)) {
      const quote = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) { quote.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      out.push(`<blockquote class="md-quote">${quote.map(inl).join("<br>")}</blockquote>`);
      continue;
    }
    const h = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (h) { const lvl = Math.min(h[1].length + 1, 4); out.push(`<h${lvl} class="md-h">${inl(h[2])}</h${lvl}>`); i++; continue; }
    if (/^\s*---+\s*$/.test(line)) { out.push("<hr class='md-hr'>"); i++; continue; }
    if (/^\s*[-*+]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*+]\s+/, "")); i++; }
      out.push('<ul class="md-ul">' + items.map(it => `<li>${inl(it)}</li>`).join("") + "</ul>");
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*\d+\.\s+/, "")); i++; }
      out.push('<ol class="md-ol">' + items.map(it => `<li>${inl(it)}</li>`).join("") + "</ol>");
      continue;
    }
    if (line.trim() === "") { i++; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() !== "" && !_mdBlockStart(lines[i]) &&
           !(/\|/.test(lines[i]) && _mdTableSep(lines[i + 1]))) {
      para.push(lines[i]); i++;
    }
    if (para.length) out.push(`<p class="md-p">${para.map(inl).join("<br>")}</p>`);
    else i++;
  }
  return sanitizeHtml(out.join(""));
}

// Завантаження файлу з JWT. Звичайне посилання <a href> заголовка
// Authorization не передає — сервер відповів би 401. Назву файлу беремо з
// Content-Disposition, щоб вона збігалася з тією, яку віддав сервер.
async function downloadAuthed(url, fallbackName) {
  const res = await fetch(url, { headers: { "Authorization": `Bearer ${state.token}` } });
  if (!res.ok) {
    let msg = "Не вдалося завантажити файл";
    try { msg = (await res.json()).message || msg; } catch (e) { /* не JSON */ }
    throw new Error(msg);
  }
  const cd = res.headers.get("Content-Disposition") || "";
  const star = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  const plain = /filename="?([^";]+)"?/i.exec(cd);
  const name = star ? decodeURIComponent(star[1]) : plain ? plain[1] : (fallbackName || "file");
  const href = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = href; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  // Відкликаємо не одразу: Safari й старий Firefox інакше обривають завантаження.
  setTimeout(() => URL.revokeObjectURL(href), 1000);
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
$("#logout-btn").addEventListener("click", () => logoutWithSurvey());

// ---------- Тема (Темна glass / Світла / Material), лише всередині застосунку ----------
const T_MOON = '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/>';
const T_SUN = '<path d="M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10Zm0-13v2m0 16v2M4 12H2m20 0h-2M5.6 5.6 4.2 4.2m15.6 1.4 1.4-1.4M5.6 18.4l-1.4 1.4m15.6-1.4 1.4 1.4" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>';
const THEMES = [
  { id: "light", label: "Світла", icon: T_SUN },
  { id: "dark", label: "Темна", icon: T_MOON },
];
function themePref() {
  const t = localStorage.getItem("theme");
  // Раніше було 3 теми — "material" і "light" зводимо до світлої.
  return t === "dark" ? "dark" : "light";
}
function applyTheme() {
  const t = themePref();
  document.body.classList.toggle("theme-dark", t === "dark");
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

// Які способи входу доступні — питаємо сервер, а не вгадуємо.
async function paintAuthOptions() {
  let cfg;
  try { cfg = await api("/auth/config", { auth: false }); }
  catch (e) { return; }
  const entra = $("#entra-btn"), or = $("#login-or"), form = $("#login-form");
  if (cfg.entra_enabled) {
    entra.classList.remove("hidden");
    if (cfg.password_enabled) or.classList.remove("hidden");
  }
  if (!cfg.password_enabled) {
    // Парольний вхід вимкнено — поля мають зникнути, а не стояти неробочими.
    form.querySelectorAll(".input-wrap, .btn-primary").forEach(el => el.classList.add("hidden"));
  }
  paintLoginHelp(cfg);
}

// «Як увійти до «AI Knowledge Hub»» — кроки саме для тих способів входу,
// що ввімкнені. Без конфігурації лишається типовий текст із розмітки.
function paintLoginHelp(cfg) {
  const box = $("#login-help-steps");
  if (!box) return;
  const steps = [];
  if (cfg.entra_enabled) {
    steps.push("Натисніть «Увійти через корпоративний акаунт» і увійдіть робочим обліковим "
      + "записом Microsoft, тим самим, що для Outlook і Teams.");
  }
  if (cfg.password_enabled) {
    steps.push((cfg.entra_enabled ? "Якщо адміністратор хабу видав вам окремі логін і пароль, "
      + "введіть їх у поля нижче й натисніть «Увійти»." : "Введіть логін і пароль, які видав "
      + "адміністратор хабу, і натисніть «Увійти».")
      + " Логіном може бути робоча пошта або ім'я користувача; великі й малі літери не важливі.");
  }
  steps.push("Після входу відкриється сторінка «Для вас» з рекомендаціями.");
  box.innerHTML = steps.map(t => `<li>${esc(t)}</li>`).join("");
}

// Повернення з каталогу: токени приходять фрагментом URL, щоб не потрапити
// в логи сервера й у заголовок Referer.
function consumeAuthFragment() {
  if (!location.hash.includes("access_token=")) return false;
  const params = new URLSearchParams(location.hash.slice(1));
  state.token = params.get("access_token");
  state.refresh = params.get("refresh_token");
  history.replaceState(null, "", location.pathname);
  saveSession();
  return true;
}

function showLogin() {
  paintAuthOptions();
  document.body.classList.remove("theme-dark");  // логін має власний вигляд
  $("#login-screen").classList.remove("hidden");
  $("#app").classList.add("hidden");
  placeAuthBadges();
}

// Бейджі на екрані входу стають у смугу між текстом ліворуч і фото праворуч.
// Фото — фон із cover і прив'язкою праворуч, тож його масштаб і зсув рахуємо
// так само, як браузер. Бракує місця — спершу компактний вигляд без підписів,
// а якщо й так тісно, бейджі ховаємо.
const HERO_W = 1672, HERO_H = 941;
const HERO_PERSON_X = 1130;   // де на фото починається волосся
const HERO_BADGES_X = 636, HERO_BADGES_Y = 251;   // місце бейджів у макеті
function placeAuthBadges() {
  const box = $("#auth-badges"), auth = $("#login-screen");
  if (!box || auth.classList.contains("hidden")) return;
  const W = auth.clientWidth, H = auth.clientHeight;
  const s = Math.max(W / HERO_W, H / HERO_H);
  const offX = W - HERO_W * s, offY = (H - HERO_H * s) / 2;
  const base = auth.getBoundingClientRect();
  const range = document.createRange();
  let left = 0;
  auth.querySelectorAll(".auth-brand, .auth-headline, .auth-sub, .login-card").forEach(el => {
    range.selectNodeContents(el);
    const r = el.classList.contains("login-card") ? el.getBoundingClientRect() : range.getBoundingClientRect();
    left = Math.max(left, r.right - base.left);
  });
  left += 28;
  const right = HERO_PERSON_X * s + offX - 16;
  box.style.transform = "none";
  box.classList.add("on");
  const fit = (compact) => {
    box.classList.toggle("compact", compact);
    const w = Math.max(...[...box.children].map(li => li.offsetLeft + li.offsetWidth));
    return { w, h: box.offsetHeight, k: Math.min(1, s * 1.05, (right - left) / w) };
  };
  let f = fit(false);
  if (f.k < 0.9) f = fit(true);
  const { w, h, k } = f;
  if (W < 900 || k < 0.8) { box.classList.remove("on"); return; }
  const x = Math.min(Math.max(left, HERO_BADGES_X * s + offX), right - w * k);
  const y = Math.min(Math.max(16, HERO_BADGES_Y * s + offY), H - h * k - 16);
  box.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px) scale(${k.toFixed(3)})`;
}
window.addEventListener("resize", placeAuthBadges);
document.addEventListener("DOMContentLoaded", () => {
  const help = $("#login-help");
  if (help) help.addEventListener("toggle", placeAuthBadges);
  if (document.fonts) document.fonts.ready.then(placeAuthBadges);
});

async function showApp() {
  $("#login-screen").classList.add("hidden");
  $("#app").classList.remove("hidden");
  applyTheme();  // застосовуємо тему користувача (лише в застосунку)
  const display = state.user.full_name || state.user.username;
  $("#user-name").textContent = display;
  $("#user-roles").textContent = roleLabel(state.user);
  $("#user-avatar").textContent = (display[0] || "?").toUpperCase();
  buildNav();
  refreshHubSettings();
  maybeStartTour();   // новому користувачу — знайомство з інтерфейсом
  // Попередній сеанс скінчився закриттям вкладки — питаємо, як він минув.
  // Знайомство з інтерфейсом має перевагу: воно вже позначило surveyShown.
  if (startSession()) setTimeout(maybeAskSurvey, 1500);
}

// ---------- Навігація: режим → бічна панель → екран ----------
//
// Два режими — база знань і консоль керування — перемикаються
// сегментованим контролом угорі (лише в того, кому доступні обидва).
// Бічна панель належить режимові: у базі знань вона тримає каталог
// (розділи й колекції), у керуванні — секції консолі.

// Монохромні гліфи одного стилю: контур 1.7, скруглені кінці.
const GLYPH = {
  hub: '<rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="2"/><rect x="13.5" y="13.5" width="7" height="7" rx="2"/>',
  gear: '<circle cx="12" cy="12" r="2.9"/><path d="M12 3.4l1.5 2 2.5-.5.6 2.5 2.3 1.1-1 2.4 1 2.4-2.3 1.1-.6 2.5-2.5-.5-1.5 2-1.5-2-2.5.5-.6-2.5-2.3-1.1 1-2.4-1-2.4 2.3-1.1.6-2.5 2.5.5 1.5-2Z"/>',
  spark: '<path d="M12 3.4l1.9 5.2 5.2 1.9-5.2 1.9-1.9 5.2-1.9-5.2-5.2-1.9 5.2-1.9L12 3.4Z"/><path d="M18.6 16.4l.7 1.9 1.9.7-1.9.7-.7 1.9-.7-1.9-1.9-.7 1.9-.7.7-1.9Z"/>',
  star: '<path d="M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.6 9.7l5.8-.8L12 3.6Z"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.2V12l3.2 2"/>',
  path: '<circle cx="5.5" cy="6" r="2.2"/><circle cx="18.5" cy="18" r="2.2"/><path d="M7.7 6h5.6a3.2 3.2 0 0 1 0 6.4h-2.6a3.2 3.2 0 0 0 0 6.4h5.6"/>',
  box: '<path d="M3.6 8.4 12 4.2l8.4 4.2-8.4 4.2L3.6 8.4Z"/><path d="M3.6 12 12 16.2 20.4 12M3.6 15.6 12 19.8l8.4-4.2"/>',
  file: '<path d="M3.5 7.2c0-1 .8-1.8 1.8-1.8h3.4l1.8 2.2h8.2c1 0 1.8.8 1.8 1.8v8.4c0 1-.8 1.8-1.8 1.8H5.3c-1 0-1.8-.8-1.8-1.8V7.2Z"/>',
  doc: '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5V8h4"/><path d="M9 12.5h6M9 16h4"/>',
  bulb: '<path d="M9.6 18.2h4.8M10.6 21h2.8M12 3.2a6.2 6.2 0 0 1 3.6 11.2v1.4H8.4v-1.4A6.2 6.2 0 0 1 12 3.2Z"/>',
  chart: '<path d="M4.5 19.5V13m5 6.5V6m5 13.5v-4.5m5 4.5V9.5"/>',
  grow: '<path d="M3.8 17.2 9.4 11.6l4 4 6.8-6.8"/><path d="M15.2 8.8h5v5"/>',
  pulse: '<path d="M3.5 12.5h4l2.5-6 4 12 2.5-6h4"/>',
  person: '<circle cx="12" cy="8" r="3.6"/><path d="M4.8 20.2c0-3.4 3.2-5.8 7.2-5.8s7.2 2.4 7.2 5.8"/>',
  ext: '<path d="M14 4h6v6M20 4l-8.5 8.5M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  plus: '<path d="M12 5.5v13M5.5 12h13"/>',
  list: '<path d="M9 6.5h11M9 12h11M9 17.5h11"/><circle cx="4.9" cy="6.5" r="1"/><circle cx="4.9" cy="12" r="1"/><circle cx="4.9" cy="17.5" r="1"/>',
  pen: '<path d="M4.5 19.5h3.6L18.4 9.2a2.2 2.2 0 0 0-3.1-3.1L5 16.4v3.1Z"/><path d="M13.4 8l3.1 3.1"/>',
  upload: '<path d="M12 16V4.5M7.5 9 12 4.5 16.5 9"/><path d="M4.5 15.5v2.6c0 .8.7 1.4 1.4 1.4h12.2c.8 0 1.4-.6 1.4-1.4v-2.6"/>',
  chat: '<path d="M5 5h14a1.5 1.5 0 0 1 1.5 1.5V15a1.5 1.5 0 0 1-1.5 1.5h-8.5L6 20v-3.5H5A1.5 1.5 0 0 1 3.5 15V6.5A1.5 1.5 0 0 1 5 5Z"/><path d="M8 10.8h.01M12 10.8h.01M16 10.8h.01"/>',
  mail: '<rect x="3.5" y="5.5" width="17" height="13" rx="2"/><path d="m4.5 7 7.5 6 7.5-6"/>',
  help: '<circle cx="12" cy="12" r="8.6"/><path d="M9.6 9.4a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.7"/><circle cx="12" cy="16.9" r=".4"/>',
};
const gsvg = (name, cls = "") =>
  `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true" fill="none"
        stroke="currentColor" stroke-width="1.7" stroke-linecap="round"
        stroke-linejoin="round">${GLYPH[name] || ""}</svg>`;

// Акценти розділів із довідника → відтінки візуальної мови.
const ACCENT_TONE = { red: "red", green: "green", amber: "orange", steel: "teal",
                      ink: "ink", blue: "blue", purple: "purple", pink: "pink" };
function sectionTone(sec) {
  return ACCENT_TONE[sec && sec.accent] || "ink";
}

const MODES = [
  { id: "hub", label: "База знань", glyph: "hub", home: "hub:home" },
  { id: "admin", label: "Керування", glyph: "gear", home: "admin:content",
    show: () => hasConsole() },
];

// Секції консолі керування. `panel` малює вміст у #view, `show` — кому видно
// (фідбек тестування, п. 11): модерацію й аналітику — менеджерам розділів,
// структуру, маршрути й користувачів — адміністратору, ідеї — відповідальним.
const ADMIN_SECTIONS = [
  { group: "Контент", items: [
    { id: "content", label: "Модерація", glyph: "pen", panel: renderContentManage,
      show: () => isModerator() },
    { id: "structure", label: "Структура каталогу", glyph: "doc", panel: renderResourcesManage,
      show: () => isAdmin() },
    { id: "ideas", label: "Ідеї", glyph: "bulb", panel: renderIdeasManage,
      show: () => canManageIdeas() },
    { id: "paths", label: "Маршрути", glyph: "path", panel: renderPathsManage,
      show: () => isAdmin() },
  ]},
  { group: "Аналітика", items: [
    { id: "kpi", label: "KPI хабу", glyph: "chart", panel: renderKpiDashboard,
      show: () => isModerator() },
    { id: "analytics", label: "Використання", glyph: "pulse", panel: renderHubAnalytics,
      show: () => isModerator() },
  ]},
  { group: "Система", items: [
    { id: "users", label: "Користувачі", glyph: "person", panel: viewUsers,
      show: () => isAdmin() },
    { id: "contact", label: "Зворотний зв'язок", glyph: "mail", panel: renderContactManage,
      show: () => isAdmin() },
  ]},
];
// Перша доступна секція консолі — туди веде перемикач «Керування».
function adminHome() {
  for (const grp of ADMIN_SECTIONS) {
    const hit = grp.items.find(i => !i.show || i.show());
    if (hit) return `admin:${hit.id}`;
  }
  return "hub:home";
}

// Поточне місце. `dest` — рядок виду "hub:sec:12"; перший сегмент — режим.
let nav = { mode: "hub", dest: "hub:home" };

function destMode(dest) {
  const head = String(dest).split(":")[0];
  return MODES.some(m => m.id === head) ? head : "hub";
}
function visibleModes() {
  return MODES.filter(m => !m.show || m.show());
}

// Ролі (фідбек тестування, п. 11) — ті самі правила, що й app/core/access.py:
// адміністратор — усе; менеджер розділу — модерація матеріалів і навичок
// призначених розділів; користувач — переглядає, коментує, оцінює й подає
// своє на розгляд. «Експерт» і «Відповідальний за ідеї» — ознаки профілю.
function isAdmin() { return hasRole("admin"); }
function managedSections() {
  return new Set(hasRole("section_manager") && state.user
    ? (state.user.managed_section_ids || []) : []);
}
function isModerator() { return isAdmin() || managedSections().size > 0; }
function canModerateSection(sectionId) {
  if (isAdmin()) return true;
  return sectionId != null && managedSections().has(Number(sectionId));
}
// Чи модерує поточний користувач матеріал чи навичку (за розділом).
function canModerate(raw) { return !!raw && canModerateSection(raw.section_id); }
function canManageIdeas() { return isAdmin() || !!(state.user && state.user.is_ideas_owner); }
function hasConsole() { return isAdmin() || hasRole("section_manager") || canManageIdeas(); }

const ROLE_LABELS = { admin: "Адміністратор", section_manager: "Менеджер розділу" };
function roleLabel(u) {
  const code = ["admin", "section_manager"].find(c => (u.roles || []).includes(c));
  return code ? ROLE_LABELS[code] : "Користувач";
}

// Сумісність зі старими викликами openTab("catalog") тощо.
const LEGACY_TAB = {
  catalog: "hub:home", mine: "hub:contrib", contrib: "hub:contrib",
  "manage-skills": "admin:content", users: "admin:users",
};
function openTab(id) { return go(LEGACY_TAB[id] || "hub:home"); }

function buildNav() {
  buildModeSeg();
  buildTabbar();
  bindProfileMenu();
  bindNotifications();
  bindGlobalSearch();
  go("hub:home");
}

function buildModeSeg() {
  const seg = $("#mode-seg");
  if (!seg) return;
  // На «Керуванні» — скільки подань і змін чекає рішення: менеджер бачить
  // це з будь-якої сторінки бази знань.
  const queue = isModerator() ? moderationCount() : 0;
  seg.innerHTML = visibleModes().map(m =>
    `<button data-mode="${m.id}" title="${esc(m.label)}"${m.id === nav.mode ? ' class="active"' : ""}>${gsvg(m.glyph)}<span>${esc(m.label)}</span>${
      m.id === "admin" && queue ? `<b class="seg-badge" title="Чекають рішення">${queue}</b>` : ""}</button>`
  ).join("");
  // Перемикач із однієї кнопки нічого не перемикає — ховаємо його.
  seg.classList.toggle("hidden", visibleModes().length < 2);
  seg.querySelectorAll("[data-mode]").forEach(b =>
    b.addEventListener("click", () => {
      const m = MODES.find(x => x.id === b.dataset.mode);
      // Повернення в режим відкриває те місце, де користувач був востаннє.
      go(nav.lastIn && nav.lastIn[m.id] ? nav.lastIn[m.id]
         : m.id === "admin" ? adminHome() : m.home);
    }));
}

// Телефон: ті самі режими плюс пряме потрапляння в обране й профіль.
function buildTabbar() {
  const bar = $("#tabbar");
  if (!bar) return;
  const items = visibleModes().map(m => ({ dest: m.id === "admin" ? adminHome() : m.home,
                                          mode: m.id, ...m }));
  items.splice(1, 0, { id: "all", label: "Матеріали", glyph: "list", dest: "hub:all" });
  items.push({ id: "fav", label: "Обране", glyph: "star", dest: "hub:fav" });
  bar.innerHTML = items.map(it =>
    `<button data-dest="${it.dest}" data-tab="${it.id}">${gsvg(it.glyph)}<span>${esc(it.label)}</span></button>`
  ).join("");
  bar.querySelectorAll("[data-dest]").forEach(b =>
    b.addEventListener("click", () => go(b.dataset.dest)));
}

// ---- Меню профілю ----
function bindProfileMenu() {
  const btn = $("#me-btn"), menu = $("#me-menu");
  if (!btn || !menu || btn.dataset.bound) return;
  btn.dataset.bound = "1";
  const close = () => { menu.classList.add("hidden"); btn.setAttribute("aria-expanded", "false"); };
  btn.addEventListener("click", e => {
    e.stopPropagation();
    const open = menu.classList.toggle("hidden");
    btn.setAttribute("aria-expanded", String(!open));
  });
  document.addEventListener("click", e => {
    if (!menu.contains(e.target) && !btn.contains(e.target)) close();
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
  menu.querySelectorAll("[data-go]").forEach(b =>
    b.addEventListener("click", () => { close(); go(b.dataset.go); }));
  menu.querySelectorAll("[data-act]").forEach(b =>
    b.addEventListener("click", () => {
      close();
      if (b.dataset.act === "my-ideas") openMyIdeas();
      if (b.dataset.act === "my-content") openMyContent();
      if (b.dataset.act === "tour") replayTour();
    }));
}

// ---- Сповіщення: дзвіночок у верхній панелі ----
//
// Сповіщення — це дії, що чекають на людину: менеджеру — нова публікація
// користувача, авторові — рішення менеджера чи коментар до його матеріалу,
// власникові — нагадування про перегляд. Тому вони в шапці, а не в
// прихованому меню, і клік відкриває саме ту картку, де діяти.
let notifState = { items: [], unread: 0, timer: null, queue: 0 };

// Черга модерації (фідбек тестування, п. 12): подання на розгляд і зміни до
// опублікованого, що чекають рішення, — з сервера, у розділах, які модерує
// поточний користувач. Лічильник — лише те, що чекає рішення; старі
// сповіщення про публікації (див. reviewQueue) показуються в консолі окремо.
let modState = { items: [], count: 0 };
async function refreshModeration() {
  if (!isModerator()) { modState = { items: [], count: 0 }; return modState; }
  try { modState = await api("/catalog/moderation"); } catch (e) { /* лишаємо попереднє */ }
  return modState;
}
function moderationCount() { return modState.count || 0; }

async function refreshNotifications() {
  if (!state.token || !$("#notif-btn")) return;
  let data;
  try {
    [data] = await Promise.all([api("/catalog/notifications"), refreshModeration()]);
  } catch (e) { return; }
  notifState.items = data.items || [];
  notifState.unread = data.unread || 0;
  paintNotifBadge();
  syncReviewCount();
  if (!$("#notif-pop").classList.contains("hidden")) paintNotifications();
}

// Лічильник непереглянутих публікацій у бічній панелі менеджера живе зі
// сповіщень — перемальовуємо панель, лише коли число справді змінилося.
function syncReviewCount() {
  if (!isModerator()) return;
  const n = moderationCount();
  if (n === notifState.queue) return;
  notifState.queue = n;
  buildModeSeg();
  if ($("#sidebar") && (nav.mode === "admin" || catalogState.sections)) {
    buildSidebar();
    paintNavActive();
  }
}

function paintNotifBadge() {
  const n = $("#notif-n");
  n.textContent = notifState.unread > 9 ? "9+" : String(notifState.unread);
  n.classList.toggle("hidden", !notifState.unread);
  $("#notif-btn").classList.toggle("has-new", notifState.unread > 0);
}

// «5 хв», «2 год», «вчора» — час із бази UTC без позначки зони.
function timeAgo(iso) {
  if (!iso) return "";
  const t = new Date(/Z|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z").getTime();
  const m = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (m < 1) return "щойно";
  if (m < 60) return `${m} хв`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} год`;
  const d = Math.round(h / 24);
  return d === 1 ? "вчора" : d < 7 ? `${d} дн` : new Date(t).toLocaleDateString("uk-UA");
}

function paintNotifications() {
  const list = $("#notif-list");
  if (!notifState.items.length) {
    list.innerHTML = `<p class="notif-empty">Сповіщень поки немає.</p>`;
    return;
  }
  list.innerHTML = notifState.items.slice(0, 20).map(nt => `
    <button class="notif-item${nt.is_read ? "" : " unread"}" data-id="${nt.id}">
      <span class="notif-dot"></span>
      <span class="notif-body"><b>${esc(nt.title)}</b><span>${esc(nt.body || "")}</span>
        <i>${timeAgo(nt.created_at)}</i></span>
    </button>`).join("");
  list.querySelectorAll(".notif-item").forEach(b =>
    b.addEventListener("click", () => openNotification(Number(b.dataset.id))));
}

async function openNotification(id) {
  const nt = notifState.items.find(x => x.id === id);
  if (!nt) return;
  closeNotifPop();
  if (!nt.is_read) {
    nt.is_read = true;
    notifState.unread = Math.max(0, notifState.unread - 1);
    paintNotifBadge();
    api("/catalog/notifications/read", { method: "POST", body: { ids: [id] } }).catch(() => {});
  }
  if (nt.kind === "review_due" || nt.kind === "review_overdue") { openMyContent(); return; }
  if (nt.kind === "contact_message") { go("admin:contact"); return; }
  // Картку відкриваємо поверх поточної сторінки — звідти й діють.
  if (nt.skill_id) { openSkillModal(nt.skill_id); return; }
  if (nt.resource_id) { openResourceModal(nt.resource_id); return; }
  // Матеріал уже видалено: сповіщення несе лише назву й причину.
  go("hub:contrib");
}

function closeNotifPop() {
  const pop = $("#notif-pop"), btn = $("#notif-btn");
  if (pop) pop.classList.add("hidden");
  if (btn) btn.setAttribute("aria-expanded", "false");
}

function bindNotifications() {
  const btn = $("#notif-btn"), pop = $("#notif-pop");
  if (!btn || !pop || btn.dataset.bound) return;
  btn.dataset.bound = "1";
  btn.addEventListener("click", e => {
    e.stopPropagation();
    const open = pop.classList.toggle("hidden");
    btn.setAttribute("aria-expanded", String(!open));
    if (!open) paintNotifications();
  });
  document.addEventListener("click", e => {
    if (!pop.contains(e.target) && !btn.contains(e.target)) closeNotifPop();
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeNotifPop(); });
  $("#notif-read-all").addEventListener("click", async () => {
    try { await api("/catalog/notifications/read", { method: "POST", body: {} }); } catch (e) { /* ignore */ }
    notifState.items.forEach(x => { x.is_read = true; });
    notifState.unread = 0;
    paintNotifBadge();
    paintNotifications();
  });
  // Раз на хвилину достатньо: публікації й коментарі — не щосекундні події,
  // а при кожному переході сторінок лічильник оновлюється й так.
  if (notifState.timer) clearInterval(notifState.timer);
  notifState.timer = setInterval(refreshNotifications, 60000);
}

// ---- Пошук: один на весь застосунок ----
// У базі знань він звужує каталог; з іншого режиму — спершу переводить туди,
// бо шукати можна тільки те, що в каталозі.
function setGlobalQuery(v) {
  catalogState.query = v;
  const inp = $("#global-q");
  if (inp && inp.value !== v) inp.value = v;
}
function syncGlobalQuery() {
  const inp = $("#global-q");
  if (inp) inp.value = nav.mode === "hub" ? (catalogState.query || "") : "";
}
function bindGlobalSearch() {
  const inp = $("#global-q");
  if (!inp || inp.dataset.bound) return;
  inp.dataset.bound = "1";
  inp.addEventListener("input", async () => {
    const v = inp.value;
    if (!document.getElementById("cat-grid")) {
      await go("hub:all");
      const back = $("#global-q");
      if (back) { back.value = v; back.focus(); }
    }
    catalogState.query = v;
    paintCatalog();
    scheduleSearchLog();
  });
  inp.addEventListener("keydown", e => {
    if (e.key === "Enter" && inp.value.trim()) openAssistant(inp.value.trim());
    if (e.key === "Escape") { setGlobalQuery(""); paintCatalog(); inp.blur(); }
  });
  // ⌘K / Ctrl+K — фокус у пошук із будь-якого місця
  document.addEventListener("keydown", e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault(); inp.focus(); inp.select();
    }
  });
}

// ---- Шухляда бічної панелі на телефоні ----
function bindSideToggle() {
  const t = $("#side-toggle"), scrim = $("#side-scrim");
  if (!t || t.dataset.bound) return;
  t.dataset.bound = "1";
  t.addEventListener("click", () => document.body.classList.toggle("side-open"));
  if (scrim) scrim.addEventListener("click", () => document.body.classList.remove("side-open"));
}

// ---- Маршрутизація ----
async function go(dest) {
  if (dest === "hub:paths") dest = "hub:dev";   // колишні «Мої маршрути»
  const mode = destMode(dest);
  nav.lastIn = nav.lastIn || {};
  nav.lastIn[mode] = dest;
  nav.mode = mode;
  nav.dest = dest;
  document.body.classList.remove("side-open");

  document.querySelectorAll("#mode-seg [data-mode]").forEach(b =>
    b.classList.toggle("active", b.dataset.mode === mode));
  bindSideToggle();

  const view = $("#view");
  view.innerHTML = `<p class="muted">Завантаження…</p>`;
  try {
    if (mode === "admin") { buildSidebar(); await renderAdminDest(dest); }
    else { await renderHubDest(dest); }
  } catch (err) {
    view.innerHTML = `<div class="card"><p class="error">${esc(err.message)}</p></div>`;
  }
  paintNavActive();
  syncGlobalQuery();
  refreshNotifications();
}

function paintNavActive() {
  document.querySelectorAll("#sidebar [data-dest]").forEach(b =>
    b.classList.toggle("active", b.dataset.dest === nav.dest));
  // Окремі вкладки «Матеріали» й «Обране» підсвічуються самі; вкладка режиму —
  // для решти його сторінок.
  const own = { all: "hub:all", fav: "hub:fav" };
  document.querySelectorAll("#tabbar [data-dest]").forEach(b => {
    const t = b.dataset.tab;
    b.classList.toggle("active", own[t] ? nav.dest === own[t]
      : destMode(b.dataset.dest) === nav.mode && !Object.values(own).includes(nav.dest));
  });
}

// ---- Бічна панель ----
function sbItem(dest, label, glyph, count, extra = "") {
  return `<button class="sb-item ${extra}" data-dest="${dest}">
      ${glyph ? gsvg(glyph) : ""}
      <span class="sb-label">${esc(label)}</span>
      ${count != null && count !== "" ? `<span class="sb-count">${count}</span>` : ""}
    </button>`;
}

// Низ панелі в обох режимах (фідбек тестування, п. 21): підтримка —
// AI SD агент для технічних питань і «Зворотний зв'язок» адміністратору, —
// а останнім пунктом інструкція. Посилання відкриваються в новій вкладці,
// як і значок «?» угорі.
const GUIDE_URL = "guide/";
const AGENT_HINT = "Для вирішення технічних питань";
const CONTACT_HINT = "Нетехнічні питання адміністратору хабу";

// Адресу агента змінює адміністратор у «Керуванні»; порожня ховає посилання.
function supportAgentUrl() {
  return (catalogState.settings && catalogState.settings.support_agent_url) || "";
}

// Налаштування, потрібні на кожній сторінці (адреса агента), вантажимо одразу
// після входу, хоч би з якої сторінки людина почала, і після їх зміни.
async function refreshHubSettings() {
  try { catalogState.settings = await api("/catalog/settings"); } catch (e) { return; }
  if ($("#sidebar")) buildSidebar();
  paintHomeSupport();
}

function sbGuideHtml() {
  const agent = supportAgentUrl();
  return `<div class="sb-spacer"></div>
    ${agent ? `<a class="sb-item sb-guide" href="${esc(agent)}" target="_blank" rel="noopener"
       title="${AGENT_HINT}" data-support="agent">
      ${gsvg("chat")}<span class="sb-label">AI SD агент</span>${gsvg("ext", "sb-ext")}
    </a>` : ""}
    <button class="sb-item" data-contact title="${CONTACT_HINT}">
      ${gsvg("mail")}<span class="sb-label">Зворотний зв'язок</span>
    </button>
    <a class="sb-item sb-guide" href="${GUIDE_URL}" target="_blank" rel="noopener"
       title="Інструкція відкриється в новій вкладці">
      ${gsvg("help")}<span class="sb-label">Інструкція</span>${gsvg("ext", "sb-ext")}
    </a>`;
}

function buildSidebar() {
  const box = $("#sidebar");
  if (!box) return;
  box.innerHTML = (nav.mode === "admin" ? adminSidebarHtml() : hubSidebarHtml()) + sbGuideHtml();
  box.querySelectorAll("[data-dest]").forEach(b =>
    b.addEventListener("click", () => go(b.dataset.dest)));
  box.querySelectorAll("[data-ext]").forEach(b =>
    b.addEventListener("click", () => window.open(b.dataset.ext, "_blank", "noopener")));
  box.querySelector("[data-contact]").addEventListener("click", () => {
    document.body.classList.remove("side-open");
    openContactForm();
  });
  paintNavActive();
}

function adminSidebarHtml() {
  return ADMIN_SECTIONS.map(grp => {
    const items = grp.items.filter(i => !i.show || i.show());
    if (!items.length) return "";
    const queue = moderationCount();
    return `<div class="sb-group">${esc(grp.group)}</div>`
      + items.map(i => i.id === "content"
        ? sbItem(`admin:${i.id}`, i.label, i.glyph, queue || "", queue ? "sb-attn" : "")
        : sbItem(`admin:${i.id}`, i.label, i.glyph)).join("");
  }).join("");
}

function hubSidebarHtml() {
  const favCount = catalogState.favorites.skill.size + catalogState.favorites.resource.size;
  const recent = recentItems().length;
  let html = sbItem("hub:home", "Для вас", "spark")
    + sbItem("hub:fav", "Обране", "star", favCount || "")
    + (recent ? sbItem("hub:recent", "Нещодавні", "clock", recent) : "");
  // «Матеріали» — увесь каталог із фільтрами. Головний вхід у базу знань,
  // тому виділений і стоїть над розділами, а не губиться серед них.
  html += sbItem("hub:all", "Матеріали", "list", catalogState.items.length || "", "sb-feature");

  const sections = catalogState.sections.filter(s => s.items_count > 0 || s.url);
  if (sections.length) {
    html += `<div class="sb-group">Розділи</div>`;
    for (const sec of sections) {
      if (sec.url) {
        html += `<button class="sb-item" data-ext="${esc(sec.url)}" title="${esc(sec.description || sec.name)}">
            <span class="sb-dot ${sectionTone(sec)}">${gsvg("box")}</span>
            <span class="sb-label">${esc(sec.name)}</span>
            ${gsvg("ext", "sb-ext")}
          </button>`;
        continue;
      }
      const on = nav.dest === `hub:sec:${sec.id}`
        || catalogState.folders.some(f => f.section_id === sec.id
                                          && nav.dest === `hub:fol:${f.id}`);
      html += `<button class="sb-item" data-dest="hub:sec:${sec.id}" title="${esc(sec.description || sec.name)}">
          <span class="sb-dot ${sectionTone(sec)}">${gsvg("box")}</span>
          <span class="sb-label">${esc(sec.name)}</span>
          <span class="sb-count">${sec.items_count}</span>
        </button>`;
      // Колекції показуємо лише в розкритому розділі — інакше панель
      // перетворюється на простирадло.
      if (on) {
        const folders = catalogState.folders
          .filter(f => f.section_id === sec.id && f.items_count > 0);
        html += folders.map(f =>
          sbItem(`hub:fol:${f.id}`, f.name, null, f.items_count, "sb-sub")).join("");
      }
    }
  }

  // Рівень, наступний крок і маршрути — одна сторінка (фідбек, п. 5 і 8).
  html += `<div class="sb-group">Навчання</div>`
    + sbItem("hub:dev", "Мій розвиток", "grow");
  // Наповнення каталогу — особисте для кожного: лічильник — власні матеріали
  // й навички. Черга модерації менеджера — у «Керуванні».
  const mine = (catalogState.mine || []).length + (catalogState.mineSkills || []).length;
  html += `<div class="sb-group">Контент</div>`
    + sbItem("hub:contrib", "Наповнення каталогу", "pen", mine || "");
  return html;
}

// Нещодавно відкриті матеріали живуть у браузері користувача: окремий
// ендпоінт заради списку з десяти карток був би зайвим.
const RECENT_KEY = "hub-recent";
function recentItems() {
  try {
    const ids = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    return ids.map(k => catalogState.items.find(i => `${i.item_type}:${i.id}` === k))
      .filter(Boolean);
  } catch (e) { return []; }
}
function pushRecent(item) {
  try {
    const key = `${item.item_type}:${item.id}`;
    const ids = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]")
      .filter(k => k !== key);
    ids.unshift(key);
    localStorage.setItem(RECENT_KEY, JSON.stringify(ids.slice(0, 12)));
  } catch (e) { /* приватний режим — просто без історії */ }
}

// ---- Режим «Керування» ----
function adminDestMeta(dest) {
  const id = dest.split(":")[1];
  for (const grp of ADMIN_SECTIONS) {
    const hit = grp.items.find(i => i.id === id);
    if (hit) return hit;
  }
  return ADMIN_SECTIONS[0].items[0];
}

async function renderAdminDest(dest) {
  let meta = adminDestMeta(dest);
  // Секція, недоступна цій ролі (стара закладка, зміна прав), — до першої своєї.
  if (meta.show && !meta.show()) {
    const home = adminHome();
    if (home.startsWith("admin:")) { nav.dest = home; meta = adminDestMeta(home); }
    else { go(home); return; }
  }
  $("#view").innerHTML = `
    <div class="page-head">
      <div class="page-head-txt"><h1 class="page-h1">${esc(meta.label)}</h1></div>
    </div>
    <div id="skills-panel"></div>`;
  await meta.panel();
  // Панель часто повторює власну назву заголовком картки — тоді на екрані
  // два однакові заголовки поспіль. Другий прибираємо.
  const dup = document.querySelector("#skills-panel h2");
  if (dup && dup.textContent.trim().toLowerCase() === meta.label.toLowerCase()) dup.remove();
}

// Екрани консолі керування малюють у спільний #skills-panel під заголовком
// сторінки; поза консоллю — просто у #view.
function panelEl() {
  return document.getElementById("skills-panel") || document.getElementById("view");
}

// ---------- Каталог: навички, промпти, інструкції, агенти, посилання ----------

// Стандартна іконка навички (коли власну не задано) — фірмова «блискавка».
const DEFAULT_SKILL_ICON =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"/></svg>';
const DESC_PREVIEW_LEN = 120;  // скільки символів опису показувати на плитці

// Види наповнення каталогу. Порядок = порядок фільтрів-пігулок.
// Види матеріалів. Гліф — один стиль, одна товщина; відтінок робить тип
// упізнаваним периферійним зором, ще до читання підпису.
const CAT_KINDS = {
  skill: {
    label: "Навички", one: "Навичка", accent: "red",
    icon: '<path d="M13.2 3 5 13.4h5.2L9.8 21l8.2-10.4h-5.2L13.2 3Z"/>',
  },
  prompt: {
    label: "Промпти", one: "Промпт", accent: "blue",
    icon: '<path d="M12 3.4l1.9 5.2 5.2 1.9-5.2 1.9-1.9 5.2-1.9-5.2-5.2-1.9 5.2-1.9L12 3.4Z"/><path d="M18.6 16.4l.7 1.9 1.9.7-1.9.7-.7 1.9-.7-1.9-1.9-.7 1.9-.7.7-1.9Z"/>',
  },
  instruction: {
    label: "Інструкції", one: "Інструкція", accent: "purple",
    icon: '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5V8h4"/><path d="M9 12.5h6M9 16h4"/>',
  },
  case: {
    label: "Кейси", one: "Кейс", accent: "green",
    icon: '<path d="M4 19.5h16"/><path d="M7.5 16.5V10M12 16.5V5.5M16.5 16.5v-4"/>',
  },
  agent: {
    label: "Агенти", one: "Агент", accent: "orange",
    icon: '<rect x="4" y="8" width="16" height="11" rx="3.2"/><path d="M12 4.2V8M9.2 13h.01M14.8 13h.01M9.8 16.4h4.4"/>',
  },
  mcp: {
    label: "MCP-сервери", one: "MCP-сервер", accent: "teal",
    icon: '<path d="M9 3.2v4.6M15 3.2v4.6"/><path d="M6.2 7.8h11.6v3.4a5.8 5.8 0 0 1-11.6 0V7.8Z"/><path d="M12 17v3.8"/>',
  },
  link: {
    label: "Корисні посилання", one: "Посилання", accent: "pink",
    icon: '<path d="M10.2 13.4a4.2 4.2 0 0 0 6 .4l2.6-2.6a4.2 4.2 0 1 0-6-6l-1.5 1.5"/><path d="M13.8 10.6a4.2 4.2 0 0 0-6-.4l-2.6 2.6a4.2 4.2 0 1 0 6 6l1.5-1.5"/>',
  },
};
const CAT_ORDER = ["skill", "prompt", "instruction", "case", "agent", "mcp", "link"];
const CAT_ALL_ICON = '<path d="M4 4h7v7H4V4Zm9 0h7v7h-7V4ZM4 13h7v7H4v-7Zm9 0h7v7h-7v-7Z"/>';

const ICO_STAR = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.6 9.7l5.8-.8L12 3.6Z"/></svg>';
const ICO_COPY = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 2h9a2 2 0 0 1 2 2v11h-2V4H9V2ZM5 6h9a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2Z"/></svg>';
const ICO_DOWNLOAD = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v10.6l3.3-3.3 1.4 1.4L12 17.4 6.3 11.7l1.4-1.4L11 13.6V3h1Zm-7 15h14v2H5v-2Z"/></svg>';

let catalogState = {
  skills: [], resources: [], items: [], sections: [], folders: [], terms: [],
  paths: [], recommendations: [], maturity: null, types: [],
  // Налаштування хабу (адреса AI SD агента тощо), див. refreshHubSettings.
  settings: {},
  favorites: { skill: new Set(), resource: new Set() },
  query: "", kind: "all", sectionId: null, folderId: null, favOnly: false,
  // Фільтри за атрибутами картки (BR-06). Порожній рядок = фільтр не активний.
  filters: { category: "", complexity: "", businessValue: "", tool: "",
             owner: "", reuse: "", tag: "", status: "" },
  // Лог пошуку: id останнього зареєстрованого запиту (BR-08) та таймер debounce.
  searchLogId: null, searchTimer: null,
};

// Опис кожного фільтра: звідки брати значення матеріалу і як підписати.
// value() повертає рядок або масив рядків — збіг за будь-яким елементом.
const CAT_FILTERS = [
  { key: "category", label: "Категорія", value: i => i.category },
  { key: "complexity", label: "Складність", value: i => i.complexity },
  { key: "businessValue", label: "Бізнес-цінність", value: i => i.businessValue },
  { key: "tool", label: "Інструмент", value: i => i.tools },
  { key: "owner", label: "Власник", value: i => i.owner },
  { key: "reuse", label: "Повторне використання", value: i => i.reuseLabel },
  { key: "tag", label: "Тег", value: i => i.tags },
  { key: "maturity", label: "Рівень зрілості", value: i => i.maturityNames },
  { key: "status", label: "Статус", value: i => STATUS_LABELS[i.status], manager: true },
];

const REUSE_LABELS = { ready: "Готове до використання",
                       adaptable: "Потребує адаптації", reference: "Довідково" };
// Що рівень означає на практиці — інакше позначка нічого не підказує (BR-14).
const REUSE_HINTS = {
  ready: "Можна застосувати як є: налаштування не потрібні.",
  adaptable: "Основа робоча, але під свій процес доведеться змінити деталі.",
  reference: "Приклад для орієнтира — повторювати доведеться з нуля.",
};

// Матеріал, що потребує оновлення, лишається видимим — але з попередженням.
const STATUS_LABELS = {
  draft: "Чернетка", published: "Опублікований",
  needs_update: "Потребує оновлення", archived: "Архів",
};

// Дата публікації (fallback — дата створення) для сортування «новіші зверху».
function pubTime(s) {
  const d = s.published_at || s.created_at;
  return d ? new Date(d).getTime() : 0;
}

// Приводить навичку та ресурс до спільного вигляду для плиток каталогу.
function normSkill(s) {
  return {
    item_type: "skill", kind: "skill", id: s.id, name: s.name,
    description: s.description || "", category: s.category || "",
    author: s.author || "", authorExpert: !!s.author_is_expert, version: s.version, tags: [],
    accent: "red", popularity: s.downloads_count || 0, when: pubTime(s),
    featured: false, iconUrl: s.has_icon ? s.icon_url : null, emoji: null,
    url: null, linkScope: null, body: null,
    sectionId: s.section_id || null, folderId: s.folder_id || null,
    folderName: null, complexity: "", businessValue: "", tools: "",
    maturityNames: [],
    reuseLabel: "", status: s.status, owner: s.owner || "",
    ratingAvg: null, ratingCount: 0, raw: s,
  };
}

function normResource(r) {
  return {
    item_type: "resource", kind: r.resource_type, id: r.id, name: r.name,
    description: r.description || "", category: r.category || "",
    author: r.author || "", authorExpert: !!r.author_is_expert,
    version: r.version, tags: r.tags || [],
    // Колір — лише за видом: однаковий вид завжди виглядає однаково (п. 2.2).
    accent: kindMeta(r.resource_type).accent || "red",
    popularity: r.opens_count || 0, when: pubTime(r),
    featured: !!r.is_featured, iconUrl: null, emoji: r.icon_emoji || null,
    url: r.url, linkScope: r.link_scope, body: r.body,
    sectionId: r.section_id || null, folderId: r.folder_id || null,
    folderName: r.folder_name || null,
    complexity: r.complexity_name || "", businessValue: r.business_value_name || "",
    maturityNames: r.maturity_names || [],
    tools: r.tools || "", reuseLabel: REUSE_LABELS[r.reuse_level] || "",
    status: r.status, owner: r.owner || "",
    ratingAvg: r.rating_avg != null ? r.rating_avg : null,
    ratingCount: r.rating_count || 0, raw: r,
  };
}

// Дані каталогу вантажимо один раз: бічна панель показує розділи ще до
// того, як користувач відкриє сторінку, тож завантаження не може бути
// частиною рендера сторінки.
async function ensureHubData(force = false) {
  if (catalogState.loaded && !force) return;
  const [all, resources, favs, sections, folders] = await Promise.all([
    api("/skills"),
    api("/catalog/resources"), api("/catalog/favorites"), api("/catalog/sections"),
    api("/catalog/folders"),
  ]);
  // Види матеріалів — з довідника: доданий адміністратором вид має одразу
  // з'явитися і в перемикачі, і у фільтрах, без зміни коду (AKH-17).
  catalogState.types = await api("/catalog/terms?kind=material_type").catch(() => []);
  // Персональні блоки не мають затримувати каталог: якщо щось із них впаде,
  // сторінка все одно має відкритися.
  const [paths, recs, maturity, mineRes, mineSkills] = await Promise.all([
    api("/paths?mine=1").catch(() => []),
    api("/catalog/recommendations").catch(() => ({ items: [] })),
    api("/catalog/maturity").catch(() => null),
    api("/catalog/resources?mine=1").catch(() => []),
    api("/skills?mine=1").catch(() => []),
  ]);
  catalogState.paths = paths || [];
  // Власні матеріали й навички — для лічильника «Наповнення каталогу».
  catalogState.mine = mineRes || [];
  catalogState.mineSkills = mineSkills || [];
  catalogState.recommendations = (recs && recs.items) || [];
  catalogState.maturity = maturity;
  catalogState.sections = sections;
  catalogState.folders = folders;
  // Розділ чи колекцію могли видалити, поки вкладка була відкрита — інакше
  // каталог показав би порожній список без видимої причини.
  if (catalogState.sectionId !== null
      && !sections.some(x => x.id === catalogState.sectionId)) {
    catalogState.sectionId = null;
  }
  if (catalogState.folderId !== null
      && !folders.some(f => f.id === catalogState.folderId)) {
    catalogState.folderId = null;
  }
  catalogState.skills = all.filter(x => x.status === "published").map(normSkill);
  // Каталог показує опубліковані матеріали та ті, що потребують оновлення;
  // чернетки й архів API віддає лише менеджерам — тут їх відфільтровуємо.
  catalogState.resources = resources
    .filter(r => r.status === "published" || r.status === "needs_update")
    .map(normResource);
  catalogState.favorites = {
    skill: new Set(favs.skill || []), resource: new Set(favs.resource || []),
  };
  catalogState.items = [...catalogState.skills, ...catalogState.resources]
    // 1) рекомендовані, 2) за популярністю, 3) за новизною.
    .sort((a, b) => (b.featured - a.featured) || (b.popularity - a.popularity)
                    || (b.when - a.when));
  catalogState.loaded = true;
}

// Куди саме дивиться користувач у базі знань.
function applyHubScope(dest) {
  const parts = String(dest).split(":");   // hub : (home|all|fav|recent|sec|fol) : id
  const what = parts[1] || "home";
  // Скидання — першим, ще за старим зрізом; позначку «лише обране» далі
  // ставимо явно за новим.
  resetCatFilters();
  catalogState.scope = (what === "sec" || what === "fol") ? "catalog" : what;
  catalogState.favOnly = what === "fav";
  catalogState.query = "";
  if (what === "sec") {
    catalogState.sectionId = Number(parts[2]);
    catalogState.folderId = null;
    trackView("section", catalogState.sectionId);
  } else if (what === "fol") {
    catalogState.folderId = Number(parts[2]);
    const f = catalogState.folders.find(x => x.id === catalogState.folderId);
    catalogState.sectionId = f ? f.section_id : null;
    trackView("folder", catalogState.folderId);
  } else {
    catalogState.sectionId = null;
    catalogState.folderId = null;
  }
  catalogState.kind = "all";
}

async function renderHubDest(dest) {
  await ensureHubData();
  if (dest === "hub:dev") { buildSidebar(); return viewDevelopment(); }
  if (dest === "hub:contrib") { buildSidebar(); return viewContributions(); }
  applyHubScope(dest);
  buildSidebar();
  return catalogState.scope === "home" ? viewHome() : viewCatalog();
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}
function nMaterials(n) { return `${n} ${plural(n, "матеріал", "матеріали", "матеріалів")}`; }

// Заголовок сторінки залежить від того, де ми стоїмо: у базі знань
// «де я» показує не меню, а сама сторінка.
function hubHead() {
  const sec = catalogState.sections.find(x => x.id === catalogState.sectionId);
  const fol = catalogState.folders.find(f => f.id === catalogState.folderId);

  if (catalogState.scope === "fav") {
    const n = sectionScope().length;
    return { crumb: "", title: "Обране",
             sub: n ? nMaterials(n)
                    : "Позначайте матеріали зірочкою — вони збиратимуться тут." };
  }
  if (catalogState.scope === "recent") {
    return { crumb: "", title: "Нещодавні",
             sub: "Матеріали, які ви відкривали останніми." };
  }
  if (catalogState.scope === "all") {
    return { crumb: "База знань", title: "Матеріали",
             sub: `${nMaterials(catalogState.items.length)} — увесь каталог. Звужуйте видом, фільтрами чи пошуком.` };
  }
  if (fol) {
    return { crumb: `База знань › ${esc(sec ? sec.name : "Розділ")}`,
             title: esc(fol.name),
             sub: fol.description || nMaterials(fol.items_count) };
  }
  if (sec) {
    return { crumb: "База знань › Розділи", title: esc(sec.name),
             sub: sec.description || nMaterials(sec.items_count) };
  }
  const hour = new Date().getHours();
  const part = hour < 5 ? "Доброї ночі" : hour < 12 ? "Доброго ранку"
             : hour < 18 ? "Доброго дня" : "Доброго вечора";
  const full = (state.user && (state.user.full_name || state.user.username)) || "";
  const name = full.split(" ").filter(Boolean).slice(-1)[0] || "";
  const n = catalogState.items.length;
  const secs = catalogState.sections.filter(x => x.items_count > 0 || x.url).length;
  return {
    crumb: "",
    title: name ? `${part}, ${esc(name)}` : "База знань",
    sub: `${nMaterials(n)} · ${secs} ${plural(secs, "розділ", "розділи", "розділів")}`,
  };
}

const PAGE_ACTS_HTML = `
  <div class="page-acts">
    <button class="ghost hidden" id="cat-my-content">Мій контент</button>
    <button class="ghost" id="cat-idea">Поділитися ідеєю</button>
    <button class="ghost" id="cat-add">${gsvg("plus")}Додати матеріал</button>
    <button id="cat-assistant">Опишіть задачу</button>
  </div>`;

function bindPageActs() {
  $("#cat-assistant").addEventListener("click", () => openAssistant());
  $("#cat-idea").addEventListener("click", () => openIdeaForm());
  $("#cat-add").addEventListener("click", () => openContentWizard());
  paintMyContentButton();
}

// Головна (фідбек тестування, п. 5 і 8): привітання з рівнем під ним і два
// блоки — «Рекомендовано вам» і «Нещодавно додані». Каталог із фільтрами живе
// в «Матеріалах», рівень і маршрути — на сторінці «Мій розвиток». Пошук із
// головної веде в «Матеріали»: сітки, яку він звужував би, тут немає.
const RECENT_ADDED_LIMIT = 8;

async function viewHome() {
  const head = hubHead();
  $("#view").innerHTML = `
    <div class="catalog home">
      <div class="page-head">
        <div class="page-head-txt">
          <h1 class="page-h1 page-h1-lg">${head.title}</h1>
          <p class="page-sub home-level hidden" id="home-level"></p>
        </div>
        ${PAGE_ACTS_HTML}
      </div>
      <div class="cat-personal" id="cat-personal"></div>
      <div id="home-support"></div>
    </div>`;
  paintCatalog();
  paintHomeSupport();
  bindPageActs();
}

// Підтримка на головній (фідбек тестування, п. 21): два канали й пояснення,
// куди з яким питанням. Ті самі пункти є внизу бічної панелі.
function paintHomeSupport() {
  const box = $("#home-support");
  if (!box) return;
  const agent = supportAgentUrl();
  box.innerHTML = `
    <section class="show-block support-block">
      <div class="show-head"><div>
        <h2>Потрібна допомога?</h2>
        <p class="muted">Технічні питання вирішує AI SD агент, решту — адміністратор хабу.</p>
      </div></div>
      <div class="support-row">
        ${agent ? `<a class="support-card" href="${esc(agent)}" target="_blank" rel="noopener"
             title="${AGENT_HINT}" data-support="agent">
          <span class="support-ic">${gsvg("chat")}</span>
          <span class="support-txt"><b>AI SD агент${gsvg("ext", "btn-ext")}</b>
            <i>${AGENT_HINT}: помилки, доступ до сервісів, налаштування. Відкриється в Teams.</i></span>
        </a>` : ""}
        <button class="support-card" id="home-contact" title="${CONTACT_HINT}">
          <span class="support-ic">${gsvg("mail")}</span>
          <span class="support-txt"><b>Зворотний зв'язок</b>
            <i>${CONTACT_HINT}: ролі й розділи, зміст каталогу, пропозиції.</i></span>
        </button>
      </div>
    </section>`;
  box.querySelector("#home-contact").addEventListener("click", () => openContactForm());
}

// «Зворотний зв'язок»: повідомлення адміністратору хабу. Автор, дата й
// сторінка підставляються самі — людині лишається лише написати.
function contactPageLabel() {
  const h1 = document.querySelector("#view .page-h1");
  const title = h1 ? h1.textContent.trim() : "";
  return title ? `${title} (${nav.dest})` : nav.dest;
}

function openContactForm() {
  const page = contactPageLabel();
  const agent = supportAgentUrl();
  const who = (state.user && (state.user.full_name || state.user.username)) || "";
  const { overlay, close } = catalogModal(`
    <div class="modal res-editor contact-modal">
      <div class="modal-head">
        <h2>Зворотний зв'язок</h2>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      <p class="muted">Питання й пропозиції адміністратору хабу: ролі, розділи, зміст
      каталогу${agent ? `. З технічними питаннями швидше допоможе
        <a href="${esc(agent)}" target="_blank" rel="noopener">AI SD агент</a>` : ""}.</p>
      <label class="contact-field">Повідомлення
        <textarea id="ct-msg" rows="6" maxlength="5000"
          placeholder="Ваше питання чи пропозиція"></textarea></label>
      <p class="contact-meta">Від: <b>${esc(who)}</b> · сторінка: ${esc(page)}</p>
      <p class="error" id="ct-err"></p>
      <div class="row">
        <button class="mdc-act" id="ct-send">Відправити</button>
        <button class="ghost" id="ct-cancel">Скасувати</button>
      </div>
    </div>`);
  const input = overlay.querySelector("#ct-msg");
  input.focus();
  overlay.querySelector("#ct-cancel").addEventListener("click", () => close());
  const send = overlay.querySelector("#ct-send");
  send.addEventListener("click", async () => {
    const message = input.value.trim();
    if (!message) {
      overlay.querySelector("#ct-err").textContent = "Напишіть повідомлення";
      input.focus();
      return;
    }
    send.disabled = true;
    try {
      await api("/contact", { method: "POST", body: { message, page } });
      toast("Дякуємо! Повідомлення надіслано адміністратору");
      close(true);
    } catch (err) {
      overlay.querySelector("#ct-err").textContent = err.message;
      send.disabled = false;
    }
  });
}

async function viewCatalog() {
  const head = hubHead();
  const view = $("#view");
  view.innerHTML = `
    <div class="catalog">
      <div class="page-head">
        <div class="page-head-txt">
          ${head.crumb ? `<div class="page-crumb">${head.crumb}</div>` : ""}
          <h1 class="page-h1">${head.title}</h1>
          <p class="page-sub">${esc(head.sub)}</p>
        </div>
        ${PAGE_ACTS_HTML}
      </div>
      <div class="cat-toolbar">
        <div class="cat-kinds" id="cat-kinds"></div>
        <div class="cat-tools">
          <div class="cat-filter-wrap">
            <button class="ghost cat-filter-btn" id="cat-filter-btn">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                   stroke-linecap="round" aria-hidden="true"><path d="M3 6h18M6 12h12M10 18h4"/></svg>
              <span>Фільтри</span><b id="cat-filter-n" class="hidden"></b>
            </button>
            <div class="cat-filters hidden" id="cat-filters"></div>
          </div>
        </div>
      </div>
      <div class="cat-active" id="cat-active"></div>
      <div class="cat-grid" id="cat-grid"></div>
    </div>`;

  paintCatalog();
  bindPageActs();
  bindFilterPopover();
}

// Панель фільтрів відкривається кнопкою й закривається кліком поза нею:
// вісім списків не мають займати екран, поки вони не потрібні.
function bindFilterPopover() {
  const btn = $("#cat-filter-btn"), box = $("#cat-filters");
  if (!btn || !box) return;
  const close = () => { box.classList.add("hidden"); btn.classList.remove("on"); };
  btn.addEventListener("click", e => {
    e.stopPropagation();
    const open = box.classList.toggle("hidden");
    btn.classList.toggle("on", !open);
  });
  document.addEventListener("click", e => {
    if (!box.contains(e.target) && !btn.contains(e.target)) close();
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
}

// «Мій розвиток» (фідбек тестування, п. 5 і 8): рівень і дата, наступний
// крок і навчальні маршрути — окремою сторінкою, щоб головна лишалася
// короткою. Рівень визначає корпоративне тестування поза хабом, а вносить
// адміністратор (п. 4): сама людина його не змінює, тож кнопок тут немає.
const LEVEL_SOURCE = "Рівень визначається за результатами корпоративного тестування";

async function viewDevelopment() {
  const [paths, maturity] = await Promise.all([
    api("/paths").catch(() => []),
    api("/catalog/maturity").catch(() => catalogState.maturity),
  ]);
  catalogState.maturity = maturity;
  const m = maturity;

  const ladder = m && m.levels.length ? `<ol class="dev-ladder">${m.levels.map((l, i) => {
    const at = m.current ? m.levels.findIndex(x => x.id === m.current.id) : -1;
    const cls = i === at ? "on" : i < at ? "done" : "";
    return `<li class="${cls}" title="${esc(l.description || "")}"><span>${esc(l.name)}</span></li>`;
  }).join("")}</ol>` : "";

  const levelHtml = !m ? `<p class="muted">Не вдалося завантажити рівні. Оновіть сторінку.</p>`
    : m.current ? `
      <div class="dev-level-main">
        <span class="dev-eyebrow">Ваш рівень</span>
        <b class="dev-level-name">${esc(m.current.name)}</b>
        <p class="muted">${esc(m.current.description || "")}</p>
        ${m.assessed_at ? `<span class="dev-date">Визначено ${esc(dateUa(m.assessed_at))}</span>`
          : `<span class="dev-date">${LEVEL_SOURCE}</span>`}
      </div>` : `
      <div class="dev-level-main">
        <span class="dev-eyebrow">Ваш рівень</span>
        <b class="dev-level-name">Ще не визначено</b>
        <p class="muted">${LEVEL_SOURCE}. Щойно адміністратор внесе результат, хаб
        почне підбирати матеріали саме під ваш рівень.</p>
      </div>`;

  const nextHtml = !m || !m.current ? "" : m.next
    ? `<p class="lvl-next dev-next"><b>Наступний крок — ${esc(m.next.name)}:</b> ${esc(m.next_hint || "")}</p>`
    : `<p class="lvl-next dev-next">Ви на найвищому рівні — тепер ваші рішення стають
       матеріалами для інших: поділіться ними в каталозі.</p>`;
  const itemsHtml = !m || !m.current || !m.items.length ? "" : `
    <section class="show-block">
      <div class="show-head"><div>
        <h2>Матеріали для рівня ${esc(m.current.name)}</h2>
        <p class="muted">Те, що варто опанувати зараз.</p>
      </div></div>
      <div class="show-row" id="dev-items"></div>
    </section>`;

  $("#view").innerHTML = `
    <div class="page-head">
      <div class="page-head-txt">
        <h1 class="page-h1">Мій розвиток</h1>
        <p class="page-sub">Ваш рівень роботи з AI, наступний крок і навчальні маршрути.</p>
      </div>
    </div>
    <div class="dev">
      <section class="dev-level">${levelHtml}${ladder}${nextHtml}</section>
      ${itemsHtml}
      <section class="show-block">
        <div class="show-head"><div><h2>Навчальні маршрути</h2>
          <p class="muted">Впорядкована послідовність матеріалів — від простого до складного.</p></div></div>
        ${paths.length ? `<div class="path-list">${paths.map(pt => `
          <button class="path-row-item" data-path="${pt.id}">
            <span class="path-ic">${gsvg("path")}</span>
            <span class="path-body">
              <b>${esc(pt.name)}${pt.is_starter ? ` <i class="path-tag">для старту</i>` : ""}</b>
              <i>${esc(pt.description || "")}</i>
              <span class="path-bar"><span style="width:${pt.progress_pct}%"></span></span>
            </span>
            <span class="path-pct">${pt.steps_done}/${pt.steps_total}</span>
          </button>`).join("")}</div>`
          : `<div class="card"><p class="muted">Маршрутів ще немає.</p></div>`}
      </section>
    </div>`;

  if (m && m.current && m.items.length) {
    const row = $("#dev-items");
    m.items.forEach(r => row.appendChild(catalogCard(normResource(r))));
  }
  document.querySelectorAll("#view [data-path]").forEach(b =>
    b.addEventListener("click", () => openPath(Number(b.dataset.path))));
}

// Облік переглядів (FR-11). Аналітика не має заважати роботі, тому запит
// відправляємо «у фоні» й свідомо ігноруємо і результат, і помилку.
function trackView(targetType, targetId, targetName) {
  api("/catalog/views", { method: "POST",
    body: { target_type: targetType, target_id: targetId, target_name: targetName || null },
  }).catch(() => {});
}

// Матеріали поточного розділу й колекції (або всі, якщо нічого не обрано).
function sectionScope() {
  if (catalogState.scope === "recent") return recentItems();
  let items = catalogState.items;
  if (catalogState.favOnly) {
    items = items.filter(i => catalogState.favorites[i.item_type].has(i.id));
  }
  if (catalogState.sectionId !== null) {
    items = items.filter(i => i.sectionId === catalogState.sectionId);
  }
  if (catalogState.folderId !== null) {
    items = items.filter(i => i.folderId === catalogState.folderId);
  }
  return items;
}

// ---- Лог пошукових запитів (BR-08, FR-09) ----
// Пишемо запит через паузу після набору, щоб не логувати кожну літеру.
function scheduleSearchLog() {
  clearTimeout(catalogState.searchTimer);
  catalogState.searchLogId = null;
  const query = catalogState.query.trim();
  if (query.length < 3) return;
  catalogState.searchTimer = setTimeout(async () => {
    try {
      const res = await api("/catalog/search-log", { method: "POST", body: {
        query,
        results_count: currentMatches().length,
        section_id: catalogState.sectionId,
        kind: catalogState.kind === "all" ? null : catalogState.kind,
      } });
      catalogState.searchLogId = res.id;
    } catch (e) { /* аналітика не має заважати роботі */ }
  }, 800);
}

// Позначає, що після пошуку користувач відкрив матеріал (Search Success Rate).
function markSearchOpened(item) {
  const logId = catalogState.searchLogId;
  if (!logId) return;
  catalogState.searchLogId = null;
  api(`/catalog/search-log/${logId}/opened`, { method: "POST",
    body: { item_type: item.item_type, item_id: item.id } }).catch(() => {});
}

// Порядок видів наповнення: навички плюс те, що є в довіднику.
// Константи CAT_KINDS лишаються джерелом іконок для типових видів і
// запасним варіантом, поки довідник не наповнено.
function catalogKinds() {
  const fromDict = (catalogState.types || [])
    .filter(t => t.is_active && t.code)
    .map(t => t.code);
  return ["skill", ...(fromDict.length
    ? fromDict : CAT_ORDER.filter(k => k !== "skill"))];
}

// Підпис і іконка виду: спершу довідник, потім константи, потім розумний дефолт.
function kindMeta(code) {
  const term = (catalogState.types || []).find(t => t.code === code);
  const known = CAT_KINDS[code];
  if (!term) return known || { label: code, one: code, accent: "ink", icon: CAT_ALL_ICON };
  return {
    label: term.name,
    // Довідник зберігає назву в множині («Інструкції»); там, де йдеться про
    // одну картку, вбудовані види мають однину, а доданий адміністратором
    // вид — лише те, що є.
    one: (known && known.one) || term.name,
    accent: term.color || (known && known.accent) || "ink",
    icon: (known && known.icon) || CAT_ALL_ICON,
    emoji: term.icon_emoji || null,
  };
}

// Пігулки-фільтри за видом наповнення (з лічильниками).
function renderKindPills() {
  const box = $("#cat-kinds");
  if (!box) return;
  const scope = sectionScope();
  // Нема чим керувати — нема й рядка керування: «Усі 0» поруч із кнопкою
  // «Фільтри» на порожній сторінці лише додає шуму.
  const bar = document.querySelector(".cat-toolbar");
  if (bar) bar.classList.toggle("hidden", !scope.length);
  const order = catalogKinds();
  const counts = { all: scope.length };
  order.forEach(k => counts[k] = scope.filter(i => i.kind === k).length);

  // Сегмент несе назву й кількість; іконка тут була б третім сигналом
  // про те саме, тому її немає.
  const seg = (id, label) => `
    <button class="cat-kind${catalogState.kind === id ? " active" : ""}" data-kind="${id}">
      <span>${esc(label)}</span><b class="cat-kind-n">${counts[id] || 0}</b>
    </button>`;

  box.innerHTML = seg("all", "Усі")
    + order.filter(k => counts[k] > 0).map(k => seg(k, kindMeta(k).label)).join("");

  box.querySelectorAll(".cat-kind").forEach(b =>
    b.addEventListener("click", () => {
      catalogState.kind = b.dataset.kind;
      resetCatFilters();
      paintCatalog();
    }));
}

// Перемальовує весь каталог одним викликом: навігація, фільтри, сітка.
// Усі вони залежать одне від одного (лічильники рахуються за поточним зрізом),
// тому окремі виклики легко розсинхронізувати.
function paintCatalog() {
  renderHomeLevel();
  renderPersonal();
  renderKindPills();
  renderCatFilters();
  renderActiveFilters();
  renderCatalogGrid();
}

// Рядок під привітанням на головній: де я зараз і вхід у «Мій розвиток».
// Видимий рівень підштовхує розвиватися (фідбек, п. 5).
function renderHomeLevel() {
  const box = $("#home-level");
  if (!box) return;
  const m = catalogState.maturity;
  box.classList.toggle("hidden", !m);
  if (!m) return;
  box.innerHTML = m.current
    ? `<span>Ваш рівень: <b>${esc(m.current.name)}</b></span>
       <button class="link-btn" id="home-dev">Мій розвиток →</button>`
    : `<span>Рівень роботи з AI ще не визначено.</span>
       <button class="link-btn" id="home-dev">Мій розвиток →</button>`;
  box.querySelector("#home-dev").addEventListener("click", () => go("hub:dev"));
}

// Блоки головної: «Рекомендовано вам» і «Нещодавно додані». Те, що вже є
// серед рекомендацій, у новинках не повторюємо.
function renderPersonal() {
  const box = $("#cat-personal");
  if (!box) return;
  const recs = catalogState.recommendations;
  const recIds = new Set(recs.map(r => r.id));
  const fresh = catalogState.items
    .filter(i => !(i.item_type === "resource" && recIds.has(i.id)))
    .sort((a, b) => b.when - a.when)
    .slice(0, RECENT_ADDED_LIMIT);

  box.innerHTML = (recs.length ? `
    <section class="show-block">
      <div class="show-head">
        <div><h2>Рекомендовано вам</h2>
          <p class="muted">Дібрано за вашим рівнем, підрозділом та тим, що ви вже дивилися.</p></div>
      </div>
      <div class="show-row" id="rec-row"></div>
    </section>` : "") + `
    <section class="show-block">
      <div class="show-head">
        <div><h2>Нещодавно додані</h2>
          <p class="muted">Нові матеріали каталогу — найсвіжіші першими.</p></div>
        <button class="ghost-act" id="home-all">Усі матеріали</button>
      </div>
      ${fresh.length ? `<div class="show-row" id="fresh-row"></div>`
        : catalogState.items.length
        ? `<p class="muted">Усі нові матеріали вже серед рекомендованих вище.</p>`
        : `<p class="muted">Каталог поки що порожній.</p>`}
    </section>`;

  if (recs.length) {
    const row = box.querySelector("#rec-row");
    recs.forEach(r => {
      const card = catalogCard(normResource(r));
      card.appendChild(el(`<div class="rec-why">
          <span>${esc(r.recommend_reason || "")}</span>
          <button class="rec-hide" data-hide="${r.id}" title="Більше не показувати"
                  aria-label="Приховати">×</button>
        </div>`));
      row.appendChild(card);
    });
    row.querySelectorAll("[data-hide]").forEach(b =>
      b.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const id = Number(b.dataset.hide);
        try {
          await api("/catalog/recommendations/hide", { method: "POST",
            body: { item_id: id } });
          catalogState.recommendations =
            catalogState.recommendations.filter(x => x.id !== id);
          renderPersonal();
        } catch (err) { toast(err.message, "err"); }
      }));
  }
  const freshRow = box.querySelector("#fresh-row");
  if (freshRow) fresh.forEach(i => freshRow.appendChild(catalogCard(i)));
  box.querySelector("#home-all").addEventListener("click", () => go("hub:all"));
}

function resetCatFilters() {
  Object.keys(catalogState.filters).forEach(k => catalogState.filters[k] = "");
  // У «Обраному» позначка «лише обране» — це сама сторінка, а не фільтр:
  // вибір виду чи «Скинути всі» звужують обране, а не відкривають увесь
  // каталог (фідбек тестування, п. 2).
  catalogState.favOnly = catalogState.scope === "fav";
}

// Доступні фільтри: менеджерські показуємо лише менеджерам.
function activeFilterDefs() {
  const manager = isModerator();
  return CAT_FILTERS.filter(f => !f.manager || manager);
}

// Значення фільтра для матеріалу — завжди масив (тег може бути не один).
function filterValues(def, item) {
  const raw = def.value(item);
  return (Array.isArray(raw) ? raw : [raw]).filter(Boolean);
}

// Матеріали, що проходять усі фільтри, окрім одного — щоб порахувати варіанти
// саме для нього. Без цього списки показували б значення, які дають нуль карток.
function scopeExcept(skipKey) {
  const query = catalogState.query.trim().toLowerCase();
  return sectionScope().filter(i => {
    if (catalogState.kind !== "all" && i.kind !== catalogState.kind) return false;
    if (catalogState.favOnly && !catalogState.favorites[i.item_type].has(i.id)) return false;
    for (const def of activeFilterDefs()) {
      const picked = catalogState.filters[def.key];
      if (!picked || def.key === skipKey) continue;
      if (!filterValues(def, i).includes(picked)) return false;
    }
    if (!query) return true;
    return [i.name, i.description, i.category, i.author, i.owner, i.tools,
            ...(i.tags || [])].some(v => (v || "").toLowerCase().includes(query));
  });
}

// Панель фільтрів за атрибутами картки (BR-06, FR-06).
// Список показуємо лише тоді, коли є з чого вибирати: два й більше значень.
function renderCatFilters() {
  const box = $("#cat-filters");
  if (!box) return;
  const selects = activeFilterDefs().map(def => {
    const picked = catalogState.filters[def.key];
    const values = new Set();
    scopeExcept(def.key).forEach(i => filterValues(def, i).forEach(v => values.add(v)));
    if (picked) values.add(picked);   // активне значення лишається у списку
    const sorted = [...values].sort((a, b) => a.localeCompare(b, "uk"));
    if (sorted.length < 2 && !picked) return "";
    return `<label class="cat-sel${picked ? " active" : ""}">
      <span>${esc(def.label)}</span>
      <select data-filter="${def.key}">
        <option value="">Усі</option>
        ${sorted.map(v => `<option value="${esc(v)}"${
          v === picked ? " selected" : ""}>${esc(v)}</option>`).join("")}
      </select></label>`;
  }).join("");

  const active = Object.values(catalogState.filters).filter(Boolean).length;
  box.innerHTML = `<div class="cf-head"><b>Фільтри</b>${
    active ? `<button id="cf-reset">Скинути</button>` : ""}</div>`
    + (selects || `<div class="cf-empty">Для цього зрізу фільтрів немає.</div>`);

  // Кількість активних фільтрів видно на кнопці, навіть коли поповер закрито.
  const badge = $("#cat-filter-n");
  if (badge) {
    badge.textContent = active || "";
    badge.classList.toggle("hidden", !active);
  }

  box.querySelectorAll("[data-filter]").forEach(sel =>
    sel.addEventListener("change", () => {
      catalogState.filters[sel.dataset.filter] = sel.value;
      paintCatalog();
    }));
  const reset = $("#cf-reset");
  if (reset) reset.addEventListener("click", () => { resetCatFilters(); paintCatalog(); });
}

// Рядок під фільтрами: скільки знайдено, що саме звужує вибірку і як скинути.
function renderActiveFilters() {
  const box = $("#cat-active");
  if (!box) return;
  const chips = [];
  if (catalogState.kind !== "all") {
    // kindMeta, а не CAT_KINDS: вид, доданий адміністратором, теж має свій чип.
    chips.push(["kind", `Вид: ${kindMeta(catalogState.kind).label}`]);
  }
  activeFilterDefs().forEach(def => {
    const picked = catalogState.filters[def.key];
    if (picked) chips.push([def.key, `${def.label}: ${picked}`]);
  });
  if (catalogState.favOnly && catalogState.scope !== "fav") {
    chips.push(["fav", "Лише обране"]);
  }
  if (catalogState.query.trim()) chips.push(["query", `Пошук: ${catalogState.query.trim()}`]);

  const found = currentMatches().length;
  box.classList.toggle("hidden", !chips.length);
  if (!chips.length) return;
  box.innerHTML = `<span class="cat-found">Знайдено <b>${found}</b></span>`
    + chips.map(([key, label]) =>
        `<button class="cat-tagf" data-drop="${esc(key)}">${esc(label)}<i>×</i></button>`).join("")
    + `<button class="cat-reset" id="cat-reset">Скинути всі</button>`;

  box.querySelectorAll("[data-drop]").forEach(b =>
    b.addEventListener("click", () => {
      const key = b.dataset.drop;
      if (key === "section") { go("hub:home"); return; }
      else if (key === "folder") { go(`hub:sec:${catalogState.sectionId}`); return; }
      else if (key === "kind") catalogState.kind = "all";
      else if (key === "fav") catalogState.favOnly = false;
      else if (key === "query") setGlobalQuery("");
      else catalogState.filters[key] = "";
      paintCatalog();
    }));
  $("#cat-reset").addEventListener("click", () => {
    catalogState.kind = "all";
    setGlobalQuery("");
    resetCatFilters();
    if (catalogState.sectionId !== null || catalogState.folderId !== null) {
      go("hub:home");
    } else {
      paintCatalog();
    }
  });
}

// Матеріали, що відповідають усім активним фільтрам і пошуковому запиту.
function currentMatches() {
  return scopeExcept(null);
}

function renderCatalogGrid() {
  const grid = $("#cat-grid");
  if (!grid) return;
  const query = catalogState.query.trim().toLowerCase();
  const list = currentMatches();

  grid.innerHTML = "";
  if (!catalogState.items.length) {
    grid.innerHTML = `<p class="muted cat-empty">Каталог поки що порожній.</p>`;
    return;
  }
  if (!list.length && catalogState.scope === "fav" && !query) {
    return;   // підзаголовок сторінки вже пояснює, як наповнити «Обране»
  }
  if (!list.length) {
    // Глухий кут — найгірше, що може статися в базі знань. Тому замість
    // «нічого не знайдено» даємо два наступні кроки: спитати помічника
    // (він покаже найближче за змістом) або віддати задачу у воронку ідей.
    grid.innerHTML = `<div class="cat-empty">
        <p class="muted">Нічого не знайдено${
          query ? ` за запитом «${esc(catalogState.query)}»` : ""}.</p>
        ${query ? `<div class="cat-empty-acts">
          <button class="mdc-act" id="empty-assistant">Спитати помічника</button>
          <button class="ghost-act" id="empty-idea">Поділитися ідеєю</button>
        </div>` : ""}
      </div>`;
    const askBtn = $("#empty-assistant");
    if (askBtn) askBtn.addEventListener("click",
      () => openAssistant(catalogState.query.trim()));
    const ideaBtn = $("#empty-idea");
    if (ideaBtn) ideaBtn.addEventListener("click", () => openIdeaForm());
    return;
  }
  list.forEach(i => grid.appendChild(catalogCard(i)));
}

function skillLogo(s, cls) {
  return s.has_icon
    ? `<img class="${cls}-img" src="${esc(s.icon_url)}" alt="${esc(s.name)}">`
    : `<span class="${cls}-default">${DEFAULT_SKILL_ICON}</span>`;
}

// Плитка-іконка картки: власна PNG, емодзі або типова іконка виду.
function catTile(it, cls = "mdc-tile") {
  const known = CAT_KINDS[it.kind];
  const inner = it.iconUrl
    ? `<img src="${esc(it.iconUrl)}" alt="">`
    : (known
        ? `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
                stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${known.icon}</svg>`
        // Вид, доданий адміністратором, власного гліфа не має — показуємо
        // емодзі з довідника (AKH-17), інакше зникла б і сама позначка.
        : (it.emoji ? `<span class="mdc-emoji">${esc(it.emoji)}</span>`
                    : `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
                            stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${GLYPH.box}</svg>`));
  return `<span class="${cls} a-${esc(it.accent)}${it.iconUrl ? " has-img" : ""}">${inner}</span>`;
}

async function toggleFavorite(it) {
  const res = await api("/catalog/favorites", { method: "POST",
    body: { item_type: it.item_type, item_id: it.id } });
  const set = catalogState.favorites[it.item_type];
  if (res.favorited) set.add(it.id); else set.delete(it.id);
  return res.favorited;
}

// Копіює текст у буфер обміну (з фолбеком для несекюрних контекстів).
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* фолбек нижче */ }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.cssText = "position:fixed;left:-9999px;top:0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  ta.remove();
  return ok;
}

function openExternal(it) {
  if (!it.url) { toast("Посилання не задано", "err"); return; }
  api(`/catalog/resources/${it.id}/open`, { method: "POST" }).catch(() => {});
  window.open(it.url, "_blank", "noopener");
}

// Дія на плитці: залежить від виду наповнення.
function cardAction(it) {
  // Навичку з пакетом завантажують одразу з плитки; без пакета — читають.
  if (it.kind === "skill") {
    if (!it.raw.has_package) {
      return { cls: "mdc-act", html: "Детальніше",
               run: async () => { markSearchOpened(it); openSkillModal(it.id); } };
    }
    return { cls: "mdc-act", html: `${ICO_DOWNLOAD}Завантажити`, run: async () => {
      markSearchOpened(it);
      await downloadSkillPackage(it.raw);
    } };
  }
  if (it.kind === "prompt") {
    return { cls: "mdc-act", html: "Копіювати", run: async () => {
      const ok = await copyText(it.body || "");
      toast(ok ? "Промпт скопійовано" : "Не вдалося скопіювати", ok ? "ok" : "err");
      if (ok) api(`/catalog/resources/${it.id}/open`, { method: "POST" }).catch(() => {});
    } };
  }
  if (it.kind === "instruction" || it.kind === "case") {
    return { cls: "mdc-act", html: "Читати",
             run: async () => { markSearchOpened(it); openResourceModal(it.id); } };
  }
  // Адресу MCP-сервера вставляють у конфіг клієнта, а не відкривають у браузері.
  if (it.kind === "mcp") {
    return { cls: "mdc-act", html: "Копіювати endpoint", run: async () => {
      const ok = await copyText(it.url || "");
      toast(ok ? "Endpoint скопійовано" : "Не вдалося скопіювати", ok ? "ok" : "err");
      if (ok) {
        markSearchOpened(it);
        api(`/catalog/resources/${it.id}/open`, { method: "POST" }).catch(() => {});
      }
    } };
  }
  return { cls: "mdc-act", html: it.kind === "agent" ? "Відкрити агента" : "Перейти",
           run: async () => { markSearchOpened(it); openExternal(it); } };
}

// Компактний показ середньої оцінки на картці.
function ratingHtml(it) {
  if (!it.ratingCount) return "";
  return `<span class="mdc-rate" title="Середня оцінка ${it.ratingAvg} з 5 (${it.ratingCount})">
    ${ICO_STAR}<b>${it.ratingAvg}</b><i>(${it.ratingCount})</i></span>`;
}

// Тег — кнопка: клік показує всі матеріали з цим тегом (фідбек, п. 8.4).
function tagButton(tag) {
  return `<button type="button" class="mdc-tag mdc-tag-link" data-tag="${esc(tag)}"
    title="Усі матеріали з тегом «${esc(tag)}»">${esc(tag)}</button>`;
}

function bindTagButtons(root, beforeGo) {
  root.querySelectorAll("[data-tag]").forEach(b => {
    b.addEventListener("keydown", (e) => e.stopPropagation());
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      if (beforeGo) beforeGo();
      showTag(b.dataset.tag);
    });
  });
}

async function showTag(tag) {
  await go("hub:all");
  catalogState.filters.tag = tag;
  paintCatalog();
}

// Автор із позначкою «Експерт» перед ім'ям (фідбек тестування, п. 11).
// Позначка — ознака автора на момент публікації, її зберігає сам матеріал.
function authorHtml(name, expert) {
  if (!name) return "";
  return `${expert ? `<span class="exp-tag" title="Автор має статус «Експерт»">Експерт</span> ` : ""}${
    esc(name)}`;
}

function catalogCard(it) {
  const kind = kindMeta(it.kind);
  const desc = it.description || "";
  const shortDesc = desc.length > DESC_PREVIEW_LEN
    ? desc.slice(0, DESC_PREVIEW_LEN).trimEnd() + "…" : desc;
  const fav = catalogState.favorites[it.item_type].has(it.id);

  const tags = [`<span class="mdc-tag t-${esc(it.accent)}">${esc(kind.one)}</span>`];
  if (it.status === "needs_update") {
    tags.push(`<span class="mdc-tag stale" title="Матеріал очікує актуалізації">Потребує оновлення</span>`);
  }
  if (it.linkScope === "internal") tags.push(`<span class="mdc-tag">Внутрішній</span>`);
  // Три теги матеріалу; клік відкриває всі матеріали з тим самим тегом (фідбек, п. 8).
  (it.tags || []).slice(0, 3).forEach(t => tags.push(tagButton(t)));

  const meta = it.kind === "skill"
    ? `Версія ${esc(it.version)} · ${it.author ? authorHtml(it.author, it.authorExpert) : "—"}`
    : (it.author ? authorHtml(it.author, it.authorExpert) : "&nbsp;");

  const act = cardAction(it);
  const card = el(`<article class="mdc${it.featured ? " featured" : ""}" tabindex="0" role="button" title="Детальніше">
    <div class="mdc-top">
      ${catTile(it)}
      <button class="mdc-fav${fav ? " on" : ""}" title="${fav ? "Прибрати з обраного" : "Додати в обране"}" aria-label="Обране">${ICO_STAR}</button>
    </div>
    <h3 class="mdc-title">${esc(it.name)}</h3>
    <p class="mdc-desc">${esc(shortDesc)}</p>
    <div class="mdc-div"></div>
    <div class="mdc-tags">${tags.join("")}</div>
    <div class="mdc-foot">
      <span class="mdc-meta">${ratingHtml(it)}${meta}</span>
      ${act ? `<button class="${act.cls}">${act.html}</button>` : ""}
    </div>
  </article>`);

  bindTagButtons(card);
  const favBtn = card.querySelector(".mdc-fav");
  // Enter на кнопці всередині картки не має відкривати саму картку.
  favBtn.addEventListener("keydown", (e) => e.stopPropagation());
  favBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    try {
      const on = await toggleFavorite(it);
      favBtn.classList.toggle("on", on);
      favBtn.title = on ? "Прибрати з обраного" : "Додати в обране";
      renderCatFilters();
      renderActiveFilters();
      if (catalogState.favOnly) renderCatalogGrid();
    } catch (err) { toast(err.message, "err"); }
  });

  const actBtn = card.querySelector(".mdc-act");
  if (actBtn && act) {
    actBtn.addEventListener("click", async (e) => {
      e.stopPropagation();  // не відкривати модалку
      try { await act.run(actBtn); }
      catch (err) { toast(err.message, "err"); }
    });
  }

  const open = () => {
    markSearchOpened(it);
    pushRecent(it);
    return it.kind === "skill" ? openSkillModal(it.id) : openResourceModal(it.id);
  };
  card.addEventListener("click", open);
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
  });
  return card;
}

// Обговорення під карткою (BR-13): публічні коментарі й оцінка. Коментарі
// бачать усі, автор матеріалу отримує сповіщення про новий. Оцінка — одна
// на людину: клік по зірці зберігає її одразу, новий клік замінює попередню.
function commentsBlockHtml() {
  const stars = [1, 2, 3, 4, 5].map(n =>
    `<button type="button" class="fb-star" data-star="${n}" aria-label="Оцінка ${n} з 5">${ICO_STAR}</button>`).join("");
  return `<section class="cm" id="cm">
      <div class="cm-head">
        <h3>Обговорення <span class="cm-n" id="cm-n"></span></h3>
        <div class="cm-rate"><span class="cm-rate-k" id="cm-rate-k">Оцініть</span>
          <div class="fb-rate" id="cm-stars">${stars}</div></div>
      </div>
      <div class="cm-list" id="cm-list"><p class="muted cm-empty">Отримую коментарі…</p></div>
      <div class="cm-form">
        <textarea id="cm-text" rows="2" maxlength="5000"
          placeholder="Поділіться досвідом, запитайте автора або підкажіть, що покращити"></textarea>
        <button id="cm-send">Надіслати</button>
      </div>
    </section>`;
}

// `base` — адреса стрічки (`/catalog/resources/1/feedback`, `/skills/2/feedback`);
// `onRated(data)` — щоб картка оновила середню оцінку в заголовку.
function bindComments(overlay, base, { onRated } = {}) {
  const list = overlay.querySelector("#cm-list");
  const stars = [...overlay.querySelectorAll("#cm-stars .fb-star")];
  const rateK = overlay.querySelector("#cm-rate-k");
  let mine = 0;
  const paintStars = (v) => stars.forEach((b, i) => b.classList.toggle("on", i < v));
  const paint = (data) => {
    mine = data.my_rating || 0;
    paintStars(mine);
    rateK.textContent = mine ? `Ваша оцінка: ${mine} з 5` : "Оцініть";
    overlay.querySelector("#cm-n").textContent = data.items.length || "";
    list.innerHTML = data.items.length ? data.items.map(c => `
      <div class="cm-item">
        <span class="cm-ava" aria-hidden="true">${esc((c.author || "?").trim().charAt(0).toUpperCase())}</span>
        <div class="cm-main">
          <div class="cm-meta"><b>${esc(c.author)}</b><span>${timeAgo(c.created_at)}</span>${
            c.rating ? `<span class="cm-stars" title="Оцінка ${c.rating} з 5">${"★".repeat(c.rating)}</span>` : ""}</div>
          <div class="cm-text">${esc(c.message)}</div>
        </div>
        ${c.can_delete ? `<button class="cm-del" data-del="${c.id}" title="Видалити коментар"
          aria-label="Видалити коментар">×</button>` : ""}
      </div>`).join("")
      : `<p class="muted cm-empty">Коментарів ще немає — будьте першим.</p>`;
    list.querySelectorAll("[data-del]").forEach(b => b.addEventListener("click", async () => {
      if (!confirm("Видалити коментар?")) return;
      try { await api(`/catalog/feedback/${b.dataset.del}`, { method: "DELETE" }); load(); }
      catch (err) { toast(err.message, "err"); }
    }));
    if (onRated) onRated(data);
  };
  const load = async () => {
    try { paint(await api(base)); }
    catch (err) { list.innerHTML = `<p class="error">${esc(err.message)}</p>`; }
  };
  stars.forEach((b, i) => {
    b.addEventListener("mouseenter", () => paintStars(i + 1));
    b.addEventListener("click", async () => {
      try { await api(base, { method: "POST", body: { rating: i + 1 } }); toast("Дякуємо за оцінку"); load(); }
      catch (err) { toast(err.message, "err"); }
    });
  });
  overlay.querySelector("#cm-stars").addEventListener("mouseleave", () => paintStars(mine));
  const text = overlay.querySelector("#cm-text");
  const send = overlay.querySelector("#cm-send");
  send.addEventListener("click", async () => {
    const message = text.value.trim();
    if (!message) { text.focus(); return; }
    send.disabled = true;
    try { await api(base, { method: "POST", body: { message } }); text.value = ""; await load(); }
    catch (err) { toast(err.message, "err"); }
    finally { send.disabled = false; }
  });
  load();
}

// Блок керування в картці: автор бачить «Ваш матеріал», менеджер розділу
// чужого — «Модерація». Стоїть першим у картці: менеджер, що прийшов зі
// сповіщення, має одразу бачити дії, а не гортати до них. Ті самі дії, що й
// у списках «Наповнення каталогу» та «Модерація».
function manageBarHtml(ref) {
  const own = isOwnItem(ref), mod = canModerate(ref.raw);
  if (!(own || mod)) return "";
  const btn = (m, label, cls = "") => `<button class="ghost-act${cls}" data-m="${m}">${label}</button>`;
  const acts = [btn("edit", `${gsvg("pen")}Редагувати`)];
  if (ref.status === "review") {
    acts.push(...(mod ? [btn("approve", "Схвалити", " accent-act"), btn("return", "Повернути")]
                      : [btn("withdraw", "Забрати з розгляду")]));
  } else if (itemPublic(ref)) {
    acts.push(btn("unpub", "Зняти з публікації"));
  } else {
    acts.push(btn("pub", mod ? "Опублікувати" : "Подати на розгляд"));
  }
  if (ref.raw.has_pending) {
    acts.push(mod ? btn("changes", "Переглянути зміни", " accent-act")
                  : btn("drop-changes", "Скасувати зміни"));
  }
  acts.push(btn("del", "Видалити", " danger-act"));
  return `<div class="sm-manage">
      <span class="sm-manage-k">${own ? "Ваш матеріал" : "Модерація"}</span>${acts.join("")}
    </div>`;
}
function bindManageBar(overlay, ref, close) {
  const after = async () => { close(true); await ensureHubData(true); go(nav.dest); };
  overlay.querySelectorAll("[data-m]").forEach(b => b.addEventListener("click", () => {
    const m = b.dataset.m;
    if (m === "edit") { close(true); editItem(ref, after); }
    else if (m === "pub" || m === "approve") setItemStatus(ref, "published", after);
    else if (m === "unpub" || m === "return" || m === "withdraw") setItemStatus(ref, "draft", after);
    else if (m === "changes") { close(true); openChangesReview(ref, after); }
    else if (m === "drop-changes") changesAction(ref, "withdraw", "", after);
    else if (m === "del") deleteItem(ref, after);
  }));
}

// Що бачить автор чи менеджер у картці про стан модерації: на розгляді,
// повернуто з причиною, зміни чекають схвалення.
function moderationNoteHtml(ref) {
  const raw = ref.raw, own = isOwnItem(ref), mod = canModerate(raw);
  const out = [];
  if (raw.status === "review") {
    out.push(own && !mod
      ? `<p class="sm-draft review">На розгляді — менеджер розділу перевірить матеріал, і ви
         отримаєте сповіщення про рішення.</p>`
      : `<p class="sm-draft review">Подано на розгляд${raw.submitted_at ? ` ${timeAgo(raw.submitted_at)}` : ""}.
         Схваліть публікацію або поверніть автору з причиною.</p>`);
  } else if (raw.status === "draft" && raw.review_note) {
    const reason = /[.!?…]$/.test(raw.review_note.trim()) ? raw.review_note.trim() : `${raw.review_note.trim()}.`;
    out.push(`<p class="sm-draft returned"><b>Повернуто на доопрацювання:</b> ${esc(reason)}${
      own ? " Виправте й подайте на розгляд знову." : ""}</p>`);
  } else if (raw.status === "draft") {
    out.push(`<p class="sm-draft">Чернетка — у каталозі її не видно${
      own ? `. ${mod ? "Опублікуйте" : "Подайте на розгляд"}, коли матеріал готовий.`
          : ": бачать лише автор і менеджери розділу."}</p>`);
  }
  if (raw.has_pending) {
    out.push(own && !mod
      ? `<p class="sm-draft review">Ваші зміни на розгляді: у каталозі поки попередня версія.</p>`
      : `<p class="sm-draft review">Автор запропонував зміни до опублікованого — перегляньте їх.</p>`);
  }
  return out.join("");
}

// Спільний каркас модалки каталогу: повертає overlay і функцію закриття.
// beforeClose() → false зупиняє закриття (Escape, клік поза вікном, хрестик):
// форма з набраним текстом не має зникати від випадкового кліку. close(true)
// закриває без запитань — для успішного збереження.
function catalogModal(innerHtml, { beforeClose } = {}) {
  const overlay = el(`<div class="modal-overlay">${innerHtml}</div>`);
  document.body.appendChild(overlay);
  const close = (force) => {
    if (!force && beforeClose && !beforeClose()) return;
    overlay.remove(); document.removeEventListener("keydown", onKey);
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  const x = overlay.querySelector(".modal-x");
  if (x) x.addEventListener("click", close);
  return { overlay, close };
}

// Детальна картка ресурсу: промпт/інструкція — з текстом, агент/посилання — з URL.
// ------- Мій контент і нагадування про перегляд (AKH-18) -------

// Кнопку показуємо лише тим, у кого справді є свій контент: інакше вона
// займає місце в кожного, нікуди не ведучи.
async function paintMyContentButton() {
  const btn = $("#cat-my-content");
  if (!btn) return;
  let data;
  try { data = await api("/catalog/my-content"); }
  catch (e) { return; }
  if (!data.items.length) return;
  const pending = data.overdue + data.soon;
  btn.textContent = pending ? `Мій контент · ${pending}` : "Мій контент";
  btn.classList.toggle("warn", data.overdue > 0);
  btn.classList.remove("hidden");
  btn.addEventListener("click", () => openMyContent());
}

async function openMyContent() {
  let data, notes;
  try {
    [data, notes] = await Promise.all([
      api("/catalog/my-content"), api("/catalog/notifications"),
    ]);
  } catch (err) { toast(err.message, "err"); return; }

  const stateLabel = { overdue: "прострочено", soon: "скоро", ok: "актуальний" };
  const row = (r) => `
    <div class="own-row ${esc(r.review_state)}" data-own="${r.id}">
      <div class="own-main">
        <div class="own-name">${esc(r.name)}</div>
        <div class="own-sub">${esc(stateLabel[r.review_state])}${
          r.next_review_at ? ` · перегляд ${dateUa(r.next_review_at)}` : ""}${
          r.status === "needs_update" ? " · потребує оновлення" : ""}</div>
      </div>
      <div class="own-acts">
        <button class="small" data-confirm="${r.id}">Актуальний</button>
        ${canModerate(r) || r.created_by === state.user.id ? `<select class="small own-status" data-st="${r.id}">
          ${statusChoices(r.status, { item: r }).map(([v, m]) =>
            `<option value="${v}"${v === r.status ? " selected" : ""}>${esc(m.label)}</option>`).join("")}
        </select>` : ""}
      </div>
    </div>`;

  const { overlay, close } = catalogModal(`
    <div class="modal sm-modal">
      <div class="modal-head">
        <h2>Мій контент</h2>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      ${notes.items.length ? `<div class="note-list">${notes.items.slice(0, 5).map(n => `
        <div class="note-row${n.is_read ? "" : " unread"}">
          <b>${esc(n.title)}</b><span>${esc(n.body || "")}</span>
        </div>`).join("")}</div>` : ""}
      <p class="muted">Матеріали, за які ви відповідаєте. Підтвердьте, що вони актуальні,
      або оновіть — просто звідси.</p>
      <div class="own-list">${data.items.map(row).join("")}</div>
      <hr class="md-hr">
      <label class="ind-toggle">
        <input type="checkbox" id="own-digest"${data.email_digest ? " checked" : ""}>
        <span>Щотижневий підсумок на пошту</span>
      </label>
    </div>`);

  // Відкрили список — нагадування вважаються прочитаними.
  if (notes.unread) api("/catalog/notifications/read", { method: "POST" }).catch(() => {});

  const reload = () => { close(); openMyContent(); };

  overlay.querySelectorAll("[data-confirm]").forEach(b =>
    b.addEventListener("click", async () => {
      try {
        await api(`/catalog/resources/${b.dataset.confirm}/confirm-review`,
                  { method: "POST", body: { months: 6 } });
        toast("Актуальність підтверджено");
        reload();
      } catch (err) { toast(err.message, "err"); }
    }));

  overlay.querySelectorAll(".own-status").forEach(sel =>
    sel.addEventListener("change", async () => {
      try {
        await api(`/catalog/resources/${sel.dataset.st}/status`,
                  { method: "POST", body: { status: sel.value } });
        toast(`Статус: ${RES_STATUS_META[sel.value].label.toLowerCase()}`);
        reload();
      } catch (err) { toast(err.message, "err"); reload(); }
    }));

  overlay.querySelector("#own-digest").addEventListener("change", async (ev) => {
    try {
      await api("/catalog/my-content/email-digest", { method: "POST",
        body: { enabled: ev.target.checked } });
      toast(ev.target.checked ? "Підсумок увімкнено" : "Підсумок вимкнено");
    } catch (err) { toast(err.message, "err"); }
  });
}

// ---------- Додати матеріал: майстер «вид → зміст → опис» ----------
//
// Повний редактор менеджера має два десятки полів: для того, хто просто
// хоче поділитися промптом чи інструкцією, це стіна. Майстер веде трьома
// кроками й питає лише те, без чого картка не має сенсу; решту метаданих
// менеджер за потреби додає згодом. Будь-хто публікує одразу або зберігає
// чернетку. Той самий майстер править власний матеріал автора
// (opts.resource).

const WIZ_HINTS = {
  prompt: "Готовий запит до моделі: колеги скопіюють і застосують",
  instruction: "Покроковий порядок дій — текстом, з Word або PDF",
  case: "Що зробили, як і з яким результатом — досвід, який можна повторити",
  agent: "Посилання на готового агента: Copilot Studio, Azure тощо",
  mcp: "Адреса MCP-сервера і як підключити його до клієнта",
  link: "Корисний зовнішній сервіс або внутрішній ресурс компанії",
  skill: "Пакет .zip або .skill зі SKILL.md: назва, опис і версія беруться з нього",
};
// Файли, з яких сервер уміє витягти текст; решта стає вкладенням до картки.
const WIZ_TEXT_RE = /\.(txt|md|markdown|text|csv|json|ya?ml|docx|pdf)$/i;
const WIZ_TEXT_ACCEPT = ".txt,.md,.markdown,.text,.csv,.json,.yaml,.yml,.docx,.pdf";

// Поведінка виду — з довідника; для порожньої бази така сама, як у редакторі.
function wizBehaviour(term, code) {
  if (term) return term;
  return {
    requires_url: ["agent", "mcp", "link"].includes(code),
    requires_body: ["prompt", "instruction", "case"].includes(code),
    has_scope: ["mcp", "link"].includes(code),
    url_label: code === "mcp" ? "Endpoint сервера" : "Посилання",
    body_label: code === "mcp" ? "Як підключити" : "Текст",
  };
}

async function uploadResourceFile(resourceId, file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`${API}/catalog/resources/${resourceId}/files`, {
    method: "POST", headers: { Authorization: `Bearer ${state.token}` }, body: fd });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.message || "Не вдалося завантажити файл");
  return data;
}

async function extractTextFromFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`${API}/catalog/resources/extract-text`, {
    method: "POST", headers: { Authorization: `Bearer ${state.token}` }, body: fd });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.message || "Не вдалося прочитати файл");
  return data;
}

// Назва з імені файлу: «analiz_tenderu-v2.docx» → «analiz tenderu v2».
function nameFromFile(filename) {
  return (filename || "").replace(/\.[^.]+$/, "").replace(/[_\-]+/g, " ")
    .replace(/\s+/g, " ").trim().slice(0, 120);
}
// Назва з першого рядка тексту — без решітки Markdown-заголовка.
function nameFromBody(body) {
  const line = (body || "").split("\n").map(s => s.trim()).find(Boolean) || "";
  return line.replace(/^#+\s*/, "").slice(0, 120);
}
// Пропозиція назви: заголовок документа кращий за ім'я файлу («perevirka_v2»),
// а ім'я файлу — за перший-ліпший рядок тексту.
function suggestName(body, filename) {
  if (/^\s*#/.test(body || "")) return nameFromBody(body);
  return (filename && nameFromFile(filename)) || nameFromBody(body);
}

// Матеріал із тією самою назвою (без огляду на регістр, лапки й розділові
// знаки) серед опублікованих і власних чернеток. Дубль «Питання та відповіді
// (FAQ)» у тестуванні з'явився саме так (фідбек, п. 2).
function sameNameMaterial(name, exceptId) {
  const norm = (v) => String(v || "").toLowerCase()
    .replace(/[«»"'„“”‘’`.,:;!?()\[\]{}\-–—_/]/g, " ").replace(/\s+/g, " ").trim();
  const key = norm(name);
  if (!key) return null;
  const pool = catalogState.items.filter(i => i.item_type === "resource")
    .map(i => ({ id: i.id, name: i.name, author: i.author, draft: false }))
    .concat((catalogState.mine || []).filter(r => r.status === "draft")
      .map(r => ({ id: r.id, name: r.name, author: r.author || "", draft: true })));
  return pool.find(x => x.id !== exceptId && norm(x.name) === key) || null;
}

async function openContentWizard(opts = {}) {
  const existing = opts.resource || null;
  let terms = [], sections = [], folders = [], cats = [];
  try {
    [terms, sections, folders, cats] = await Promise.all([
      api("/catalog/terms?kind=material_type"), api("/catalog/sections"),
      api("/catalog/folders"), api("/categories"),
    ]);
  } catch (e) { /* довідники не обов'язкові — майстер працює і без них */ }
  const typeTerms = terms.filter(t => t.is_active);

  // Перелік видів: з довідника в порядку CAT_ORDER, невідомі коди — у кінці.
  const codes = typeTerms.length ? typeTerms.map(t => t.code)
                                 : CAT_ORDER.filter(k => k !== "skill");
  const ordered = CAT_ORDER.filter(k => codes.includes(k))
    .concat(codes.filter(k => !CAT_ORDER.includes(k)));
  const kindEntry = (code) => {
    const term = typeTerms.find(t => t.code === code) || null;
    const meta = kindMeta(code);
    // Довідник зберігає назву виду в множині («Промпти»); на плитці — однина.
    const one = (CAT_KINDS[code] && CAT_KINDS[code].one) || meta.one;
    return { code, term, one, accent: meta.accent, emoji: meta.emoji || null,
             hint: WIZ_HINTS[code] || (term && term.description) || "" };
  };
  const kinds = ordered.map(kindEntry);
  // Вид чернетки могли тим часом приховати — редагувати її все одно треба.
  if (existing && !kinds.some(k => k.code === existing.resource_type)) {
    kinds.push(kindEntry(existing.resource_type));
  }
  // Навичку створює пакет, а не форма: її метадані беруться зі skill.md.
  if (!existing) {
    kinds.push({ code: "skill", one: "Навичка", accent: "red", emoji: null,
                 hint: WIZ_HINTS.skill, pkg: true });
  }

  const st = {
    step: existing ? 2 : 1,
    kind: existing ? existing.resource_type : null,
    body: existing ? (existing.body || "") : "",
    url: existing ? (existing.url || "") : "",
    scope: (existing && existing.link_scope) || "external",
    name: existing ? (existing.name || "") : "",
    desc: existing ? (existing.description || "") : "",
    sectionId: existing && existing.section_id ? String(existing.section_id) : "",
    folderId: existing && existing.folder_id ? String(existing.folder_id) : "",
    category: existing ? (existing.category || "") : "",
    tags: existing ? (existing.tags || []).join(", ") : "",
    publish: true,  // яку з двох кнопок натиснули на останньому кроці
    source: null,   // { file, kind, chars, truncated, keep } — файл, з якого взято текст
    files: [],      // вкладення до картки — завантажуються після її створення
    pkg: null,      // пакет навички (лише для виду skill)
  };
  const kindOf = () => kinds.find(k => k.code === st.kind) || null;
  let bodyEditor = null;   // візуальний редактор тексту на кроці «Зміст»
  const dirty = () => !!(st.body.trim() || st.url.trim() || st.name.trim()
                         || st.desc.trim() || st.files.length || st.pkg);

  const { overlay, close } = catalogModal(`
    <div class="modal wiz" role="dialog" aria-labelledby="wiz-title">
      <div class="wiz-head">
        <div class="modal-head">
          <h2 id="wiz-title">${existing ? "Редагувати матеріал" : "Додати матеріал"}</h2>
          <button class="modal-x" aria-label="Закрити">×</button>
        </div>
        <ol class="wiz-steps" id="wiz-steps"></ol>
      </div>
      <div class="wiz-body" id="wiz-body"></div>
      <div class="wiz-foot">
        <button class="ghost" id="wiz-back">Назад</button>
        <p class="error" id="wiz-err"></p>
        <button class="ghost hidden" id="wiz-draft"
                title="Чернетку бачите лише ви й менеджери розділу">Зберегти чернетку</button>
        <button id="wiz-next">Далі</button>
      </div>
    </div>`, { beforeClose: () => !dirty()
      || confirm("Закрити без збереження? Введене буде втрачено.") });

  const body = overlay.querySelector("#wiz-body");
  const err = overlay.querySelector("#wiz-err");
  const backBtn = overlay.querySelector("#wiz-back");
  const nextBtn = overlay.querySelector("#wiz-next");
  const draftBtn = overlay.querySelector("#wiz-draft");
  const setErr = (m) => { err.textContent = m || ""; };
  const tileOf = (k, cls) =>
    catTile({ kind: k.code, accent: k.accent, emoji: k.emoji, iconUrl: null }, cls);

  function paintSteps() {
    const labels = [[1, "Вид"], [2, "Зміст"], [3, "Опис"]];
    overlay.querySelector("#wiz-steps").innerHTML = labels.map(([n, l]) =>
      `<li class="${n < st.step ? "done" : n === st.step ? "active" : ""}"><i>${n}</i>${l}</li>`
    ).join("");
  }

  // ---- крок 1: вид ----
  function paintKinds() {
    body.innerHTML = `
      <p class="wiz-lead">Що ви хочете додати до бази знань?</p>
      <div class="wiz-kinds">${kinds.map(k => `
        <button class="wiz-kind${st.kind === k.code ? " active" : ""}" data-kind="${esc(k.code)}">
          ${tileOf(k, "mdc-tile lg")}
          <span class="wiz-kind-txt"><b>${esc(k.one)}</b><span>${esc(k.hint)}</span></span>
        </button>`).join("")}
      </div>`;
    body.querySelectorAll("[data-kind]").forEach(b => b.addEventListener("click", () => {
      if (st.kind && st.kind !== b.dataset.kind && (st.body || st.url || st.pkg)) {
        if (!confirm("Змінити вид? Уже введений зміст буде очищено.")) return;
        st.body = ""; st.url = ""; st.source = null; st.pkg = null;
      }
      st.kind = b.dataset.kind;
      st.step = 2;
      paint();
    }));
    // Плитка і є дією — окрема кнопка «Далі» тут лише відволікала б.
    backBtn.classList.add("hidden");
    nextBtn.classList.add("hidden");
  }

  // ---- крок 2: зміст ----
  function kindLine() {
    const k = kindOf();
    return `<div class="wiz-kindline">
        ${tileOf(k, "mdc-tile sm")}<span>${esc(k.one)}</span>
        ${existing ? "" : `<button class="plain" id="wiz-rekind">Змінити вид</button>`}
      </div>`;
  }
  function filesBlock() {
    if (existing) {
      return `<p class="wiz-hint">Файли до картки додаються з неї самої: відкрийте картку й натисніть «Додати файл».</p>`;
    }
    return `<div class="wiz-files">
        <div class="wiz-files-head"><span>Файли до картки</span>
          <label class="md-mini quiet file-pick">Додати файл<input type="file" id="wiz-extra" multiple hidden></label></div>
        <div class="wiz-chips" id="wiz-file-list"></div>
        <p class="wiz-hint">Презентація, таблиця, скриншоти — усе, що допоможе колезі повторити рішення.</p>
      </div>`;
  }
  function paintContent() {
    const k = kindOf();
    if (k.pkg) {
      body.innerHTML = `${kindLine()}
        <div class="wiz-drop" id="wiz-drop" tabindex="0">${gsvg("upload")}
          <div><b>${st.pkg ? esc(st.pkg.name) : "Перетягніть пакет навички або <u>оберіть</u>"}</b>
            <span>.zip або .skill зі SKILL.md — назва, версія, автор, опис і категорія беруться з нього</span></div>
          <input type="file" id="wiz-file" hidden accept=".zip,.skill"></div>
        ${placementHtml()}
        <p class="wiz-hint">Іконку, інструкцію та інші деталі можна змінити згодом — з картки навички.</p>`;
      bindDrop(f => { collect(); st.pkg = f; paint(); });
      bindPlacement();
      backBtn.classList.remove("hidden");
      nextBtn.classList.remove("hidden");
      paintSubmitLabel();
      nextBtn.disabled = draftBtn.disabled = !st.pkg;
      return;
    }
    const b = wizBehaviour(k.term, k.code);
    const showBody = !!b.requires_body || !!b.body_label;
    const bodyPh = k.code === "prompt"
      ? "Вставте текст промпту. Місця для підстановки позначайте так: {текст документа}"
      : k.code === "mcp"
      ? "Які інструменти надає сервер і як додати його в Copilot Studio чи іншого клієнта"
      : "Пишіть або вставляйте текст: оформлення з Word чи сайту збережеться. Файл можна перетягнути в рамку вище";
    const urlBlock = !b.requires_url ? "" : `
      <label class="wiz-label">${esc(b.url_label || "Посилання")}
        <input id="wiz-url" type="url" placeholder="https://… або /внутрішній/шлях" value="${esc(st.url)}"></label>
      ${b.has_scope ? `<div class="wiz-seg" id="wiz-scope">
        <button data-v="external"${st.scope === "external" ? ' class="active"' : ""}>Зовнішній сервіс</button>
        <button data-v="internal"${st.scope === "internal" ? ' class="active"' : ""}>Внутрішній ресурс</button>
      </div>` : ""}`;
    const bodyBlock = !showBody ? "" : `
      <div class="wiz-drop" id="wiz-drop" tabindex="0">${gsvg("doc")}
        <div><b>Перетягніть файл сюди або <u>оберіть</u></b>
          <span>.docx, .pdf, .md, .txt — текст з'явиться в редакторі нижче разом з оформленням</span></div>
        <input type="file" id="wiz-file" hidden accept="${WIZ_TEXT_ACCEPT}"></div>
      <div class="wiz-source" id="wiz-source"></div>
      <div class="wiz-label">${esc(b.body_label || "Текст")}${b.requires_body ? "" : " <i>необов'язково</i>"}
        <div id="wiz-editor"></div>
        <span class="wiz-count" id="wiz-count"></span></div>`;
    // MCP: спершу адреса, потім «як підключити»; текстові види — спершу текст.
    body.innerHTML = kindLine()
      + (k.code === "mcp" ? urlBlock + bodyBlock : bodyBlock + urlBlock)
      + filesBlock();

    const rekind = body.querySelector("#wiz-rekind");
    if (rekind) rekind.addEventListener("click", () => { collect(); st.step = 1; paint(); });
    const holder = body.querySelector("#wiz-editor");
    if (holder) {
      // Візуальний редактор: оформлений текст, зберігається Markdown.
      const count = body.querySelector("#wiz-count");
      const upd = (md) => {
        count.textContent = md.length ? `${md.length.toLocaleString("uk-UA")} симв.` : "";
      };
      bodyEditor = RichEditor.create({
        value: st.body, kind: k.code === "prompt" ? "prompt" : "doc",
        placeholder: bodyPh, label: b.body_label || "Текст",
        onChange: (md) => { st.body = md; upd(md); },
        onFiles: (files) => takeTextFile(files[0]),
        onImage: uploadInlineImage,
      });
      bodyEditor.el.id = "wiz-editor";
      // Редагування наявного матеріалу: підписи для його картинок.
      if (existing && existing.id) {
        api(`/catalog/resources/${existing.id}/files`).then(files => {
          registerFileSigs(files.filter(f => f.source === "inline"));
          if (bodyEditor) bodyEditor.refreshImages();
        }).catch(() => {});
      }
      holder.replaceWith(bodyEditor.el);
      upd(st.body);
      bindDrop(takeTextFile);
      paintSource();
    }
    body.querySelectorAll("#wiz-scope button").forEach(bt => bt.addEventListener("click", () => {
      st.scope = bt.dataset.v;
      body.querySelectorAll("#wiz-scope button").forEach(x => x.classList.toggle("active", x === bt));
    }));
    const extra = body.querySelector("#wiz-extra");
    if (extra) extra.addEventListener("change", () => {
      [...extra.files].forEach(f => st.files.push(f));
      extra.value = "";
      paintFiles();
    });
    paintFiles();
    backBtn.classList.remove("hidden");
    nextBtn.classList.remove("hidden");
    nextBtn.textContent = "Далі";
    nextBtn.disabled = false;
  }

  function bindDrop(onFile) {
    const drop = body.querySelector("#wiz-drop"), input = body.querySelector("#wiz-file");
    if (!drop || !input) return;
    drop.addEventListener("click", () => input.click());
    drop.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
    });
    input.addEventListener("change", () => {
      if (input.files[0]) onFile(input.files[0]);
      input.value = "";
    });
    ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => {
      e.preventDefault(); drop.classList.add("over");
    }));
    ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => {
      e.preventDefault(); drop.classList.remove("over");
    }));
    drop.addEventListener("drop", e => {
      const f = e.dataTransfer && e.dataTransfer.files[0];
      if (f) onFile(f);
    });
  }

  async function takeTextFile(file) {
    setErr("");
    if (!WIZ_TEXT_RE.test(file.name)) {
      // Текст із презентації чи таблиці не витягнеться — але сам файл потрібен.
      st.files.push(file);
      paintFiles();
      toast("Текст із такого файлу не витягується — додано як вкладення");
      return;
    }
    const drop = body.querySelector("#wiz-drop");
    drop.classList.add("busy");
    try {
      const data = await extractTextFromFile(file);
      if (!data.text) {
        st.files.push(file);
        paintFiles();
        setErr("У файлі не знайдено тексту (можливо, це скан) — додано як вкладення.");
        return;
      }
      if (bodyEditor.getValue().trim()
          && !confirm("У редакторі вже є текст. Замінити його текстом із файлу?")) return;
      registerFileSigs(data.images);   // картинки з .docx — уже в тексті
      bodyEditor.setValue(data.text);
      st.body = data.text;
      // Оригінал Word/PDF лишаємо при картці: витягнутий текст втрачає оформлення.
      st.source = { file, kind: data.kind, chars: data.chars, truncated: data.truncated,
                    keep: data.kind !== "text" };
      if (!st.name) st.name = suggestName(data.text, file.name);
      paintSource();
      if (data.truncated) setErr("Текст довший за ліміт картки (100 000 символів) і був обрізаний.");
    } catch (e) {
      setErr(e.message);
    } finally {
      drop.classList.remove("busy");
    }
  }

  function paintSource() {
    const box = body.querySelector("#wiz-source");
    if (!box) return;
    if (!st.source) { box.innerHTML = ""; return; }
    const s = st.source;
    box.innerHTML = `
      <span class="wiz-chip" title="${esc(s.file.name)}">${gsvg("doc")}
        <span>З файлу: ${esc(s.file.name)} · ${s.chars.toLocaleString("uk-UA")} симв.</span>
        <button type="button" id="wiz-src-x" aria-label="Прибрати">×</button></span>
      ${existing ? "" : `<label class="wiz-keep"><input type="checkbox" id="wiz-keep"${
        s.keep ? " checked" : ""}>Прикріпити оригінал до картки</label>`}`;
    box.querySelector("#wiz-src-x").addEventListener("click", () => {
      st.source = null; paintSource();
    });
    const keep = box.querySelector("#wiz-keep");
    if (keep) keep.addEventListener("change", () => { st.source.keep = keep.checked; });
  }

  function paintFiles() {
    const list = body.querySelector("#wiz-file-list");
    if (!list) return;
    list.innerHTML = st.files.map((f, i) => `
      <span class="wiz-chip" title="${esc(f.name)}">${gsvg("file")}
        <span>${esc(f.name)}</span><em>${fmtSize(f.size)}</em>
        <button type="button" data-rm="${i}" aria-label="Прибрати">×</button></span>`).join("");
    list.querySelectorAll("[data-rm]").forEach(b => b.addEventListener("click", () => {
      st.files.splice(Number(b.dataset.rm), 1);
      paintFiles();
    }));
  }

  // Розділ і колекція: від розділу залежить, хто розгляне матеріал, тож
  // кнопка завершення каже «Опублікувати» лише тому, хто цей розділ модерує.
  function placementHtml() {
    const opt = (v, label, cur) =>
      `<option value="${esc(v)}"${String(v) === String(cur) ? " selected" : ""}>${esc(label)}</option>`;
    return `<div class="wiz-two">
        <label class="wiz-label">Розділ <i>його менеджер розгляне матеріал</i>
          <select id="wiz-section">${opt("", "— не вказано —", st.sectionId)}${
            sections.filter(s => !s.url).map(s => opt(s.id, s.name, st.sectionId)).join("")}</select></label>
        <label class="wiz-label">Колекція<select id="wiz-folder"></select></label>
      </div>`;
  }
  function bindPlacement() {
    const sectionSel = body.querySelector("#wiz-section");
    const folderSel = body.querySelector("#wiz-folder");
    const syncFolders = (keep) => {
      const inSection = folders.filter(f => String(f.section_id) === sectionSel.value);
      folderSel.innerHTML = `<option value="">— не вказано —</option>` + inSection.map(f =>
        `<option value="${f.id}"${String(f.id) === String(keep || "") ? " selected" : ""}>${esc(f.name)}</option>`).join("");
      folderSel.disabled = !sectionSel.value;
    };
    syncFolders(st.folderId);
    sectionSel.addEventListener("change", () => {
      st.sectionId = sectionSel.value; syncFolders(""); paintSubmitLabel();
    });
  }
  function paintSubmitLabel() {
    const sec = body.querySelector("#wiz-section");
    const sectionId = sec ? sec.value : st.sectionId;
    nextBtn.textContent = existing ? "Зберегти"
      : canModerateSection(sectionId || null) ? "Опублікувати" : "Подати на розгляд";
  }

  // ---- крок 3: опис ----
  // Останній крок завершують дві кнопки внизу: «Зберегти чернетку» й
  // «Опублікувати». Перемикача режиму з тими самими назвами більше немає:
  // його приймали за кнопку, яка «нічого не робить» (фідбек тестування, п. 1).
  function paintDescribe() {
    const opt = (v, label, cur) =>
      `<option value="${esc(v)}"${String(v) === String(cur) ? " selected" : ""}>${esc(label)}</option>`;
    if (!st.name) st.name = suggestName(st.body, st.source && st.source.file.name);
    body.innerHTML = `${kindLine()}
      <label class="wiz-label">Назва
        <input id="wiz-name" type="text" maxlength="200" value="${esc(st.name)}"
          placeholder="Описує задачу, а не інструмент: «Аналіз тендерної документації»"></label>
      <p class="wiz-dup hidden" id="wiz-dup" role="status"></p>
      <label class="wiz-label">Короткий опис
        <textarea id="wiz-desc" rows="2" class="short"
          placeholder="Одне-два речення: що це і кому корисне">${esc(st.desc)}</textarea></label>
      ${placementHtml()}
      <div class="wiz-two">
        <label class="wiz-label">Категорія
          <input id="wiz-cat" type="text" list="wiz-cats" value="${esc(st.category)}" placeholder="Напр. Аналітика">
          <datalist id="wiz-cats">${cats.map(c => `<option value="${esc(c.name)}">`).join("")}</datalist></label>
        <label class="wiz-label">Теги <i>через кому</i>
          <input id="wiz-tags" type="text" value="${esc(st.tags)}" placeholder="RAG, закупівлі"></label>
      </div>`;

    bindPlacement();
    const nameInput = body.querySelector("#wiz-name");
    const paintDup = () => {
      const box = body.querySelector("#wiz-dup");
      const twin = sameNameMaterial(nameInput.value, existing && existing.id);
      box.classList.toggle("hidden", !twin);
      if (!twin) return;
      box.innerHTML = `У каталозі вже є матеріал <b>«${esc(twin.name)}»</b>${
        twin.author ? ` (автор: ${esc(twin.author)})` : ""}${
        twin.draft ? ", це ваша чернетка" : ""}. Якщо це його нова версія, краще
        оновити наявний матеріал, ніж створювати другий із тією самою назвою.`;
    };
    nameInput.addEventListener("input", paintDup);
    paintDup();
    backBtn.classList.remove("hidden");
    nextBtn.classList.remove("hidden");
    paintSubmitLabel();
    nextBtn.disabled = draftBtn.disabled = false;
    body.querySelector("#wiz-name").focus();
  }

  // Поточні значення полів — у стан, перш ніж перемалювати крок.
  function collect() {
    const v = (sel) => { const e = body.querySelector(sel); return e ? e.value : null; };
    if (st.step === 2) {
      if (bodyEditor) st.body = bodyEditor.getValue();
      if (v("#wiz-url") !== null) st.url = v("#wiz-url");
      if (v("#wiz-section") !== null) {     // крок пакета навички
        st.sectionId = v("#wiz-section") || ""; st.folderId = v("#wiz-folder") || "";
      }
    } else if (st.step === 3) {
      st.name = v("#wiz-name") || ""; st.desc = v("#wiz-desc") || "";
      st.sectionId = v("#wiz-section") || ""; st.folderId = v("#wiz-folder") || "";
      st.category = v("#wiz-cat") || ""; st.tags = v("#wiz-tags") || "";
    }
  }

  function validate() {
    const k = kindOf();
    if (st.step === 2) {
      if (k.pkg) return st.pkg ? "" : "Оберіть пакет навички";
      const b = wizBehaviour(k.term, k.code);
      if (b.requires_body && !st.body.trim()) {
        return "Додайте текст — вставте його або перетягніть файл";
      }
      if (b.requires_url) {
        const u = st.url.trim();
        if (!u) return `Вкажіть ${(b.url_label || "посилання").toLowerCase()}`;
        if (!/^(https?:\/\/|\/)/i.test(u)) {
          return "Посилання має починатися з http://, https:// або «/»";
        }
      }
    } else if (st.step === 3) {
      if (!st.name.trim()) return "Дайте матеріалу назву";
      if (!st.desc.trim()) return "Додайте короткий опис — за ним матеріал знаходитимуть";
    }
    return "";
  }

  function paint() {
    setErr("");
    bodyEditor = null;
    paintSteps();
    if (st.step === 1) paintKinds();
    else if (st.step === 2) paintContent();
    else paintDescribe();
    // На кроці вибору виду кнопок немає — порожній футер лише забирав би місце.
    overlay.querySelector(".wiz-foot").classList.toggle("hidden", st.step === 1);
    // «Зберегти чернетку» — лише на останньому кроці нового матеріалу; у
    // навички останній крок — пакет.
    const k = kindOf();
    const lastStep = st.step === 3 || (st.step === 2 && !!k && !!k.pkg);
    draftBtn.classList.toggle("hidden", !lastStep || !!existing);
    body.scrollTop = 0;
  }

  backBtn.addEventListener("click", () => {
    collect();
    st.step = Math.max(existing ? 2 : 1, st.step - 1);
    paint();
  });
  // Завершення майстра: publish — яку з двох кнопок натиснули.
  async function finish(publish) {
    collect();
    const problem = validate();
    if (problem) { setErr(problem); return; }
    // Без розділу нікому розглянути матеріал (адміністратор модерує й такі).
    if (publish && !existing && !st.sectionId && !isAdmin()) {
      setErr("Оберіть розділ — його менеджер розгляне матеріал");
      return;
    }
    st.publish = publish;
    await (kindOf().pkg ? submitSkill() : submit());
  }
  nextBtn.addEventListener("click", async () => {
    if (st.step === 1 || (st.step === 2 && !kindOf().pkg)) {
      collect();
      const problem = validate();
      if (problem) { setErr(problem); return; }
      st.step += 1;
      paint();
      return;
    }
    await finish(true);
  });
  draftBtn.addEventListener("click", () => finish(false));

  async function submitSkill() {
    nextBtn.disabled = draftBtn.disabled = true;
    try {
      const data = await uploadSkillPackage(st.pkg, null, st.publish ? "published" : "draft",
        { section_id: st.sectionId, folder_id: st.folderId });
      toast(data.status === "published" ? `Навичку «${data.name}» опубліковано`
        : data.status === "review" ? `Навичку «${data.name}» подано на розгляд`
        : `Навичку «${data.name}» збережено чернеткою`);
      close(true);
      await ensureHubData(true);
      await go(data.status === "published" ? nav.dest : "hub:contrib");
      openSkillModal(data.id);
    } catch (e) {
      setErr(e.message);
      nextBtn.disabled = draftBtn.disabled = false;
    }
  }

  async function submit() {
    const num = (s) => s ? Number(s) : null;
    const payload = {
      resource_type: st.kind, name: st.name, description: st.desc,
      section_id: num(st.sectionId), folder_id: num(st.folderId),
      category: st.category, tags: st.tags,
      body: st.body, url: st.url, link_scope: st.scope,
    };
    nextBtn.disabled = draftBtn.disabled = backBtn.disabled = true;
    try {
      const r = existing
        ? await api(`/catalog/resources/${existing.id}`, { method: "PATCH", body: payload })
        : await api("/catalog/resources", { method: "POST",
            body: { ...payload, status: st.publish ? "published" : "draft" } });
      // Вкладення — після картки: їм потрібен її id. Помилка одного файлу не
      // скасовує вже створений матеріал — про неї просто кажемо.
      const uploads = (st.source && st.source.keep ? [st.source.file] : []).concat(st.files);
      let failed = 0;
      for (const f of uploads) {
        try { await uploadResourceFile(r.id, f); } catch (e) { failed++; }
      }
      close(true);
      await ensureHubData(true);
      if (failed) {
        toast(`Матеріал збережено, але ${failed} ${plural(failed, "файл", "файли", "файлів")} не завантажилось`, "err");
      } else {
        toast(existing ? (r.has_pending && !canModerate(r)
                ? "Зміни надіслано на розгляд — у каталозі поки попередня версія" : "Зміни збережено")
              : r.status === "published" ? "Матеріал опубліковано"
              : r.status === "review" ? "Подано на розгляд — менеджер розділу отримає сповіщення"
              : "Чернетку збережено");
      }
      // Опубліковане — одразу показуємо картку; решту — серед своїх.
      await go(existing || r.status !== "published" ? "hub:contrib" : nav.dest);
      openResourceModal(r.id);
    } catch (e) {
      setErr(e.message);
      nextBtn.disabled = draftBtn.disabled = backBtn.disabled = false;
    }
  }

  paint();
}

// ---------- «Наповнення каталогу»: усе, що додають у базу знань ----------
//
// Дві сторінки. У базі знань — особисте для кожного: усе, що людина
// опублікувала, з діями над ним. У «Керуванні» — консоль менеджера: черга
// перегляду публікацій користувачів, усі матеріали разом із навичками,
// коментарі й оцінки, категорії навичок і сувора публікація.

let contribKind = "all";
let contribFilter = "all";   // all | needs_update | no_owner | overdue | draft

// --- Спільні дії з матеріалом чи навичкою: для сторінки й для карток ---

// Елемент, з яким працюють дії: матеріал (resource) або навичка (skill).
function itemRef(type, raw) {
  return { type, id: raw.id, name: raw.name, status: raw.status,
           createdBy: raw.created_by, raw };
}
function isOwnItem(ref) { return !!(state.user && ref.createdBy === state.user.id); }
function itemPublic(ref) { return ref.status === "published" || ref.status === "needs_update"; }
function itemUrl(ref) {
  return ref.type === "skill" ? `/skills/${ref.id}` : `/catalog/resources/${ref.id}`;
}

// Рішення менеджера щодо чужого матеріалу: причина необов'язкова, але автор
// отримає її сповіщенням — і зрозуміє, що виправити. null — передумали.
function askReason({ title, text, confirm, danger = false }) {
  return new Promise(resolve => {
    const { overlay, close } = catalogModal(`
      <div class="modal res-editor reason-dlg">
        <div class="modal-head"><h2>${esc(title)}</h2>
          <button class="modal-x" aria-label="Закрити">×</button></div>
        <p class="muted">${text}</p>
        <label class="wiz-label">Причина для автора <i>необов'язково</i>
          <textarea id="rsn-note" rows="3" maxlength="2000"
            placeholder="Напр.: дублює наявний матеріал — доповніть його замість нового"></textarea></label>
        <div class="row">
          <button id="rsn-ok"${danger ? ' class="danger"' : ""}>${esc(confirm)}</button>
          <button class="ghost" id="rsn-cancel">Скасувати</button>
        </div>
      </div>`, { beforeClose: () => { resolve(null); return true; } });
    const note = overlay.querySelector("#rsn-note");
    note.focus();
    overlay.querySelector("#rsn-cancel").addEventListener("click", () => close());
    overlay.querySelector("#rsn-ok").addEventListener("click", () => {
      resolve(note.value.trim());
      close(true);
    });
  });
}

// Зміна стану. Автор подає на розгляд, забирає з розгляду чи знімає з
// публікації; менеджер розділу схвалює, повертає, знімає, архівує. Рішення
// щодо чужого матеріалу менеджер може пояснити — автор отримає причину.
async function setItemStatus(ref, status, onDone) {
  const foreign = canModerate(ref.raw) && !isOwnItem(ref);
  let note = "";
  if (status !== "published" && status !== "review" && foreign) {
    const ret = ref.status === "review" && status === "draft";
    const archive = status === "archived", stale = status === "needs_update";
    const title = ret ? "Повернути на доопрацювання" : archive ? "Перенести в архів"
      : stale ? "Позначити: потребує оновлення" : "Зняти з публікації";
    note = await askReason({
      title,
      text: ret
        ? `«${esc(ref.name)}» повернеться авторові чернеткою. Він отримає сповіщення з
           причиною, виправить і подасть знову.`
        : `«${esc(ref.name)}» ${stale
          ? "лишиться в каталозі з позначкою «потребує оновлення»"
          : "зникне з каталогу"}. Автор отримає сповіщення${archive || stale
          ? "" : " й зможе виправити матеріал і подати знову"}.`,
      confirm: ret ? "Повернути" : archive ? "Перенести в архів" : stale ? "Позначити"
        : "Зняти з публікації" });
    if (note === null) return false;
  }
  try {
    const res = await api(`${itemUrl(ref)}/status`, { method: "POST", body: { status, note } });
    const now = res.status;
    toast(now === "review" ? "Подано на розгляд — менеджер розділу отримає сповіщення"
      : now === "published" ? (foreign && ref.status === "review" ? "Схвалено й опубліковано" : "Опубліковано")
      : now === "archived" ? "Перенесено в архів"
      : now === "needs_update" ? "Позначено: потребує оновлення"
      : ref.status === "review" ? (foreign ? "Повернуто авторові" : "Забрано з розгляду")
      : foreign ? "Знято з публікації — автор отримає сповіщення" : "Знято з публікації");
    if (onDone) await onDone();
    return true;
  } catch (err) { toast(err.message, "err"); return false; }
}

async function deleteItem(ref, onDone) {
  let note = "";
  if (canModerate(ref.raw) && !isOwnItem(ref)) {
    note = await askReason({ title: "Видалити назавжди",
      text: `«${esc(ref.name)}» буде видалено разом із файлами — дію не можна скасувати.
        Автор отримає сповіщення.`,
      confirm: "Видалити", danger: true });
    if (note === null) return false;
  } else if (!confirm(`Видалити «${ref.name}»? Дію не можна скасувати.`)) return false;
  try {
    await api(itemUrl(ref), { method: "DELETE", body: { note } });
    toast("Видалено");
    if (onDone) await onDone();
    return true;
  } catch (err) { toast(err.message, "err"); return false; }
}

// Редагування: автор — у тому ж майстрі, де створював; менеджер розділу — у
// повному редакторі з усіма метаданими. Навичку обоє правлять у редакторі
// навички. Якщо зміни автора вже чекають схвалення, він продовжує з них.
async function editItem(ref, onDone) {
  if (ref.type === "skill") return openSkillEditor(ref.id, { onSaved: onDone });
  if (canModerate(ref.raw)) return openResourceEditor(ref.id, { onSaved: onDone });
  try { openContentWizard({ resource: withPending(await api(`/catalog/resources/${ref.id}`)) }); }
  catch (err) { toast(err.message, "err"); }
}

// Матеріал разом із запропонованими, ще не схваленими змінами автора.
function withPending(r) {
  if (!r.pending) return r;
  const out = { ...r, ...r.pending.data };
  if (typeof out.tags === "string") out.tags = out.tags.split(",").map(t => t.trim()).filter(Boolean);
  return out;
}

async function changesAction(ref, action, note, onDone) {
  try {
    await api(`${itemUrl(ref)}/changes`, { method: "POST", body: { action, note } });
    toast(action === "approve" ? "Зміни схвалено й опубліковано"
      : action === "reject" ? "Зміни відхилено — автор отримає сповіщення" : "Зміни скасовано");
    if (onDone) await onDone();
    return true;
  } catch (err) { toast(err.message, "err"); return false; }
}

// Підписи полів у пропозиції змін; значення-ідентифікатори показуємо назвами.
const CHANGE_LABELS = {
  name: "Назва", description: "Короткий опис", body: "Текст", url: "Посилання",
  link_scope: "Область посилання", section_id: "Розділ", folder_id: "Колекція",
  category: "Категорія", tags: "Теги", maturity_ids: "Рівні зрілості",
  complexity_id: "Складність", business_value_id: "Бізнес-цінність",
  resource_type: "Вид", owner: "Власник", owner_contact: "Контакт власника",
  tools: "Інструменти", reuse_level: "Повторне використання",
  reuse_guidance: "Як повторити", version: "Версія", author: "Автор",
  input_spec: "Вхідні дані", output_spec: "Результат роботи",
  starter_prompt: "Стартовий промпт", instructions: "Інструкція",
  icon_emoji: "Іконка", reviewed_at: "Дата перегляду", next_review_at: "Наступний перегляд",
};
function changeValue(key, v) {
  if (v == null || v === "") return "—";
  if (key === "section_id") {
    const sec = (catalogState.sections || []).find(x => x.id === Number(v));
    return sec ? sec.name : String(v);
  }
  if (key === "folder_id") {
    const f = (catalogState.folders || []).find(x => x.id === Number(v));
    return f ? f.name : String(v);
  }
  if (key === "link_scope") return v === "internal" ? "Внутрішній ресурс" : "Зовнішній сервіс";
  if (Array.isArray(v)) return v.join(", ") || "—";
  return String(v);
}

// Огляд пропозиції змін до опублікованого: що було й що стане. Опублікована
// версія лишається в каталозі, доки менеджер розділу не схвалить зміни.
async function openChangesReview(ref, onDone) {
  let item;
  try { item = await api(itemUrl(ref)); } catch (err) { toast(err.message, "err"); return; }
  const p = item.pending;
  if (!p) { toast("Змін, що чекають схвалення, немає"); return; }
  if (ref.type === "resource") {
    try {
      const files = await api(`/catalog/resources/${item.id}/files`);
      registerFileSigs(files.filter(f => f.source === "inline"));
    } catch (e) { /* картинки покажуться заглушками */ }
  }
  const textKeys = ["body", "instructions", "reuse_guidance"];
  const rows = Object.entries(p.data).filter(([k]) => k !== "package").map(([k, v]) => {
    const old = k === "tags" ? (item.tags || []) : item[k];
    const norm = (x) => Array.isArray(x) ? x.join(", ")
      : typeof x === "string" ? x.split(",").map(t => t.trim()).join(", ") : x;
    if (String(norm(old) ?? "") === String(norm(v) ?? "")) return "";
    const label = CHANGE_LABELS[k] || k;
    if (textKeys.includes(k)) {
      return `<div class="chg-row chg-text"><div class="chg-k">${esc(label)}</div>
          <div class="chg-new md-doc">${renderMarkdown(v || "")}</div>
          <details class="chg-old"><summary>Опублікована версія</summary>
            <div class="md-doc">${renderMarkdown(old || "")}</div></details></div>`;
    }
    return `<div class="chg-row"><div class="chg-k">${esc(label)}</div>
        <div class="chg-old-v">${esc(changeValue(k, old))}</div>
        <div class="chg-arrow" aria-hidden="true">→</div>
        <div class="chg-new-v">${esc(changeValue(k, v))}</div></div>`;
  }).join("");
  const pkg = p.data.package;
  const pkgRow = pkg ? `<div class="chg-row"><div class="chg-k">Новий пакет</div>
      <div class="chg-new-v">${esc(pkg.filename || "архів")}${
        pkg.meta && pkg.meta.version ? ` · версія ${esc(pkg.meta.version)}` : ""}</div>
      <button class="small ghost" id="chg-dl">Завантажити нову версію</button></div>` : "";

  const { overlay, close } = catalogModal(`
    <div class="modal sm-modal chg-modal">
      <div class="modal-head">
        <div><div class="md-eyebrow">Зміни чекають схвалення</div><h2>${esc(item.name)}</h2></div>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      <p class="muted">${p.by ? `Запропонував(ла) ${esc(p.by)}` : "Зміни автора"}${
        p.at ? ` · ${timeAgo(p.at)}` : ""}. Опублікована версія лишається в каталозі, доки ви не
        схвалите зміни.</p>
      <div class="chg-list">${rows + pkgRow || `<p class="muted">Зміни не відрізняються від опублікованої версії.</p>`}</div>
      <div class="row">
        <button id="chg-ok">Схвалити й опублікувати</button>
        <button class="ghost" id="chg-no">Відхилити</button>
        <button class="ghost" id="chg-open">Відкрити картку</button>
      </div>
    </div>`);
  hydrateImages(overlay);
  const dl = overlay.querySelector("#chg-dl");
  if (dl) dl.addEventListener("click", () => downloadAuthed(
    `${API}/skills/${item.id}/download?pending=1`, `${item.name}-нова-версія.zip`)
    .catch(err => toast(err.message, "err")));
  overlay.querySelector("#chg-ok").addEventListener("click", async () => {
    if (await changesAction(ref, "approve", "", null)) { close(true); if (onDone) await onDone(); }
  });
  overlay.querySelector("#chg-no").addEventListener("click", async () => {
    const note = await askReason({ title: "Відхилити зміни",
      text: `У каталозі лишиться опублікована версія «${esc(item.name)}». Автор отримає
        сповіщення з причиною.`, confirm: "Відхилити" });
    if (note === null) return;
    if (await changesAction(ref, "reject", note, null)) { close(true); if (onDone) await onDone(); }
  });
  overlay.querySelector("#chg-open").addEventListener("click", () => {
    close(true);
    (ref.type === "skill" ? openSkillModal : openResourceModal)(item.id);
  });
}

// Що менеджер має переглянути вже після публікації: матеріали, опубліковані
// ще за пост-модерації, і файли, додані до опублікованого. Кожне прийшло
// сповіщенням, і доки воно не прочитане, матеріал стоїть у списку.
const REVIEW_KINDS = ["content_published", "content_updated"];
function reviewQueue() {
  const map = new Map();
  (notifState.items || []).forEach(n => {
    if (n.is_read || !REVIEW_KINDS.includes(n.kind)) return;
    const type = n.skill_id ? "skill" : n.resource_id ? "resource" : null;
    if (!type) return;
    const id = n.skill_id || n.resource_id;
    const key = `${type}:${id}`;
    const entry = map.get(key) || { type, id, kind: n.kind, at: n.created_at, ids: [] };
    entry.ids.push(n.id);
    map.set(key, entry);
  });
  return [...map.values()];
}

// Відкрив картку — отже переглянув: сповіщення про неї більше не висять.
function markItemSeen(type, id) {
  const field = type === "skill" ? "skill_id" : "resource_id";
  const fresh = (notifState.items || []).filter(n => !n.is_read && n[field] === id);
  if (!fresh.length) return;
  fresh.forEach(n => { n.is_read = true; });
  notifState.unread = Math.max(0, notifState.unread - fresh.length);
  paintNotifBadge();
  syncReviewCount();
  api("/catalog/notifications/read", { method: "POST",
    body: { ids: fresh.map(n => n.id) } }).catch(() => {});
}

const SKILL_STATUS_META = {
  published: { label: "Опублікована", cls: "published" },
  review: { label: "На розгляді", cls: "review" },
  draft: { label: "Чернетка", cls: "draft" },
};
function itemStatusMeta(ref) {
  return ref.type === "skill"
    ? SKILL_STATUS_META[ref.status] || SKILL_STATUS_META.draft
    : RES_STATUS_META[ref.status] || RES_STATUS_META.draft;
}

// Рядок матеріалу чи навички у списках наповнення: плитка, назва, вид, стан і
// дії. `q` — запис черги (що й коли сталося); `manage` — консоль менеджера
// розділу: стан обирається списком, видно автора й проблеми з метаданими.
function contribRow(ref, { q = null, manage = false } = {}) {
  const raw = ref.raw;
  const it = ref.type === "skill" ? normSkill(raw) : normResource(raw);
  const kindName = ref.type === "skill" ? "Навичка" : (kindMeta(raw.resource_type).one || raw.resource_type);
  const st = itemStatusMeta(ref);
  const mod = canModerate(raw);
  const warn = [];
  if (manage && itemPublic(ref) && !raw.owner) warn.push("без власника");
  if (manage && raw.review_overdue) warn.push(`перегляд прострочено (${dateUa(raw.next_review_at)})`);
  const when = !q ? `змінено ${dateUa(raw.updated_at)}`
    : q.kind === "submitted" ? `подано ${timeAgo(q.at)}`
    : q.kind === "changes" ? `зміни ${timeAgo(q.at)}`
    : `${q.kind === "content_updated" ? "додано файл" : "опубліковано"} ${timeAgo(q.at)}`;
  const author = manage && raw.author ? ` · ${esc(raw.author)}` : "";
  const note = raw.review_note && raw.status === "draft"
    ? `<div class="res-note">Повернуто: ${esc(raw.review_note)}</div>`
    : raw.review_note && raw.status === "archived"
    ? `<div class="res-note">В архіві: ${esc(raw.review_note)}</div>` : "";
  const choices = ref.type === "skill" ? Object.entries(SKILL_STATUS_META)
                                       : statusChoices(raw.status, { item: raw });
  let statusCtl;
  if (q && (q.kind === "submitted" || q.kind === "changes")) {
    statusCtl = q.kind === "submitted"
      ? `<button class="small accent" data-act="approve">Схвалити</button>
         <button class="small ghost" data-act="return">Повернути</button>`
      : `<button class="small accent" data-act="changes">Переглянути зміни</button>`;
  } else if (manage && mod) {
    statusCtl = `<select class="small res-status" data-act="status" title="Стан">
        ${choices.map(([v, m]) => `<option value="${v}"${v === raw.status ? " selected" : ""}>${esc(m.label)}</option>`).join("")}
      </select>`;
  } else {
    statusCtl = raw.status === "review"
      ? `<button class="small ghost" data-act="withdraw">Забрати з розгляду</button>`
      : itemPublic(ref)
      ? `<button class="small ghost" data-act="unpub">Зняти з публікації</button>`
      : `<button class="small accent" data-act="pub">${mod ? "Опублікувати" : "Подати на розгляд"}</button>`;
  }
  return `<div class="res-row" data-type="${ref.type}" data-id="${ref.id}">
      ${catTile(it, "mdc-tile")}
      <div class="res-main">
        <div class="res-name">${esc(raw.name)}${raw.is_featured ? ` <span class="res-star" title="Рекомендований">★</span>` : ""}</div>
        <div class="res-sub">${esc(kindName)}${
          raw.section_name ? ` · ${esc(raw.section_name)}` : ""}${author} · ${when}${
          warn.length ? ` · <span class="res-warn">${esc(warn.join(", "))}</span>` : ""}</div>
        ${note}
      </div>
      <span class="badge ${st.cls}">${esc(st.label)}</span>
      ${raw.has_pending && !(q && q.kind === "changes")
        ? `<span class="badge review" title="Зміни до опублікованого чекають схвалення">Зміни на розгляді</span>` : ""}
      <div class="res-acts">
        <button class="small" data-act="open">Відкрити</button>
        <button class="small ghost" data-act="edit">Редагувати</button>
        ${statusCtl}
        <button class="small danger" data-act="del">Видалити</button>
        ${q && !["submitted", "changes"].includes(q.kind)
          ? `<button class="small ghost" data-act="seen" title="Прибрати зі списку без змін">Переглянуто</button>` : ""}
      </div>
    </div>`;
}

// Дії в рядках — однакові для сторінки автора й консолі менеджера.
function bindContribRows(root, refOf, reload, rerender) {
  root.querySelectorAll(".res-row [data-act]").forEach(ctl => {
    const run = () => {
      const rowEl = ctl.closest(".res-row");
      const ref = refOf(rowEl.dataset.type, rowEl.dataset.id);
      if (!ref) return;
      const act = ctl.dataset.act;
      if (act === "open") (ref.type === "skill" ? openSkillModal : openResourceModal)(ref.id);
      else if (act === "edit") editItem(ref, reload);
      else if (act === "pub" || act === "approve") setItemStatus(ref, "published", reload);
      else if (act === "unpub" || act === "return" || act === "withdraw") setItemStatus(ref, "draft", reload);
      else if (act === "changes") openChangesReview(ref, reload);
      else if (act === "del") deleteItem(ref, reload);
      else if (act === "seen") { markItemSeen(ref.type, ref.id); rerender(); }
      else if (act === "status") {
        ctl.disabled = true;
        setItemStatus(ref, ctl.value, reload).then(ok => {
          if (!ok) { ctl.value = ref.status; ctl.disabled = false; }
        });
      }
    };
    ctl.addEventListener(ctl.tagName === "SELECT" ? "change" : "click", run);
  });
}

const byUpdated = (a, b) => String(b.raw.updated_at || "").localeCompare(String(a.raw.updated_at || ""));

// «Наповнення каталогу» в базі знань — особисте для кожного: усе, що людина
// опублікувала сама, з діями над ним. Модерацію чужого менеджер веде в
// «Керуванні», а тут бачить лише підказку, скільки публікацій чекає.
async function viewContributions() {
  let resources = [], skills = [];
  try {
    [resources, skills] = await Promise.all([
      api("/catalog/resources?mine=1"), api("/skills?mine=1"), refreshNotifications(),
    ]);
  } catch (e) { resources = resources || []; skills = skills || []; }
  catalogState.mine = resources;
  catalogState.mineSkills = skills;
  buildSidebar();   // лічильник у панелі міг змінитися

  const view = $("#view");
  const reload = async () => { await ensureHubData(true); go("hub:contrib"); };
  const refs = resources.map(r => itemRef("resource", r))
    .concat(skills.map(s => itemRef("skill", s))).sort(byUpdated);
  const refOf = (type, id) => refs.find(x => x.type === type && x.id === Number(id));
  const waiting = isModerator() ? moderationCount() : 0;
  const published = refs.filter(itemPublic).length;
  const inReview = refs.filter(r => r.status === "review").length;

  view.innerHTML = `
    <div class="page-head">
      <div class="page-head-txt">
        <h1 class="page-h1">Наповнення каталогу</h1>
        <p class="page-sub">${esc(refs.length
          ? `опубліковано: ${published} · на розгляді: ${inReview} · чернеток: ${refs.length - published - inReview}`
          : "Усе, що ви додаєте в базу знань.")}</p>
      </div>
      <div class="page-acts"><button id="contrib-add">${gsvg("plus")}Додати матеріал</button></div>
    </div>
    ${waiting ? `<div class="nudge contrib-nudge">
        ${gsvg("pen", "nudge-ic")}
        <div class="nudge-txt"><b>${waiting} ${plural(waiting, "матеріал чекає", "матеріали чекають", "матеріалів чекають")} вашого рішення</b>
          <i>Черга модерації — у «Керування → Модерація».</i></div>
        <button class="ghost" id="contrib-to-admin">Переглянути</button>
      </div>` : ""}
    ${refs.length ? `
      <div class="mine-list">${refs.map(ref => contribRow(ref)).join("")}</div>
      <p class="mine-foot">Поданий матеріал перевіряє менеджер розділу: він схвалить
        публікацію або поверне матеріал з причиною — ви отримаєте сповіщення. Зміни до
        опублікованого теж чекають схвалення, а в каталозі тим часом лишається попередня
        версія. Власником опублікованого матеріалу стаєте ви. Чернетку бачите лише ви й
        менеджери розділу.</p>`
    : `
      <div class="mine-empty">${gsvg("pen")}
        <h2>Поділіться тим, що працює</h2>
        <p>Промпт, інструкція, кейс, агент, навичка, MCP-сервер чи корисне посилання —
          три кроки, а менеджер розділу перевірить і опублікує.</p>
        <div class="mine-steps">
          <span><b>1</b>Оберіть вид</span>
          <span><b>2</b>Вставте текст або файл</span>
          <span><b>3</b>Назвіть і подайте на розгляд</span>
        </div>
        <button id="contrib-add-first">Додати перший матеріал</button>
      </div>`}`;

  view.querySelectorAll("#contrib-add, #contrib-add-first").forEach(b =>
    b.addEventListener("click", () => openContentWizard()));
  const toAdmin = view.querySelector("#contrib-to-admin");
  if (toAdmin) toAdmin.addEventListener("click", () => go("admin:content"));
  bindContribRows(view, refOf, reload, viewContributions);
}

// «Керування → Модерація»: черга на розгляд і змін до опублікованого в
// розділах, які модерує менеджер (адміністратор — усі), список того, що
// варто переглянути вже після публікації, усі матеріали своїх розділів,
// коментарі й оцінки. Категорії навичок і сувора публікація — адміністратору.
async function renderContentManage() {
  const panel = panelEl();
  panel.innerHTML = `<div class="card"><p class="muted">Завантаження…</p></div>`;
  let resources, skills, settings;
  try {
    [resources, skills, settings] = await Promise.all([
      api("/catalog/resources"), api("/skills"),
      api("/catalog/settings").catch(() => ({ strict_publish: false })),
      refreshNotifications(),
    ]);
  } catch (err) {
    panel.innerHTML = `<div class="card"><p class="error">${esc(err.message)}</p></div>`;
    return;
  }
  // Поки дані вантажилися, людина могла перейти на інший екран.
  if (!panel.isConnected) return;
  const reload = async () => { await ensureHubData(true); go("admin:content"); };
  // Менеджер розділу бачить у консолі лише матеріали своїх розділів.
  const refs = resources.filter(canModerate).map(r => itemRef("resource", r))
    .concat(skills.filter(canModerate).map(s => itemRef("skill", s))).sort(byUpdated);
  const queue = (modState.items || []).map(x => ({
    q: { kind: x.kind, at: x.at }, ref: itemRef(x.type, x.item) }));
  const queueKey = new Set(queue.map(x => `${x.ref.type}:${x.ref.id}`));
  const refOf = (type, id) => {
    const hit = refs.find(x => x.type === type && x.id === Number(id));
    return hit || (queue.find(x => x.ref.type === type && x.ref.id === Number(id)) || {}).ref;
  };
  const seen = reviewQueue().map(q => ({ q, ref: refOf(q.type, q.id) }))
    .filter(x => x.ref && !queueKey.has(`${x.ref.type}:${x.ref.id}`));

  const kindOf = (ref) => ref.type === "skill" ? "skill" : ref.raw.resource_type;
  const kinds = CAT_ORDER;
  const counts = { all: refs.length };
  kinds.forEach(k => counts[k] = refs.filter(r => kindOf(r) === k).length);
  const chips = [["all", "Усі"]].concat(kinds.map(k => [k, CAT_KINDS[k].label]));
  const health = {
    review: refs.filter(r => r.status === "review").length,
    needs_update: refs.filter(r => r.status === "needs_update").length,
    no_owner: refs.filter(r => itemPublic(r) && !r.raw.owner).length,
    overdue: refs.filter(r => r.raw.review_overdue).length,
    draft: refs.filter(r => r.status === "draft").length,
  };
  const healthChip = (key, label, cnt) =>
    `<button class="cat-chipf${contribFilter === key ? " active" : ""}${cnt ? " warn" : ""}" data-f="${key}">${esc(label)} <b>${cnt}</b></button>`;
  const shown = refs.filter(r => {
    if (contribKind !== "all" && kindOf(r) !== contribKind) return false;
    if (contribFilter === "review") return r.status === "review";
    if (contribFilter === "needs_update") return r.status === "needs_update";
    if (contribFilter === "no_owner") return itemPublic(r) && !r.raw.owner;
    if (contribFilter === "overdue") return !!r.raw.review_overdue;
    if (contribFilter === "draft") return r.status === "draft";
    return true;
  });
  const scope = isAdmin() ? "усі розділи"
    : (catalogState.sections || []).filter(x => managedSections().has(x.id)).map(x => x.name).join(", ");

  panel.innerHTML = `
    <div class="content-top">
      <p class="page-sub">Чекають рішення: ${queue.length} · матеріалів: ${refs.length}
        ${scope ? ` · ${esc(scope)}` : ""}</p>
      <button id="content-add">${gsvg("plus")}Додати матеріал</button>
    </div>

    <div class="card content-queue">
      <h2>На розгляді <span class="content-n${queue.length ? " attn" : ""}">${queue.length}</span></h2>
      ${queue.length ? `
        <p class="muted">Подані матеріали й зміни до опублікованого. Схваліть — і матеріал
          з'явиться в каталозі; поверніть з причиною — автор отримає сповіщення, виправить і
          подасть знову. Поки зміни не схвалено, у каталозі лишається попередня версія.</p>
        <div class="mine-list">${queue.map(({ q, ref }) => contribRow(ref, { q, manage: true })).join("")}</div>`
      : `<p class="muted">Черга порожня. Коли автор подасть матеріал у ваш розділ, ви отримаєте
          сповіщення, а матеріал з'явиться тут.</p>`}
    </div>

    ${seen.length ? `<div class="card">
      <h2>Переглянути після публікації <span class="content-n">${seen.length}</span></h2>
      <p class="muted">Опубліковане ще до попередньої модерації та файли, додані до
        опублікованих матеріалів. Відкрита картка вважається переглянутою.</p>
      <div class="mine-list">${seen.map(({ q, ref }) => contribRow(ref, { q, manage: true })).join("")}</div>
    </div>` : ""}

    <div class="card">
      <h2>Усі матеріали <span class="content-n">${refs.length}</span></h2>
      <div class="fb-tabs res-tabs">${chips.map(([k, label]) =>
        `<button class="fb-tab${contribKind === k ? " active" : ""}" data-k="${k}">${esc(label)} <b>${counts[k] || 0}</b></button>`).join("")}</div>
      <div class="res-health">
        ${healthChip("all", "Усі", refs.length)}
        ${healthChip("review", "На розгляді", health.review)}
        ${healthChip("needs_update", "Потребують оновлення", health.needs_update)}
        ${healthChip("no_owner", "Без власника", health.no_owner)}
        ${healthChip("overdue", "Перегляд прострочено", health.overdue)}
        ${healthChip("draft", "Чернетки", health.draft)}
      </div>
      <div class="mine-list">${shown.map(ref => contribRow(ref, { manage: true })).join("")
        || `<p class="muted">За цим фільтром матеріалів немає.</p>`}</div>
      ${isAdmin() ? `<label class="ind-toggle contrib-strict">
        <input type="checkbox" id="strict-publish" ${settings.strict_publish ? "checked" : ""}>
        <span><b>Сувора публікація</b> — не публікувати матеріал без розділу, категорії,
        опису, власника та дати наступного перегляду. Власником стає автор, а перегляд, якщо
        дату не вказано, призначається через пів року.</span>
      </label>` : ""}
    </div>

    <div class="card" id="fb-card">
      <div class="fb-head">
        <h2>Коментарі й оцінки <span id="fb-unread" class="fb-badge hidden"></span></h2>
        <div class="fb-controls">
          <div class="fb-tabs">
            <button class="fb-tab" data-fb="new">Нові</button>
            <button class="fb-tab" data-fb="all">Усі</button>
          </div>
          <button class="small ghost" id="fb-read-all">Позначити всі прочитаними</button>
        </div>
      </div>
      <div id="fb-list" class="fb-list"></div>
    </div>

    ${isAdmin() ? `<div class="card">
      <h2>Категорії навичок</h2>
      <p class="muted">Список, з якого обирають категорію в редакторі навички.</p>
      <div class="row" style="margin-bottom:12px">
        <input id="cat-new" placeholder="Назва нової категорії">
        <button id="cat-add">Додати</button>
      </div>
      <div id="cat-chips" class="cat-chips"></div>
    </div>` : ""}`;

  panel.querySelector("#content-add").addEventListener("click", () => openContentWizard());
  bindContribRows(panel, refOf, reload, renderContentManage);
  panel.querySelectorAll(".fb-tab[data-k]").forEach(b =>
    b.addEventListener("click", () => { contribKind = b.dataset.k; renderContentManage(); }));
  panel.querySelectorAll("[data-f]").forEach(b =>
    b.addEventListener("click", () => { contribFilter = b.dataset.f; renderContentManage(); }));
  const strict = panel.querySelector("#strict-publish");
  if (strict) strict.addEventListener("change", async () => {
    strict.disabled = true;
    try {
      const res = await api("/catalog/settings", { method: "POST", body: { strict_publish: strict.checked } });
      toast(res.strict_publish ? "Сувору публікацію увімкнено" : "Сувору публікацію вимкнено");
    } catch (err) { strict.checked = !strict.checked; toast(err.message, "err"); }
    finally { strict.disabled = false; }
  });

  panel.querySelectorAll(".fb-tab[data-fb]").forEach(b =>
    b.addEventListener("click", () => { manageFbFilter = b.dataset.fb; renderFeedback(); }));
  panel.querySelector("#fb-read-all").addEventListener("click", async () => {
    try { await api("/skills/feedback/read-all", { method: "POST" }); renderFeedback(); }
    catch (err) { toast(err.message, "err"); }
  });
  renderFeedback();
  if (isAdmin()) {
    renderCategoryChips();
    panel.querySelector("#cat-add").addEventListener("click", addCategory);
    panel.querySelector("#cat-new").addEventListener("keydown", (e) => { if (e.key === "Enter") addCategory(); });
  }
}

// ------------------ Навчальні маршрути (BR-9) ------------------

async function openPath(pathId) {
  let p;
  try { p = await api(`/paths/${pathId}`); }
  catch (err) { toast(err.message, "err"); return; }

  const stepHtml = (s, i) => `
    <div class="step-row${s.done ? " done" : ""}${s.available ? "" : " gone"}"
         data-step="${s.id}">
      <button class="step-check" data-toggle="${s.id}"
              aria-label="${s.done ? "Зняти позначку" : "Позначити пройденим"}">
        ${s.done ? "✓" : i + 1}</button>
      <div class="step-body">
        <div class="step-title">${esc(s.title || "Крок " + (i + 1))}</div>
        ${s.note ? `<div class="step-note">${esc(s.note)}</div>` : ""}
        ${!s.available ? `<div class="step-note step-gone">Матеріал вилучено з каталогу —
          зверніться до команди хабу.</div>` : ""}
      </div>
      ${s.available ? `<button class="small" data-go="${s.id}">Відкрити</button>` : ""}
    </div>`;

  const { overlay, close } = catalogModal(`
    <div class="modal sm-modal">
      <div class="modal-head">
        <div>
          <div class="md-eyebrow">Навчальний маршрут</div>
          <h2>${esc(p.name)}</h2>
        </div>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      ${p.description ? `<p class="sm-desc">${esc(p.description)}</p>` : ""}
      <div class="path-progress">
        <span class="path-bar lg"><span style="width:${p.progress_pct}%"></span></span>
        <b>${p.progress_pct}%</b>
        <span class="muted">${p.steps_done} з ${p.steps_total} кроків</span>
        ${p.maturity_level_name ? `<span class="muted">· рівень ${esc(p.maturity_level_name)}</span>` : ""}
      </div>
      ${p.next_step_id ? `<button class="mdc-act" id="path-continue">Продовжити</button>` : ""}
      <div class="step-list" id="step-list">${p.steps.map(stepHtml).join("")}</div>
    </div>`);

  const openStep = (step) => {
    if (!step || !step.available) return;
    if (step.resource_id) { close(); openResourceModal(step.resource_id); }
    else if (step.external_url) window.open(step.external_url, "_blank", "noopener");
  };

  const refresh = (data) => {
    Object.assign(p, data);
    close();
    openPath(pathId);
  };

  overlay.querySelectorAll("[data-toggle]").forEach(b =>
    b.addEventListener("click", async () => {
      const step = p.steps.find(s => s.id === Number(b.dataset.toggle));
      try {
        const data = await api(`/paths/${pathId}/steps/${step.id}/complete`,
                               { method: "POST", body: { done: !step.done } });
        // Сторінка «Мій розвиток» теж має оновитись, інакше прогрес розійдеться.
        catalogState.paths = await api("/paths?mine=1").catch(() => catalogState.paths);
        if (nav.dest === "hub:dev") viewDevelopment();
        refresh(data);
      } catch (err) { toast(err.message, "err"); }
    }));

  overlay.querySelectorAll("[data-go]").forEach(b =>
    b.addEventListener("click", () => openStep(p.steps.find(s => s.id === Number(b.dataset.go)))));

  const contBtn = overlay.querySelector("#path-continue");
  if (contBtn) contBtn.addEventListener("click",
    () => openStep(p.steps.find(s => s.id === p.next_step_id)));
}

// ---------------- Помічник пошуку (BR-08, FR-09) ----------------
// Помічник не вигадує посилань: картки добирає пошук по базі знань, а
// пояснення будується з того, які слова задачі збіглися з карткою. Тому
// кожна картка у відповіді — клікабельна й справжня.

function openAssistant(prefill) {
  const { overlay, close } = catalogModal(`
    <div class="modal sm-modal asst-modal">
      <div class="modal-head">
        <div>
          <div class="md-eyebrow">Помічник пошуку</div>
          <h2>Опишіть задачу своїми словами</h2>
        </div>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      <p class="muted">Наприклад: «хочу автоматизувати збір ідей», «як швидше
      обробляти документи», «потрібен протокол наради». Помічник шукає лише
      серед матеріалів хабу — нічого не вигадує.</p>
      <div class="asst-ask">
        <input id="asst-q" type="text" maxlength="900"
               placeholder="Що потрібно зробити?" value="${esc(prefill || "")}">
        <button class="mdc-act" id="asst-go">Знайти</button>
      </div>
      <div class="asst-out" id="asst-out"></div>
    </div>`);

  const input = overlay.querySelector("#asst-q");
  const out = overlay.querySelector("#asst-out");
  const goBtn = overlay.querySelector("#asst-go");

  const ask = async () => {
    const question = input.value.trim();
    if (!question) { input.focus(); return; }
    goBtn.disabled = true;
    out.innerHTML = `<p class="muted">Шукаю…</p>`;
    let data;
    try { data = await api("/catalog/assistant", { method: "POST", body: { question } }); }
    catch (err) { out.innerHTML = `<p class="error">${esc(err.message)}</p>`; goBtn.disabled = false; return; }
    goBtn.disabled = false;
    renderAssistantAnswer(out, data, close);
  };

  goBtn.addEventListener("click", ask);
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") ask(); });
  input.focus();
  if (prefill) ask();
}

function renderAssistantAnswer(out, data, close) {
  const cards = data.items.map(item => `
    <button class="asst-card" data-open="${item.id}">
      <b>${esc(item.name)}</b>
      <span class="asst-why">${esc(item.assistant_reason || "")}</span>
    </button>`).join("");

  out.innerHTML = `<p class="asst-reply">${esc(data.reply)}</p>${cards}
    ${data.suggest_idea ? `<button class="ghost-act asst-idea">Поділитися ідеєю</button>` : ""}
    ${data.items.length ? `<div class="asst-rate">
        <span class="muted">Відповідь допомогла?</span>
        <button class="small" data-rate="1">Так</button>
        <button class="small" data-rate="0">Ні</button>
      </div>` : ""}`;

  out.querySelectorAll("[data-open]").forEach(b =>
    b.addEventListener("click", () => {
      close();
      openResourceModal(Number(b.dataset.open));
    }));

  const ideaBtn = out.querySelector(".asst-idea");
  if (ideaBtn) ideaBtn.addEventListener("click", () => { close(); openIdeaForm(); });

  out.querySelectorAll("[data-rate]").forEach(b =>
    b.addEventListener("click", async () => {
      try {
        await api("/catalog/assistant/feedback", { method: "POST", body: {
          search_log_id: data.search_log_id, helpful: b.dataset.rate === "1" } });
        out.querySelector(".asst-rate").innerHTML =
          `<span class="muted">Дякуємо — це допоможе покращити базу знань.</span>`;
      } catch (err) { toast(err.message, "err"); }
    }));
}

// ------------------ Опитування задоволеності (BR-16) ------------------
// Питаємо тоді, коли людина закінчує роботу (фідбек тестування, п. 6): після
// кнопки «Вийти» — перед самим виходом, а якщо вкладку просто закрили — на
// початку наступного сеансу («Як вам минулий сеанс?»). Показати власну форму
// в момент закриття вкладки браузер не дозволяє. Чи час питати і про що,
// вирішує сервер: щонайбільше раз на 30 днів і лише тих, хто вже відкривав
// матеріали.

let surveyShown = false;   // одне опитування на завантаження сторінки

// Сеанс — це робота у вкладці. Остання ознака життя сеансу лежить у
// localStorage й оновлюється щохвилини, кнопка «Вийти» її прибирає. Нова
// вкладка, що бачить ознаку, старішу за пів години, знає: попередній сеанс
// скінчився закриттям вкладки. Свіжа ознака означає, що поруч відкрита
// інша вкладка або її щойно перезавантажили — тоді не питаємо.
const SESSION_KEY = "hub-session";
const SESSION_TAB = "hub-session-tab";
const SESSION_GAP = 30 * 60 * 1000;
let sessionTimer = null;

function sessionMark() {
  try { localStorage.setItem(SESSION_KEY, JSON.stringify({ user: state.user.id, at: Date.now() })); }
  catch (e) { /* без сховища опитування просто не з'явиться */ }
}

// Початок роботи у вкладці. true — попередній сеанс цього користувача
// закінчився закриттям вкладки, а не кнопкою «Вийти».
function startSession() {
  let prev = null, newTab = false;
  try {
    prev = JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
    newTab = !sessionStorage.getItem(SESSION_TAB);
    sessionStorage.setItem(SESSION_TAB, "1");
  } catch (e) { /* ні */ }
  sessionMark();
  clearInterval(sessionTimer);
  sessionTimer = setInterval(() => { if (state.user) sessionMark(); }, 60 * 1000);
  return newTab && !!prev && prev.user === state.user.id && Date.now() - prev.at > SESSION_GAP;
}

function endSession() {
  clearInterval(sessionTimer);
  try { localStorage.removeItem(SESSION_KEY); sessionStorage.removeItem(SESSION_TAB); }
  catch (e) { /* ні */ }
}

async function surveyDue() {
  try { return (await api("/catalog/survey/due")).due; }
  catch (e) { return null; }       // опитування не має заважати роботі
}

function surveyScaleHtml(due) {
  const scale = [];
  for (let i = due.min; i <= due.max; i++) {
    scale.push(`<button class="sv-score" data-score="${i}">${i}</button>`);
  }
  return `<div class="sv-title">${esc(due.title)}</div>
    <div class="sv-scale">${scale.join("")}</div>
    <div class="sv-ends"><span>${esc(due.low)}</span><span>${esc(due.high)}</span></div>`;
}

const SURVEY_COMMENT_HTML = `
  <div class="sv-title">Дякуємо! Що варто покращити?</div>
  <div class="sv-comment">
    <input type="text" id="sv-text" maxlength="2000"
           placeholder="Необов'язково — але дуже допомагає">
    <button class="mdc-act" id="sv-send">Надіслати</button>
  </div>`;

// Новий сеанс після закритої вкладки: смужка внизу, яку можна закрити.
async function maybeAskSurvey() {
  if (surveyShown || !state.token) return;
  const due = await surveyDue();
  if (!due || surveyShown) return;
  surveyShown = true;
  renderSurveyBar(due);
}

function renderSurveyBar(due) {
  const bar = el(`<div class="survey-bar" role="dialog" aria-label="Опитування">
      <button class="sv-close" aria-label="Закрити">×</button>
      <div class="sv-body"><div class="sv-lead">Як вам минулий сеанс?</div>${surveyScaleHtml(due)}</div>
    </div>`);
  document.body.appendChild(bar);

  const finish = async (body) => {
    try { await api("/catalog/survey", { method: "POST", body }); }
    catch (e) { /* мовчки: відповідь не критична для роботи */ }
    bar.remove();
  };

  bar.querySelector(".sv-close").addEventListener("click",
    () => finish({ kind: due.kind, dismissed: true }));

  bar.querySelectorAll(".sv-score").forEach(b =>
    b.addEventListener("click", () => {
      const score = Number(b.dataset.score);
      // Після оцінки пропонуємо коментар — але не вимагаємо його.
      bar.querySelector(".sv-body").innerHTML = SURVEY_COMMENT_HTML;
      const input = bar.querySelector("#sv-text");
      input.focus();
      const send = () => finish({ kind: due.kind, score,
                                  comment: input.value, context: "session" });
      bar.querySelector("#sv-send").addEventListener("click", send);
      input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") send(); });
      // Закриття після оцінки все одно зберігає саму оцінку.
      bar.querySelector(".sv-close").replaceWith(
        el(`<button class="sv-close" aria-label="Закрити">×</button>`));
      bar.querySelector(".sv-close").addEventListener("click", send);
    }));
}

// «Вийти»: якщо час питати — одне питання перед виходом. Вихід відбувається
// в будь-якому разі: оцінка, коментар, «Пропустити» чи хрестик ведуть далі.
async function logoutWithSurvey() {
  const menu = $("#me-menu");
  if (menu) menu.classList.add("hidden");
  const due = surveyShown ? null : await surveyDue();
  if (due) {
    surveyShown = true;
    await askBeforeLogout(due);
  }
  endSession();
  logout();
}

function askBeforeLogout(due) {
  return new Promise(resolve => {
    let score = null, sent = false;
    const answer = (comment) => score == null
      ? { kind: due.kind, dismissed: true }
      : { kind: due.kind, score, comment: comment || "", context: "logout" };
    const send = async (body) => {
      if (sent) return;
      sent = true;
      try { await api("/catalog/survey", { method: "POST", body }); }
      catch (e) { /* відповідь не критична — вихід важливіший */ }
      close(true);
      resolve();
    };
    const { overlay, close } = catalogModal(`
      <div class="modal sm-modal sv-modal" role="dialog" aria-label="Опитування перед виходом">
        <div class="modal-head">
          <h2>Перш ніж вийти</h2>
          <button class="modal-x" aria-label="Закрити">×</button>
        </div>
        <div class="sv-body">${surveyScaleHtml(due)}</div>
        <div class="sv-foot"><button class="ghost" id="sv-skip">Пропустити й вийти</button></div>
      </div>`, { beforeClose: () => { send(answer()); return false; } });

    overlay.querySelectorAll(".sv-score").forEach(b =>
      b.addEventListener("click", () => {
        score = Number(b.dataset.score);
        overlay.querySelector(".sv-body").innerHTML = SURVEY_COMMENT_HTML;
        const input = overlay.querySelector("#sv-text");
        input.focus();
        overlay.querySelector("#sv-send").textContent = "Надіслати й вийти";
        overlay.querySelector("#sv-send").addEventListener("click", () => send(answer(input.value)));
        input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") send(answer(input.value)); });
        overlay.querySelector("#sv-skip").textContent = "Вийти без коментаря";
      }));
    overlay.querySelector("#sv-skip").addEventListener("click", () => {
      const input = overlay.querySelector("#sv-text");
      send(answer(input ? "" : undefined));
    });
  });
}

// ---------------------- Воронка ідей (BR-15, FR-10) ----------------------

const IDEA_STATUS_META = {
  submitted: { label: "Подано", cls: "draft" },
  in_review: { label: "На розгляді", cls: "stale" },
  accepted: { label: "Прийнято", cls: "published" },
  rejected: { label: "Відхилено", cls: "delisted" },
  implemented: { label: "Реалізовано", cls: "published" },
};

// Форма подання ідеї. `source` — картка, з якої ідея виникла (необов'язково).
function openIdeaForm(source) {
  const { overlay, close } = catalogModal(`
    <div class="modal res-editor">
      <div class="modal-head">
        <h2>Поділитися ідеєю</h2>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      <p class="muted">Ідея потрапить до черги команди хабу. Ви побачите її статус
      у «Мої ідеї», а рішення прийде з коментарем.</p>
      ${source ? `<p class="idea-source">Виникла з матеріалу: <b>${esc(source.name)}</b></p>` : ""}
      <div class="form-grid">
        <label class="wide">Суть ідеї<input id="idea-title" type="text" maxlength="200"
          placeholder="Одним рядком: що пропонуєте"></label>
        <label class="wide">Опис<textarea id="idea-body" rows="4"
          placeholder="Що саме зробити і як це має працювати"></textarea></label>
        <label class="wide">Яку бізнес-задачу вирішує<textarea id="idea-problem" rows="3"
          placeholder="Який процес зараз болить і в кого"></textarea></label>
        <label class="wide">Очікуваний ефект<textarea id="idea-effect" rows="3"
          placeholder="Що зміниться: час, якість, обсяг, гроші"></textarea></label>
        <label class="wide">Контакт для уточнень<input id="idea-contact" type="text"
          maxlength="200" placeholder="email, Teams — щоб було де уточнити деталі"></label>
      </div>
      <p class="error" id="idea-err"></p>
      <div class="row">
        <button class="mdc-act" id="idea-save">Надіслати</button>
        <button class="ghost" id="idea-cancel">Скасувати</button>
      </div>
    </div>`);

  overlay.querySelector("#idea-cancel").addEventListener("click", close);
  const saveBtn = overlay.querySelector("#idea-save");
  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    try {
      await api("/ideas", { method: "POST", body: {
        title: overlay.querySelector("#idea-title").value,
        body: overlay.querySelector("#idea-body").value,
        problem: overlay.querySelector("#idea-problem").value,
        expected_effect: overlay.querySelector("#idea-effect").value,
        contact: overlay.querySelector("#idea-contact").value,
        source_resource_id: source ? source.id : null,
      } });
      toast("Дякуємо! Ідею надіслано.");
      close();
    } catch (err) {
      overlay.querySelector("#idea-err").textContent = err.message;
      saveBtn.disabled = false;
    }
  });
}

function ideaCardHtml(idea, { manager = false } = {}) {
  const st = IDEA_STATUS_META[idea.status] || IDEA_STATUS_META.submitted;
  const field = (label, value) => value
    ? `<div class="sm-attr"><div class="sm-attr-k">${esc(label)}</div><div class="sm-attr-v">${esc(value)}</div></div>` : "";
  return `<div class="idea-row" data-idea="${idea.id}">
      <div class="idea-head">
        <div>
          <div class="idea-title">${esc(idea.title)}</div>
          <div class="idea-sub">${esc(idea.author_name || "—")} · ${dateUa(idea.created_at)}${
            idea.source_resource_name ? ` · з матеріалу «${esc(idea.source_resource_name)}»` : ""}</div>
        </div>
        <span class="badge ${st.cls}">${esc(st.label)}</span>
      </div>
      <p class="idea-body">${esc(idea.body)}</p>
      ${field("Бізнес-задача", idea.problem)}
      ${field("Очікуваний ефект", idea.expected_effect)}
      ${manager ? field("Контакт автора", idea.contact) : ""}
      ${idea.status_note ? `<p class="idea-note"><b>Рішення:</b> ${esc(idea.status_note)}</p>` : ""}
      ${idea.resource_name ? `<p class="idea-note">Реалізовано в матеріалі: <b>${esc(idea.resource_name)}</b></p>` : ""}
      ${idea.external_url ? `<p class="idea-note"><a href="${esc(idea.external_url)}" target="_blank" rel="noopener">Картка ідеї у процесі оцінки</a></p>` : ""}
      ${manager ? `<div class="idea-acts">
        <select class="small idea-status" data-st="${idea.id}">
          ${Object.entries(IDEA_STATUS_META).map(([v, m]) =>
            `<option value="${v}"${v === idea.status ? " selected" : ""}>${esc(m.label)}</option>`).join("")}
        </select>
        <button class="small" data-idea-route="${idea.id}">Маршрутизувати</button>
      </div>` : ""}
    </div>`;
}

// «Мої ідеї» — автор бачить статус кожної своєї ідеї та рішення по ній.
async function openMyIdeas() {
  let ideas = [];
  try { ideas = await api("/ideas?mine=1"); }
  catch (err) { toast(err.message, "err"); return; }

  const { overlay, close } = catalogModal(`
    <div class="modal sm-modal">
      <div class="modal-head">
        <h2>Мої ідеї</h2>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      <div class="idea-list">${ideas.length
        ? ideas.map(i => ideaCardHtml(i)).join("")
        : `<p class="muted">Ви ще не подавали ідей. Кнопка «Поділитися ідеєю» —
           на головній сторінці каталогу та в кожній картці.</p>`}</div>
      <div class="row">
        <button class="mdc-act" id="idea-new">Подати нову ідею</button>
      </div>
    </div>`);
  overlay.querySelector("#idea-new").addEventListener("click", () => {
    close();
    openIdeaForm();
  });
}

// Посилання для зв'язку з власником: email → mailto, решта — як є.
function ownerContactHref(contact) {
  const value = (contact || "").trim();
  if (!value) return null;
  if (/^(https?:\/\/|mailto:|msteams:)/i.test(value)) return value;
  if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return `mailto:${value}`;
  return null;
}

// Блок «Повторити рішення» (BR-14). Якщо сценарію немає — показуємо власника
// й кнопку зв'язку, щоб у користувача завжди був наступний крок, а не глухий кут.
function reuseBlockHtml(r) {
  const level = r.reuse_level;
  const badge = level
    ? `<span class="reuse-badge r-${esc(level)}">${esc(REUSE_LABELS[level])}</span>
       <span class="reuse-hint">${esc(REUSE_HINTS[level] || "")}</span>`
    : "";
  const guidance = (r.reuse_guidance || "").trim();
  if (!guidance && !level && !r.owner) return "";

  let inner;
  if (guidance) {
    inner = `<div class="md-doc">${renderMarkdown(guidance)}</div>`;
  } else {
    const href = ownerContactHref(r.owner_contact);
    const who = r.owner || "власник матеріалу";
    inner = `<p class="reuse-empty">Покрокового сценарію ще немає.
      ${r.owner ? `Щоб повторити рішення, зверніться до відповідального: <b>${esc(who)}</b>.`
                : "Щоб повторити рішення, зверніться до команди хабу."}</p>`
      + (href ? `<a class="mdc-act reuse-contact" href="${esc(href)}"
           target="_blank" rel="noopener">Зв'язатися з власником</a>` : "");
  }
  return `<div class="md-block reuse-block">
      <div class="md-block-head"><span class="md-eyebrow">Повторити рішення</span></div>
      ${badge ? `<div class="reuse-level">${badge}</div>` : ""}
      ${inner}
    </div>`;
}

// Вкладення картки (FR-03): перегляд — усім, хто бачить картку;
// завантаження й видалення — автору картки та менеджерам.
// Зображення й відео з вкладень — прямо в картці, одразу під описом:
// мініатюри (клік — на весь екран) і вбудований плеєр. Посилання на перегляд
// підписані сервером, бо <img> і <video> не передають JWT.
function paintMedia(overlay, files) {
  const box = overlay.querySelector("#res-media");
  if (!box) return;
  const images = files.filter(f => f.media === "image" && f.preview_url);
  const videos = files.filter(f => f.media === "video" && f.preview_url);
  if (!images.length && !videos.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="res-media">
      ${images.length ? `<div class="res-shots${images.length === 1 ? " single" : ""}">${
        images.map((f, i) => `<button type="button" class="res-shot" data-shot="${i}"
            title="${esc(f.filename)}" aria-label="Переглянути ${esc(f.filename)}">
            <img src="${esc(f.preview_url)}" alt="${esc(f.filename)}" loading="lazy"></button>`).join("")
      }</div>` : ""}
      ${videos.map(f => `<figure class="res-video">
          <video controls playsinline preload="metadata" src="${esc(f.preview_url)}"></video>
          <figcaption>${esc(f.filename)}</figcaption>
        </figure>`).join("")}
    </div>`;
  box.querySelectorAll("[data-shot]").forEach(b =>
    b.addEventListener("click", () => openLightbox(images, Number(b.dataset.shot))));
}

// Перегляд зображень на весь екран: стрілки — сусідні, Esc — закрити.
// Слухаємо клавіші на фазі захоплення: Esc закриває перегляд, а не картку під ним.
function openLightbox(images, start) {
  let i = start;
  const many = images.length > 1;
  const box = el(`<div class="lightbox" role="dialog" aria-label="Перегляд зображення">
      <button type="button" class="lb-btn lb-x" aria-label="Закрити">×</button>
      ${many ? `<button type="button" class="lb-btn lb-prev" aria-label="Попереднє">‹</button>
        <button type="button" class="lb-btn lb-next" aria-label="Наступне">›</button>` : ""}
      <figure class="lb-fig"><img alt=""><figcaption></figcaption></figure>
    </div>`);
  const img = box.querySelector("img");
  const cap = box.querySelector("figcaption");
  const show = () => {
    const f = images[i];
    img.src = f.preview_url;
    img.alt = f.filename;
    cap.textContent = many ? `${f.filename} · ${i + 1} з ${images.length}` : f.filename;
  };
  const step = (d) => { i = (i + d + images.length) % images.length; show(); };
  const close = () => { box.remove(); document.removeEventListener("keydown", onKey, true); };
  const onKey = (e) => {
    if (e.key === "Escape") { e.stopImmediatePropagation(); close(); }
    else if (many && e.key === "ArrowLeft") step(-1);
    else if (many && e.key === "ArrowRight") step(1);
  };
  box.addEventListener("click", (e) => {
    if (e.target === box || e.target.classList.contains("lb-fig")) close();
  });
  box.querySelector(".lb-x").addEventListener("click", close);
  if (many) {
    box.querySelector(".lb-prev").addEventListener("click", () => step(-1));
    box.querySelector(".lb-next").addEventListener("click", () => step(1));
  }
  document.addEventListener("keydown", onKey, true);
  document.body.appendChild(box);
  show();
}

async function renderResourceFiles(overlay, r) {
  const box = overlay.querySelector("#res-files");
  if (!box) return;
  const mayEdit = canModerate(r) || !!(state.user && r.created_by === state.user.id);

  let files = [];
  try { files = await api(`/catalog/resources/${r.id}/files`); }
  catch (e) { box.innerHTML = ""; return; }
  // Картинки з тексту показані в самому тексті — у вкладеннях їх немає.
  registerFileSigs(files.filter(f => f.source === "inline"));
  const doc = overlay.querySelector(".md-doc");
  hydrateImages(doc);
  files = files.filter(f => f.source !== "inline");
  paintMedia(overlay, files);
  if (!files.length && !mayEdit) { box.innerHTML = ""; return; }

  const icon = f => f.media === "image" && f.preview_url
    ? `<img class="res-file-thumb" src="${esc(f.preview_url)}" alt="" loading="lazy">`
    : `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 2h8l4 4v16H6V2Zm7 1.5V7h3.5L13 3.5Z"/></svg>`;
  const row = f => `<div class="res-file" data-file="${f.id}">
      ${icon(f)}
      <div class="res-file-main">
        <a href="#" class="res-file-name" data-dl-file="${f.id}">${esc(f.filename)}</a>
        <span class="res-file-meta">${fmtSize(f.size)}${
          f.uploaded_by ? ` · ${esc(f.uploaded_by)}` : ""} · ${dateUa(f.created_at)}</span>
      </div>
      ${mayEdit ? `<button class="small danger" data-del-file="${f.id}">Видалити</button>` : ""}
    </div>`;

  box.innerHTML = `<div class="md-block">
      <div class="md-block-head"><span class="md-eyebrow">Матеріали до картки</span>
        ${mayEdit ? `<label class="md-mini quiet file-pick">Додати файл
          <input type="file" id="res-file-input" hidden></label>` : ""}</div>
      ${files.length ? files.map(row).join("")
                     : `<p class="muted res-file-empty">Файлів ще немає.</p>`}
    </div>`;

  const input = overlay.querySelector("#res-file-input");
  if (input) input.addEventListener("change", async () => {
    const file = input.files[0];
    if (!file) return;
    try {
      await uploadResourceFile(r.id, file);
      toast("Файл додано");
      renderResourceFiles(overlay, r);
    } catch (err) { toast(err.message, "err"); }
  });

  // Файл віддається лише з JWT, тому звичайне посилання не підходить.
  box.querySelectorAll("[data-dl-file]").forEach(a =>
    a.addEventListener("click", async (e) => {
      e.preventDefault();
      const f = files.find(x => String(x.id) === a.dataset.dlFile);
      try { await downloadAuthed(f.download_url, f.filename); }
      catch (err) { toast(err.message, "err"); }
    }));

  box.querySelectorAll("[data-del-file]").forEach(b =>
    b.addEventListener("click", async () => {
      if (!confirm("Видалити файл із картки?")) return;
      try {
        await api(`/catalog/resources/${r.id}/files/${b.dataset.delFile}`,
                  { method: "DELETE" });
        toast("Вкладення видалено");
        renderResourceFiles(overlay, r);
      } catch (err) { toast(err.message, "err"); }
    }));
}

async function openResourceModal(resourceId) {
  let r;
  try { r = await api(`/catalog/resources/${resourceId}`); }
  catch (err) { toast(err.message, "err"); return; }
  const it = normResource(r);
  const kind = kindMeta(it.kind);
  const fav = catalogState.favorites.resource.has(it.id);

  let bodyBlock = "";
  if (it.kind === "prompt" && it.body) {
    bodyBlock = `<div class="md-block">
        <div class="md-block-head"><span class="md-eyebrow">Текст промпту</span>
          <button class="md-mini" id="res-copy">${ICO_COPY}Копіювати</button></div>
        <div class="md-doc md-prompt">${renderMarkdown(it.body, { html: false })}</div>
      </div>`;
  } else if ((it.kind === "instruction" || it.kind === "case" || it.kind === "mcp") && it.body) {
    const bodyTitle = { case: "Опис кейсу", mcp: "Як підключити" }[it.kind] || "Інструкція";
    bodyBlock = `<div class="md-block">
        <div class="md-block-head"><span class="md-eyebrow">${esc(bodyTitle)}</span></div>
        <div class="md-doc">${renderMarkdown(it.body)}</div>
      </div>`;
  }

  const scopeLabel = it.linkScope === "internal"
    ? "Внутрішній ресурс" : "Зовнішній сервіс";
  // Endpoint MCP-сервера копіюють у конфіг клієнта, тому — кнопка, а не посилання.
  const linkBlock = !it.url ? ""
    : it.kind === "mcp"
    ? `<div class="md-block">
        <div class="md-block-head"><span class="md-eyebrow">Endpoint · ${esc(scopeLabel)}</span>
          <button class="md-mini" id="res-copy-url">${ICO_COPY}Копіювати</button></div>
        <pre class="md-pre">${esc(it.url)}</pre>
      </div>`
    : `<div class="md-block">
        <div class="md-block-head"><span class="md-eyebrow">${esc(scopeLabel)}</span></div>
        <a class="md-url" href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.url)}</a>
      </div>`;

  const tags = (it.tags || []).map(tagButton).join("");

  const dateUa = (iso) => iso ? new Date(iso).toLocaleDateString("uk-UA") : "";
  const attr = (label, value) => value
    ? `<div class="sm-attr"><div class="sm-attr-k">${esc(label)}</div><div class="sm-attr-v">${esc(value)}</div></div>`
    : "";
  const meta = attr("Власник матеріалу", r.owner)
    + attr("Складність", r.complexity_name)
    + attr("Бізнес-цінність", r.business_value_name)
    + attr("Інструменти та платформи", r.tools)
    + attr("Наступний перегляд", dateUa(r.next_review_at));
  const reuseBlock = reuseBlockHtml(r);
  const own = !!(state.user && r.created_by === state.user.id);
  const ref = itemRef("resource", r);
  const staleNote = r.status === "needs_update"
    ? `<p class="sm-stale">Матеріал очікує актуалізації — звірте його з поточними правилами
       перед використанням${r.owner ? ` або зверніться до відповідального: ${esc(r.owner)}` : ""}.</p>`
    : r.status === "archived"
    ? `<p class="sm-draft">В архіві — користувачі матеріал не бачать${
        r.review_note ? `. Причина: ${esc(r.review_note)}` : ""}.</p>`
    : "";

  const { overlay, close } = catalogModal(`
    <div class="modal sm-modal md-modal">
      <div class="modal-head sm-head">
        <div class="sm-title">
          ${catTile(it, "mdc-tile lg")}
          <div>
            <div class="md-eyebrow">${esc(kind.one)}</div>
            <h2>${esc(it.name)}</h2>
            <div class="sm-meta">
              ${it.category ? `<span class="mdc-tag primary"><i class="md-dot"></i>${esc(it.category)}</span>` : ""}
              ${tags}
              <span class="muted">Версія: <b>${esc(it.version)}</b>${it.author ? ` · Автор: <b>${authorHtml(it.author, it.authorExpert)}</b>` : ""}</span>
              <span class="muted" id="res-rating">${it.ratingCount ? `Оцінка: <b>${it.ratingAvg}</b> з 5 (${it.ratingCount})` : ""}</span>
              ${r.section_name ? `<span class="muted">Розділ: <b>${esc(r.section_name)}</b>${
                r.folder_name ? ` → <b>${esc(r.folder_name)}</b>` : ""}</span>` : ""}
            </div>
          </div>
        </div>
        <div class="md-head-side">
          <button class="mdc-fav${fav ? " on" : ""}" id="res-fav" title="Обране" aria-label="Обране">${ICO_STAR}</button>
          <button class="modal-x" aria-label="Закрити">×</button>
        </div>
      </div>
      <div class="sm-body">
        ${manageBarHtml(ref)}
        ${moderationNoteHtml(ref)}
        ${staleNote}
        ${it.description ? `<p class="sm-desc">${esc(it.description)}</p>` : ""}
        <div id="res-media"></div>
        ${it.kind === "mcp" ? linkBlock + bodyBlock : bodyBlock + linkBlock}
        ${reuseBlock}
        ${meta}
        <div class="sm-files" id="res-files"></div>
      </div>
      <div class="sm-actions" id="res-actions">
        <button class="ghost-act" id="res-idea">Поділитися ідеєю</button>
      </div>
      <hr class="md-hr">
      ${commentsBlockHtml()}
    </div>`);

  bindTagButtons(overlay.querySelector(".sm-meta"), () => close(true));
  hydrateImages(overlay);
  overlay.querySelectorAll(".md-doc").forEach(doc => doc.addEventListener("click", (e) => {
    const img = e.target.closest("img.md-img[src]");
    if (!img) return;
    const all = [...doc.querySelectorAll("img.md-img[src]")];
    openLightbox(all.map(x => ({ preview_url: x.src, filename: x.alt || "Зображення" })),
                 all.indexOf(img));
  }));
  bindComments(overlay, `/catalog/resources/${it.id}/feedback`, { onRated: (d) => {
    overlay.querySelector("#res-rating").innerHTML = d.rating_count
      ? `Оцінка: <b>${d.rating_avg}</b> з 5 (${d.rating_count})` : "";
  } });
  bindManageBar(overlay, ref, close);
  markItemSeen("resource", r.id);

  const favBtn = overlay.querySelector("#res-fav");
  favBtn.addEventListener("click", async () => {
    try {
      const on = await toggleFavorite(it);
      favBtn.classList.toggle("on", on);
      paintCatalog();
    } catch (err) { toast(err.message, "err"); }
  });

  const copyBtn = overlay.querySelector("#res-copy");
  if (copyBtn) copyBtn.addEventListener("click", async () => {
    const ok = await copyText(it.body || "");
    toast(ok ? "Промпт скопійовано" : "Не вдалося скопіювати", ok ? "ok" : "err");
    if (ok) api(`/catalog/resources/${it.id}/open`, { method: "POST" }).catch(() => {});
  });

  const copyUrlBtn = overlay.querySelector("#res-copy-url");
  if (copyUrlBtn) copyUrlBtn.addEventListener("click", async () => {
    const ok = await copyText(it.url || "");
    toast(ok ? "Endpoint скопійовано" : "Не вдалося скопіювати", ok ? "ok" : "err");
    if (ok) api(`/catalog/resources/${it.id}/open`, { method: "POST" }).catch(() => {});
  });

  renderResourceFiles(overlay, r);
  trackView("resource", it.id, it.name);

  const actions = overlay.querySelector("#res-actions");
  overlay.querySelector("#res-idea").addEventListener("click", () => {
    close();
    openIdeaForm({ id: it.id, name: it.name });
  });

  const act = cardAction(it);
  // Інструкції та кейси читають у самій модалці; в MCP кнопка копіювання
  // вже є в блоці endpoint — дублювати її внизу не потрібно.
  if (act && !["instruction", "case", "mcp"].includes(it.kind)) {
    const b = el(`<button class="${act.cls}">${act.html}</button>`);
    b.addEventListener("click", async () => {
      try { await act.run(b); } catch (err) { toast(err.message, "err"); }
    });
    // Головна дія картки — попереду підказки про ідею.
    actions.insertBefore(b, actions.firstChild);
  }
}

// Детальна картка навички. Навички в хабі не запускаються: їх читають і
// завантажують, а використовують у власному інструменті. Порядок — як в
// аркуші матеріалу: опис → головна дія (завантажити) → інструкція →
// вміст пакета → відгук. Кнопка вгорі, бо інструкція буває довгою.
async function openSkillModal(skillId) {
  let s;
  try { s = await api(`/skills/${skillId}`); }
  catch (err) { toast(err.message, "err"); return; }
  const fav = catalogState.favorites.skill.has(s.id);
  const pkg = !!s.has_package;

  const attr = (label, value) => value && value.trim()
    ? `<div class="sm-attr"><div class="sm-attr-k">${esc(label)}</div><div class="sm-attr-v">${esc(value)}</div></div>`
    : "";
  const textBlock = (title, id, inner) => `<div class="md-block">
      <div class="md-block-head"><span class="md-eyebrow">${esc(title)}</span>
        <button class="md-mini" id="${id}">${ICO_COPY}Копіювати</button></div>
      ${inner}
    </div>`;
  const starter = s.starter_prompt && s.starter_prompt.trim()
    ? textBlock("Стартовий промпт", "sm-copy-starter", `<pre class="md-pre">${esc(s.starter_prompt)}</pre>`)
    : "";
  const instructions = s.instructions && s.instructions.trim()
    ? textBlock(pkg ? "Інструкція з пакета" : "Інструкція", "sm-copy-instr",
                `<div class="md-doc">${renderMarkdown(s.instructions)}</div>`)
    : "";
  const downloadsText = n => `${n} ${plural(n, "завантаження", "завантаження", "завантажень")}`;
  const ref = itemRef("skill", s);
  const draftNote = moderationNoteHtml(ref);

  const { overlay, close } = catalogModal(`
    <div class="modal sm-modal md-modal" id="skill-modal">
      <div class="modal-head sm-head">
        <div class="sm-title">
          <span class="sm-logo">${skillLogo(s, "sm-logo")}</span>
          <div>
            <div class="md-eyebrow">Навичка</div>
            <h2>${esc(s.name)}</h2>
            <div class="sm-meta">
              <span class="mdc-tag primary"><i class="md-dot"></i>${esc(s.category || "Загальне")}</span>
              <span class="muted">Версія: <b>${esc(s.version)}</b> · Автор: <b>${
                s.author ? authorHtml(s.author, s.author_is_expert) : "—"}</b></span>
              ${pkg ? `<span class="muted" id="sm-downloads">${downloadsText(s.downloads_count || 0)}</span>` : ""}
              <span class="muted" id="sm-rating"></span>
            </div>
          </div>
        </div>
        <div class="md-head-side">
          <button class="mdc-fav${fav ? " on" : ""}" id="sm-fav" title="Обране" aria-label="Обране">${ICO_STAR}</button>
          <button class="modal-x" id="sm-close" aria-label="Закрити">×</button>
        </div>
      </div>
      <div class="sm-body">
        ${manageBarHtml(ref)}
        ${draftNote}
        ${s.description ? `<p class="sm-desc">${esc(s.description)}</p>` : ""}
        ${attr("Вхідні дані", s.input_spec)}
        ${attr("Результат роботи", s.output_spec)}
        ${pkg ? `<div class="sm-actions">
          <button class="mdc-act" id="sm-download">${ICO_DOWNLOAD}Завантажити пакет</button>
        </div>` : ""}
        ${starter}
        ${instructions}
        ${pkg ? `<div class="md-block" id="sm-files">
          <div class="md-block-head"><span class="md-eyebrow">Вміст пакета</span>
            <span class="res-file-meta">${esc(s.package_filename || "")}</span></div>
          <p class="muted res-file-empty">Отримую перелік файлів…</p>
        </div>` : ""}
      </div>
      <hr class="md-hr">
      ${commentsBlockHtml()}
    </div>`);

  bindComments(overlay, `/skills/${s.id}/feedback`, { onRated: (d) => {
    overlay.querySelector("#sm-rating").innerHTML = d.rating_count
      ? `Оцінка: <b>${d.rating_avg}</b> з 5 (${d.rating_count})` : "";
  } });
  bindManageBar(overlay, ref, close);
  markItemSeen("skill", s.id);

  const favBtn = overlay.querySelector("#sm-fav");
  favBtn.addEventListener("click", async () => {
    try {
      const on = await toggleFavorite({ item_type: "skill", id: s.id });
      favBtn.classList.toggle("on", on);
      paintCatalog();
    } catch (err) { toast(err.message, "err"); }
  });

  const bindCopy = (sel, text, okMsg) => {
    const b = overlay.querySelector(sel);
    if (b) b.addEventListener("click", async () => {
      const ok = await copyText(text);
      toast(ok ? okMsg : "Не вдалося скопіювати", ok ? "ok" : "err");
    });
  };
  bindCopy("#sm-copy-starter", s.starter_prompt || "", "Промпт скопійовано");
  bindCopy("#sm-copy-instr", s.instructions || "", "Інструкцію скопійовано");

  const dlBtn = overlay.querySelector("#sm-download");
  if (dlBtn) dlBtn.addEventListener("click", async () => {
    dlBtn.disabled = true;
    try {
      if (await downloadSkillPackage(s)) {
        const counter = overlay.querySelector("#sm-downloads");
        if (counter) counter.textContent = downloadsText(s.downloads_count || 0);
      }
    } finally { dlBtn.disabled = false; }
  });

  if (pkg) {
    const box = overlay.querySelector("#sm-files");
    api(`/skills/${s.id}/files`).then(files => {
      const shown = files.slice(0, 12);
      const rest = files.length - shown.length;
      box.querySelector(".res-file-empty").outerHTML = files.length
        ? shown.map(f => `<div class="res-file">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 2h8l4 4v16H6V2Zm7 1.5V7h3.5L13 3.5Z"/></svg>
            <div class="res-file-main">
              <span class="res-file-name is-static">${esc(f.name)}</span>
              <span class="res-file-meta">${fmtSize(f.size)}</span>
            </div>
          </div>`).join("") + (rest > 0 ? `<p class="muted res-file-empty">…і ще ${rest}</p>` : "")
        : `<p class="muted res-file-empty">Пакет порожній.</p>`;
    }).catch(() => { box.remove(); });
  }

  trackView("skill", s.id, s.name);
}

// ---------- Управління навичками (каталог) ----------

// Завантаження пакета: без skillId — НОВА навичка (status: published | draft);
// зі skillId — нова ВЕРСІЯ наявної.
async function uploadSkillPackage(file, skillId, status, placement = {}) {
  const form = new FormData();
  form.append("file", file);
  if (status) form.append("status", status);
  Object.entries(placement).forEach(([k, v]) => { if (v) form.append(k, v); });
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

// Завантаження пакета навички. Сервер рахує завантаження опублікованих
// навичок — це їхня популярність у каталозі. Лічильник на клієнті
// оновлюємо одразу, щоб картка й сортування не чекали перезавантаження.
async function downloadSkillPackage(s) {
  try {
    await downloadAuthed(`${API}/skills/${s.id}/download`, `${s.name}-${s.version}.zip`);
  } catch (err) { toast(err.message, "err"); return false; }
  if (s.status === "published") {
    s.downloads_count = (s.downloads_count || 0) + 1;
    const it = catalogState.items.find(x => x.kind === "skill" && x.id === s.id);
    if (it) { it.popularity = s.downloads_count; it.raw.downloads_count = s.downloads_count; }
  }
  return true;
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
  // Поки відгуки вантажилися, людина могла перейти на іншу сторінку.
  if (!listEl.isConnected) return;

  const badge = $("#fb-unread");
  badge.textContent = data.unread || "";
  badge.classList.toggle("hidden", !data.unread);
  document.querySelectorAll(".fb-tab[data-fb]").forEach(b =>
    b.classList.toggle("active", b.dataset.fb === manageFbFilter));

  if (!data.items.length) {
    listEl.innerHTML = `<p class="muted">${manageFbFilter === "new"
      ? "Нових коментарів немає." : "Коментарів ще немає."}</p>`;
    return;
  }
  listEl.innerHTML = "";
  data.items.forEach(f => {
    const item = el(`<div class="fb-item ${f.is_read ? "" : "fb-new"}">
      <div class="fb-line-top">
        <span class="fb-dt">${esc(fmtFeedbackDate(f.created_at))}</span>
        ${f.is_read ? "" : `<span class="fb-tag">Нове</span>`}
      </div>
      <div class="fb-sub">${f.item_type === "resource" ? "Матеріал" : "Навичка"}: <b>${esc(f.skill_name)}</b></div>
      <div class="fb-sub">${esc(f.username || "—")}${f.rating ? ` · оцінка ${f.rating} з 5` : ""}${
        f.is_public ? "" : ` · <span class="fb-private">приватний відгук</span>`}</div>
      <div class="fb-text">${f.message && f.message !== "(без коментаря)" ? esc(f.message) : "<i>лише оцінка</i>"}</div>
      <div class="fb-item-actions">
        ${f.resource_id || f.skill_id ? `<button class="small ghost fb-open">Відкрити</button>` : ""}
        ${f.is_read ? "" : `<button class="small ghost fb-mark">Позначити прочитаним</button>`}
        ${f.message && f.message !== "(без коментаря)" ? `<button class="small danger fb-del">Видалити</button>` : ""}
      </div>
    </div>`);
    const openBtn = item.querySelector(".fb-open");
    if (openBtn) openBtn.addEventListener("click", () =>
      f.skill_id ? openSkillModal(f.skill_id) : openResourceModal(f.resource_id));
    const delBtn = item.querySelector(".fb-del");
    if (delBtn) delBtn.addEventListener("click", async () => {
      if (!confirm("Видалити коментар? Оцінка, якщо була, збережеться.")) return;
      try { await api(`/catalog/feedback/${f.id}`, { method: "DELETE" }); renderFeedback(); }
      catch (err) { toast(err.message, "err"); }
    });
    const mark = item.querySelector(".fb-mark");
    if (mark) mark.addEventListener("click", async () => {
      try { await api(`/skills/feedback/${f.id}/read`, { method: "POST" }); renderFeedback(); }
      catch (err) { toast(err.message, "err"); }
    });
    listEl.appendChild(item);
  });
}

// активна вкладка «Навички»: config | access | resources

// ---------- Аналітика хабу (BR-16, FR-11) ----------

// Горизонтальна смужка розподілу — без зовнішніх бібліотек.
function barRow(label, value, total, extra = "") {
  const pct = total ? Math.round(value / total * 100) : 0;
  return `<div class="an-row">
      <span class="an-label">${esc(label)}</span>
      <span class="an-bar"><i style="width:${pct}%"></i></span>
      <span class="an-val"><b>${value}</b>${extra || ` · ${pct}%`}</span>
    </div>`;
}

function statTile(value, label, tone = "") {
  return `<div class="an-tile ${tone}"><b>${value}</b><span>${esc(label)}</span></div>`;
}

async function renderHubAnalytics() {
  const panel = panelEl();
  panel.innerHTML = `<div class="card"><p class="muted">Завантаження…</p></div>`;
  let a;
  try { a = await api("/catalog/analytics"); }
  catch (err) { panel.innerHTML = `<div class="card"><p class="error">${esc(err.message)}</p></div>`; return; }

  const f = a.freshness;
  const totalItems = Object.values(a.by_type).reduce((x, y) => x + y, 0);
  const typeRows = ["skill", ...CAT_ORDER.filter(k => k !== "skill")]
    .filter(k => a.by_type[k])
    .map(k => barRow((CAT_KINDS[k] || {}).label || k, a.by_type[k], totalItems)).join("");

  const sectionTotal = a.by_section.reduce((n, s) => n + s.resources + s.skills, 0) + a.unassigned;
  const sectionRows = a.by_section
    .map(s => barRow(s.name, s.resources + s.skills, sectionTotal)).join("")
    + (a.unassigned ? barRow("Без розділу", a.unassigned, sectionTotal) : "");

  const queryList = (rows, empty) => rows.length
    ? `<ol class="an-queries">${rows.map(q =>
        `<li><span>${esc(q.query)}</span><b>${q.count}</b></li>`).join("")}</ol>`
    : `<p class="muted">${esc(empty)}</p>`;

  const topList = a.top_opened.length
    ? `<ol class="an-queries">${a.top_opened.map(t =>
        `<li><span>${esc(t.name)}</span><b>${t.opens_count}</b></li>`).join("")}</ol>`
    : `<p class="muted">Матеріали ще не відкривали.</p>`;

  panel.innerHTML = `
    <div class="card">
      <h2>Наповнення хабу</h2>
      <p class="muted">Скільки матеріалів і як вони розподілені за видами та розділами.</p>
      <div class="an-tiles">
        ${statTile(totalItems, "усього матеріалів")}
        ${statTile(a.by_section.length, "розділів")}
        ${statTile(a.rating.avg != null ? a.rating.avg : "—", `середня оцінка (${a.rating.count})`)}
        ${statTile(a.feedback_unread, "непрочитаних відгуків", a.feedback_unread ? "warn" : "")}
      </div>
      <h3 class="an-h">За видами матеріалів</h3>
      ${typeRows || `<p class="muted">Каталог порожній.</p>`}
      <h3 class="an-h">За розділами</h3>
      ${sectionRows || `<p class="muted">Розділів ще немає.</p>`}
    </div>

    <div class="card">
      <h2>Актуальність контенту</h2>
      <p class="muted">Те, за чим стежить відповідальний за хаб: що застаріває і де немає власника.</p>
      <div class="an-tiles">
        ${statTile(f.published, "опубліковано")}
        ${statTile(f.needs_update, "потребують оновлення", f.needs_update ? "warn" : "")}
        ${statTile(f.review_overdue, "перегляд прострочено", f.review_overdue ? "warn" : "")}
        ${statTile(f.no_owner, "без власника", f.no_owner ? "warn" : "")}
        ${statTile(f.no_tags, "без тегів", f.no_tags ? "warn" : "")}
        ${statTile(f.draft, "чернеток")}
        ${statTile(f.archived, "в архіві")}
      </div>
      ${a.strict_publish ? "" : `<p class="muted an-note">Сувора публікація вимкнена —
        матеріал можна опублікувати без власника й дати перегляду. Увімкнути можна
        у вкладці «Каталог».</p>`}
    </div>

    <div class="card">
      <h2>Пошук користувачів</h2>
      <p class="muted">Що шукають у хабі та які запити не знаходять нічого — це підказка,
      якого контенту бракує.</p>
      <div class="an-tiles">
        ${statTile(a.search.total, "запитів")}
        ${statTile(a.search.success_rate != null ? a.search.success_rate + "%" : "—", "завершились відкриттям")}
        ${statTile(a.search.no_results_rate != null ? a.search.no_results_rate + "%" : "—",
                   "без результатів", a.search.no_results_rate ? "warn" : "")}
      </div>
      <div class="an-cols">
        <div>
          <h3 class="an-h">Найчастіші запити</h3>
          ${queryList(a.search.top, "Запитів ще не було.")}
        </div>
        <div>
          <h3 class="an-h">Запити без результатів</h3>
          ${queryList(a.search.no_results, "Усі запити знаходили матеріали.")}
        </div>
      </div>
    </div>

    <div class="card">
      <h2>Найпопулярніші матеріали</h2>
      <p class="muted">ТОП-10 карток за кількістю відкриттів і копіювань.</p>
      ${topList}
    </div>`;
}

// ------------------------- Дашборд KPI (BR-16) -------------------------

let kpiPeriod = "30d";
const KPI_PERIOD_LABELS = { "7d": "7 днів", "30d": "30 днів", "90d": "90 днів" };

// Напрям зміни відносно попереднього періоду. Для показників, де менше — краще
// (запити без результатів), кольори інвертуються прапорцем `lowerIsBetter`.
function kpiDelta(delta, { lowerIsBetter = false } = {}) {
  if (!delta || delta.change_pct == null) {
    return `<span class="kpi-delta flat">немає з чим порівняти</span>`;
  }
  const pct = delta.change_pct;
  if (pct === 0) return `<span class="kpi-delta flat">без змін</span>`;
  const good = lowerIsBetter ? pct < 0 : pct > 0;
  return `<span class="kpi-delta ${good ? "up" : "down"}">${
    pct > 0 ? "▲" : "▼"} ${Math.abs(pct)}% до попереднього</span>`;
}

function kpiTile(value, label, delta, opts) {
  return `<div class="kpi-tile">
      <div class="kpi-value">${value == null ? "—" : esc(String(value))}</div>
      <div class="kpi-label">${esc(label)}</div>
      ${delta ? kpiDelta(delta, opts) : ""}
    </div>`;
}

function fmtDuration(seconds) {
  if (!seconds) return "—";
  const m = Math.floor(seconds / 60), s = seconds % 60;
  return m ? `${m} хв ${s} с` : `${s} с`;
}

// Проста стовпчикова динаміка переглядів за днями — без зовнішніх бібліотек.
function kpiSpark(daily) {
  if (!daily.length) return `<p class="muted">За цей період переглядів не було.</p>`;
  const max = Math.max(...daily.map(d => d.views), 1);
  return `<div class="kpi-spark">${daily.map(d => `
      <span class="kpi-bar" style="height:${Math.max(4, d.views / max * 100)}%"
            title="${esc(d.day)}: ${d.views} переглядів, ${d.users} користувачів"></span>`).join("")}
    </div>
    <div class="kpi-spark-x"><span>${esc(daily[0].day)}</span>
      <span>${esc(daily[daily.length - 1].day)}</span></div>`;
}

async function renderKpiDashboard() {
  const panel = panelEl();
  panel.innerHTML = `<div class="card"><p class="muted">Завантаження…</p></div>`;
  let k, sa;
  try {
    [k, sa] = await Promise.all([api(`/catalog/kpi?period=${kpiPeriod}`),
                                api("/catalog/search-analytics")]);
  }
  catch (err) { panel.innerHTML = `<div class="card"><p class="error">${esc(err.message)}</p></div>`; return; }

  const a = k.audience, e = k.engagement, c = k.content, s = k.search, sv = k.survey;
  const pct = v => v == null ? "—" : `${v}%`;
  const queryList = (rows, empty) => rows.length
    ? `<ol class="an-queries">${rows.map(q =>
        `<li><span>${esc(q.query)}</span><b>${q.count}</b></li>`).join("")}</ol>`
    : `<p class="muted">${esc(empty)}</p>`;

  const typeRows = ["skill", ...CAT_ORDER.filter(x => x !== "skill")]
    .filter(x => c.by_type[x])
    .map(x => barRow((CAT_KINDS[x] || {}).label || x, c.by_type[x], c.total)).join("");
  const sectionRows = c.by_section.filter(x => x.items)
    .map(x => barRow(`${x.name} — ${x.share_pct}%`, x.items, c.total)).join("");

  const topRows = e.top_viewed.length
    ? `<ol class="an-queries">${e.top_viewed.map(t =>
        `<li><span>${esc(t.name || "#" + t.target_id)}</span><b>${t.views}</b></li>`).join("")}</ol>`
    : `<p class="muted">Матеріалів ще не переглядали.</p>`;

  const npsRow = !sv.nps.responses ? `<p class="muted">Відповідей на NPS ще немає.</p>`
    : `<div class="kpi-nps">
        <div class="kpi-nps-bar">
          <span class="seg detractor" style="width:${sv.nps.detractor_pct}%" title="Критики"></span>
          <span class="seg passive" style="width:${sv.nps.passive_pct}%" title="Нейтрали"></span>
          <span class="seg promoter" style="width:${sv.nps.promoter_pct}%" title="Промоутери"></span>
        </div>
        <div class="kpi-nps-legend">
          <span><i class="d"></i>Критики ${sv.nps.buckets.detractor} (${pct(sv.nps.detractor_pct)})</span>
          <span><i class="p"></i>Нейтрали ${sv.nps.buckets.passive} (${pct(sv.nps.passive_pct)})</span>
          <span><i class="r"></i>Промоутери ${sv.nps.buckets.promoter} (${pct(sv.nps.promoter_pct)})</span>
        </div>
      </div>`;

  const comments = sv.comments.length
    ? sv.comments.map(x => `<div class="kpi-comment">
        <b>${esc((x.kind || "").toUpperCase())} ${x.score}</b>
        <span class="muted">${esc(x.role === "manager" ? "менеджер" : "користувач")}</span>
        <p>${esc(x.comment)}</p></div>`).join("")
    : `<p class="muted">Коментарів ще не залишали.</p>`;

  panel.innerHTML = `
    <div class="card">
      <div class="fb-head">
        <div>
          <h2>KPI хабу</h2>
          <p class="muted">Період ${esc(k.range.from)} — ${esc(k.range.to)};
          порівняння з ${esc(k.range.previous_from)} — ${esc(k.range.previous_to)}.</p>
        </div>
        <button class="mdc-act" id="kpi-export">Вивантажити CSV</button>
      </div>
      <div class="fb-tabs res-tabs">${Object.entries(KPI_PERIOD_LABELS).map(([v, label]) =>
        `<button class="fb-tab${kpiPeriod === v ? " active" : ""}" data-kp="${v}">${esc(label)}</button>`).join("")}</div>
    </div>

    <div class="card">
      <h2>Аудиторія</h2>
      <p class="muted">Скільки різних людей користуються хабом і як часто повертаються.</p>
      <div class="kpi-tiles">
        ${kpiTile(a.dau, "унікальних за день")}
        ${kpiTile(a.wau, "унікальних за тиждень")}
        ${kpiTile(a.mau, "унікальних за місяць")}
        ${kpiTile(pct(a.stickiness_pct), "залученість DAU/MAU")}
        ${kpiTile(a.active_users.value, "активних за період", a.active_users)}
      </div>
      <h3 class="an-h">Не поверталися</h3>
      <div class="kpi-tiles">
        ${kpiTile(a.churn.gap_30, "понад 30 днів", null)}
        ${kpiTile(a.churn.gap_60, "понад 60 днів", null)}
        ${kpiTile(a.churn.gap_90, "понад 90 днів", null)}
      </div>
    </div>

    <div class="card">
      <h2>Використання</h2>
      <p class="muted">Скільки дивляться, як довго й наскільки глибоко.</p>
      <div class="kpi-tiles">
        ${kpiTile(e.views.value, "відкриттів карток", e.views)}
        ${kpiTile(e.views_per_item, "переглядів на матеріал")}
        ${kpiTile(fmtDuration(e.avg_session_seconds.value), "середня сесія", e.avg_session_seconds)}
        ${kpiTile(e.avg_depth, "матеріалів за сесію")}
        ${kpiTile(e.sessions.value, "сесій", e.sessions)}
        ${kpiTile(e.reuse_views, "відкриттів готових до повторення")}
      </div>
      <h3 class="an-h">Динаміка переглядів</h3>
      ${kpiSpark(e.daily)}
      <h3 class="an-h">ТОП-10 матеріалів за переглядами</h3>
      ${topRows}
    </div>

    <div class="card">
      <h2>Контент і пошук</h2>
      <div class="kpi-tiles">
        ${kpiTile(c.total, "усього матеріалів")}
        ${kpiTile(pct(c.published_pct), "опубліковано")}
        ${kpiTile(pct(c.needs_update_pct), "потребують оновлення")}
        ${kpiTile(s.total.value, "пошукових запитів", s.total)}
        ${kpiTile(pct(s.success_pct), "запитів із відкриттям")}
        ${kpiTile(pct(s.no_results_pct), "запитів без результатів")}
      </div>
      <h3 class="an-h">За видами матеріалів</h3>
      ${typeRows || `<p class="muted">Каталог порожній.</p>`}
      <h3 class="an-h">За розділами</h3>
      ${sectionRows || `<p class="muted">Розділів ще немає.</p>`}
    </div>

    <div class="card">
      <h2>Задоволеність</h2>
      <p class="muted">NPS — чи порекомендують хаб; CSAT — чи задоволені контентом;
      CES — наскільки легко було знайти потрібне.</p>
      <div class="kpi-tiles">
        ${kpiTile(sv.nps.score, `NPS (${sv.nps.responses} відповідей)`)}
        ${kpiTile(sv.csat.avg, `CSAT із 5 (${sv.csat.responses})`)}
        ${kpiTile(sv.ces.avg, `CES із 5 (${sv.ces.responses})`)}
      </div>
      ${npsRow}
      <h3 class="an-h">Коментарі користувачів</h3>
      ${comments}
    </div>

    <div class="card">
      <h2>Про що питають</h2>
      <p class="muted">Запити без результатів і запити, після яких нічого не відкрили, —
      найточніша підказка, якого контенту бракує.</p>
      <div class="kpi-tiles">
        ${kpiTile(sa.total, "запитів усього")}
        ${kpiTile(sa.assistant.questions, "питань до помічника")}
        ${kpiTile(sa.assistant.helpful_pct == null ? "—" : `${sa.assistant.helpful_pct}%`,
                  `відповідей визнали корисними (${sa.assistant.rated} оцінок)`)}
      </div>
      <div class="an-cols">
        <div>
          <h3 class="an-h">Найчастіші запити</h3>
          ${queryList(sa.top, "Запитів ще не було.")}
        </div>
        <div>
          <h3 class="an-h">Без результатів</h3>
          ${queryList(sa.no_results, "Усі запити знаходили матеріали.")}
        </div>
        <div>
          <h3 class="an-h">Знайшли, але не відкрили</h3>
          ${queryList(sa.no_open, "Таких запитів немає.")}
        </div>
      </div>
    </div>`;

  panel.querySelectorAll("[data-kp]").forEach(b =>
    b.addEventListener("click", () => { kpiPeriod = b.dataset.kp; renderKpiDashboard(); }));
  $("#kpi-export").addEventListener("click", () => downloadKpiCsv());
}

// Вивантаження CSV: через fetch із токеном, бо простий <a> не несе авторизації.
async function downloadKpiCsv() {
  try {
    const res = await fetch(`${API}/catalog/kpi/export?period=${kpiPeriod}`,
                            { headers: { Authorization: `Bearer ${state.token}` } });
    if (!res.ok) throw new Error("Не вдалося сформувати вивантаження");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `ai-knowledge-hub-kpi-${kpiPeriod}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (err) { toast(err.message, "err"); }
}

// ---------- Наповнення каталогу: розділи, матеріали, життєвий цикл ----------
// Статуси, які може обрати поточний користувач: менеджер — будь-який,
// автор — опублікувати або зняти з публікації. Новий матеріал буває лише
// опублікованим або чернеткою.
// Які стани можна обрати: менеджер розділу — будь-який для матеріалів свого
// розділу; автор — чернетку чи подання (published для нього означає розгляд).
function statusChoices(current, { creating = false, item = null } = {}) {
  const allowed = !creating && canModerate(item) ? Object.keys(RES_STATUS_META)
                                                 : ["draft", "published"];
  return Object.entries(RES_STATUS_META)
    .filter(([v]) => allowed.includes(v) || v === current);
}

const RES_STATUS_META = {
  draft: { label: "Чернетка", cls: "draft" },
  review: { label: "На розгляді", cls: "review" },
  published: { label: "Опублікований", cls: "published" },
  needs_update: { label: "Потребує оновлення", cls: "stale" },
  archived: { label: "Архів", cls: "delisted" },
};
const REUSE_OPTIONS = [["", "Не вказано"], ["ready", "Готове до використання"],
                       ["adaptable", "Потребує адаптації"], ["reference", "Довідково"]];

function dateInputValue(iso) { return iso ? String(iso).slice(0, 10) : ""; }
function dateUa(iso) { return iso ? new Date(iso).toLocaleDateString("uk-UA") : "—"; }
// Сервер віддає час у UTC без зони — показуємо його в місцевому часі.
function dateTimeUa(iso) {
  if (!iso) return "—";
  const t = new Date(/Z|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
  return t.toLocaleString("uk-UA", { day: "2-digit", month: "2-digit", year: "numeric",
                                     hour: "2-digit", minute: "2-digit" });
}

async function renderResourcesManage() {
  const panel = panelEl();
  panel.innerHTML = `<div class="card"><p class="muted">Завантаження…</p></div>`;
  let sections, folders, terms, synonyms;
  try {
    [sections, folders, terms, synonyms] = await Promise.all([
      api("/catalog/sections"), api("/catalog/folders"), api("/catalog/terms"),
      api("/catalog/synonyms"),
    ]);
  } catch (err) { panel.innerHTML = `<div class="card"><p class="error">${esc(err.message)}</p></div>`; return; }

  panel.innerHTML = `
    <div class="card">
      <div class="fb-head">
        <h2>Розділи каталогу</h2>
        <button class="mdc-act" id="sec-add">+ Додати розділ</button>
      </div>
      <p class="muted">Друга вісь навігації хабу. Якщо розділ поки живе у зовнішньому порталі —
      вкажіть посилання, і плитка вестиме туди. Коли контент переїде сюди, приберіть посилання.</p>
      <div class="sec-list" id="sec-list"></div>
    </div>

    <div class="card">
      <div class="fb-head">
        <h2>Колекції</h2>
        <button class="mdc-act" id="fol-add"${sections.length ? "" : " disabled"}>+ Додати колекцію</button>
      </div>
      <p class="muted">Третій рівень навігації: розділ → колекція → картка. Колекція групує
      матеріали одного призначення всередині розділу — наприклад «Created Agents»
      чи «Use Cases». Матеріал може лишатися без колекції: він доступний на рівні розділу.</p>
      <div class="sec-list" id="fol-list"></div>
    </div>

    <div class="card">
      <div class="fb-head">
        <h2>Довідники метаданих</h2>
        <button class="mdc-act" id="term-add">+ Додати значення</button>
      </div>
      <p class="muted">Керовані значення тегів, складності та бізнес-цінності. Перейменування
      одразу видно в усіх картках, які посилаються на значення. Види матеріалів визначає код —
      тут можна змінити лише назву й видимість.</p>
      <div class="fb-tabs res-tabs" id="term-tabs"></div>
      <div class="sec-list" id="term-list"></div>
    </div>

    <div class="card">
      <div class="fb-head">
        <h2>Словник формулювань задач</h2>
        <button class="mdc-act" id="syn-add">+ Додати формулювання</button>
      </div>
      <p class="muted">Користувач шукає свою задачу («обробляти документи»), а картка
      названа інструментом («Аналіз тендерної документації»). Словник з'єднує одне з
      одним: ліворуч — як питають, праворуч — за якими словами шукати. Запити без
      результатів дивіться у вкладці «KPI».</p>
      <div class="sec-list" id="syn-list"></div>
    </div>`;

  renderSectionRows(sections);
  renderFolderRows(folders, sections);
  renderTermRows(terms);
  renderSynonymRows(synonyms);
  $("#syn-add").addEventListener("click", () => openSynonymEditor(null));

  const folAdd = $("#fol-add");
  folAdd.title = sections.length ? "" : "Спершу створіть хоча б один розділ";
  folAdd.addEventListener("click", () => openFolderEditor(null, sections));
  $("#term-add").addEventListener("click", () => openTermEditor(null));
  $("#sec-add").addEventListener("click", () => openSectionEditor(null));
}

// Черга ідей для менеджера: фільтр за статусом, зміна статусу з коментарем,
// маршрутизація до зовнішнього процесу оцінки та прив'язка до готової картки.
let ideaMgrFilter = "open";

async function renderIdeasManage() {
  const panel = panelEl();
  panel.innerHTML = `<div class="card"><p class="muted">Завантаження…</p></div>`;
  let ideas, stats, resources;
  try {
    [ideas, stats, resources] = await Promise.all([
      api(`/ideas?status=${encodeURIComponent(ideaMgrFilter)}`),
      api("/ideas/stats"), api("/catalog/resources"),
    ]);
  } catch (err) {
    panel.innerHTML = `<div class="card"><p class="error">${esc(err.message)}</p></div>`;
    return;
  }

  const chip = (key, label, n) =>
    `<button class="cat-chipf${ideaMgrFilter === key ? " active" : ""}${
      key === "open" && n ? " warn" : ""}" data-if="${key}">${esc(label)} <b>${n}</b></button>`;

  panel.innerHTML = `
    <div class="card">
      <h2>Воронка ідей</h2>
      <p class="muted">Hub збирає й маршрутизує ідеї, але не веде їхнє опрацювання.
      Коли ідею беруть у роботу — передайте її у процес оцінки посиланням
      («Маршрутизувати»), а коли реалізують — прив'яжіть до створеної картки.</p>
      <div class="res-health">
        ${chip("open", "У роботі", stats.open)}
        ${chip("submitted", "Подано", stats.by_status.submitted)}
        ${chip("in_review", "На розгляді", stats.by_status.in_review)}
        ${chip("accepted", "Прийнято", stats.by_status.accepted)}
        ${chip("implemented", "Реалізовано", stats.by_status.implemented)}
        ${chip("rejected", "Відхилено", stats.by_status.rejected)}
      </div>
      <div class="idea-list">${ideas.length
        ? ideas.map(i => ideaCardHtml(i, { manager: true })).join("")
        : `<p class="muted" style="margin-top:16px">За цим фільтром ідей немає.</p>`}</div>
    </div>`;

  panel.querySelectorAll("[data-if]").forEach(b =>
    b.addEventListener("click", () => { ideaMgrFilter = b.dataset.if; renderIdeasManage(); }));

  panel.querySelectorAll(".idea-status").forEach(sel =>
    sel.addEventListener("change", () => {
      const idea = ideas.find(x => x.id === Number(sel.dataset.st));
      openIdeaDecision(idea, sel.value, resources);
      sel.value = idea.status;   // фактичний статус змінить лише збережене рішення
    }));

  panel.querySelectorAll("[data-idea-route]").forEach(b =>
    b.addEventListener("click", () => openIdeaRouting(
      ideas.find(x => x.id === Number(b.dataset.ideaRoute)), resources)));
}

// «Зворотний зв'язок» (фідбек тестування, п. 21): повідомлення адміністратору
// зі статусами «нове» й «опрацьовано» і адреса AI SD агента, яку видно внизу
// бічної панелі й на головній.
let contactFilter = "new";

async function renderContactManage() {
  const panel = panelEl();
  panel.innerHTML = `<div class="card"><p class="muted">Завантаження…</p></div>`;
  let data;
  try {
    [data] = await Promise.all([
      api(`/contact${contactFilter === "all" ? "" : `?status=${contactFilter}`}`),
      refreshHubSettings(),
    ]);
  } catch (err) {
    panel.innerHTML = `<div class="card"><p class="error">${esc(err.message)}</p></div>`;
    return;
  }
  if (!panel.isConnected) return;
  const counts = data.counts || {};
  const chip = (key, label, n) =>
    `<button class="cat-chipf${contactFilter === key ? " active" : ""}${
      key === "new" && n ? " warn" : ""}" data-cf="${key}">${esc(label)}${
      n != null ? ` <b>${n}</b>` : ""}</button>`;
  const row = (m) => {
    const done = m.status === "done";
    return `<div class="contact-row${done ? " done" : ""}" data-msg="${m.id}">
      <div class="contact-head">
        <div>
          <b>${esc(m.author_name || "Користувача видалено")}</b>${m.author_email
            ? ` · <a href="mailto:${esc(m.author_email)}">${esc(m.author_email)}</a>` : ""}
          <div class="contact-sub">${dateTimeUa(m.created_at)}${
            m.page ? ` · сторінка: ${esc(m.page)}` : ""}</div>
        </div>
        <span class="badge ${done ? "published" : "review"}">${done ? "Опрацьовано" : "Нове"}</span>
      </div>
      <p class="contact-text">${esc(m.message)}</p>
      <div class="contact-foot">
        ${done ? `<span class="muted">Опрацював(ла) ${esc(m.handled_by_name || "—")},
          ${dateTimeUa(m.handled_at)}</span>
          <button class="small ghost" data-cs="new">Повернути в нові</button>`
          : `<button class="small" data-cs="done">Позначити опрацьованим</button>`}
      </div>
    </div>`;
  };

  panel.innerHTML = `
    <div class="card">
      <h2>Повідомлення адміністратору</h2>
      <p class="muted">Нетехнічні питання користувачів. Автор, час і сторінку, з якої
      написали, хаб підставляє сам. Відповідайте листом на пошту автора, а потім
      позначте повідомлення опрацьованим.</p>
      <div class="res-health">
        ${chip("new", "Нові", counts.new || 0)}
        ${chip("done", "Опрацьовані", counts.done || 0)}
        ${chip("all", "Усі", null)}
      </div>
      <div class="contact-list">${data.items.length ? data.items.map(row).join("")
        : `<p class="muted" style="margin-top:16px">${contactFilter === "new"
            ? "Нових повідомлень немає." : "Повідомлень немає."}</p>`}</div>
    </div>
    <div class="card">
      <h2>AI SD агент</h2>
      <p class="muted">Канал технічної підтримки в Teams. Посилання видно внизу бічної
      панелі й на головній, підказка: «${AGENT_HINT}». Порожнє поле ховає його.</p>
      <div class="test-url-row">
        <input id="agent-url" type="url" value="${esc(supportAgentUrl())}"
               placeholder="https://teams.microsoft.com/l/app/…" aria-label="Посилання на AI SD агента">
        <button class="mdc-act" id="agent-url-save">Зберегти</button>
      </div>
    </div>`;

  panel.querySelectorAll("[data-cf]").forEach(b =>
    b.addEventListener("click", () => { contactFilter = b.dataset.cf; renderContactManage(); }));
  panel.querySelectorAll("[data-cs]").forEach(b =>
    b.addEventListener("click", async () => {
      const id = Number(b.closest("[data-msg]").dataset.msg);
      try {
        await api(`/contact/${id}`, { method: "PATCH", body: { status: b.dataset.cs } });
        toast(b.dataset.cs === "done" ? "Позначено опрацьованим" : "Повернуто в нові");
        renderContactManage();
      } catch (err) { toast(err.message, "err"); }
    }));
  panel.querySelector("#agent-url-save").addEventListener("click", async () => {
    try {
      const res = await api("/catalog/settings", { method: "POST",
        body: { support_agent_url: panel.querySelector("#agent-url").value } });
      catalogState.settings = res;
      buildSidebar();
      toast(res.support_agent_url ? "Посилання на AI SD агента збережено"
                                  : "Посилання на AI SD агента приховано");
    } catch (err) { toast(err.message, "err"); }
  });
}

// Зміна статусу ідеї з коментарем — автор має розуміти рішення, а не лише
// побачити нову позначку.
function openIdeaDecision(idea, newStatus, resources) {
  const meta = IDEA_STATUS_META[newStatus] || {};
  const closing = ["rejected", "implemented"].includes(newStatus);
  const opt = (v, label) => `<option value="${esc(v)}">${esc(label)}</option>`;

  const { overlay, close } = catalogModal(`
    <div class="modal res-editor">
      <div class="modal-head">
        <h2>Статус: ${esc(meta.label || newStatus)}</h2>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      <p class="muted">Ідея: <b>${esc(idea.title)}</b> · автор ${esc(idea.author_name || "—")}</p>
      <div class="form-grid">
        <label class="wide">Коментар для автора${closing ? " (обов'язковий)" : ""}
          <textarea id="id-note" rows="3" placeholder="${closing
            ? "Чому саме таке рішення" : "Що відбувається з ідеєю"}"></textarea></label>
        ${newStatus === "implemented" ? `<label class="wide">Картка-результат
          <select id="id-res">${opt("", "— не вказано —")}${resources
            .map(r => opt(r.id, r.name)).join("")}</select></label>` : ""}
      </div>
      <p class="error" id="id-err"></p>
      <div class="row">
        <button class="mdc-act" id="id-save">Зберегти рішення</button>
        <button class="ghost" id="id-cancel">Скасувати</button>
      </div>
    </div>`);

  overlay.querySelector("#id-cancel").addEventListener("click", close);
  const saveBtn = overlay.querySelector("#id-save");
  saveBtn.addEventListener("click", async () => {
    const resSel = overlay.querySelector("#id-res");
    saveBtn.disabled = true;
    try {
      await api(`/ideas/${idea.id}/status`, { method: "POST", body: {
        status: newStatus,
        note: overlay.querySelector("#id-note").value,
        ...(resSel && resSel.value ? { resource_id: Number(resSel.value) } : {}),
      } });
      toast(`Статус: ${(meta.label || newStatus).toLowerCase()}`);
      close();
      renderIdeasManage();
    } catch (err) {
      overlay.querySelector("#id-err").textContent = err.message;
      saveBtn.disabled = false;
    }
  });
}

// Передача ідеї у зовнішній процес оцінки + прив'язка до картки.
function openIdeaRouting(idea, resources) {
  const opt = (v, label, cur) =>
    `<option value="${esc(v)}"${String(v) === String(cur) ? " selected" : ""}>${esc(label)}</option>`;
  const { overlay, close } = catalogModal(`
    <div class="modal res-editor">
      <div class="modal-head">
        <h2>Маршрутизувати ідею</h2>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      <p class="muted">Ідея: <b>${esc(idea.title)}</b></p>
      <div class="form-grid">
        <label class="wide">Посилання на процес оцінки
          <input id="ir-url" type="text" value="${esc(idea.external_url || "")}"
            placeholder="https://… — картка ідеї у системі, де її опрацьовують"></label>
        <label class="wide">Картка-результат
          <select id="ir-res">${opt("", "— не вказано —", idea.resource_id || "")}${
            resources.map(r => opt(r.id, r.name, idea.resource_id || "")).join("")}</select></label>
      </div>
      <p class="error" id="ir-err"></p>
      <div class="row">
        <button class="mdc-act" id="ir-save">Зберегти</button>
        <button class="ghost" id="ir-cancel">Скасувати</button>
      </div>
    </div>`);

  overlay.querySelector("#ir-cancel").addEventListener("click", close);
  const saveBtn = overlay.querySelector("#ir-save");
  saveBtn.addEventListener("click", async () => {
    const value = overlay.querySelector("#ir-res").value;
    saveBtn.disabled = true;
    try {
      await api(`/ideas/${idea.id}`, { method: "PATCH", body: {
        external_url: overlay.querySelector("#ir-url").value,
        resource_id: value ? Number(value) : null,
      } });
      toast("Збережено");
      close();
      renderIdeasManage();
    } catch (err) {
      overlay.querySelector("#ir-err").textContent = err.message;
      saveBtn.disabled = false;
    }
  });
}

function renderSectionRows(sections) {
  const box = $("#sec-list");
  if (!box) return;
  if (!sections.length) {
    box.innerHTML = `<p class="muted" style="margin-top:12px">Розділів ще немає.</p>`;
    return;
  }
  box.innerHTML = sections.map(s => `
    <div class="res-row">
      <span class="mdc-tile sm a-${esc(s.accent || "ink")}">${
        s.icon_emoji ? `<span class="mdc-emoji">${esc(s.icon_emoji)}</span>` : "•"}</span>
      <div class="res-main">
        <div class="res-name">${esc(s.name)}${s.is_active ? "" : ` <span class="muted">(прихований)</span>`}</div>
        <div class="res-sub">${esc(s.description || "")}${
          s.url ? ` · <span class="res-url">${esc(s.url)}</span>` : ""}</div>
      </div>
      <span class="badge ${s.url ? "draft" : "published"}">${s.url ? "Зовнішній" : `${s.items_count} матеріалів`}</span>
      <div class="res-acts">
        <button class="small" data-sec-edit="${s.id}">Редагувати</button>
        <button class="small danger" data-sec-del="${s.id}">Видалити</button>
      </div>
    </div>`).join("");

  box.querySelectorAll("[data-sec-edit]").forEach(b =>
    b.addEventListener("click", () => openSectionEditor(
      sections.find(x => x.id === Number(b.dataset.secEdit)))));
  box.querySelectorAll("[data-sec-del]").forEach(b =>
    b.addEventListener("click", async () => {
      const sec = sections.find(x => x.id === Number(b.dataset.secDel));
      if (!confirm(`Видалити розділ «${sec.name}»? Матеріали збережуться — у них лише зникне прив'язка.`)) return;
      try {
        await api(`/catalog/sections/${sec.id}`, { method: "DELETE" });
        toast("Розділ видалено");
        renderResourcesManage();
      } catch (err) { toast(err.message, "err"); }
    }));
}

// Колекції, згруповані за розділом — щоб було видно ієрархію, а не плаский список.
function renderFolderRows(folders, sections) {
  const box = $("#fol-list");
  if (!box) return;
  if (!folders.length) {
    box.innerHTML = `<p class="muted" style="margin-top:12px">Колекцій ще немає.${
      sections.length ? "" : " Спершу створіть розділ."}</p>`;
    return;
  }
  const bySection = sections
    .map(s => [s, folders.filter(f => f.section_id === s.id)])
    .filter(([, list]) => list.length);

  box.innerHTML = bySection.map(([section, list]) => `
    <div class="fol-group">
      <div class="fol-group-head">${esc(section.name)}</div>
      ${list.map(f => `
      <div class="res-row">
        <span class="mdc-tile sm a-steel">${
          f.icon_emoji ? `<span class="mdc-emoji">${esc(f.icon_emoji)}</span>` : "•"}</span>
        <div class="res-main">
          <div class="res-name">${esc(f.name)}${
            f.is_active ? "" : ` <span class="muted">(прихована)</span>`}</div>
          <div class="res-sub">${esc(f.description || "")}</div>
        </div>
        <span class="badge published">${f.items_count} матеріалів</span>
        <div class="res-acts">
          <button class="small" data-fol-edit="${f.id}">Редагувати</button>
          <button class="small danger" data-fol-del="${f.id}">Видалити</button>
        </div>
      </div>`).join("")}
    </div>`).join("");

  box.querySelectorAll("[data-fol-edit]").forEach(b =>
    b.addEventListener("click", () => openFolderEditor(
      folders.find(x => x.id === Number(b.dataset.folEdit)), sections)));
  box.querySelectorAll("[data-fol-del]").forEach(b =>
    b.addEventListener("click", async () => {
      const f = folders.find(x => x.id === Number(b.dataset.folDel));
      if (!confirm(`Видалити колекцію «${f.name}»? Матеріали залишаться в розділі.`)) return;
      try {
        await api(`/catalog/folders/${f.id}`, { method: "DELETE" });
        toast("Колекцію видалено");
        renderResourcesManage();
      } catch (err) { toast(err.message, "err"); }
    }));
}

// Редактор колекції.
function openFolderEditor(folder, sections) {
  const f = folder || { name: "", description: "", icon_emoji: "",
                        section_id: sections.length ? sections[0].id : "",
                        position: 0, is_active: true };
  const opt = (v, label, cur) =>
    `<option value="${esc(v)}"${String(v) === String(cur) ? " selected" : ""}>${esc(label)}</option>`;

  const { overlay, close } = catalogModal(`
    <div class="modal res-editor">
      <div class="modal-head">
        <h2>${folder ? "Редагувати колекцію" : "Нова колекція"}</h2>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      <div class="form-grid">
        <label class="wide">Розділ<select id="fe-section">${
          sections.map(s => opt(s.id, s.name, f.section_id)).join("")}</select></label>
        <label class="wide">Назва<input id="fe-name" type="text" value="${esc(f.name || "")}" placeholder="Напр. «Use Cases»"></label>
        <label class="wide">Призначення<input id="fe-desc" type="text" value="${esc(f.description || "")}" placeholder="Які матеріали сюди складають"></label>
        <label>Емодзі-іконка<input id="fe-emoji" type="text" maxlength="4" value="${esc(f.icon_emoji || "")}" placeholder="💼"></label>
        <label>Позиція<input id="fe-pos" type="number" value="${Number(f.position) || 0}"></label>
        <label class="check wide"><input type="checkbox" id="fe-active"${f.is_active ? " checked" : ""}>
          <span>Показувати в каталозі</span></label>
      </div>
      <p class="muted">Зміна розділу переносить у нього всі матеріали цієї колекції.</p>
      <p class="error" id="fe-err"></p>
      <div class="row">
        <button class="mdc-act" id="fe-save">Зберегти</button>
        <button class="ghost" id="fe-cancel">Скасувати</button>
      </div>
    </div>`);

  overlay.querySelector("#fe-cancel").addEventListener("click", close);
  const saveBtn = overlay.querySelector("#fe-save");
  saveBtn.addEventListener("click", async () => {
    const payload = {
      section_id: Number(overlay.querySelector("#fe-section").value) || null,
      name: overlay.querySelector("#fe-name").value,
      description: overlay.querySelector("#fe-desc").value,
      icon_emoji: overlay.querySelector("#fe-emoji").value,
      position: Number(overlay.querySelector("#fe-pos").value) || 0,
      is_active: overlay.querySelector("#fe-active").checked,
    };
    saveBtn.disabled = true;
    try {
      if (folder) await api(`/catalog/folders/${folder.id}`, { method: "PATCH", body: payload });
      else await api("/catalog/folders", { method: "POST", body: payload });
      toast("Збережено");
      close();
      renderResourcesManage();
    } catch (err) {
      overlay.querySelector("#fe-err").textContent = err.message;
      saveBtn.disabled = false;
    }
  });
}

// --------------------------- Довідники ---------------------------

let termMgrKind = "tag";
const TERM_KIND_META = {
  tag: { label: "Теги", one: "тег" },
  complexity: { label: "Складність", one: "рівень складності" },
  business_value: { label: "Бізнес-цінність", one: "значення бізнес-цінності" },
  maturity: { label: "Рівні зрілості", one: "рівень зрілості" },
  material_type: { label: "Види матеріалів", one: "вид матеріалу" },
  status: { label: "Статуси", one: "статус" },
  reuse_level: { label: "Повторне використання", one: "рівень повторного використання" },
  accent: { label: "Кольори", one: "колір" },
};

function renderTermRows(terms) {
  const tabs = $("#term-tabs");
  const box = $("#term-list");
  if (!tabs || !box) return;

  tabs.innerHTML = Object.entries(TERM_KIND_META).map(([kind, meta]) => {
    const n = terms.filter(t => t.kind === kind).length;
    return `<button class="fb-tab${termMgrKind === kind ? " active" : ""}" data-tk="${kind}">${
      esc(meta.label)} <b>${n}</b></button>`;
  }).join("");
  tabs.querySelectorAll("[data-tk]").forEach(b =>
    b.addEventListener("click", () => { termMgrKind = b.dataset.tk; renderTermRows(terms); }));

  // Види матеріалів тепер додаються з інтерфейсу (AKH-17); фіксований набір
  // лишився тільки у статусів і акцентів — від них залежить логіка.
  const fixed = ["status", "accent"].includes(termMgrKind);
  const isType = termMgrKind === "material_type";
  const rows = terms.filter(t => t.kind === termMgrKind);
  const peers = rows.length > 1 && !isType;
  $("#term-add").disabled = fixed;
  $("#term-add").title = fixed
    ? "Набір значень цього довідника задає логіка застосунку" : "";

  if (!rows.length) {
    box.innerHTML = `<p class="muted" style="margin-top:12px">Значень ще немає.</p>`;
    return;
  }
  box.innerHTML = rows.map(t => `
    <div class="res-row">
      <div class="res-main">
        <div class="res-name">${esc(t.name)}${
          t.is_active ? "" : ` <span class="muted">(приховано)</span>`}</div>
        <div class="res-sub">${esc(t.description || "")}</div>
      </div>
      <span class="badge ${t.items_count ? "published" : "draft"}">${t.items_count} матеріалів</span>
      <div class="res-acts">
        <button class="small" data-term-edit="${t.id}">Редагувати</button>
        ${fixed || !peers ? "" : `<button class="small" data-term-merge="${t.id}">Об'єднати</button>`}
        ${fixed ? "" : `<button class="small danger" data-term-del="${t.id}">Видалити</button>`}
      </div>
    </div>`).join("");

  box.querySelectorAll("[data-term-edit]").forEach(b =>
    b.addEventListener("click", () => openTermEditor(
      rows.find(x => x.id === Number(b.dataset.termEdit)))));
  box.querySelectorAll("[data-term-merge]").forEach(b =>
    b.addEventListener("click", () => openTermMerge(
      rows.find(x => x.id === Number(b.dataset.termMerge)), rows)));
  box.querySelectorAll("[data-term-del]").forEach(b =>
    b.addEventListener("click", async () => {
      const t = rows.find(x => x.id === Number(b.dataset.termDel));
      const warn = t.items_count
        ? ` Значення зникне у ${t.items_count} матеріалах.` : "";
      if (!confirm(`Видалити «${t.name}»?${warn}`)) return;
      try {
        await api(`/catalog/terms/${t.id}`, { method: "DELETE" });
        toast("Значення видалено");
        renderResourcesManage();
      } catch (err) { toast(err.message, "err"); }
    }));
}

function openTermEditor(term) {
  const kind = term ? term.kind : termMgrKind;
  const meta = TERM_KIND_META[kind] || { one: "значення" };
  const t = term || { name: "", description: "", position: 0, is_active: true };
  const isType = kind === "material_type";
  const codeBound = ["status", "accent"].includes(kind);
  const styled = isType || codeBound;

  const { overlay, close } = catalogModal(`
    <div class="modal res-editor">
      <div class="modal-head">
        <h2>${term ? "Редагувати" : "Нове значення"}: ${esc(meta.one)}</h2>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      <div class="form-grid">
        <label class="wide">Назва<input id="te-name" type="text" value="${esc(t.name || "")}"></label>
        <label class="wide">Пояснення<input id="te-desc" type="text" value="${esc(t.description || "")}" placeholder="Коли обирати це значення"></label>
        ${styled ? `
        <label>Емодзі-іконка<input id="te-emoji" type="text" maxlength="4" value="${esc(t.icon_emoji || "")}" placeholder="📄"></label>
        <label>Колір<select id="te-color">${ACCENT_OPTIONS
          .map(([v, l]) => `<option value="${esc(v)}"${v === (t.color || "") ? " selected" : ""}>${esc(l)}</option>`).join("")}</select></label>` : ""}
        ${isType ? `
        <label class="check"><input type="checkbox" id="te-url"${t.requires_url ? " checked" : ""}>
          <span>Потребує посилання</span></label>
        <label class="check"><input type="checkbox" id="te-body"${t.requires_body ? " checked" : ""}>
          <span>Потребує тексту</span></label>
        <label class="check"><input type="checkbox" id="te-scope"${t.has_scope ? " checked" : ""}>
          <span>Має область: зовнішній / внутрішній</span></label>
        <label>Підпис посилання<input id="te-url-label" type="text" value="${esc(t.url_label || "")}" placeholder="напр. «Endpoint сервера»"></label>
        <label>Підпис тексту<input id="te-body-label" type="text" value="${esc(t.body_label || "")}" placeholder="напр. «Як підключити»"></label>` : ""}
        <label>Позиція<input id="te-pos" type="number" value="${Number(t.position) || 0}"></label>
        <label class="check"><input type="checkbox" id="te-active"${t.is_active ? " checked" : ""}>
          <span>Доступне для вибору</span></label>
      </div>
      ${isType && !term ? `<p class="muted">Новий вид матеріалу одразу з'явиться в каталозі,
        фільтрах і редакторі — код підбереться автоматично з назви.</p>` : ""}
      ${codeBound ? `<p class="muted">Набір значень цього довідника задає логіка застосунку —
        змінити можна назву, іконку, колір і видимість.</p>` : ""}
      <p class="error" id="te-err"></p>
      <div class="row">
        <button class="mdc-act" id="te-save">Зберегти</button>
        <button class="ghost" id="te-cancel">Скасувати</button>
      </div>
    </div>`);

  overlay.querySelector("#te-cancel").addEventListener("click", close);
  const saveBtn = overlay.querySelector("#te-save");
  saveBtn.addEventListener("click", async () => {
    const pick = (id) => overlay.querySelector(id);
    const payload = {
      kind,
      name: pick("#te-name").value,
      description: pick("#te-desc").value,
      position: Number(pick("#te-pos").value) || 0,
      is_active: pick("#te-active").checked,
      ...(styled ? { icon_emoji: pick("#te-emoji").value,
                     color: pick("#te-color").value } : {}),
      ...(isType ? { requires_url: pick("#te-url").checked,
                     requires_body: pick("#te-body").checked,
                     has_scope: pick("#te-scope").checked,
                     url_label: pick("#te-url-label").value,
                     body_label: pick("#te-body-label").value } : {}),
    };
    saveBtn.disabled = true;
    try {
      if (term) await api(`/catalog/terms/${term.id}`, { method: "PATCH", body: payload });
      else await api("/catalog/terms", { method: "POST", body: payload });
      toast("Збережено");
      close();
      renderResourcesManage();
    } catch (err) {
      overlay.querySelector("#te-err").textContent = err.message;
      saveBtn.disabled = false;
    }
  });
}

// Об'єднання двох значень довідника: посилання переносяться, дубль зникає.
function openTermMerge(term, peers) {
  const others = peers.filter(t => t.id !== term.id);
  const { overlay, close } = catalogModal(`
    <div class="modal res-editor">
      <div class="modal-head">
        <h2>Об'єднати «${esc(term.name)}»</h2>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      <div class="form-grid">
        <label class="wide">Залишити значення<select id="tm-into">${others
          .map(t => `<option value="${t.id}">${esc(t.name)} (${t.items_count})</option>`)
          .join("")}</select></label>
      </div>
      <p class="muted">Усі ${term.items_count} матеріалів перейдуть на вибране значення,
      а «${esc(term.name)}» зникне з довідника. Дія незворотна.</p>
      <p class="error" id="tm-err"></p>
      <div class="row">
        <button class="mdc-act" id="tm-save">Об'єднати</button>
        <button class="ghost" id="tm-cancel">Скасувати</button>
      </div>
    </div>`);

  overlay.querySelector("#tm-cancel").addEventListener("click", close);
  const saveBtn = overlay.querySelector("#tm-save");
  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    try {
      const res = await api(`/catalog/terms/${term.id}/merge`, { method: "POST",
        body: { into: Number(overlay.querySelector("#tm-into").value) } });
      toast(res.message);
      close();
      renderResourcesManage();
    } catch (err) {
      overlay.querySelector("#tm-err").textContent = err.message;
      saveBtn.disabled = false;
    }
  });
}

// --------------- Керування навчальними маршрутами (BR-9) ---------------

async function renderPathsManage() {
  const panel = panelEl();
  panel.innerHTML = `<div class="card"><p class="muted">Завантаження…</p></div>`;
  let paths, resources, terms;
  try {
    [paths, resources, terms] = await Promise.all([
      api("/paths"), api("/catalog/resources"), api("/catalog/terms?kind=maturity"),
    ]);
  } catch (err) {
    panel.innerHTML = `<div class="card"><p class="error">${esc(err.message)}</p></div>`;
    return;
  }

  panel.innerHTML = `
    <div class="card">
      <div class="fb-head">
        <h2>Навчальні маршрути</h2>
        <button class="mdc-act" id="path-add">+ Додати маршрут</button>
      </div>
      <p class="muted">Упорядкована послідовність матеріалів: від простого до складного.
      Крок веде на картку каталогу або на зовнішнє посилання. Якщо картку вилучити з
      каталогу, крок лишиться на місці й буде позначений недоступним — прогрес
      користувачів не зсунеться.</p>
      <div class="sec-list">${paths.length ? paths.map(p => `
        <div class="res-row">
          <span class="mdc-tile sm a-purple">${p.icon_emoji ? `<span class="mdc-emoji">${esc(p.icon_emoji)}</span>` : gsvg("path")}</span>
          <div class="res-main">
            <div class="res-name">${esc(p.name)}${p.is_starter ? ` <span class="res-star">★ старт</span>` : ""}${
              p.is_active ? "" : ` <span class="muted">(прихований)</span>`}</div>
            <div class="res-sub">${esc(p.description || "")}${
              p.maturity_level_name ? ` · рівень ${esc(p.maturity_level_name)}` : ""}</div>
          </div>
          <span class="badge ${p.steps_total ? "published" : "draft"}">${p.steps_total} кроків</span>
          <div class="res-acts">
            <button class="small" data-path-steps="${p.id}">Кроки</button>
            <button class="small" data-path-edit="${p.id}">Редагувати</button>
            <button class="small danger" data-path-del="${p.id}">Видалити</button>
          </div>
        </div>`).join("") : `<p class="muted" style="margin-top:12px">Маршрутів ще немає.</p>`}
      </div>
    </div>`;

  $("#path-add").addEventListener("click", () => openPathEditor(null, terms));
  panel.querySelectorAll("[data-path-edit]").forEach(b =>
    b.addEventListener("click", () => openPathEditor(
      paths.find(x => x.id === Number(b.dataset.pathEdit)), terms)));
  panel.querySelectorAll("[data-path-steps]").forEach(b =>
    b.addEventListener("click", () => openStepsEditor(
      paths.find(x => x.id === Number(b.dataset.pathSteps)), resources)));
  panel.querySelectorAll("[data-path-del]").forEach(b =>
    b.addEventListener("click", async () => {
      const p = paths.find(x => x.id === Number(b.dataset.pathDel));
      if (!confirm(`Видалити маршрут «${p.name}»? Прогрес користувачів буде втрачено.`)) return;
      try {
        await api(`/paths/${p.id}`, { method: "DELETE" });
        toast("Маршрут видалено");
        renderPathsManage();
      } catch (err) { toast(err.message, "err"); }
    }));
}

function openPathEditor(path, levels) {
  const p = path || { name: "", description: "", icon_emoji: "", audience_role: "",
                      maturity_level_id: "", is_starter: false, is_active: true,
                      position: 0 };
  const opt = (v, label, cur) =>
    `<option value="${esc(v)}"${String(v) === String(cur) ? " selected" : ""}>${esc(label)}</option>`;

  const { overlay, close } = catalogModal(`
    <div class="modal res-editor">
      <div class="modal-head">
        <h2>${path ? "Редагувати маршрут" : "Новий маршрут"}</h2>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      <div class="form-grid">
        <label class="wide">Назва<input id="pe-name" type="text" value="${esc(p.name)}"
          placeholder="Напр. «Старт з AI за тиждень»"></label>
        <label class="wide">Опис<textarea id="pe-desc" rows="2"
          placeholder="Кому і для чого цей маршрут">${esc(p.description || "")}</textarea></label>
        <label>Емодзі<input id="pe-emoji" type="text" maxlength="4" value="${esc(p.icon_emoji || "")}" placeholder="🎓"></label>
        <label>Рівень зрілості<select id="pe-level">${opt("", "— будь-який —", p.maturity_level_id || "")}${
          levels.map(l => opt(l.id, l.name, p.maturity_level_id || "")).join("")}</select></label>
        <label>Для ролі<input id="pe-role" type="text" value="${esc(p.audience_role || "")}" placeholder="напр. «Закупівлі»"></label>
        <label>Позиція<input id="pe-pos" type="number" value="${Number(p.position) || 0}"></label>
        <label class="check"><input type="checkbox" id="pe-starter"${p.is_starter ? " checked" : ""}>
          <span>Стартовий — пропонувати тим, хто ще нічого не почав</span></label>
        <label class="check"><input type="checkbox" id="pe-active"${p.is_active ? " checked" : ""}>
          <span>Показувати користувачам</span></label>
      </div>
      <p class="error" id="pe-err"></p>
      <div class="row">
        <button class="mdc-act" id="pe-save">Зберегти</button>
        <button class="ghost" id="pe-cancel">Скасувати</button>
      </div>
    </div>`);

  overlay.querySelector("#pe-cancel").addEventListener("click", close);
  const saveBtn = overlay.querySelector("#pe-save");
  saveBtn.addEventListener("click", async () => {
    const level = overlay.querySelector("#pe-level").value;
    const payload = {
      name: overlay.querySelector("#pe-name").value,
      description: overlay.querySelector("#pe-desc").value,
      icon_emoji: overlay.querySelector("#pe-emoji").value,
      audience_role: overlay.querySelector("#pe-role").value,
      maturity_level_id: level ? Number(level) : null,
      position: Number(overlay.querySelector("#pe-pos").value) || 0,
      is_starter: overlay.querySelector("#pe-starter").checked,
      is_active: overlay.querySelector("#pe-active").checked,
    };
    saveBtn.disabled = true;
    try {
      if (path) await api(`/paths/${path.id}`, { method: "PATCH", body: payload });
      else await api("/paths", { method: "POST", body: payload });
      toast("Збережено");
      close();
      renderPathsManage();
    } catch (err) {
      overlay.querySelector("#pe-err").textContent = err.message;
      saveBtn.disabled = false;
    }
  });
}

// Кроки маршруту: додавання, порядок, видалення.
async function openStepsEditor(path, resources) {
  let data;
  try { data = await api(`/paths/${path.id}`); }
  catch (err) { toast(err.message, "err"); return; }

  const { overlay, close } = catalogModal(`
    <div class="modal sm-modal">
      <div class="modal-head">
        <h2>Кроки: ${esc(path.name)}</h2>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      <div class="step-list" id="mgr-steps">${data.steps.map((s, i) => `
        <div class="step-row">
          <span class="step-check">${i + 1}</span>
          <div class="step-body">
            <div class="step-title">${esc(s.title || s.resource_name || "Крок")}</div>
            <div class="step-note">${s.resource_name
              ? `матеріал: ${esc(s.resource_name)}`
              : `посилання: ${esc(s.external_url || "")}`}${
              s.available ? "" : " · матеріал вилучено"}</div>
          </div>
          <div class="res-acts">
            <button class="small" data-up="${s.id}"${i === 0 ? " disabled" : ""}>↑</button>
            <button class="small" data-down="${s.id}"${i === data.steps.length - 1 ? " disabled" : ""}>↓</button>
            <button class="small danger" data-del="${s.id}">×</button>
          </div>
        </div>`).join("") || `<p class="muted">Кроків ще немає.</p>`}</div>
      <hr class="md-hr">
      <div class="form-grid">
        <label class="wide">Додати крок — матеріал каталогу
          <select id="st-res"><option value="">— оберіть матеріал —</option>${
            resources.map(r => `<option value="${r.id}">${esc(r.name)}</option>`).join("")}</select></label>
        <label class="wide">…або зовнішнє посилання
          <input id="st-url" type="text" placeholder="https://…"></label>
        <label class="wide">Назва кроку (обов'язкова для посилання)
          <input id="st-title" type="text"></label>
        <label class="wide">Що зробити на цьому кроці
          <input id="st-note" type="text" placeholder="напр. «прочитати і застосувати на своєму документі»"></label>
      </div>
      <p class="error" id="st-err"></p>
      <div class="row"><button class="mdc-act" id="st-add">Додати крок</button></div>
    </div>`);

  const reload = () => { close(); openStepsEditor(path, resources); };

  overlay.querySelector("#st-add").addEventListener("click", async () => {
    const resId = overlay.querySelector("#st-res").value;
    try {
      await api(`/paths/${path.id}/steps`, { method: "POST", body: {
        resource_id: resId ? Number(resId) : null,
        external_url: overlay.querySelector("#st-url").value,
        title: overlay.querySelector("#st-title").value,
        note: overlay.querySelector("#st-note").value,
      } });
      toast("Крок додано");
      reload();
    } catch (err) { overlay.querySelector("#st-err").textContent = err.message; }
  });

  const move = async (stepId, delta) => {
    const order = data.steps.map(s => s.id);
    const index = order.indexOf(stepId);
    const target = index + delta;
    if (target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    try {
      await api(`/paths/${path.id}/steps/reorder`, { method: "POST", body: { order } });
      reload();
    } catch (err) { toast(err.message, "err"); }
  };
  overlay.querySelectorAll("[data-up]").forEach(b =>
    b.addEventListener("click", () => move(Number(b.dataset.up), -1)));
  overlay.querySelectorAll("[data-down]").forEach(b =>
    b.addEventListener("click", () => move(Number(b.dataset.down), 1)));
  overlay.querySelectorAll("[data-del]").forEach(b =>
    b.addEventListener("click", async () => {
      try {
        await api(`/paths/${path.id}/steps/${b.dataset.del}`, { method: "DELETE" });
        toast("Крок видалено");
        reload();
      } catch (err) { toast(err.message, "err"); }
    }));
}

// ------------------ Словник формулювань задач (BR-07) ------------------

function renderSynonymRows(rows) {
  const box = $("#syn-list");
  if (!box) return;
  if (!rows.length) {
    box.innerHTML = `<p class="muted" style="margin-top:12px">Формулювань ще немає.</p>`;
    return;
  }
  box.innerHTML = rows.map(s => `
    <div class="res-row">
      <div class="res-main">
        <div class="res-name">${esc(s.phrase)}${
          s.is_active ? "" : ` <span class="muted">(вимкнено)</span>`}</div>
        <div class="res-sub">шукати за: ${esc(s.terms)}</div>
      </div>
      <div class="res-acts">
        <button class="small" data-syn-edit="${s.id}">Редагувати</button>
        <button class="small danger" data-syn-del="${s.id}">Видалити</button>
      </div>
    </div>`).join("");

  box.querySelectorAll("[data-syn-edit]").forEach(b =>
    b.addEventListener("click", () => openSynonymEditor(
      rows.find(x => x.id === Number(b.dataset.synEdit)))));
  box.querySelectorAll("[data-syn-del]").forEach(b =>
    b.addEventListener("click", async () => {
      const row = rows.find(x => x.id === Number(b.dataset.synDel));
      if (!confirm(`Видалити формулювання «${row.phrase}»?`)) return;
      try {
        await api(`/catalog/synonyms/${row.id}`, { method: "DELETE" });
        toast("Формулювання видалено");
        renderResourcesManage();
      } catch (err) { toast(err.message, "err"); }
    }));
}

function openSynonymEditor(row) {
  const s = row || { phrase: "", terms: "", is_active: true };
  const { overlay, close } = catalogModal(`
    <div class="modal res-editor">
      <div class="modal-head">
        <h2>${row ? "Редагувати формулювання" : "Нове формулювання"}</h2>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      <div class="form-grid">
        <label class="wide">Як питає користувач
          <input id="sy-phrase" type="text" value="${esc(s.phrase)}"
                 placeholder="напр. «хочу автоматизувати збір ідей»"></label>
        <label class="wide">За якими словами шукати (через кому)
          <input id="sy-terms" type="text" value="${esc(s.terms)}"
                 placeholder="напр. «ідея, воронка, збір»"></label>
        <label class="check wide"><input type="checkbox" id="sy-active"${
          s.is_active ? " checked" : ""}><span>Використовувати в пошуку</span></label>
      </div>
      <p class="error" id="sy-err"></p>
      <div class="row">
        <button class="mdc-act" id="sy-save">Зберегти</button>
        <button class="ghost" id="sy-cancel">Скасувати</button>
      </div>
    </div>`);

  overlay.querySelector("#sy-cancel").addEventListener("click", close);
  const saveBtn = overlay.querySelector("#sy-save");
  saveBtn.addEventListener("click", async () => {
    const payload = {
      phrase: overlay.querySelector("#sy-phrase").value,
      terms: overlay.querySelector("#sy-terms").value,
      is_active: overlay.querySelector("#sy-active").checked,
    };
    saveBtn.disabled = true;
    try {
      if (row) await api(`/catalog/synonyms/${row.id}`, { method: "PATCH", body: payload });
      else await api("/catalog/synonyms", { method: "POST", body: payload });
      toast("Збережено");
      close();
      renderResourcesManage();
    } catch (err) {
      overlay.querySelector("#sy-err").textContent = err.message;
      saveBtn.disabled = false;
    }
  });
}

// Редактор розділу каталогу.
function openSectionEditor(section) {
  const s = section || { name: "", description: "", icon_emoji: "", accent: "",
                         url: "", position: 0, is_active: true };
  const accentOpts = ACCENT_OPTIONS
    .map(([v, l]) => `<option value="${esc(v)}"${v === (s.accent || "") ? " selected" : ""}>${esc(l)}</option>`).join("");

  const { overlay, close } = catalogModal(`
    <div class="modal res-editor">
      <div class="modal-head">
        <h2>${section ? "Редагувати розділ" : "Новий розділ"}</h2>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      <div class="form-grid">
        <label class="wide">Назва<input id="se-name" type="text" value="${esc(s.name || "")}" placeholder="Напр. «Toolbox»"></label>
        <label class="wide">Опис<input id="se-desc" type="text" value="${esc(s.description || "")}" placeholder="Що містить розділ"></label>
        <label>Емодзі-іконка<input id="se-emoji" type="text" maxlength="4" value="${esc(s.icon_emoji || "")}" placeholder="🧰"></label>
        <label>Колір плитки<select id="se-accent">${accentOpts}</select></label>
        <label class="wide">Посилання на зовнішній портал (необов'язково)
          <input id="se-url" type="text" value="${esc(s.url || "")}" placeholder="https://… — плитка вестиме туди замість фільтра"></label>
        <label>Позиція<input id="se-pos" type="number" value="${Number(s.position) || 0}"></label>
        <label class="check"><input type="checkbox" id="se-active"${s.is_active ? " checked" : ""}>
          <span>Показувати в каталозі</span></label>
      </div>
      <p class="error" id="se-err"></p>
      <div class="row">
        <button class="mdc-act" id="se-save">Зберегти</button>
        <button class="ghost" id="se-cancel">Скасувати</button>
      </div>
    </div>`);

  overlay.querySelector("#se-cancel").addEventListener("click", close);
  const saveBtn = overlay.querySelector("#se-save");
  saveBtn.addEventListener("click", async () => {
    const payload = {
      name: overlay.querySelector("#se-name").value,
      description: overlay.querySelector("#se-desc").value,
      icon_emoji: overlay.querySelector("#se-emoji").value,
      accent: overlay.querySelector("#se-accent").value,
      url: overlay.querySelector("#se-url").value,
      position: Number(overlay.querySelector("#se-pos").value) || 0,
      is_active: overlay.querySelector("#se-active").checked,
    };
    saveBtn.disabled = true;
    try {
      if (section) await api(`/catalog/sections/${section.id}`, { method: "PATCH", body: payload });
      else await api("/catalog/sections", { method: "POST", body: payload });
      toast("Збережено");
      close();
      renderResourcesManage();
    } catch (err) {
      overlay.querySelector("#se-err").textContent = err.message;
      saveBtn.disabled = false;
    }
  });
}

const ACCENT_OPTIONS = [["", "Типовий для виду"], ["red", "Червоний"], ["ink", "Графітовий"],
                        ["steel", "Сталевий"], ["amber", "Бурштиновий"], ["green", "Зелений"]];

// Редактор матеріалу каталогу (створення або зміна).
async function openResourceEditor(resourceId, { onSaved } = {}) {
  let r = { resource_type: "prompt", status: "draft", version: "1.0.0",
            link_scope: "external", tags: [] };
  if (resourceId) {
    try { r = await api(`/catalog/resources/${resourceId}`); }
    catch (err) { toast(err.message, "err"); return; }
  }
  let cats = [], sections = [], folders = [], terms = [];
  try {
    [cats, sections, folders, terms] = await Promise.all([
      api("/categories"), api("/catalog/sections"),
      api("/catalog/folders"), api("/catalog/terms"),
    ]);
  } catch (e) { /* довідники не обов'язкові */ }
  const termsOf = kind => terms.filter(t => t.kind === kind && t.is_active);

  const opt = (v, label, cur) =>
    `<option value="${esc(v)}"${String(v) === String(cur) ? " selected" : ""}>${esc(label)}</option>`;
  // Види матеріалів беремо з довідника: доданий адміністратором вид має
  // з'явитися тут без зміни коду.
  const typeTerms = termsOf("material_type");
  const kindOpts = (typeTerms.length
    ? typeTerms.map(t => opt(t.code, t.name, r.resource_type))
    : CAT_ORDER.filter(k => k !== "skill")
        .map(k => opt(k, CAT_KINDS[k].label, r.resource_type))).join("");
  const sectionOpts = opt("", "— не вказано —", r.section_id || "")
    + sections.map(s => opt(s.id, s.name, r.section_id || "")).join("");
  const statusOpts = statusChoices(r.status, { creating: !resourceId, item: r })
    .map(([v, m]) => opt(v, m.label, r.status)).join("");
  const reuseOpts = REUSE_OPTIONS.map(([v, l]) => opt(v, l, r.reuse_level || "")).join("");
  const termOpts = (kind, cur) => opt("", "— не вказано —", cur || "")
    + termsOf(kind).map(t => opt(t.id, t.name, cur || "")).join("");

  const { overlay, close } = catalogModal(`
    <div class="modal res-editor">
      <div class="modal-head">
        <h2>${resourceId ? "Редагувати матеріал" : "Новий матеріал каталогу"}</h2>
        <button class="modal-x" aria-label="Закрити">×</button>
      </div>
      <div class="form-grid">
        <label>Вид<select id="re-type">${kindOpts}</select></label>
        <label>Статус<select id="re-status">${statusOpts}</select></label>
        <label class="wide">Назва<input id="re-name" type="text" value="${esc(r.name || "")}" placeholder="Напр. «Аналіз тендерної документації»"></label>
        <label class="wide">Короткий опис<textarea id="re-desc" rows="2" placeholder="Одне-два речення — що це і кому корисне">${esc(r.description || "")}</textarea></label>
        <label>Розділ<select id="re-section">${sectionOpts}</select></label>
        <label>Колекція<select id="re-folder"></select></label>
        <label>Категорія<input id="re-cat" type="text" list="re-cats" value="${esc(r.category || "")}">
          <datalist id="re-cats">${cats.map(c => `<option value="${esc(c.name)}">`).join("")}</datalist></label>
        <label>Складність<select id="re-complexity">${termOpts("complexity", r.complexity_id)}</select></label>
        <label class="wide">Рівні AI-зрілості, для яких матеріал
          <span class="lvl-checks">${termsOf("maturity").map(t => `
            <label class="lvl-check"><input type="checkbox" value="${t.id}"${
              (r.maturity_ids || []).includes(t.id) ? " checked" : ""}>
              <span>${esc(t.name)}</span></label>`).join("") ||
            `<span class="muted">Довідник рівнів порожній.</span>`}</span></label>
        <label>Бізнес-цінність<select id="re-value">${termOpts("business_value", r.business_value_id)}</select></label>
        <label>Автор<input id="re-author" type="text" value="${esc(r.author || "")}"></label>
        <label>Власник (відповідальний)<input id="re-owner" type="text" value="${esc(r.owner || "")}" placeholder="Хто підтримує матеріал"></label>
        <label>Контакт власника<input id="re-owner-contact" type="text" value="${esc(r.owner_contact || "")}" placeholder="email або посилання на чат"></label>
        <label>Дата останнього перегляду<input id="re-reviewed" type="date" value="${dateInputValue(r.reviewed_at)}"></label>
        <label>Дата наступного перегляду<input id="re-next-review" type="date" value="${dateInputValue(r.next_review_at)}"></label>
        <label>Інструменти та платформи<input id="re-tools" type="text" value="${esc(r.tools || "")}" placeholder="Copilot Studio, SharePoint"></label>
        <label>Повторне використання<select id="re-reuse">${reuseOpts}</select></label>
        <div class="wide re-body-row"><span>Як повторити рішення</span>
          <div id="re-reuse-guidance"></div></div>
        <label>Теги (через кому)<input id="re-tags" type="text" list="re-taglist"
          value="${esc((r.tags || []).join(", "))}" placeholder="RAG, аналітика">
          <datalist id="re-taglist">${termsOf("tag")
            .map(t => `<option value="${esc(t.name)}">`).join("")}</datalist></label>
        <label>Версія<input id="re-version" type="text" value="${esc(r.version || "1.0.0")}"></label>
        <label>Емодзі-іконка<input id="re-emoji" type="text" maxlength="4" value="${esc(r.icon_emoji || "")}" placeholder="⚙️"></label>
        <label class="wide" id="re-url-row"><span id="re-url-label">Посилання</span>
          <input id="re-url" type="text" value="${esc(r.url || "")}" placeholder="https://… або /внутрішній/шлях"></label>
        <label id="re-scope-row">Тип ресурсу<select id="re-scope">
          ${opt("external", "Зовнішній сервіс", r.link_scope || "external")}
          ${opt("internal", "Внутрішній ресурс", r.link_scope || "external")}
        </select></label>
        <div class="wide re-body-row" id="re-body-row"><span id="re-body-label">Текст</span>
          <div id="re-body"></div></div>
        <label class="check wide"><input type="checkbox" id="re-featured"${r.is_featured ? " checked" : ""}>
          <span>Рекомендований — показувати першим у каталозі</span></label>
      </div>
      <p class="error" id="re-err"></p>
      <div class="row">
        <button class="mdc-act" id="re-save">Зберегти</button>
        <button class="ghost" id="re-cancel">Скасувати</button>
      </div>
    </div>`);

  // Колекції належать розділу, тому список перебудовується при зміні розділу.
  const sectionSel = overlay.querySelector("#re-section");
  const folderSel = overlay.querySelector("#re-folder");
  const syncFolders = (keep) => {
    const sectionId = sectionSel.value;
    const inSection = folders.filter(f => String(f.section_id) === String(sectionId));
    folderSel.innerHTML = opt("", "— не вказано —", keep || "")
      + inSection.map(f => opt(f.id, f.name, keep || "")).join("");
    folderSel.disabled = !sectionId;
    folderSel.title = sectionId ? "" : "Спочатку виберіть розділ";
  };
  syncFolders(r.folder_id);
  sectionSel.addEventListener("change", () => syncFolders(null));

  const typeSel = overlay.querySelector("#re-type");
  // Текст — у візуальному редакторі; промпт і документ відрізняються тим, чи
  // екранується розмітка (див. editor.js).
  const bodyEd = RichEditor.create({ value: r.body || "", label: "Текст",
    kind: typeSel.value === "prompt" ? "prompt" : "doc", onImage: uploadInlineImage });
  if (resourceId) {
    api(`/catalog/resources/${resourceId}/files`).then(files => {
      registerFileSigs(files.filter(f => f.source === "inline"));
      bodyEd.refreshImages();
    }).catch(() => {});
  }
  overlay.querySelector("#re-body").replaceWith(bodyEd.el);
  const reuseEd = RichEditor.create({ value: r.reuse_guidance || "", label: "Як повторити рішення",
    placeholder: "Передумови, кроки, до кого звертатися — щоб колега міг застосувати це у себе" });
  overlay.querySelector("#re-reuse-guidance").replaceWith(reuseEd.el);
  const behaviourOf = (code) => {
    const term = typeTerms.find(t => t.code === code);
    if (term) return term;
    // Довідник порожній (свіжа база) — поведінка за замовчуванням.
    return {
      requires_url: ["agent", "mcp", "link"].includes(code),
      requires_body: ["prompt", "instruction", "case"].includes(code),
      has_scope: ["mcp", "link"].includes(code),
      url_label: code === "mcp" ? "Endpoint сервера" : "Посилання",
      body_label: code === "mcp" ? "Як підключити" : "Текст",
    };
  };
  const syncFields = () => {
    const kind = typeSel.value;
    const b = behaviourOf(kind);
    const showUrl = !!b.requires_url;
    // Текст показуємо і там, де він обов'язковий, і там, де корисний (MCP).
    const showBody = !!b.requires_body || !!b.body_label;
    const showScope = !!b.has_scope;
    overlay.querySelector("#re-url-row").classList.toggle("hidden", !showUrl);
    overlay.querySelector("#re-scope-row").classList.toggle("hidden", !showScope);
    overlay.querySelector("#re-body-row").classList.toggle("hidden", !showBody);

    // Підписи полів теж із довідника — новий вид одразу має свої формулювання.
    overlay.querySelector("#re-url-label").textContent = b.url_label || "Посилання";
    overlay.querySelector("#re-body-label").textContent = b.body_label || "Текст";
    bodyEd.setPlaceholder(kind === "mcp"
      ? "Які інструменти надає сервер і як додати його в Copilot Studio чи іншого клієнта…"
      : "Текст промпту, інструкції або опис кейсу…");
    bodyEd.setKind(kind === "prompt" ? "prompt" : "doc");
  };
  typeSel.addEventListener("change", syncFields);
  syncFields();

  overlay.querySelector("#re-cancel").addEventListener("click", close);
  const saveBtn = overlay.querySelector("#re-save");
  saveBtn.addEventListener("click", async () => {
    const status = overlay.querySelector("#re-status").value;
    const num = (id) => overlay.querySelector(id).value
      ? Number(overlay.querySelector(id).value) : null;
    const payload = {
      resource_type: typeSel.value,
      name: overlay.querySelector("#re-name").value,
      description: overlay.querySelector("#re-desc").value,
      section_id: num("#re-section"),
      folder_id: num("#re-folder"),
      complexity_id: num("#re-complexity"),
      maturity_ids: [...overlay.querySelectorAll(".lvl-check input:checked")]
        .map(cb => Number(cb.value)),
      business_value_id: num("#re-value"),
      category: overlay.querySelector("#re-cat").value,
      author: overlay.querySelector("#re-author").value,
      owner: overlay.querySelector("#re-owner").value,
      owner_contact: overlay.querySelector("#re-owner-contact").value,
      reuse_guidance: reuseEd.getValue(),
      reviewed_at: overlay.querySelector("#re-reviewed").value,
      next_review_at: overlay.querySelector("#re-next-review").value,
      tools: overlay.querySelector("#re-tools").value,
      reuse_level: overlay.querySelector("#re-reuse").value,
      tags: overlay.querySelector("#re-tags").value,
      version: overlay.querySelector("#re-version").value,
      icon_emoji: overlay.querySelector("#re-emoji").value,
      url: overlay.querySelector("#re-url").value,
      link_scope: overlay.querySelector("#re-scope").value,
      body: bodyEd.getValue(),
      is_featured: overlay.querySelector("#re-featured").checked,
    };
    saveBtn.disabled = true;
    try {
      if (resourceId) {
        await api(`/catalog/resources/${resourceId}`, { method: "PATCH", body: payload });
        if (status !== r.status) {
          await api(`/catalog/resources/${resourceId}/status`, { method: "POST", body: { status } });
        }
      } else {
        await api("/catalog/resources", { method: "POST", body: { ...payload, status } });
      }
      toast("Збережено");
      close();
      // Редактор відкривають і з «Керування», і з «Наповнення каталогу» —
      // повертатися треба туди, звідки прийшли.
      (onSaved || renderResourcesManage)();
    } catch (err) {
      overlay.querySelector("#re-err").textContent = err.message;
      saveBtn.disabled = false;
    }
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

// Редактор навички: атрибути, інструкція, іконка, нова версія пакета.
// Відкривається у вікні поверх сторінки — з картки навички чи з рядка в
// «Наповненні каталогу».
async function openSkillEditor(skillId, { onSaved } = {}) {
  const [loaded, cats] = await Promise.all([
    api(`/skills/${skillId}`), api("/categories").catch(() => []),
  ]);
  // Зміни автора, що вже чекають схвалення, — він продовжує з них.
  const s = loaded.pending ? { ...loaded, ...loaded.pending.data } : loaded;
  const sectionOpts = (catalogState.sections || []).filter(x => !x.url)
    .map(x => `<option value="${x.id}"${x.id === s.section_id ? " selected" : ""}>${esc(x.name)}</option>`)
    .join("");
  // Гарантуємо, що поточна категорія навички є серед варіантів (навіть якщо її
  // видалили з довідника).
  const names = cats.map(c => c.name);
  if (s.category && !names.includes(s.category)) names.unshift(s.category);
  const catOptions = [`<option value="">— без категорії —</option>`]
    .concat(names.map(n =>
      `<option value="${esc(n)}" ${n === s.category ? "selected" : ""}>${esc(n)}</option>`))
    .join("");

  const m = catalogModal(`<div class="modal res-editor sk-editor-modal"><div id="sk-edit-m"></div></div>`);
  const box = m.overlay.querySelector("#sk-edit-m");
  const closeBox = () => m.close(true);
  // Після збереження сторінку під вікном оновлює той, хто відкрив редактор.
  const saved = () => { closeBox(); if (onSaved) onSaved(); else go(nav.dest); };
  box.innerHTML = `
    <div class="sk-edit-in">
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
      <div class="row">
        <div class="field" style="flex:1"><label>Розділ <i>його менеджер розгляне навичку</i></label>
          <select id="se-section"><option value="">— не вказано —</option>${sectionOpts}</select></div>
        <div class="field" style="flex:1"><label>Колекція</label><select id="se-folder"></select></div>
      </div>
      ${loaded.pending && !canModerate(loaded) ? `<p class="sm-draft review">Ваші зміни на розгляді: нижче — запропонована версія.
        У каталозі поки опублікована.</p>` : ""}
      <div class="field"><label>Опис</label><textarea id="se-desc">${esc(s.description || "")}</textarea></div>
      <div class="field"><label>Вхідні дані</label><textarea id="se-input" placeholder="Що подавати на вхід навички">${esc(s.input_spec || "")}</textarea></div>
      <div class="field"><label>Результат роботи</label><textarea id="se-output" placeholder="Що навичка повертає на виході">${esc(s.output_spec || "")}</textarea></div>
      <div class="field"><label>Стартовий промпт</label><textarea id="se-starter" placeholder="Приклад стартового промпту для користувача">${esc(s.starter_prompt || "")}</textarea></div>
      ${s.has_package ? "" : `<div class="field"><span class="field-k">Інструкція</span><div id="se-instr"></div></div>`}
      <div class="row">
        <button id="se-save">Зберегти</button>
        <button id="se-close" class="ghost">Закрити</button>
      </div>
      <hr class="md-hr">
      <h3>${s.has_package ? "Нова версія пакета" : "Додати пакет"}</h3>
      <p class="muted">Завантаження пакета тут оновить <b>цю саму</b> навичку: лічильник завантажень, відгуки та місце в каталозі збережуться, а інструкцію буде взято з нового <code>skill.md</code>.</p>
      <div class="row">
        <input type="file" id="se-file" accept=".zip,.skill">
        <button id="se-upload">Завантажити нову версію</button>
      </div>
      <div id="se-files" class="muted" style="margin-top:10px"></div>
    </div>`;

  const syncSkillFolders = (keep) => {
    const sec = $("#se-section").value;
    $("#se-folder").innerHTML = `<option value="">— не вказано —</option>` + (catalogState.folders || [])
      .filter(f => String(f.section_id) === sec)
      .map(f => `<option value="${f.id}"${String(f.id) === String(keep) ? " selected" : ""}>${esc(f.name)}</option>`)
      .join("");
    $("#se-folder").disabled = !sec;
  };
  syncSkillFolders(s.folder_id);
  $("#se-section").addEventListener("change", () => syncSkillFolders(""));

  // Інструкція навички без пакета — у візуальному редакторі.
  const instrEd = s.has_package ? null : RichEditor.create({ value: s.instructions || "",
    label: "Інструкція", placeholder: "Як користуватися навичкою: кроки, приклади, обмеження" });
  if (instrEd) $("#se-instr").replaceWith(instrEd.el);

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
      const num = (v) => v ? Number(v) : null;
      const res = await api(`/skills/${s.id}`, { method: "PATCH", body: {
        section_id: num($("#se-section").value), folder_id: num($("#se-folder").value),
        name: $("#se-name").value, version: $("#se-version").value,
        author: $("#se-author").value, category: $("#se-category").value,
        description: $("#se-desc").value,
        input_spec: $("#se-input").value, output_spec: $("#se-output").value,
        starter_prompt: $("#se-starter").value,
        // Інструкцію пакетної навички бере зі skill.md — тут її не правлять.
        ...(s.has_package ? {} : { instructions: instrEd.getValue() }),
      }});
      toast(res.has_pending && !canModerate(res)
        ? "Зміни надіслано на розгляд — у каталозі поки попередня версія" : "Зміни збережено");
      saved();
    } catch (err) { toast(err.message, "err"); }
  });
  $("#se-close").addEventListener("click", () => closeBox());
  $("#se-upload").addEventListener("click", async () => {
    const file = $("#se-file").files[0];
    if (!file) { toast("Оберіть файл архіву", "err"); return; }
    $("#se-upload").disabled = true;
    try {
      const data = await uploadSkillPackage(file, s.id);
      toast(data.has_pending && !canModerate(data)
        ? "Нову версію надіслано на розгляд — у каталозі поки попередня"
        : `Нову версію «${data.name}» v${data.version} завантажено`);
      saved();
    } catch (err) { toast(err.message, "err"); }
    finally { $("#se-upload").disabled = false; }
  });
}

// ---------- Користувачі (Admin) ----------
// Ролі призначає адміністратор вручну (фідбек тестування, п. 11). Менеджер
// розділу модерує лише обрані розділи; «Експерт» і «Відповідальний за ідеї» —
// ознаки, які можна поставити будь-кому.
const USER_ROLE_OPTS = [
  { v: "", label: "Користувач" },
  { v: "section_manager", label: "Менеджер розділу" },
  { v: "admin", label: "Адміністратор" },
];
function primaryRole(u) {
  if (u.roles.includes("admin")) return "admin";
  if (u.roles.includes("section_manager")) return "section_manager";
  return "";
}
// Роль, розділи й ознаки одним рядком для таблиці користувачів.
function userAccessHtml(u, sections, levels = []) {
  const names = (u.managed_section_ids || [])
    .map(id => (sections.find(x => x.id === id) || {}).name).filter(Boolean);
  const flags = [u.is_expert ? "Експерт" : "", u.is_ideas_owner ? "Відповідальний за ідеї" : ""]
    .filter(Boolean);
  const level = levels.find(l => l.id === u.maturity_level_id);
  return `<div>${esc(roleLabel(u))}</div>${
    primaryRole(u) === "section_manager"
      ? `<div class="u-who-login">${names.length ? esc(names.join(", ")) : "розділи не призначено"}</div>` : ""}${
    flags.length ? `<div class="u-who-login">${esc(flags.join(" · "))}</div>` : ""}${
    level ? `<div class="u-who-login">Рівень: ${esc(level.name)}${
      u.maturity_assessed_at ? `, ${esc(dateUa(u.maturity_assessed_at))}` : ""}</div>` : ""}`;
}

async function viewUsers() {
  const [users, sections, levels] = await Promise.all([
    api("/users"), api("/catalog/sections").catch(() => []),
    api("/catalog/terms?kind=maturity").catch(() => [])]);
  const view = panelEl();
  const roleOpts = (sel) => USER_ROLE_OPTS.map(o =>
    `<option value="${o.v}" ${o.v === sel ? "selected" : ""}>${esc(o.label)}</option>`).join("");
  view.innerHTML = `
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
    <div class="card"><h2>Користувачі</h2><table class="t-users"><thead><tr><th>ID</th><th>Користувач</th><th>Роль і рівень</th><th>Дії</th></tr></thead><tbody id="u-body"></tbody></table></div>`;

  $("#u-add").addEventListener("click", async () => {
    const roles = $("#u-role").value ? [$("#u-role").value] : [];
    try {
      const created = await api("/users", { method: "POST", body: {
        username: $("#u-username").value, full_name: $("#u-name").value,
        email: $("#u-email").value || null, password: $("#u-pass").value, roles }});
      await openTab("users");
      // Менеджер без розділів нічого не модерує — одразу пропонуємо їх обрати.
      if (roles.includes("section_manager")) {
        toast("Користувача створено — оберіть розділи, які він модеруватиме");
        openUserEditor(created.id);
      } else toast("Користувача створено");
    }
    catch (err) { toast(err.message, "err"); }
  });

  const body = $("#u-body");
  users.forEach(u => {
    const protectedUser = u.is_system_admin || u.id === (state.user && state.user.id);
    // Пошту показуємо лише тоді, коли вона відрізняється від логіна:
    // у корпоративних акаунтів це той самий рядок.
    const mail = u.email && u.email.toLowerCase() !== (u.username || "").toLowerCase()
      ? u.email : "";
    const tr = el(`<tr>
      <td>${u.id}</td>
      <td class="u-who">
        <!-- Окремої колонки «Активний» немає: активність — стан за
             замовчуванням. Позначаємо тільки виняток. -->
        <div class="u-who-name">${esc(u.full_name || u.username)}${
          u.is_active ? "" : ` <span class="badge delisted">вимкнений</span>`}</div>
        <div class="u-who-login">${esc(u.username)}${mail ? " · " + esc(mail) : ""}</div>
      </td>
      <td>${userAccessHtml(u, sections, levels)}</td>
      <td class="actions">
        <button class="small ghost" data-act="edit">Редагувати</button>
        <button class="small" data-act="reset">Пароль</button>
        <button class="small danger" data-act="del" ${protectedUser ? "disabled title='Захищений обліковий запис'" : ""}>Видалити</button>
      </td>
    </tr>`);
    tr.querySelector("[data-act='edit']").addEventListener("click", () => openUserEditor(u.id));
    tr.querySelector("[data-act='reset']").addEventListener("click", async () => {
      const p = prompt("Новий пароль (мін. 6 символів):");
      if (!p) return;
      try { await api(`/users/${u.id}/reset-password`, { method: "POST", body: { password: p }}); toast("Пароль оновлено"); }
      catch (err) { toast(err.message, "err"); }
    });
    const delBtn = tr.querySelector("[data-act='del']");
    if (delBtn && !protectedUser) delBtn.addEventListener("click", async () => {
      if (!confirm(`Видалити користувача «${u.username}»?\n\nБудуть видалені його обране, сповіщення, прогрес навчання та файли, які він додав до карток. Ідеї й відгуки лишаться без прив'язки до автора. Дію не можна скасувати.`)) return;
      try { await api(`/users/${u.id}`, { method: "DELETE" }); toast("Користувача видалено"); openTab("users"); }
      catch (err) { toast(err.message, "err"); }
    });
    body.appendChild(tr);
  });
  watchTableFit(body.closest("table"));
}

// Редактор користувача: ім'я, пошта, роль, розділи менеджера, ознаки, стан
// і рівень AI-зрілості за результатами корпоративного тестування (фідбек, п. 4).
async function openUserEditor(userId) {
  const [u, sections, levels] = await Promise.all([
    api(`/users/${userId}`), api("/catalog/sections").catch(() => []),
    api("/catalog/terms?kind=maturity").catch(() => [])]);
  const levelOpts = `<option value="">Не визначено</option>` + levels.filter(l => l.is_active !== false
    || l.id === u.maturity_level_id).map(l =>
    `<option value="${l.id}" ${l.id === u.maturity_level_id ? "selected" : ""}>${esc(l.name)}</option>`).join("");
  const today = new Date().toISOString().slice(0, 10);
  const assessed = u.maturity_assessed_at ? u.maturity_assessed_at.slice(0, 10) : "";
  const roleOpts = USER_ROLE_OPTS.map(o =>
    `<option value="${o.v}" ${o.v === primaryRole(u) ? "selected" : ""}>${esc(o.label)}</option>`).join("");
  const mine = new Set(u.managed_section_ids || []);
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
        <div class="field" style="flex:1"><label>Рівень AI-зрілості</label>
          <select id="ue-level">${levelOpts}</select></div>
        <div class="field" style="flex:1"><label>Дата визначення</label>
          <input id="ue-level-date" type="date" max="${today}" value="${esc(assessed)}"></div>
      </div>
      <p class="muted ue-hint">Рівень — за результатами корпоративного тестування; сам
      користувач його не змінює. Без дати збережеться сьогоднішня.</p>
      <div class="field ue-sections" id="ue-sections">
        <span class="field-k">Розділи, які модерує</span>
        <div class="ue-checks">${sections.filter(x => !x.url).map(x => `
          <label class="ind-toggle"><input type="checkbox" value="${x.id}"${mine.has(x.id) ? " checked" : ""}>
            <span>${esc(x.name)}</span></label>`).join("") || `<p class="muted">Розділів ще немає.</p>`}</div>
        <p class="muted ue-hint">Заступнику призначте ту саму роль і ті самі розділи — права в них однакові.</p>
      </div>
      <div class="field">
        <span class="field-k">Ознаки</span>
        <div class="ue-checks">
          <label class="ind-toggle"><input type="checkbox" id="ue-expert"${u.is_expert ? " checked" : ""}>
            <span><b>Експерт</b> — позначка біля автора на матеріалах, які він опублікує відтепер</span></label>
          <label class="ind-toggle"><input type="checkbox" id="ue-ideas"${u.is_ideas_owner ? " checked" : ""}>
            <span><b>Відповідальний за ідеї</b> — опрацьовує ідеї в «Керуванні»</span></label>
        </div>
      </div>
      <div class="row">
        <button id="ue-save">Зберегти</button>
        <button id="ue-close" class="ghost">Закрити</button>
      </div>
    </div>`;
  box.scrollIntoView({ behavior: "smooth", block: "start" });
  const paintSections = () => {
    $("#ue-sections").classList.toggle("hidden", $("#ue-role").value !== "section_manager");
  };
  paintSections();
  $("#ue-role").addEventListener("change", paintSections);
  // Дата має сенс лише разом із рівнем.
  const paintLevelDate = () => {
    const none = !$("#ue-level").value;
    $("#ue-level-date").disabled = none;
    if (none) $("#ue-level-date").value = "";
  };
  paintLevelDate();
  $("#ue-level").addEventListener("change", paintLevelDate);

  $("#ue-close").addEventListener("click", () => { box.innerHTML = ""; });
  $("#ue-save").addEventListener("click", async () => {
    const role = $("#ue-role").value;
    const sectionIds = role === "section_manager"
      ? [...box.querySelectorAll("#ue-sections input:checked")].map(i => Number(i.value)) : [];
    if (role === "section_manager" && !sectionIds.length) {
      toast("Оберіть розділи, які модеруватиме менеджер", "err");
      return;
    }
    const level = $("#ue-level").value;
    const levelDate = $("#ue-level-date").value;
    try {
      await api(`/users/${userId}`, { method: "PATCH", body: {
        full_name: $("#ue-name").value, email: $("#ue-email").value || null,
        is_active: $("#ue-active").value === "1", roles: role ? [role] : [],
        managed_section_ids: sectionIds,
        is_expert: $("#ue-expert").checked, is_ideas_owner: $("#ue-ideas").checked,
        maturity_level_id: level ? Number(level) : null,
        ...(level && levelDate ? { maturity_assessed_at: levelDate } : {}) }});
      toast("Користувача оновлено"); openTab("users");
    } catch (err) { toast(err.message, "err"); }
  });
}

// ---------- Знайомство з інтерфейсом ----------
//
// Під час першого входу хаб по черзі підсвічує головні елементи екрана й
// пояснює кожен одним-двома реченнями. Людина проклацує кроки («Далі» або
// клік по підсвіченому), може повернутися чи пропустити все. Останній крок —
// інструкція. Позначка «пройдено» живе на сервері (users.tour_completed_at),
// тож на іншому пристрої знайомство не повториться; повторити можна з меню
// профілю.

const TOUR_LOCAL_KEY = "hub-tour-done";
let tourState = null;
let tourPending = false;

function tourVisible(node) {
  if (!node || !node.isConnected) return false;
  const r = node.getBoundingClientRect();
  // На телефоні бічна панель схована за лівим краєм — її пункти «невидимі».
  return r.width > 0 && r.height > 0 && r.right > 0 && r.left < window.innerWidth
    && getComputedStyle(node).visibility !== "hidden";
}

// Перший видимий з переліку: той самий крок на телефоні показує інший
// елемент (вкладку внизу замість пункту панелі).
function tourPick(...sels) {
  for (const sel of sels) {
    const node = document.querySelector(sel);
    if (tourVisible(node)) return node;
  }
  return null;
}

// «Розділи» — заголовок групи разом із пунктами під ним.
function tourSections() {
  const items = [...document.querySelectorAll("#sidebar > *")];
  const i = items.findIndex(x => x.classList.contains("sb-group") && /Розділи/.test(x.textContent));
  if (i < 0 || !tourVisible(items[i])) return null;
  const out = [items[i]];
  for (let j = i + 1; j < items.length && items[j].classList.contains("sb-item"); j++) out.push(items[j]);
  return out;
}

// «Підтримка» — агент і «Зворотний зв'язок» унизу бічної панелі.
function tourSupport() {
  const items = [...document.querySelectorAll('#sidebar [data-support="agent"], #sidebar [data-contact]')]
    .filter(tourVisible);
  return items.length ? items : null;
}

function tourSteps() {
  const phone = window.matchMedia("(max-width: 760px)").matches;
  return [
    { at: () => tourPick(".spotlight"), title: "Пошук",
      text: "Пишіть звичайними словами: «протокол наради», «тендер». Каталог звузиться одразу, "
        + "а Enter запустить помічника, який підбере матеріали під задачу."
        + (phone ? "" : " Швидкий виклик: Ctrl+K.") },
    { at: () => tourPick('#sidebar [data-dest="hub:all"]', '#tabbar [data-tab="all"]'),
      side: "right", title: "Матеріали",
      text: "Увесь каталог в одному місці. Обирайте вид угорі й звужуйте вибір фільтрами." },
    { at: () => tourSections() || tourPick("#side-toggle"), side: "right",
      title: phone ? "Бічна панель" : "Розділи",
      text: phone ? "Тут розділи каталогу, ваші публікації й інструкція."
                  : "Каталог за темами. Розділ зі стрілкою відкриває зовнішній портал." },
    { at: () => tourPick('#sidebar [data-dest="hub:dev"]', "#home-level"), side: "right",
      title: "Мій розвиток",
      text: "Ваш рівень роботи з AI за результатами корпоративного тестування, наступний "
        + "крок і навчальні маршрути. Рівень видно й на головній — під привітанням." },
    { at: () => tourPick("#view article.mdc"), title: "Картка матеріалу",
      text: "Клацніть картку, щоб відкрити опис. Кнопка внизу копіює промпт чи завантажує "
        + "навичку, а зірочка зберігає матеріал в «Обране»." },
    { at: () => tourPick("#cat-add"), title: "Додати матеріал",
      text: "Додайте свій промпт, інструкцію чи навичку за три кроки. Власником матеріалу "
        + "стаєте ви." },
    { at: () => tourPick('#sidebar [data-dest="hub:contrib"]'), side: "right",
      title: "Наповнення каталогу",
      text: "Усе, що ви додали: чернетки, подане на розгляд і опубліковане. Матеріал "
        + "з'являється в каталозі після схвалення менеджером розділу." },
    { when: () => hasConsole(),
      at: () => tourPick('#mode-seg [data-mode="admin"]', '#tabbar [data-tab="admin"]'),
      title: "Керування",
      text: "Консоль за вашою роллю: черга матеріалів на розгляді у ваших розділах, "
        + "аналітика, а в адміністратора — структура й користувачі. Число показує, "
        + "скільки матеріалів чекає рішення." },
    { at: () => tourPick("#notif-btn"), title: "Сповіщення",
      text: "Коментарі до ваших матеріалів, рішення менеджера й нагадування про перевірку. "
        + "Клік відкриває потрібну картку." },
    { at: () => tourPick("#me-btn"), title: "Профіль",
      text: "Обране, ваші ідеї й контент, світла чи темна тема. Тут же можна ще раз пройти "
        + "це знайомство." },
    { at: () => tourSupport(), side: "right", title: "Підтримка",
      text: "AI SD агент у Teams допоможе з технічними питаннями. «Зворотний зв'язок» — "
        + "для нетехнічних питань адміністратору хабу: ролі, розділи, зміст каталогу." },
    { at: () => tourPick("#help-btn"), title: "Інструкція", guide: true,
      text: "Детальна інструкція з кроками й знімками екрана. Відкривається в новій вкладці; "
        + "посилання на неї є й останнім пунктом бічної панелі." },
  ].filter(s => (!s.when || s.when()) && s.at());
}

function tourDone() {
  if (!state.user || state.user.tour_completed_at) return true;
  try { return localStorage.getItem(`${TOUR_LOCAL_KEY}:${state.user.id}`) === "1"; }
  catch (e) { return false; }
}

function markTourDone() {
  if (!state.user) return;
  state.user.tour_completed_at = new Date().toISOString();
  saveSession();
  api("/catalog/profile", { method: "PATCH", body: { tour_completed: true } }).catch(() => {
    // Сервер недоступний — хоча б у цьому браузері не показуємо знову.
    try { localStorage.setItem(`${TOUR_LOCAL_KEY}:${state.user.id}`, "1"); } catch (e) { /* ні */ }
  });
}

function waitFor(cond, timeout) {
  return new Promise(resolve => {
    const t0 = Date.now();
    const tick = () => {
      if (cond()) resolve(true);
      else if (Date.now() - t0 > timeout) resolve(false);
      else setTimeout(tick, 120);
    };
    tick();
  });
}

// Чекаємо, доки головна домалюється: панель із розділами й картки.
async function tourWhenReady() {
  const ok = await waitFor(() => $("#view .page-h1") && $("#sidebar .sb-item"), 10000);
  if (ok) await waitFor(() => $("#view article.mdc"), 2500);
  return ok;
}

async function maybeStartTour() {
  if (tourPending || tourState || tourDone()) return;
  tourPending = true;
  surveyShown = true;   // опитування не накладається на знайомство
  const ok = await tourWhenReady();
  tourPending = false;
  if (ok && !tourDone()) startTour();
}

async function replayTour() {
  if (tourState) return;
  if (nav.dest !== "hub:home") go("hub:home");
  if (await tourWhenReady()) startTour();
}

function startTour() {
  if (tourState) return;
  const steps = tourSteps();
  if (!steps.length) return;
  document.querySelectorAll(".survey-bar").forEach(b => b.remove());
  const root = el(`<div class="tour" role="dialog" aria-modal="true"
        aria-labelledby="tour-title" aria-describedby="tour-text">
      <div class="tour-hole" title="Далі"></div>
      <div class="tour-pop">
        <div class="tour-bar" aria-hidden="true"><i id="tour-bar"></i></div>
        <div class="tour-top">
          <span class="tour-count" id="tour-count"></span>
          <button type="button" class="tour-skip" id="tour-skip">Пропустити</button>
        </div>
        <h3 id="tour-title"></h3>
        <p id="tour-text"></p>
        <div class="tour-foot">
          <button type="button" class="ghost" id="tour-back">Назад</button>
          <button type="button" class="ghost hidden" id="tour-guide">Відкрити інструкцію</button>
          <button type="button" id="tour-next">Далі</button>
        </div>
      </div>
    </div>`);
  document.body.appendChild(root);
  const s = tourState = {
    steps, i: 0, root,
    hole: root.querySelector(".tour-hole"), pop: root.querySelector(".tour-pop"),
  };

  const openGuide = () => window.open(GUIDE_URL, "_blank", "noopener");
  const next = () => {
    if (s.i >= s.steps.length - 1) { endTour(); return; }
    tourShow(s.i + 1, 1);
  };
  const back = () => { if (s.i > 0) tourShow(s.i - 1, -1); };
  root.querySelector("#tour-next").addEventListener("click", next);
  root.querySelector("#tour-back").addEventListener("click", back);
  root.querySelector("#tour-skip").addEventListener("click", () => endTour());
  root.querySelector("#tour-guide").addEventListener("click", () => { openGuide(); endTour(); });
  // Клік по підсвіченому елементу — теж «далі»; на останньому кроці він
  // робить те саме, що й сам значок: відкриває інструкцію.
  s.hole.addEventListener("click", () => {
    if (s.steps[s.i].guide) { openGuide(); endTour(); } else next();
  });

  s.onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); endTour(); return; }
    if (e.key === "ArrowRight") { e.preventDefault(); e.stopPropagation(); next(); return; }
    if (e.key === "ArrowLeft") { e.preventDefault(); e.stopPropagation(); back(); return; }
    if (e.key === "Tab") {
      // Фокус не виходить за межі підказки.
      const btns = [...s.pop.querySelectorAll("button:not(.hidden):not([disabled])")];
      const i = btns.indexOf(document.activeElement);
      e.preventDefault(); e.stopPropagation();
      btns[(i + (e.shiftKey ? -1 : 1) + btns.length) % btns.length].focus();
    }
  };
  document.addEventListener("keydown", s.onKey, true);
  let raf = 0;
  s.onMove = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(tourPlace); };
  window.addEventListener("resize", s.onMove);
  window.addEventListener("scroll", s.onMove, true);
  tourShow(0, 1);
}

function tourShow(i, dir) {
  const s = tourState;
  // Елемент міг зникнути (панель перебудувалася) — беремо сусідній крок.
  while (i >= 0 && i < s.steps.length && !s.steps[i].at()) i += dir;
  if (i < 0 || i >= s.steps.length) { if (dir > 0) endTour(); return; }
  s.i = i;
  const step = s.steps[i];
  const target = step.at();
  const anchor = Array.isArray(target) ? target[0] : target;
  const r = anchor.getBoundingClientRect();
  if (r.top < 64 || r.bottom > window.innerHeight - 90) {
    anchor.scrollIntoView({ block: "center", inline: "nearest" });
  }
  const last = i === s.steps.length - 1;
  s.root.querySelector("#tour-count").textContent = `Крок ${i + 1} з ${s.steps.length}`;
  s.root.querySelector("#tour-title").textContent = step.title;
  s.root.querySelector("#tour-text").textContent = step.text;
  s.root.querySelector("#tour-bar").style.width = `${((i + 1) / s.steps.length) * 100}%`;
  // На останньому кроці дві дії — «Назад» лишається за клавішею ←.
  s.root.querySelector("#tour-back").classList.toggle("hidden", i === 0 || last);
  s.root.querySelector("#tour-guide").classList.toggle("hidden", !step.guide);
  const nextBtn = s.root.querySelector("#tour-next");
  nextBtn.textContent = last ? "Готово" : "Далі";
  s.hole.title = step.guide ? "Відкрити інструкцію" : last ? "Готово" : "Далі";
  tourPlace();
  requestAnimationFrame(tourPlace);   // після прокрутки
  nextBtn.focus({ preventScroll: true });
}

function tourRect(target) {
  const list = Array.isArray(target) ? target : [target];
  const rs = list.map(x => x.getBoundingClientRect());
  const top = Math.min(...rs.map(r => r.top)), left = Math.min(...rs.map(r => r.left));
  const bottom = Math.max(...rs.map(r => r.bottom)), right = Math.max(...rs.map(r => r.right));
  return { top, left, bottom, right, width: right - left, height: bottom - top };
}

// Підсвітка — «дірка» в затемненні навколо елемента; підказка стає збоку
// від неї там, де вистачає місця.
function tourPlace() {
  const s = tourState;
  if (!s) return;
  const target = s.steps[s.i].at();
  if (!target) return;
  const pad = 6, gap = 14, m = 12;
  const r = tourRect(target);
  const H = window.innerHeight, W = window.innerWidth;
  const hole = { top: r.top - pad, left: r.left - pad, width: r.width + pad * 2, height: r.height + pad * 2 };
  Object.assign(s.hole.style, { top: `${hole.top}px`, left: `${hole.left}px`,
                                width: `${hole.width}px`, height: `${hole.height}px` });
  const pw = s.pop.offsetWidth, ph = s.pop.offsetHeight;
  const clampX = x => Math.max(m, Math.min(x, W - pw - m));
  const clampY = y => Math.max(m, Math.min(y, H - ph - m));
  const midX = clampX(hole.left + hole.width / 2 - pw / 2);
  const midY = clampY(hole.top + hole.height / 2 - ph / 2);
  const spots = {
    bottom: { ok: hole.top + hole.height + gap + ph <= H - m, top: hole.top + hole.height + gap, left: midX },
    top: { ok: hole.top - gap - ph >= m, top: hole.top - gap - ph, left: midX },
    right: { ok: hole.left + hole.width + gap + pw <= W - m, top: midY, left: hole.left + hole.width + gap },
    left: { ok: hole.left - gap - pw >= m, top: midY, left: hole.left - gap - pw },
  };
  const order = s.steps[s.i].side === "right"
    ? ["right", "bottom", "top", "left"] : ["bottom", "top", "right", "left"];
  const pick = order.map(k => spots[k]).find(x => x.ok)
    || { top: H - ph - m, left: clampX((W - pw) / 2) };
  s.pop.style.top = `${pick.top}px`;
  s.pop.style.left = `${pick.left}px`;
}

function endTour() {
  const s = tourState;
  if (!s) return;
  tourState = null;
  document.removeEventListener("keydown", s.onKey, true);
  window.removeEventListener("resize", s.onMove);
  window.removeEventListener("scroll", s.onMove, true);
  s.root.remove();
  markTourDone();
}

// ---------- Старт ----------
loadSession();
// Повернення з корпоративного входу перекриває збережену сесію: користувач
// щойно свідомо увійшов саме цим акаунтом.
consumeAuthFragment();
if (state.token) {
  // Перевіряємо валідність токена; якщо протух — на логін.
  api("/auth/me").then(u => { state.user = u; saveSession(); showApp(); })
    .catch(() => logout());
} else {
  showLogin();
}
