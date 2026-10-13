// Offline renderer: markdown + KaTeX + Mermaid + sandboxed SVG / p5.js / three.js.
// Everything loads from ./vendor. The page has no network access (CSP + Qt interceptor).
const SPECIAL = new Set(['mermaid', 'svg', 'p5', 'p5js', 'three', 'threejs', 'scisketch-run']);
const BASE = new URL('.', location.href).href;
const IFRAME_CSP = "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; img-src data: blob:; media-src data: blob:; connect-src 'none'";
const dark = matchMedia('(prefers-color-scheme: dark)').matches;
mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: dark ? 'dark' : 'default' });

const log = document.getElementById('log');
const rendered = [];            // per-message cache: {content, streaming, el}
let frameSeq = 0, mermaidSeq = 0;

const esc = s => s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function tex(src, display) {
  try { return katex.renderToString(src, { displayMode: display, throwOnError: false, trust: false }); }
  catch (e) { return '<code>' + esc(src) + '</code>'; }
}

function process(text, streaming) {
  const widgets = [], math = [], codes = [];
  const wtok = w => { widgets.push(w); return `\n\nZZW${widgets.length - 1}ZZ\n\n`; };
  const mtok = (src, d) => { math.push(tex(src, d)); return `ZZM${math.length - 1}ZZ`; };

  text = text.replace(/```([A-Za-z0-9_+-]*)[^\n]*\n([\s\S]*?)```/g, (m, lang, body) => {
    lang = lang.toLowerCase();
    if (SPECIAL.has(lang)) return wtok({ lang, body, pending: streaming });
    codes.push(m); return `ZZC${codes.length - 1}ZZ`;
  });
  if (streaming) {   // a special fence that is still being written
    text = text.replace(/```(mermaid|svg|p5js?|three(?:js)?|scisketch-run)[^\n]*\n[\s\S]*$/i,
      (m, lang) => wtok({ lang: lang.toLowerCase(), body: '', pending: true }));
  }
  text = text.replace(/`[^`\n]+`/g, m => { codes.push(m); return `ZZC${codes.length - 1}ZZ`; });
  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (m, t) => mtok(t, true))
             .replace(/\\\[([\s\S]+?)\\\]/g, (m, t) => mtok(t, true))
             .replace(/\\\(([\s\S]+?)\\\)/g, (m, t) => mtok(t, false))
             .replace(/(?<![\\$])\$(?![\s$])([^$\n]+?)(?<![\s\\])\$(?!\d)/g, (m, t) => mtok(t, false));
  text = text.replace(/ZZC(\d+)ZZ/g, (m, i) => codes[+i]);

  let html = DOMPurify.sanitize(marked.parse(text, { gfm: true, breaks: true }));
  html = html.replace(/<p>\s*ZZW(\d+)ZZ\s*<\/p>/g, '<div class="widget" data-w="$1"></div>')
             .replace(/ZZW(\d+)ZZ/g, '<div class="widget" data-w="$1"></div>')
             .replace(/ZZM(\d+)ZZ/g, (m, i) => math[+i]);
  return { html, widgets };
}

function widgetShell(w, label) {
  const box = document.createElement('div');
  box.className = 'widget' + (w.pending ? ' pending' : '');
  box.innerHTML = `<div class="wbar"><span>${label}</span></div><div class="wbody"></div>`;
  return { box, body: box.querySelector('.wbody') };
}

function showCode(body, code, note) {
  body.innerHTML = (note ? `<div class="warn">${esc(note)}</div>` : '') + `<pre><code>${esc(code)}</code></pre>`;
}

function frameDoc(kind, code, id) {
  const tagFor = k => `<script>${window.LIBSRC[k]}<\/script>`;       // library source inlined: works in an opaque-origin sandbox
  const libTag = tagFor(kind);
  const meta = `<meta http-equiv="Content-Security-Policy" content="${IFRAME_CSP}">`;
  const probe = `<script>addEventListener('load',function(){parent.postMessage({probe:1,id:${id},p5:typeof p5,three:typeof THREE},'*')});` +
                `addEventListener('error',function(e){parent.postMessage({err:String(e.message),id:${id}},'*')});<\/script>`;
  let doc;
  if (/<\s*(html|body|script)\b/i.test(code)) {            // full HTML page from the AI: swap CDN tags for the local copies
    let used = false;
    const swap = k => () => { used = used || k === kind; return tagFor(k); };
    doc = code
      .replace(/<script[^>]*src=["'][^"']*p5(?:\.min)?\.js[^"']*["'][^>]*>\s*<\/script>/gi, swap('p5'))
      .replace(/<script[^>]*src=["'][^"']*three(?:\.min)?\.js[^"']*["'][^>]*>\s*<\/script>/gi, swap('three'));
    if (!used) doc = libTag + doc;
    doc = /<head[^>]*>/i.test(doc) ? doc.replace(/<head[^>]*>/i, m => m + meta)
                                   : '<!doctype html><head>' + meta + '</head>' + doc;
  } else {                                                  // bare sketch / scene code
    doc = `<!doctype html><html><head><meta charset="utf-8">${meta}<style>html,body{margin:0;background:#fff;overflow:hidden}canvas{display:block}</style></head><body>${libTag}<script>${code.replace(/<\/script/gi, '<\\/script')}<\/script></body></html>`;
  }
  return doc + probe;
}

function build(w) {
  const lang = w.lang === 'p5js' ? 'p5' : w.lang === 'threejs' ? 'three' : w.lang;
  const names = { mermaid: 'Diagram', svg: 'Illustration', p5: 'Interactive 2D simulation (p5.js)', three: 'Interactive 3D (three.js)', 'scisketch-run': 'Simulation request' };
  const { box, body } = widgetShell(w, names[lang]);
  if (w.pending) { body.textContent = 'Building ' + names[lang].toLowerCase() + '...'; return box; }

  if (lang === 'scisketch-run') {
    body.innerHTML = `<details><summary>Sent to the simulator</summary><pre><code>${esc(w.body.trim())}</code></pre></details>`;
  } else if (lang === 'mermaid') {
    const id = 'mm' + (++mermaidSeq);
    mermaid.render(id, w.body.trim()).then(r => { body.innerHTML = r.svg; })
      .catch(e => { document.getElementById('d' + id)?.remove(); showCode(body, w.body, 'Diagram could not be drawn: ' + (e.message || e)); });
  } else if (lang === 'svg') {
    const clean = DOMPurify.sanitize(w.body, { USE_PROFILES: { svg: true, svgFilters: true } });
    clean.trim() ? body.innerHTML = clean : showCode(body, w.body, 'Empty or unsafe SVG removed');
  } else {                                                   // p5 / three: sandboxed iframe
    const id = ++frameSeq;
    const f = document.createElement('iframe');
    f.setAttribute('sandbox', 'allow-scripts allow-pointer-lock');   // opaque origin: no file/parent access
    f.srcdoc = frameDoc(lang, w.body, id);
    f.dataset.id = id; f.dataset.kind = lang;
    body.style.padding = '0'; body.appendChild(f);
    const det = document.createElement('details');
    det.innerHTML = `<summary>Show code</summary><pre><code>${esc(w.body)}</code></pre>`;
    body.appendChild(det);
  }
  return box;
}

window.addEventListener('message', e => {
  const d = e.data || {};
  const f = document.querySelector(`iframe[data-id="${d.id}"]`);
  if (!f || e.source !== f.contentWindow) return;
  const need = f.dataset.kind === 'p5' ? d.p5 : d.three;
  if (d.err || (d.probe && need === 'undefined')) {
    const n = document.createElement('div'); n.className = 'warn';
    n.textContent = d.err ? 'Script error: ' + d.err : 'Required library did not load (offline bundle problem).';
    f.parentNode.insertBefore(n, f);
  }
});

function renderMessage(m, streaming) {
  const el = document.createElement('div');
  el.className = 'msg ' + m.role;
  const who = { user: 'You', assistant: 'AI tutor', system: 'Note' }[m.role] || m.role;
  el.innerHTML = `<div class="who">${who}</div><div class="content"></div>`;
  const content = el.querySelector('.content');
  if (m.role === 'user' || m.role === 'system') {
    content.textContent = m.content;
  } else {
    const { html, widgets } = process(m.content || '', streaming);
    content.innerHTML = html;
    content.querySelectorAll('.widget[data-w]').forEach(slot => slot.replaceWith(build(widgets[+slot.dataset.w])));
  }
  return el;
}

// Called from Python: messages = [{role, content}], streaming = bool (last message still arriving)
window.setMessages = function (messages, streaming) {
  messages.forEach((m, i) => {
    const live = streaming && i === messages.length - 1;
    const c = rendered[i];
    if (c && c.content === m.content && c.live === live && c.role === m.role) return;   // unchanged: keep sims running
    const el = renderMessage(m, live);
    if (c) c.el.replaceWith(el); else log.appendChild(el);
    rendered[i] = { content: m.content, live, role: m.role, el };
  });
  while (rendered.length > messages.length) rendered.pop().el.remove();
  window.scrollTo(0, document.body.scrollHeight);
};
