(function () {
  document.documentElement.classList.add('js');
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
    var t0 = performance.now(); $('#out').textContent = '…';
    fetch('/demo/' + cur, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(args(cur, v)) })
      .then(function (r) { return r.json(); })
      .then(function (j) { $('#out').innerHTML = pretty(j); $('#lat').textContent = (performance.now() - t0).toFixed(0) + ' ms'; })
      .catch(function () { $('#out').textContent = 'network error'; });
  }
  $$('.bar button', demo).forEach(function (b) { b.addEventListener('click', function () { $$('.bar button', demo).forEach(function (x) { x.setAttribute('aria-pressed', x === b); }); cur = b.getAttribute('data-tool'); render(); }); });
  render();
})();
