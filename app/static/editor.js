// =====================================================================
// Візуальний редактор тексту матеріалів (фідбек тестування, п. 3).
//
// Людина бачить оформлений текст і панель кнопок, як у Word; зберігається
// той самий Markdown, що й раніше. Тому старі матеріали, пошук, копіювання
// промптів і показ у картці працюють без змін, а вкладка «Markdown» дає
// правити текст розміткою тим, кому так зручніше.
//
// Вставка з Word, Google Docs чи сайту очищується до того, що вміє картка:
// заголовки, абзаци, жирний і курсив, списки, посилання, цитати, код,
// таблиці. Стилі й шрифти відкидаються, щоб картки виглядали однаково.
//
// Колір, розмір і виділення (фідбек тестування, п. 2–3) — лише з
// фіксованого набору: колір тексту — один із TEXT_COLORS, розмір — дрібний,
// звичайний чи великий, виділення — підкреслення й маркер. У Markdown вони
// лягають безпечним HTML із назвою, а не кодом кольору:
// <span class="tc-red">, <span class="ts-large">, <u>, <mark>. Тож зміна
// палітри — це лише CSS, а збережені матеріали не міняються. Кольори з Word
// підбираються до найближчого з набору (nearestColor); чорний і білий —
// це звичайний текст.
//
// Зображення в тексті (фідбек, п. 3.2) — це файли хабу: кнопка «Зображення»,
// вставка знімка з буфера чи перетягнутий файл завантажуються через
// onImage(file) → { id, url }, а в Markdown лягає постійне посилання
// `/api/catalog/files/<id>/preview`. Картинки з документа, що вставляється
// разом із текстом, переносяться під час імпорту файла .docx, а не з буфера:
// браузер дає на них лише недоступні локальні шляхи.
//
// Два види тексту:
//   doc    — інструкції, кейси, описи: безпечний HTML у розмітці дозволено,
//            символи розмітки в тексті екрануються;
//   prompt — промпти: текст копіюють у модель як є, тож нічого не
//            екрануємо, а «<документ>» лишається текстом.
//
// Залежить від renderMarkdown(), hydrateImages() і esc() з app.js
// (викликаються лише під час роботи, тому порядок підключення скриптів не
// важливий).
// =====================================================================
(function () {
  "use strict";

  const ICON = {
    ul: '<circle cx="5" cy="7" r="1.2"/><circle cx="5" cy="12" r="1.2"/><circle cx="5" cy="17" r="1.2"/><path d="M9.5 7h10M9.5 12h10M9.5 17h10"/>',
    ol: '<path d="M4 5.5h1.5v4M4 9.5h3M4.2 14.2a1.4 1.4 0 0 1 2.6.6c0 .9-2.8 2.2-2.8 3.2h3M10 7h10M10 12h10M10 17h10"/>',
    quote: '<path d="M5 7h5v5H7.5L6 16M14 7h5v5h-2.5L15 16"/>',
    code: '<path d="m8.5 7-5 5 5 5M15.5 7l5 5-5 5"/>',
    link: '<path d="M10.5 13.5a3.5 3.5 0 0 0 5 0l3-3a3.5 3.5 0 0 0-5-5l-1 1M13.5 10.5a3.5 3.5 0 0 0-5 0l-3 3a3.5 3.5 0 0 0 5 5l1-1"/>',
    table: '<rect x="3.5" y="5" width="17" height="14" rx="1.5"/><path d="M3.5 10h17M3.5 14.5h17M9.5 5v14M15 5v14"/>',
    hr: '<path d="M4 12h16"/><path d="M8 7.5h8M8 16.5h8" opacity=".45"/>',
    image: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m4.5 17.5 5-5 4 4 2.5-2.5 4 4"/>',
    mark: '<path d="m14.5 4.5 5 5-8 8H6.5v-5l8-8Z"/><path d="M4 20.5h16" stroke-width="2.4"/>',
    size: '<path d="M3.5 18 8 6l4.5 12M5.2 14h5.6"/><path d="M14 18l3-7.5 3 7.5M15.1 15.6h3.8"/>',
  };
  // Набір кольорів тексту: назва → [підпис, колір-ознака для execCommand].
  // Ознака — службове значення, за яким нормалізація впізнає, що саме обрали;
  // на екрані колір дає CSS-клас tc-<назва> (style.css, --tc-*). Значення
  // тимчасові, остаточні дасть дизайнерка.
  const TEXT_COLORS = {
    red: ["Червоний", "#d70015"], orange: ["Помаранчевий", "#c25e00"],
    green: ["Зелений", "#1e8e3e"], blue: ["Синій", "#0064d2"],
    purple: ["Фіолетовий", "#8e44ad"], gray: ["Сірий", "#6e6e73"],
  };
  const TEXT_SIZES = { small: "Дрібний", normal: "Звичайний", large: "Великий" };
  // execCommand("fontSize") ставить <font size="N">: 2 — дрібний, 5 — великий.
  const SIZE_BY_FONT = { 2: "small", 5: "large" };
  const FONT_BY_SIZE = { small: "2", large: "5" };
  const MARK_HEX = "#fff3a3";
  const FMT_CLASS_RE = /^(tc-(red|orange|green|blue|purple|gray)|ts-(small|large))$/;

  function rgbOf(value) {
    const v = String(value || "").trim().toLowerCase();
    let m = v.match(/^#?([0-9a-f]{6})$/);
    if (m) return [0, 2, 4].map(i => parseInt(m[1].slice(i, i + 2), 16));
    m = v.match(/^#([0-9a-f]{3})$/);
    if (m) return [...m[1]].map(c => parseInt(c + c, 16));
    m = v.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
    return m ? [+m[1], +m[2], +m[3]] : null;
  }
  const sameRgb = (a, b) => a && b && a.every((x, i) => Math.abs(x - b[i]) <= 1);

  // Колір, який точно обрали в панелі (за ознакою), або null.
  function pickedColor(value) {
    const rgb = rgbOf(value);
    return Object.keys(TEXT_COLORS).find(k => sameRgb(rgb, rgbOf(TEXT_COLORS[k][1]))) || null;
  }

  // Найближчий колір набору до довільного (Word, Google Docs). Чорний,
  // темно-сірий і білий — звичайний текст: так у документах пишуть основний текст.
  function nearestColor(value) {
    const rgb = rgbOf(value);
    if (!rgb) return null;
    const [r, g, b] = rgb.map(x => x / 255);
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    const light = (max + min) / 2;
    const sat = max === min ? 0 : (max - min) / (1 - Math.abs(2 * light - 1));
    if (sat < 0.2) return light < 0.3 || light > 0.85 ? null : "gray";
    let hue;
    if (max === r) hue = 60 * (((g - b) / (max - min)) % 6);
    else if (max === g) hue = 60 * ((b - r) / (max - min) + 2);
    else hue = 60 * ((r - g) / (max - min) + 4);
    hue = (hue + 360) % 360;
    const HUES = { red: 355, orange: 30, green: 140, blue: 212, purple: 285 };
    const dist = (h) => Math.min(Math.abs(hue - h), 360 - Math.abs(hue - h));
    return Object.keys(HUES).sort((a, b2) => dist(HUES[a]) - dist(HUES[b2]))[0];
  }

  // Промпт копіюють у модель як текст: оформлення з документа чи з
  // інструкції, перетвореної на промпт, прибираємо, лишаючи слова.
  function stripFormatting(md) {
    let prev, out = String(md || "");
    do {
      prev = out;
      out = out.replace(/<span class="t[cs]-[a-z]+">([\s\S]*?)<\/span>/g, "$1")
        .replace(/<(u|mark)>([\s\S]*?)<\/\1>/g, "$2");
    } while (out !== prev);
    return out;
  }

  // Своя картинка: постійне посилання на файл хабу (у src — ще й підпис).
  const FILE_IMG_RE = /\/api\/catalog\/files\/(\d+)\/preview/;
  const IMAGE_FILE_RE = /\.(png|jpe?g|gif|webp|avif)$/i;
  const isImageFile = (f) => /^image\/(png|jpeg|gif|webp|avif)$/i.test(f.type || "") || IMAGE_FILE_RE.test(f.name || "");
  const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name]}</svg>`;

  // ------------------------------------------------------------------
  // HTML → Markdown
  // ------------------------------------------------------------------
  const BLOCK = new Set(["P", "DIV", "H1", "H2", "H3", "H4", "H5", "H6", "UL", "OL", "LI",
    "BLOCKQUOTE", "PRE", "TABLE", "HR", "SECTION", "ARTICLE", "HEADER", "FOOTER",
    "FIGURE", "MAIN", "ASIDE", "NAV", "DL", "DT", "DD"]);

  const hasBlockChild = (el) => [...el.children].some(c => BLOCK.has(c.tagName));

  function makeConverter(kind) {
    const prompt = kind === "prompt";

    function escapeText(t) {
      if (prompt) return t;
      return t.replace(/\\/g, "\\\\").replace(/([*`<])/g, "\\$1").replace(/__/g, "\\_\\_");
    }

    // Розмітку ставимо щільно до слова: «** слово**» Markdown не розпізнає.
    function wrap(inner, mark, close = mark) {
      const m = inner.match(/^(\s*)([\s\S]*?)(\s*)$/);
      if (!m[2]) return inner;
      return m[1] + mark + m[2] + close + m[3];
    }
    // Колір, розмір і виділення — у промпті їх немає: текст іде в модель.
    const fmtClass = (el) => [...el.classList].find(c => FMT_CLASS_RE.test(c));

    function safeHref(href) {
      const v = (href || "").trim();
      return /^(https?:|mailto:)/i.test(v) ? v.replace(/\)/g, "%29").replace(/\s/g, "%20") : "";
    }

    function inline(node) {
      let out = "";
      node.childNodes.forEach(ch => { out += inlineNode(ch); });
      return out;
    }

    function inlineNode(ch) {
      if (ch.nodeType === 3) {
        return escapeText(ch.nodeValue.replace(/\u00a0/g, " ").replace(/[ \t\r\n\f]+/g, " "));
      }
      if (ch.nodeType !== 1) return "";
      switch (ch.tagName) {
        case "BR": return "\n";
        case "STRONG": case "B": return wrap(inline(ch), "**");
        case "EM": case "I": return wrap(inline(ch), "*");
        case "U": return prompt ? inline(ch) : wrap(inline(ch), "<u>", "</u>");
        case "MARK": return prompt ? inline(ch) : wrap(inline(ch), "<mark>", "</mark>");
        case "SPAN": {
          const cls = prompt ? null : fmtClass(ch);
          return cls ? wrap(inline(ch), `<span class="${cls}">`, "</span>") : inline(ch);
        }
        case "CODE": {
          const t = ch.textContent;
          return t && !t.includes("`") ? "`" + t + "`" : escapeText(t);
        }
        case "A": {
          const href = safeHref(ch.getAttribute("href"));
          const text = inline(ch).trim();
          if (!href) return text;
          if (!text || text === href || text === escapeText(href)) return href;
          return `[${text.replace(/\n/g, " ")}](${href})`;
        }
        case "IMG": {
          const id = ch.getAttribute("data-file")
            || (String(ch.getAttribute("src") || "").match(FILE_IMG_RE) || [])[1];
          if (!id || prompt) return "";
          const alt = (ch.getAttribute("alt") || "").replace(/[[\]\n]/g, " ").trim();
          return `![${alt}](/api/catalog/files/${id}/preview)`;
        }
        case "SCRIPT": case "STYLE": return "";
        default: return inline(ch);
      }
    }

    const oneLine = (t) => t.replace(/\s*\n\s*/g, " ").trim();
    // Порожні рядки посеред абзацу розірвали б його на два.
    const tidyPara = (t) => t.replace(/[ \t]+\n/g, "\n").replace(/\n[ \t]+/g, "\n")
      .replace(/\n{2,}/g, "\n").trim();

    function listToMd(list, out) {
      const ordered = list.tagName === "OL";
      let n = 1;
      [...list.children].forEach(li => {
        if (li.tagName === "UL" || li.tagName === "OL") { listToMd(li, out); return; }
        const nested = [];
        let text = "";
        li.childNodes.forEach(ch => {
          if (ch.nodeType === 1 && (ch.tagName === "UL" || ch.tagName === "OL")) nested.push(ch);
          else if (ch.nodeType === 1 && BLOCK.has(ch.tagName)) text += " " + inline(ch) + " ";
          else text += inlineNode(ch);
        });
        text = oneLine(text);
        if (text) out.push((ordered ? `${n++}. ` : "- ") + text);
        // Вкладені списки картка не показує окремим рівнем — пункти йдуть далі.
        nested.forEach(nl => listToMd(nl, out));
      });
      return out;
    }

    function cell(c) {
      let t = "";
      c.childNodes.forEach(ch => {
        t += (ch.nodeType === 1 && BLOCK.has(ch.tagName)) ? " " + inline(ch) + " " : inlineNode(ch);
      });
      return oneLine(t).replace(/\|/g, "&#124;") || " ";
    }

    function tableToMd(table) {
      const rows = [...table.querySelectorAll("tr")].filter(tr => tr.closest("table") === table)
        .map(tr => [...tr.children].filter(c => c.tagName === "TD" || c.tagName === "TH").map(cell))
        .filter(r => r.length);
      if (!rows.length) return "";
      const width = Math.max(...rows.map(r => r.length));
      const norm = rows.map(r => r.concat(Array(width - r.length).fill(" ")));
      const line = (r) => "| " + r.join(" | ") + " |";
      return [line(norm[0]), line(Array(width).fill("---")), ...norm.slice(1).map(line)].join("\n");
    }

    function blocks(node, out) {
      let buf = [];
      const flush = () => {
        if (!buf.length) return;
        let t = "";
        buf.forEach(n => { t += inlineNode(n); });
        t = tidyPara(t);
        if (t) out.push(t);
        buf = [];
      };
      node.childNodes.forEach(ch => {
        if (ch.nodeType === 3 || (ch.nodeType === 1 && !BLOCK.has(ch.tagName) && !hasBlockChild(ch))) {
          buf.push(ch);
          return;
        }
        if (ch.nodeType !== 1) return;
        flush();
        const tag = ch.tagName;
        if (/^H[1-6]$/.test(tag)) {
          const level = Math.max(1, Math.min(3, Number(tag[1]) - 1));
          const t = oneLine(inline(ch));
          if (t) out.push("#".repeat(level) + " " + t);
        } else if (tag === "UL" || tag === "OL") {
          const items = listToMd(ch, []);
          if (items.length) out.push(items.join("\n"));
        } else if (tag === "LI") {
          const t = oneLine(inline(ch));
          if (t) out.push("- " + t);
        } else if (tag === "BLOCKQUOTE") {
          const inner = [];
          blocks(ch, inner);
          const t = inner.join("\n").split("\n").map(l => l.trim()).filter(Boolean)
            .map(l => "> " + l).join("\n");
          if (t) out.push(t);
        } else if (tag === "PRE") {
          const code = ch.textContent.replace(/\n+$/, "");
          if (code.trim()) out.push("```\n" + code + "\n```");
        } else if (tag === "TABLE") {
          const t = tableToMd(ch);
          if (t) out.push(t);
        } else if (tag === "HR") {
          out.push("---");
        } else if (hasBlockChild(ch)) {
          blocks(ch, out);
        } else {
          const t = tidyPara(inline(ch));
          if (t) out.push(t);
        }
      });
      flush();
      return out;
    }

    return (root) => blocks(root, []).join("\n\n").replace(/\n{3,}/g, "\n\n").trim();
  }

  // ------------------------------------------------------------------
  // Очищення вставленого HTML (Word, Google Docs, сайти)
  // ------------------------------------------------------------------
  const KEEP = new Set(["P", "BR", "H1", "H2", "H3", "H4", "H5", "H6", "STRONG", "B", "EM",
    "I", "A", "UL", "OL", "LI", "BLOCKQUOTE", "PRE", "CODE", "TABLE", "THEAD", "TBODY",
    "TFOOT", "TR", "TH", "TD", "HR", "SPAN", "U", "MARK"]);
  const DROP = new Set(["SCRIPT", "STYLE", "META", "LINK", "TITLE", "HEAD", "IMG", "SVG",
    "IFRAME", "OBJECT", "EMBED", "VIDEO", "AUDIO", "CANVAS", "BUTTON", "INPUT", "SELECT",
    "TEXTAREA", "FORM", "NOSCRIPT", "TEMPLATE"]);

  function unwrap(el) {
    const parent = el.parentNode;
    if (!parent) return;
    while (el.firstChild) parent.insertBefore(el.firstChild, el);
    el.remove();
  }

  function rename(el, tag) {
    const n = el.ownerDocument.createElement(tag);
    while (el.firstChild) n.appendChild(el.firstChild);
    el.replaceWith(n);
    return n;
  }

  // Word віддає списки абзацами з mso-list і маркером у span[mso-list:Ignore].
  function wordLists(body) {
    const isItem = (el) => el && el.nodeType === 1 && el.tagName === "P"
      && /mso-list/i.test(el.getAttribute("style") || "");
    // Номер списку Word (l0, l1…): маркований і нумерований списки підряд —
    // це два різні списки.
    const listId = (el) => ((el.getAttribute("style") || "").match(/mso-list:\s*(l\d+)/i) || [])[1] || "";
    const marker = (el) => el.querySelector('[style*="mso-list:Ignore" i], [style*="mso-list: Ignore" i]');
    const isOrdered = (el) => {
      const m = marker(el);
      return /^\s*[\dA-Za-zА-Яа-яІіЇїЄє]{1,3}[.)]/.test(m ? m.textContent : "");
    };
    [...body.querySelectorAll("p")].forEach(p => {
      if (!isItem(p) || !p.isConnected) return;
      const run = [p];
      const id = listId(p), ordered = isOrdered(p);
      let next = p.nextSibling;
      while (next && ((isItem(next) && listId(next) === id && isOrdered(next) === ordered)
                      || (next.nodeType === 3 && !next.nodeValue.trim()))) {
        if (next.nodeType === 1) run.push(next);
        next = next.nextSibling;
      }
      const list = body.ownerDocument.createElement(ordered ? "ol" : "ul");
      p.before(list);
      run.forEach(item => {
        const m = marker(item);
        if (m) m.remove();
        const li = body.ownerDocument.createElement("li");
        while (item.firstChild) li.appendChild(item.firstChild);
        item.remove();
        list.appendChild(li);
      });
    });
  }

  function cleanPastedHtml(html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const body = doc.body;
    let images = 0;
    // Коментарі (зокрема умовні коментарі Word) — геть.
    const walker = doc.createTreeWalker(body, NodeFilter.SHOW_COMMENT);
    const comments = [];
    while (walker.nextNode()) comments.push(walker.currentNode);
    comments.forEach(c => c.remove());
    wordLists(body);
    // Google Docs загортає все в <b style="font-weight:normal">.
    body.querySelectorAll("b").forEach(b => {
      if (/font-weight:\s*(normal|[1-4]00)/i.test(b.getAttribute("style") || "")) unwrap(b);
    });
    // Жирний і курсив, задані стилем (Google Docs, сайти); колір — до
    // найближчого з набору, підкреслення й заливка (маркер Word) — як є.
    body.querySelectorAll("span, font").forEach(sp => {
      const st = (sp.getAttribute("style") || "").toLowerCase();
      const color = nearestColor(sp.getAttribute("color")
        || (st.match(/(?:^|;)\s*color:\s*([^;]+)/) || [])[1]);
      const marked = /mso-highlight:\s*(?!none)\w/.test(st)
        || /background(?:-color)?:\s*(?!transparent|#fff\b|#ffffff|white|rgb\(255,\s*255,\s*255\))[#\w]/.test(st);
      const under = /text-decoration[^;]*underline/.test(st);
      let el = sp;
      if (/font-style:\s*italic/.test(st)) el = rename(el, "em");
      if (/font-weight:\s*(bold|[6-9]00)/.test(st)) {
        const strong = doc.createElement("strong");
        el.replaceWith(strong);
        strong.appendChild(el);
      }
      // Обгортки — всередині: сам span (якщо лишився span) розгорнеться нижче.
      [color && ["span", "tc-" + color], under && ["u"], marked && ["mark"]].filter(Boolean)
        .forEach(([tag, cls]) => {
          const w = doc.createElement(tag);
          if (cls) w.className = cls;
          while (el.firstChild) w.appendChild(el.firstChild);
          el.appendChild(w);
        });
    });
    const clean = (node) => {
      [...node.childNodes].forEach(ch => {
        if (ch.nodeType === 8) { ch.remove(); return; }
        if (ch.nodeType !== 1) return;
        clean(ch);
        const tag = ch.tagName.toUpperCase();
        if (tag === "IMG") images++;
        if (DROP.has(tag)) { ch.remove(); return; }
        if (tag === "DIV" || tag === "SECTION" || tag === "ARTICLE") {
          if (hasBlockChild(ch)) unwrap(ch); else rename(ch, "p");
          return;
        }
        if (!KEEP.has(tag)) { unwrap(ch); return; }
        // Span лишається, лише якщо несе колір чи розмір із набору.
        const fmt = tag === "SPAN" ? [...ch.classList].find(c => FMT_CLASS_RE.test(c)) : null;
        if (tag === "SPAN" && !fmt) { unwrap(ch); return; }
        const href = tag === "A" ? ch.getAttribute("href") : null;
        [...ch.attributes].forEach(a => ch.removeAttribute(a.name));
        if (fmt) ch.className = fmt;
        if (href && /^(https?:|mailto:)/i.test(href.trim())) ch.setAttribute("href", href.trim());
        else if (tag === "A") unwrap(ch);
      });
    };
    clean(body);
    // Порожні абзаци Word (<p>&nbsp;</p>) лише розтягують текст.
    body.querySelectorAll("p, li, h1, h2, h3, h4, h5, h6").forEach(el => {
      if (!el.textContent.replace(/\u00a0/g, " ").trim() && !el.querySelector("br, hr, table")) el.remove();
    });
    if (OFFICE_RE.test(html) && !body.querySelector("h1, h2, h3, h4, h5, h6")) boldHeadings(body);
    return { html: body.innerHTML, images };
  }

  // Документ Word чи Google Docs без стилів заголовків: заголовок там — короткий
  // жирний рядок перед звичайним текстом. Правило те саме, що й під час
  // імпорту .docx на сервері (text_extract_service): перший такий рядок на
  // початку — назва, решта — розділи; два жирні рядки поспіль, рядок із
  // двокрапкою чи крапкою в кінці або підпис наприкінці лишаються жирним
  // текстом. Сайтів правило не стосується — там заголовки справжні.
  const OFFICE_RE = /urn:schemas-microsoft-com:office|class="?Mso|mso-|docs-internal-guid/i;
  const HEADING_LIKE = /^(?=[\s\S]*[\p{L}\p{N}])[\s\S]{1,120}(?<![.,;:])$/u;

  function boldOnly(p) {
    const walker = p.ownerDocument.createTreeWalker(p, NodeFilter.SHOW_TEXT);
    let any = false;
    while (walker.nextNode()) {
      const t = walker.currentNode;
      if (!t.nodeValue.replace(/ /g, " ").trim()) continue;
      const strong = t.parentElement.closest("strong, b");
      if (!strong || !p.contains(strong) || t.parentElement.closest("a")) return false;
      any = true;
    }
    return any;
  }

  function boldHeadings(body) {
    const blocks = [...body.children];
    const text = (el) => el.textContent.replace(/ /g, " ").replace(/\s+/g, " ").trim();
    const cand = blocks.map(el => el.tagName === "P" && boldOnly(el) && HEADING_LIKE.test(text(el)));
    const picked = blocks.map((el, i) => i).filter(i => cand[i] && i + 1 < blocks.length && !cand[i + 1]);
    const titled = picked.length > 1 && picked[0] === 0;
    picked.forEach(i => {
      const h = body.ownerDocument.createElement(titled && i ? "h3" : "h2");
      h.textContent = text(blocks[i]);
      blocks[i].replaceWith(h);
    });
  }

  // ------------------------------------------------------------------
  // Компонент
  // ------------------------------------------------------------------
  function create({ value = "", kind = "doc", placeholder = "", label = "Текст", onChange, onFiles,
                   onImage } = {}) {
    let mode = "visual";
    let current = kind === "prompt" ? stripFormatting(value) : String(value || "");
    let dirty = false;
    let toMd = makeConverter(kind);
    let opts = { html: kind !== "prompt" };

    const root = document.createElement("div");
    root.className = "rte";
    root.innerHTML = `
      <div class="rte-bar" role="toolbar" aria-label="Оформлення тексту">
        <div class="rte-tools">
          <button type="button" class="rte-btn rte-txt" data-cmd="h2" title="Заголовок">Заголовок</button>
          <button type="button" class="rte-btn rte-txt" data-cmd="h3" title="Підзаголовок">Підзаголовок</button>
          <span class="rte-sep"></span>
          <button type="button" class="rte-btn rte-b" data-cmd="bold" title="Жирний (Ctrl+B)" aria-label="Жирний">Ж</button>
          <button type="button" class="rte-btn rte-i" data-cmd="italic" title="Курсив (Ctrl+I)" aria-label="Курсив">К</button>
          <span class="rte-fmt">
            <button type="button" class="rte-btn rte-u" data-cmd="underline" title="Підкреслений (Ctrl+U)" aria-label="Підкреслений">Ч</button>
            <button type="button" class="rte-btn" data-cmd="mark" title="Маркер" aria-label="Маркер">${svg("mark")}</button>
            <button type="button" class="rte-btn rte-color" data-pop="color" title="Колір тексту" aria-label="Колір тексту" aria-haspopup="true">A<i></i></button>
            <button type="button" class="rte-btn rte-size" data-pop="size" title="Розмір тексту" aria-label="Розмір тексту" aria-haspopup="true">${svg("size")}</button>
          </span>
          <button type="button" class="rte-btn" data-cmd="code" title="Код" aria-label="Код">${svg("code")}</button>
          <span class="rte-sep"></span>
          <button type="button" class="rte-btn" data-cmd="ul" title="Маркований список" aria-label="Маркований список">${svg("ul")}</button>
          <button type="button" class="rte-btn" data-cmd="ol" title="Нумерований список" aria-label="Нумерований список">${svg("ol")}</button>
          <button type="button" class="rte-btn" data-cmd="quote" title="Цитата" aria-label="Цитата">${svg("quote")}</button>
          <span class="rte-sep"></span>
          <button type="button" class="rte-btn" data-cmd="link" title="Посилання (Ctrl+K)" aria-label="Посилання">${svg("link")}</button>
          <button type="button" class="rte-btn rte-img-btn" data-cmd="image" title="Зображення" aria-label="Зображення">${svg("image")}</button>
          <button type="button" class="rte-btn" data-cmd="table" title="Таблиця" aria-label="Таблиця">${svg("table")}</button>
          <button type="button" class="rte-btn" data-cmd="hr" title="Розділювач" aria-label="Розділювач">${svg("hr")}</button>
          <span class="rte-tbl hidden">
            <button type="button" class="rte-btn rte-txt" data-cmd="row" title="Додати рядок під поточним">+ рядок</button>
            <button type="button" class="rte-btn rte-txt" data-cmd="col" title="Додати стовпець праворуч">+ стовпець</button>
          </span>
        </div>
        <div class="rte-modes" role="tablist" aria-label="Вигляд редактора">
          <button type="button" role="tab" data-mode="visual" class="on" aria-selected="true">Оформлення</button>
          <button type="button" role="tab" data-mode="source" aria-selected="false">Markdown</button>
        </div>
      </div>
      <div class="rte-area md-doc" contenteditable="true" role="textbox" aria-multiline="true"
           spellcheck="true"></div>
      <textarea class="rte-src hidden" spellcheck="false" aria-label="${esc(label)}: розмітка Markdown"></textarea>
      <input type="file" class="rte-file" hidden multiple
             accept="image/png,image/jpeg,image/gif,image/webp,image/avif">
      <div class="rte-pop hidden" role="menu"></div>`;
    const area = root.querySelector(".rte-area");
    const fileInput = root.querySelector(".rte-file");
    const imgBtn = root.querySelector(".rte-img-btn");
    const imagesOn = () => !!onImage && kind !== "prompt";
    const fmtBox = root.querySelector(".rte-fmt");
    const pop = root.querySelector(".rte-pop");
    // У промпті кольорів, розміру й картинок немає: текст копіюють у модель.
    const paintImageBtn = () => {
      imgBtn.classList.toggle("hidden", !imagesOn());
      fmtBox.classList.toggle("hidden", kind === "prompt");
    };
    paintImageBtn();
    const src = root.querySelector(".rte-src");
    const tblTools = root.querySelector(".rte-tbl");
    area.setAttribute("aria-label", label);

    const setPlaceholder = (text) => {
      area.dataset.placeholder = text || "";
      src.placeholder = text || "";
    };
    setPlaceholder(placeholder);

    const isEmpty = () => !area.textContent.trim() && !area.querySelector("table, hr, li");
    const paintEmpty = () => area.classList.toggle("is-empty", isEmpty());

    let notifyTimer = null;
    const notify = () => {
      if (!onChange) return;
      clearTimeout(notifyTimer);
      notifyTimer = setTimeout(() => onChange(api.getValue()), 120);
    };

    function render() {
      area.innerHTML = current.trim() ? renderMarkdown(current, opts) : "";
      if (typeof hydrateImages === "function") hydrateImages(area);
      paintEmpty();
    }

    function getValue() {
      if (mode === "source") return src.value;
      return dirty ? toMd(area) : current;
    }

    function setValue(md) {
      current = kind === "prompt" ? stripFormatting(md) : String(md || "");
      dirty = false;
      if (mode === "source") src.value = current; else render();
      notify();
    }

    function setMode(next) {
      if (next === mode) return;
      if (next === "source") {
        current = getValue();
        dirty = false;
        src.value = current;
      } else {
        current = src.value;
        dirty = false;
        render();
      }
      mode = next;
      area.classList.toggle("hidden", mode !== "visual");
      src.classList.toggle("hidden", mode !== "source");
      root.querySelector(".rte-tools").classList.toggle("is-off", mode !== "visual");
      root.querySelectorAll("[data-mode]").forEach(b => {
        b.classList.toggle("on", b.dataset.mode === mode);
        b.setAttribute("aria-selected", String(b.dataset.mode === mode));
      });
      (mode === "visual" ? area : src).focus();
    }

    function changed() {
      dirty = true;
      paintEmpty();
      notify();
      paintState();
    }

    // --- Команди панелі ---
    const exec = (cmd, val = null) => document.execCommand(cmd, false, val);

    function currentBlock() {
      const sel = window.getSelection();
      if (!sel.rangeCount) return null;
      let n = sel.getRangeAt(0).startContainer;
      if (n.nodeType === 3) n = n.parentNode;
      if (!area.contains(n)) return null;
      return n.closest("h1, h2, h3, h4, h5, h6, blockquote, pre, li, p, td, th");
    }

    function toggleBlock(tag) {
      const b = currentBlock();
      const inside = b && (b.tagName === tag.toUpperCase() || b.closest(tag));
      exec("formatBlock", inside ? "<p>" : `<${tag}>`);
    }

    function insertTable() {
      exec("insertHTML", '<table><thead><tr><th>Стовпець 1</th><th>Стовпець 2</th></tr></thead>'
        + '<tbody><tr><td><br></td><td><br></td></tr></tbody></table><p><br></p>');
    }

    function currentCell() {
      const b = currentBlock();
      return b ? b.closest("td, th") : null;
    }

    function addRow() {
      const c = currentCell();
      if (!c) return;
      const tr = c.closest("tr");
      const row = document.createElement("tr");
      for (let k = 0; k < tr.children.length; k++) {
        const td = document.createElement("td");
        td.innerHTML = "<br>";
        row.appendChild(td);
      }
      if (tr.parentNode.tagName === "THEAD") {
        const table = tr.closest("table");
        let tb = table.querySelector("tbody");
        if (!tb) { tb = document.createElement("tbody"); table.appendChild(tb); }
        tb.prepend(row);
      } else {
        tr.after(row);
      }
    }

    function addCol() {
      const c = currentCell();
      if (!c) return;
      const idx = [...c.parentNode.children].indexOf(c);
      c.closest("table").querySelectorAll("tr").forEach(tr => {
        const head = tr.parentNode.tagName === "THEAD";
        const n = document.createElement(head ? "th" : "td");
        n.innerHTML = head ? "Стовпець" : "<br>";
        const ref = tr.children[idx];
        if (ref) ref.after(n); else tr.appendChild(n);
      });
    }

    function link() {
      const sel = window.getSelection();
      const a = currentBlock() && sel.anchorNode
        ? (sel.anchorNode.nodeType === 3 ? sel.anchorNode.parentNode : sel.anchorNode).closest("a") : null;
      const url = window.prompt("Адреса посилання (порожньо — прибрати посилання)",
        a ? a.getAttribute("href") : "https://");
      if (url === null) return;
      const v = url.trim();
      if (!v) { exec("unlink"); return; }
      if (!/^(https?:\/\/|mailto:)/i.test(v)) {
        window.alert("Посилання має починатися з https://, http:// або mailto:");
        return;
      }
      if (sel.isCollapsed && !a) exec("insertHTML", `<a href="${esc(v)}">${esc(v)}</a>`);
      else exec("createLink", v);
    }

    function inlineCode() {
      const sel = window.getSelection();
      const text = sel.toString();
      if (!text || /\n/.test(text)) { toggleBlock("pre"); return; }
      exec("insertHTML", `<code>${esc(text)}</code>`);
    }

    // --- Колір, розмір, маркер ---
    // Браузер сам розрізає виділення на шматки (execCommand), а ми одразу
    // замінюємо його <font>/<span style> на класи з набору — інших кольорів
    // у тексті не буває.
    function selectionRange() {
      const sel = window.getSelection();
      if (!sel.rangeCount || sel.isCollapsed) return null;
      const r = sel.getRangeAt(0);
      return area.contains(r.commonAncestorContainer) ? r : null;
    }
    function needSelection() {
      if (selectionRange()) return true;
      if (typeof toast === "function") toast("Спершу виділіть текст");
      return false;
    }
    function wrapChildren(el, chain) {
      const frag = document.createDocumentFragment();
      let holder = frag;
      chain.forEach(([tag, cls]) => {
        const w = document.createElement(tag);
        if (cls) w.className = cls;
        holder.appendChild(w);
        holder = w;
      });
      while (el.firstChild) holder.appendChild(el.firstChild);
      el.replaceWith(frag);
    }
    function normalizeFormatting() {
      area.querySelectorAll("font").forEach(f => {
        const color = pickedColor(f.getAttribute("color"));
        const size = SIZE_BY_FONT[f.getAttribute("size")];
        wrapChildren(f, [color && ["span", "tc-" + color], size && ["span", "ts-" + size]].filter(Boolean));
      });
      area.querySelectorAll("span[style]").forEach(sp => {
        const st = sp.style;
        const color = pickedColor(st.color);
        const marked = sameRgb(rgbOf(st.backgroundColor), rgbOf(MARK_HEX));
        const keep = fmtClass(sp);
        wrapChildren(sp, [keep && ["span", keep], color && ["span", "tc-" + color],
                          marked && ["mark"]].filter(Boolean));
      });
      // Колір у кольорі: зовнішній, що весь складається з внутрішнього, — зайвий.
      area.querySelectorAll("span").forEach(sp => {
        const cls = fmtClass(sp);
        const only = sp.childNodes.length === 1 && sp.firstChild.nodeType === 1
          && sp.firstChild.tagName === "SPAN" && fmtClass(sp.firstChild);
        if (cls && only && cls.slice(0, 3) === fmtClass(sp.firstChild).slice(0, 3)) unwrap(sp);
        else if (!cls && !sp.attributes.length) unwrap(sp);
      });
    }
    const fmtClass = (el) => [...el.classList].find(c => FMT_CLASS_RE.test(c));
    // Прибрати колір чи розмір (або маркер) з усього, чого торкається виділення.
    function clearFormat(test) {
      const r = selectionRange();
      if (!r) return;
      [...area.querySelectorAll("span, mark")].filter(el => test(el) && r.intersectsNode(el))
        .forEach(unwrap);
    }
    function setColor(name) {
      if (!needSelection()) return;
      if (!name) { clearFormat(el => /^tc-/.test(fmtClass(el) || "")); return; }
      try { document.execCommand("styleWithCSS", false, false); } catch (e) { /* ні */ }
      exec("foreColor", TEXT_COLORS[name][1]);
      normalizeFormatting();
    }
    function setSize(name) {
      if (!needSelection()) return;
      clearFormat(el => /^ts-/.test(fmtClass(el) || ""));
      if (name === "normal") return;
      try { document.execCommand("styleWithCSS", false, false); } catch (e) { /* ні */ }
      exec("fontSize", FONT_BY_SIZE[name]);
      normalizeFormatting();
    }
    function toggleMark() {
      if (!needSelection()) return;
      const r = selectionRange();
      const inside = (n) => (n.nodeType === 3 ? n.parentNode : n).closest("mark");
      if (inside(r.startContainer)) { clearFormat(el => el.tagName === "MARK"); return; }
      exec("hiliteColor", MARK_HEX);
      normalizeFormatting();
    }

    function openPop(kindOf, btn) {
      if (!pop.classList.contains("hidden") && pop.dataset.kind === kindOf) { closePop(); return; }
      pop.dataset.kind = kindOf;
      pop.innerHTML = kindOf === "color"
        ? Object.entries(TEXT_COLORS).map(([k, [label]]) =>
            `<button type="button" class="rte-swatch" data-color="${k}" title="${label}" aria-label="${label}"><i style="background: var(--tc-${k})"></i></button>`).join("")
          + `<button type="button" class="rte-pop-txt" data-color="">Без кольору</button>`
        : Object.entries(TEXT_SIZES).map(([k, label]) =>
            `<button type="button" class="rte-pop-txt rte-size-${k}" data-size="${k}">${label}</button>`).join("");
      pop.classList.remove("hidden");
      const b = btn.getBoundingClientRect(), box = root.getBoundingClientRect();
      pop.style.left = `${Math.max(4, Math.min(b.left - box.left, box.width - pop.offsetWidth - 4))}px`;
      pop.style.top = `${b.bottom - box.top + 4}px`;
      pop.querySelectorAll("button").forEach(x => {
        x.addEventListener("mousedown", e => e.preventDefault());
        x.addEventListener("click", () => {
          area.focus();
          if ("color" in x.dataset) setColor(x.dataset.color); else setSize(x.dataset.size);
          closePop();
          changed();
        });
      });
    }
    function closePop() { pop.classList.add("hidden"); }
    root.querySelectorAll("[data-pop]").forEach(btn => {
      btn.addEventListener("mousedown", e => e.preventDefault());
      btn.addEventListener("click", () => { if (mode === "visual") openPop(btn.dataset.pop, btn); });
    });
    document.addEventListener("mousedown", e => {
      if (!root.isConnected) return;
      if (!pop.contains(e.target) && !e.target.closest("[data-pop]")) closePop();
    });

    // --- Зображення ---
    let savedRange = null;
    function saveRange() {
      const sel = window.getSelection();
      if (sel.rangeCount && area.contains(sel.getRangeAt(0).startContainer)) {
        savedRange = sel.getRangeAt(0).cloneRange();
      }
    }
    function restoreRange() {
      area.focus();
      if (!savedRange || !area.contains(savedRange.startContainer)) return;
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(savedRange);
    }

    async function insertImages(files) {
      const images = [...files].filter(isImageFile);
      if (!images.length || !imagesOn()) return;
      saveRange();
      for (const f of images) {
        let img = null;
        try { img = await onImage(f); }
        catch (err) { if (typeof toast === "function") toast(err.message, "err"); }
        if (!img) continue;
        restoreRange();
        // «image.png» зі знімка екрана — не підпис.
        const name = (f.name || "").replace(/\.[^.]+$/, "");
        const alt = /^(image|img|screenshot|знімок)[\s_-]*\d*$/i.test(name) ? "" : name;
        exec("insertHTML", `<img class="md-img" data-file="${Number(img.id)}" src="${esc(img.url)}" alt="${esc(alt)}">`);
        saveRange();
        changed();
      }
    }

    fileInput.addEventListener("change", () => {
      const files = [...fileInput.files];
      fileInput.value = "";
      insertImages(files);
    });

    const COMMANDS = {
      image: () => { saveRange(); fileInput.click(); },
      bold: () => exec("bold"), italic: () => exec("italic"), code: inlineCode,
      underline: () => exec("underline"), mark: toggleMark,
      h2: () => toggleBlock("h2"), h3: () => toggleBlock("h3"), quote: () => toggleBlock("blockquote"),
      ul: () => exec("insertUnorderedList"), ol: () => exec("insertOrderedList"),
      link, table: insertTable, hr: () => exec("insertHorizontalRule"), row: addRow, col: addCol,
    };

    root.querySelectorAll("[data-cmd]").forEach(btn => {
      // Не забираємо фокус і виділення з тексту.
      btn.addEventListener("mousedown", e => e.preventDefault());
      btn.addEventListener("click", () => {
        if (mode !== "visual") return;
        area.focus();
        COMMANDS[btn.dataset.cmd]();
        changed();
      });
    });
    root.querySelectorAll("[data-mode]").forEach(b =>
      b.addEventListener("click", () => setMode(b.dataset.mode)));

    // --- Стан кнопок ---
    function paintState() {
      if (mode !== "visual") return;
      const b = currentBlock();
      const on = (cmd, v) => {
        const el = root.querySelector(`[data-cmd="${cmd}"]`);
        el.classList.toggle("on", !!v);
        el.setAttribute("aria-pressed", String(!!v));
      };
      let bold = false, italic = false, under = false;
      try {
        bold = document.queryCommandState("bold"); italic = document.queryCommandState("italic");
        under = document.queryCommandState("underline");
      } catch (e) { /* старі браузери */ }
      // Заголовки й шапка таблиці жирні самі по собі — «Ж» там не підсвічуємо.
      on("bold", b && bold && !/^(H\d|TH)$/.test(b.tagName));
      on("italic", b && italic);
      on("underline", b && under);
      const sel = window.getSelection();
      const at = sel.rangeCount ? sel.getRangeAt(0).startContainer : null;
      const el = at && (at.nodeType === 3 ? at.parentNode : at);
      on("mark", el && area.contains(el) && !!el.closest("mark"));
      on("h2", b && b.tagName === "H2");
      on("h3", b && b.tagName === "H3");
      on("quote", b && !!b.closest("blockquote"));
      on("ul", b && !!b.closest("ul"));
      on("ol", b && !!b.closest("ol"));
      tblTools.classList.toggle("hidden", !(b && b.closest("td, th")));
    }
    const onSelection = () => {
      if (!root.isConnected) { document.removeEventListener("selectionchange", onSelection); return; }
      if (document.activeElement === area) paintState();
    };
    document.addEventListener("selectionchange", onSelection);

    // --- Введення, вставка, перетягування ---
    area.addEventListener("focus", () => {
      try { document.execCommand("defaultParagraphSeparator", false, "p"); } catch (e) { /* ні */ }
    });
    area.addEventListener("input", changed);
    src.addEventListener("input", notify);
    area.addEventListener("keydown", e => {
      // Ctrl+K тут — посилання, а не глобальний пошук застосунку.
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault(); e.stopPropagation(); link(); changed();
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "u" && kind === "prompt") {
        e.preventDefault();   // у промпті підкреслення немає
      }
      if (e.key === "Escape") closePop();
    });

    function insertContent(dt) {
      const html = dt.getData("text/html");
      const text = dt.getData("text/plain");
      if (html && html.trim()) {
        const cleaned = cleanPastedHtml(html);
        if (cleaned.html.trim()) exec("insertHTML", cleaned.html);
        if (cleaned.images && typeof toast === "function") {
          toast(imagesOn()
            ? "Зображення з документа разом із текстом не переносяться — вставте їх кнопкою «Зображення», перетягніть файл або завантажте документ .docx"
            : "Зображення не вставляються в текст — додайте їх до картки файлами, там вони показуються");
        }
      } else if (text) {
        // Звичайний текст і Markdown (наприклад, відповідь ChatGPT) — як розмітку.
        exec("insertHTML", renderMarkdown(text, opts));
      }
      changed();
    }

    area.addEventListener("paste", e => {
      if (!e.clipboardData) return;
      e.preventDefault();
      // Знімок екрана чи скопійована картинка без тексту — це зображення.
      const files = [...(e.clipboardData.files || [])].filter(isImageFile);
      if (files.length && imagesOn() && !e.clipboardData.getData("text/plain").trim()) {
        insertImages(files);
        return;
      }
      insertContent(e.clipboardData);
    });
    area.addEventListener("dragover", e => e.preventDefault());
    area.addEventListener("drop", e => {
      const dt = e.dataTransfer;
      if (!dt) return;
      e.preventDefault();
      const range = document.caretRangeFromPoint ? document.caretRangeFromPoint(e.clientX, e.clientY) : null;
      if (range) { const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range); }
      if (dt.files && dt.files.length) {
        // Картинки — у текст на місце, куди їх кинули; документи — як і раніше.
        const files = [...dt.files];
        const images = imagesOn() ? files.filter(isImageFile) : [];
        const rest = files.filter(f => !images.includes(f));
        if (images.length) insertImages(images);
        if (rest.length && onFiles) onFiles(rest);
        return;
      }
      insertContent(dt);
    });

    const api = {
      el: root,
      getValue,
      setValue,
      focus: () => (mode === "visual" ? area : src).focus(),
      setPlaceholder,
      // Підписи картинок прийшли пізніше за текст — показуємо їх.
      refreshImages: () => { if (typeof hydrateImages === "function") hydrateImages(area); },
      // Вид тексту змінюється разом із видом матеріалу в повному редакторі.
      setKind(next) {
        const md = getValue();
        kind = next;
        toMd = makeConverter(kind);
        opts = { html: kind !== "prompt" };
        paintImageBtn();
        current = kind === "prompt" ? stripFormatting(md) : md;
        dirty = false;
        if (mode === "visual") render();
      },
    };
    render();
    return api;
  }

  window.RichEditor = {
    create,
    htmlToMarkdown: (root, kind = "doc") => makeConverter(kind)(root),
    cleanPastedHtml,
    nearestColor,
    stripFormatting,
    TEXT_COLORS,
  };
})();
