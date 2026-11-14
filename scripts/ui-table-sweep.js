// 2026-11-15 (介面檢查 E-2): paste into the browser console on any page of the site (or run through a browser tool).
// It opens every tab, group and folded card, then lists each table that is wider than its card or has a row taller
// than 260 px, and how far the page itself overflows. Used at 1366, 1920 and 375 px (docs/rules/ui.md 驗收).
(() => {
  const out = [];
  const panels = [...document.querySelectorAll('[data-tabset] > *')];
  const hiddenBefore = panels.map((p) => p.hidden);
  panels.forEach((p) => { p.hidden = false; });
  document.querySelectorAll('.dash-grid > [data-group]').forEach((p) => { p.dataset.wasHidden = p.hidden ? '1' : ''; p.hidden = false; });
  document.querySelectorAll('details').forEach((d) => { d.dataset.wasOpen = d.open ? '1' : ''; d.open = true; });
  document.querySelectorAll('.table-wrap, .doc table').forEach((w) => {
    const rows = [...w.querySelectorAll('tbody tr')].filter((r) => r.offsetHeight);
    const tall = Math.max(0, ...rows.map((r) => r.offsetHeight));
    const over = w.scrollWidth - w.clientWidth;
    if (over > 4 || tall > 260) {
      const sec = w.closest('section, details, [data-tab]');
      const h = sec ? (sec.querySelector('h2') || {}).innerText || sec.dataset.tab || '' : '';
      out.push(`${h.slice(0, 24)}: over ${over}px, tallest row ${tall}px, ${rows.length} rows`);
    }
  });
  const pageOver = document.documentElement.scrollWidth - innerWidth;
  document.querySelectorAll('details').forEach((d) => { d.open = d.dataset.wasOpen === '1'; });
  document.querySelectorAll('.dash-grid > [data-group]').forEach((p) => { p.hidden = p.dataset.wasHidden === '1'; });
  panels.forEach((p, i) => { p.hidden = hiddenBefore[i]; });
  return JSON.stringify({ path: location.pathname + location.search, w: innerWidth, pageOver, tables: document.querySelectorAll('.table-wrap').length, bad: out });
})()
