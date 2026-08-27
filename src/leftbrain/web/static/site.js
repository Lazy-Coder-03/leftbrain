(function () {
  document.documentElement.classList.add('js');
  function skeleton(n) { var h = '<div class="skel-lines">'; for (var i = 0; i < n; i++) h += '<i></i>'; return h + '</div>'; }
  // Server-rendered pages: show a skeleton the instant a same-origin navigation or form submit starts.
  function startLoading(target) {
    var doc = document.querySelector('.doc') || document.querySelector('main') || document.body;
    if (!doc.classList.contains('is-loading')) { doc.classList.add('is-loading'); doc.insertAdjacentHTML('afterbegin', skeleton(6)); }
    if (target && target.classList && target.classList.contains('btn')) target.classList.add('is-busy');
  }
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]'); if (!a) return;
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target && a.target !== '_self') return;
    if (a.origin !== location.origin || (a.hash && a.pathname === location.pathname)) return;
    if (a.hasAttribute('download') || a.protocol === 'mailto:') return;
    startLoading(a);
  });
  document.addEventListener('submit', function (e) {
    var f = e.target; if (!(f instanceof HTMLFormElement)) return;
    if (f.hasAttribute('data-confirm') && !window.confirm(f.getAttribute('data-confirm'))) { e.preventDefault(); return; }
    var table = document.querySelector('table.keys'); if (table) table.classList.add('is-loading');
    var btn = f.querySelector('button[type="submit"], .btn'); if (btn) btn.classList.add('is-busy');
    if (f.classList.contains('keybar')) startLoading(btn);
  });
  window.addEventListener('pageshow', function () { // back/forward cache restores the page mid-skeleton
    document.querySelectorAll('.is-loading').forEach(function (el) { el.classList.remove('is-loading'); });
    document.querySelectorAll('.doc > .skel-lines').forEach(function (el) { el.remove(); });
    document.querySelectorAll('.is-busy').forEach(function (el) { el.classList.remove('is-busy'); });
  });
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  function toast(msg) {
    var t = $('#toast'); if (!t) { t = document.createElement('div'); t.id = 'toast'; t.className = 'toast'; document.body.appendChild(t); }
    t.textContent = msg; t.classList.add('show'); clearTimeout(t._h); t._h = setTimeout(function () { t.classList.remove('show'); }, 1600);
  }
  function copyText(txt) { try { navigator.clipboard.writeText(txt).then(function () { toast('Copied'); }); } catch (e) { toast('Select and copy manually'); } }

  // copy buttons: data-copy="#selector" or wrap <pre> in .codewrap
  $$('[data-copy]').forEach(function (b) { b.addEventListener('click', function () { var el = $(b.getAttribute('data-copy')); if (el) copyText(el.textContent); }); });
  $$('.doc pre').forEach(function (pre) {
    var w = document.createElement('div'); w.className = 'codewrap'; pre.parentNode.insertBefore(w, pre); w.appendChild(pre);
    var b = document.createElement('button'); b.type = 'button'; b.className = 'copy'; b.textContent = 'copy';
    b.addEventListener('click', function () { copyText(pre.textContent); }); w.appendChild(b);
  });

  // key lifetime: the never-expires warning appears only while that option is chosen
  var lifetime = $('[data-warn-never]'), neverWarning = $('[data-never-warning]');
  if (lifetime && neverWarning) {
    var syncWarning = function () { neverWarning.hidden = lifetime.value !== 'never'; };
    lifetime.addEventListener('change', syncWarning); syncWarning();
  }

  // docs key picker: choosing a key reloads the page with that key filled in
  var keypick = $('#keypick');
  if (keypick) keypick.addEventListener('change', function () { keypick.form.submit(); });

  // OS tabs in docs
  var os = (navigator.platform || '').match(/win/i) ? 'windows' : (navigator.platform || '').match(/mac/i) ? 'macos' : 'linux';
  try { os = localStorage.getItem('lb-os') || os; } catch (e) {}
  $$('.ostabs').forEach(function (tabs) {
    var group = tabs.parentNode;
    function pick(which) {
      $$('button', tabs).forEach(function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-os') === which); });
      $$('.os-block', group).forEach(function (blk) { blk.classList.toggle('show', blk.getAttribute('data-os') === which); });
    }
    $$('button', tabs).forEach(function (b) { b.addEventListener('click', function () { var w = b.getAttribute('data-os'); try { localStorage.setItem('lb-os', w); } catch (e) {} $$('.ostabs').forEach(function (t) { t._pick && t._pick(w); }); }); });
    tabs._pick = pick; pick(os);
  });

  // live demo
  var demo = $('[data-demo]'); if (!demo) return;
  var FIELDS = {
    numbers: [['values', 'Values (comma separated)', '9.11, 9.9, 10']],
    convert: [['value', 'Value', '3'], ['from_unit', 'From unit', 'oz'], ['to_unit', 'To unit', 'ml']],
    datetime: [['start', 'Start (YYYY-MM-DD)', '2026-08-26'], ['end', 'End (YYYY-MM-DD)', '2026-12-25']],
    text: [['text', 'Text', 'strawberry 🍓 naïve café']]
  };
  var MODE = { numbers: 'compare', convert: 'units', datetime: 'diff', text: 'count' };
  function args(tool, v) {
    if (tool === 'numbers') return { mode: 'compare', values: v.values.split(',').map(function (s) { return s.trim(); }).filter(Boolean) };
    if (tool === 'convert') return { mode: 'units', value: v.value, from_unit: v.from_unit, to_unit: v.to_unit };
    if (tool === 'datetime') return { mode: 'diff', start: v.start, end: v.end };
    return { mode: 'count', text: v.text };
  }
  function pretty(o) {
    return JSON.stringify(o, null, 2).replace(/[&<>]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]; })
      .replace(/"([^"]+)":/g, '<span class="k">"$1"</span>:').replace(/: "([^"]*)"/g, ': <span class="s">"$1"</span>')
      .replace(/: (-?\d+(\.\d+)?)/g, ': <span class="n">$1</span>').replace(/: true/g, ': <span class="n">true</span>').replace(/: false/g, ': <span class="b">false</span>');
  }
  var cur = 'numbers', body = $('#demo-body');
  function render() {
    var f = FIELDS[cur];
    body.innerHTML = '<div class="' + (f.length > 1 ? 'row' : '') + '">' + f.map(function (x) { return '<label>' + x[1] + '<input data-k="' + x[0] + '" value="' + x[2].replace(/"/g, '&quot;') + '"></label>'; }).join('') + '</div>' +
      '<button class="btn run" type="button" id="run">Run ' + cur + '</button><pre class="out" id="out"></pre><div class="latency"><span>POST /mcp · tools/call · ' + cur + ' · mode ' + MODE[cur] + '</span><span id="lat"></span></div>';
    $('#run').addEventListener('click', run);
    $$('input', body).forEach(function (i) { i.addEventListener('keydown', function (e) { if (e.key === 'Enter') run(); }); });
    run();
  }
  function run() {
    var v = {}; $$('input', body).forEach(function (i) { v[i.getAttribute('data-k')] = i.value; });
    var t0 = performance.now(); $('#out').innerHTML = skeleton(5); $('#out').classList.add('skel');
    fetch('/demo/' + cur, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(args(cur, v)) })
      .then(function (r) { return r.json(); })
      .then(function (j) { $('#out').classList.remove('skel'); $('#out').innerHTML = pretty(j); $('#lat').textContent = (performance.now() - t0).toFixed(0) + ' ms'; })
      .catch(function () { $('#out').classList.remove('skel'); $('#out').textContent = 'network error'; });
  }
  $$('.bar button', demo).forEach(function (b) { b.addEventListener('click', function () { $$('.bar button', demo).forEach(function (x) { x.setAttribute('aria-pressed', x === b); }); cur = b.getAttribute('data-tool'); render(); }); });
  render();
})();
