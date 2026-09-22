/* picker.js — flip between design variants on one page (/jg-ui).
 *
 * Drop into any page (a static file, or a dev-only route in React, Vue,
 * Svelte …) whose variants are siblings marked
 *   <section data-variant="1" data-name="Quiet">…</section>
 * One variant shows at a time, full size, in its real surroundings. Keys
 * 1–9 or the arrows flip; D flips light/dark (sets data-theme on <html>);
 * ?theme=dark and #v2 in the URL pick the start. The bar is chrome: it never
 * takes a variant's colours, so it can't flatter one of them. Switching is
 * instant on purpose — flipping is a 100-times-a-session action.
 *
 * The pattern (divergent variants behind a switcher, the human picks) is
 * Emil Kowalski's `prototype` skill (github.com/emilkowalski/skills, MIT).
 */
(function () {
  if (typeof document === 'undefined' || document.getElementById('jgui-picker')) return;
  function boot() {
    var v = [].slice.call(document.querySelectorAll('[data-variant]'));
    if (!v.length) return;
    var css = document.createElement('style');
    css.textContent = '#jgui-picker{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);display:flex;gap:4px;align-items:center;' +
      'padding:6px;border-radius:12px;background:#111;color:#eee;font:13px/1.2 system-ui,sans-serif;box-shadow:0 8px 24px rgb(0 0 0/.3);' +
      'z-index:2147483647;max-width:calc(100vw - 32px);overflow-x:auto}' +
      '#jgui-picker button{font:inherit;color:#bbb;background:transparent;border:0;border-radius:8px;padding:8px 10px;cursor:pointer;min-height:32px;white-space:nowrap}' +
      '#jgui-picker button[aria-pressed=true]{background:#333;color:#fff}#jgui-picker button:focus-visible{outline:2px solid #fff;outline-offset:1px}' +
      '#jgui-picker .sep{width:1px;height:20px;background:#333;margin:0 4px;flex:none}@media (max-width:480px){#jgui-picker .name{display:none}}';
    document.head.appendChild(css);
    var bar = document.createElement('div');
    bar.id = 'jgui-picker';
    bar.setAttribute('role', 'toolbar');
    bar.setAttribute('aria-label', 'Variants');
    v.forEach(function (el, k) {
      var b = document.createElement('button');
      b.type = 'button';
      b.setAttribute('data-to', String(k));
      b.innerHTML = (k + 1) + '<span class="name"></span>';
      b.querySelector('.name').textContent = el.getAttribute('data-name') ? ' · ' + el.getAttribute('data-name') : '';
      bar.appendChild(b);
    });
    var sep = document.createElement('span'); sep.className = 'sep'; bar.appendChild(sep);
    var tb = document.createElement('button'); tb.type = 'button'; tb.id = 'jgui-theme'; bar.appendChild(tb);
    document.body.appendChild(bar);
    var i = 0, root = document.documentElement;
    function label() { tb.textContent = root.getAttribute('data-theme') === 'dark' ? 'Light' : 'Dark'; }
    function show(n) {
      i = (n + v.length) % v.length;
      v.forEach(function (el, k) { el.hidden = k !== i; });
      [].slice.call(bar.querySelectorAll('[data-to]')).forEach(function (b, k) { b.setAttribute('aria-pressed', k === i ? 'true' : 'false'); });
      try { history.replaceState(null, '', location.pathname + location.search + '#v' + (i + 1)); } catch (e) { /* file:// in some browsers */ }
    }
    function theme() { root.setAttribute('data-theme', root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'); label(); }
    bar.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      if (b === tb) theme(); else show(+b.getAttribute('data-to'));
    });
    document.addEventListener('keydown', function (e) {
      if (e.target.closest && e.target.closest('input,textarea,select,[contenteditable]')) return;
      var k = e.key;
      if (k >= '1' && k <= '9' && +k <= v.length) show(+k - 1);
      else if (k === 'ArrowRight') show(i + 1);
      else if (k === 'ArrowLeft') show(i - 1);
      else if (k === 'd' || k === 'D') theme();
    });
    var q = /[?&]theme=(dark|light)/.exec(location.search);
    if (q) root.setAttribute('data-theme', q[1]);
    label();
    var m = /^#v(\d)$/.exec(location.hash);
    show(m ? +m[1] - 1 : 0);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
