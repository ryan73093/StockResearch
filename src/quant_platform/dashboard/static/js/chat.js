// 2026-11-22 (D-058; 使用者：AI 的部分用個對話小視窗…右下角一個按鍵，然後彈出來就一個對話視窗。不然我完全不知道後端到底有沒有在回).
// The AI chat window of every page, after VectorDB's home-ai-ui.js: the button at the bottom right, the window, the question posted and
// the answer read as a text/event-stream (event: accepted|step|wait|delta|done|error). What the backend is doing is written in the
// window as it happens — each step, "已等 N 秒" every two seconds while a slow one runs, and how long ago the backend last answered —
// so a wait is never a blank. The turn runs on the server whatever the page does: a page change or a dropped connection follows the
// same turn again (GET /research/chat/stream/<id>?after=N) and the finished turns are the thread every page shows (GET /research/chat/thread).
(() => {
  'use strict';
  const launcher = document.getElementById('chat-launcher');
  const win = document.getElementById('chat-window');
  if (!launcher || !win) return;
  const thread = win.querySelector('.chat-thread');
  const form = win.querySelector('.chat-form');
  const input = form.querySelector('textarea');
  const send = form.querySelector('[data-chat-send]');
  const newButton = win.querySelector('[data-chat-new]');
  const sizeButton = win.querySelector('[data-chat-size]');
  const dot = launcher.querySelector('.chat-launcher__dot');
  const examples = (() => { try { return JSON.parse(win.dataset.examples || '[]'); } catch (error) { return []; } })();

  const phone = () => window.matchMedia('(max-width: 767px)').matches;
  const calm = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const area = (storage) => ({
    get: (key) => { try { return storage().getItem(key); } catch (error) { return null; } },
    set: (key, value) => { try { storage().setItem(key, value); } catch (error) { /* this page only */ } },
    del: (key) => { try { storage().removeItem(key); } catch (error) { /* nothing to remove */ } },
  });
  const tab = area(() => window.sessionStorage);       // this tab: the window open, a question running, an answer not yet seen
  const browser = area(() => window.localStorage);     // this browser: the size
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
  const make = (tag, className, html) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (html !== undefined) node.innerHTML = html;
    return node;
  };
  const secs = (value) => `${Number(value || 0).toLocaleString('zh-TW', { maximumFractionDigits: 1 })} 秒`;

  let running = null;          // the turn this page is following
  let restoring = null;        // the promise of the first read of the thread
  let poller = null;

  // ---- the text of an answer: paragraphs, **bold**, [K1] as a link to the record ----
  const inline = (line, cites) => esc(line)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\[(K\d{1,2})\]/g, (whole, cite) => (cites[cite]
      ? `<a class="chat-cite" href="/research/knowledge/${encodeURIComponent(cites[cite].id)}" title="${esc(cites[cite].title)}">[${cite}]</a>` : whole));
  const paragraphs = (text, cites) => String(text || '').split('\n').filter((line) => line.trim())
    .map((line) => `<p>${inline(line, cites)}</p>`).join('');
  const citesOf = (answer) => Object.fromEntries((answer.evidence || []).filter((item) => item.cite).map((item) => [item.cite, item]));

  function answerHtml(answer) {
    const cites = citesOf(answer);
    return (answer.conclusion ? `<p class="chat-answer__lead"><strong>${inline(answer.conclusion, cites)}</strong></p>` : '') + paragraphs(answer.answer, cites);
  }

  function extrasHtml(answer) {
    const evidence = answer.evidence || [];
    const cited = new Set(answer.citations || []);
    const first = evidence.filter((item) => cited.has(item.cite));
    const rest = evidence.filter((item) => !cited.has(item.cite));
    const row = (item) => `<li><a href="/research/knowledge/${encodeURIComponent(item.id)}">${item.cite ? `[${esc(item.cite)}] ` : ''}${esc(item.title)}</a>`
      + `<small>${esc(item.kind)}｜${esc(item.verdict)}${item.date ? `｜${esc(item.date)}` : ''}</small></li>`;
    const html = [];
    if (first.length) html.push(`<div><p>出處</p><ul class="chat-sources">${first.map(row).join('')}</ul></div>`);
    if (rest.length) {
      html.push(`<details${first.length ? '' : ' open'}><summary>${first.length ? 'AI 讀過的其他紀錄' : '最相關的紀錄'}（${rest.length} 筆）</summary>`
        + `<ul class="chat-sources">${rest.map(row).join('')}</ul></details>`);
    }
    const meta = [];
    if (answer.model) meta.push(`模型 ${esc(answer.model)}`);
    if (answer.cached) meta.push('用上次的答案，不重複計費');
    meta.push(`<a href="${esc(answer.search || '/research/knowledge')}">看全部搜尋結果</a>`);
    html.push(`<p>${meta.join('・')}</p>`);
    return html.join('');
  }

  // ---- the steps of the backend ----
  function stepRow(list, rows, data) {
    let row = rows.get(data.key);
    if (!row) {
      row = make('li', 'chat-step', '<span class="chat-step__mark" aria-hidden="true"></span><span class="chat-step__text"><b></b>'
        + '<span class="chat-step__note"></span></span><span class="chat-step__time"></span>');
      list.append(row);
      rows.set(data.key, row);
    }
    row.dataset.state = data.state;
    row.querySelector('b').textContent = data.label;
    row.querySelector('.chat-step__note').textContent = data.note || (data.state === 'running' ? (data.hint || '') : '');
    row.querySelector('.chat-step__time').textContent = data.state === 'running' ? '' : secs(data.seconds);
  }

  const savedSteps = (steps, total) => {
    if (!steps || !steps.length) return null;
    const box = make('details', 'chat-steps-saved');
    box.append(make('summary', '', `後端做了 ${steps.length} 步${total ? `，共 ${secs(total)}` : ''}（點開看每一步）`));
    const list = make('ol', 'chat-steps');
    const rows = new Map();
    steps.forEach((item) => stepRow(list, rows, item));
    box.append(list);
    return box;
  };

  // ---- one question and its answer on the screen ----
  let stick = true;            // the thread follows its end until the reader scrolls up
  thread.addEventListener('scroll', () => { stick = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 40; });
  const scrollDown = (force) => {
    if (force || stick) { thread.scrollTop = thread.scrollHeight; stick = true; }
  };

  function typer(render) {
    let full = '', shown = 0, timer = null, waiting = null;
    const tick = () => {
      if (shown < full.length) {
        shown = Math.min(full.length, shown + Math.max(2, Math.ceil((full.length - shown) / 14)));
        render(full.slice(0, shown));
        timer = window.setTimeout(tick, 28);
      } else {
        timer = null;
        if (waiting) { const done = waiting; waiting = null; done(); }
      }
    };
    return {
      add(text) { full += text; if (calm()) { shown = full.length; render(full); return; } if (!timer) tick(); },
      finish() { return new Promise((resolve) => { if (calm() || shown >= full.length) resolve(); else { waiting = resolve; if (!timer) tick(); } }); },
      stop() { window.clearTimeout(timer); timer = null; },
    };
  }

  function turnUi(question) {
    const root = make('article', 'chat-turn');
    root.append(make('p', 'chat-q', esc(question)));
    const list = make('ol', 'chat-steps');
    list.hidden = true;
    list.setAttribute('aria-label', '後端的步驟');
    const wait = make('p', 'chat-wait', '送出中…');
    wait.setAttribute('role', 'status');
    wait.setAttribute('aria-live', 'off');
    const body = make('div', 'chat-body');
    root.append(list, wait, body);
    const ui = { root, question, id: null, next: 0, finished: false, rows: new Map(), started: performance.now(), last: performance.now(),
      label: '', clock: null, typing: null, lead: null, text: null, writer: null, done: Promise.resolve() };

    ui.paint = () => {
      const now = performance.now();
      const total = Math.round((now - ui.started) / 1000);
      const quiet = Math.round((now - ui.last) / 1000);
      const stale = quiet >= 8;
      wait.dataset.stale = String(stale);
      wait.textContent = `回答中・已 ${total} 秒${ui.label ? `・${ui.label}` : ''}・`
        + (stale ? `⚠ 已 ${quiet} 秒沒有收到後端的回報，連線可能有問題（還在等）` : (quiet <= 1 ? '後端剛回報' : `後端 ${quiet} 秒前回報`));
    };
    ui.startClock = () => { window.clearInterval(ui.clock); ui.paint(); ui.clock = window.setInterval(ui.paint, 1000); };
    ui.stopClock = () => { window.clearInterval(ui.clock); ui.clock = null; wait.hidden = true; };

    const ensureTyping = () => {
      if (ui.typing) return;
      ui.typing = make('div', 'chat-answer');
      ui.typing.setAttribute('aria-hidden', 'true');          // read once, when the finished answer is added
      ui.lead = make('p', 'chat-answer__lead');
      ui.text = make('div', 'chat-answer__text');
      ui.typing.append(ui.lead, ui.text);
      body.append(ui.typing);
      ui.writer = typer((shown) => {
        ui.text.innerHTML = paragraphs(shown, {}) + (ui.finished ? '' : '<span class="chat-cursor" aria-hidden="true"></span>');
        scrollDown(false);
      });
    };

    ui.event = (event, data) => {
      ui.last = performance.now();
      if (event === 'accepted') {
        ui.id = data.turn;
        tab.set('chatTurn', data.turn);
        const age = Date.now() - Date.parse(data.at || '');
        if (Number.isFinite(age) && age > 0) ui.started = performance.now() - age;      // a turn followed again keeps its own clock
        ui.startClock();
      } else if (event === 'step') {
        list.hidden = false;
        stepRow(list, ui.rows, data);
        if (data.state === 'running') ui.label = data.label;
        ui.paint();
      } else if (event === 'wait') {
        ui.label = ui.label || '';
        ui.paint();
      } else if (event === 'delta') {
        ensureTyping();
        if (data.part === 'conclusion') ui.lead.innerHTML = `<strong>${esc(data.text)}</strong>`;
        else ui.writer.add(`${data.text}\n`);
        scrollDown(false);
      } else if (event === 'done') {
        ui.finished = true;
        ui.stopClock();
        ui.done = (async () => {
          if (ui.writer) await ui.writer.finish();
          ui.typing?.remove();
          ui.show(data.answer, data.seconds);
        })();
      } else if (event === 'error') {
        ui.fail(data.error);
      }
    };

    // the finished answer, and the steps folded under it
    ui.show = (answer, total) => {
      ui.typing?.remove();
      body.textContent = '';
      const hasAnswer = Boolean(answer.answer || answer.conclusion);
      if (hasAnswer) {
        const box = make('div', 'chat-answer', answerHtml(answer));
        body.append(box);
      } else {
        body.append(make('p', 'chat-notice', `${esc(answer.note || 'AI 這次沒有回答')}<br>下面是最相關的紀錄，可以點進去看。`));
      }
      if (hasAnswer && answer.note) body.append(make('p', 'chat-notice', esc(answer.note)));
      body.append(make('div', 'chat-extra', extrasHtml(answer)));
      const folded = savedSteps(ui.steps(), total);
      if (folded) { list.replaceWith(folded); }
      wait.hidden = true;
      scrollDown(false);
    };
    ui.steps = () => [...ui.rows.keys()].map((key) => {
      const row = ui.rows.get(key);
      return { key, label: row.querySelector('b').textContent, state: row.dataset.state, note: row.querySelector('.chat-step__note').textContent,
        seconds: Number.parseFloat(row.querySelector('.chat-step__time').textContent) || 0 };
    });

    ui.fail = (message) => {
      ui.finished = true;
      ui.stopClock();
      ui.writer?.stop();
      ui.typing?.remove();
      list.querySelectorAll('.chat-step[data-state="running"]').forEach((row) => { row.dataset.state = 'failed'; });
      body.append(make('p', 'chat-notice chat-notice--bad', esc(message || '這一題沒有回完')));
      tab.del('chatTurn');
      scrollDown(false);
    };
    ui.reconnecting = (count) => {
      wait.hidden = false;
      wait.textContent = `連線中斷，重新接上（第 ${count} 次）…後端還在處理的話，答案不會丟`;
    };
    return ui;
  }

  // ---- the saved thread ----
  function renderSaved(item) {
    const root = make('article', 'chat-turn');
    root.append(make('p', 'chat-q', esc(item.q)));
    const folded = savedSteps(item.steps, item.seconds);
    if (folded) root.append(folded);
    const body = make('div', 'chat-body');
    if (item.status === 'done' && item.answer) {
      const answer = item.answer;
      if (answer.answer || answer.conclusion) body.append(make('div', 'chat-answer', answerHtml(answer)));
      else body.append(make('p', 'chat-notice', `${esc(answer.note || 'AI 這次沒有回答')}`));
      if ((answer.answer || answer.conclusion) && answer.note) body.append(make('p', 'chat-notice', esc(answer.note)));
      body.append(make('div', 'chat-extra', extrasHtml(answer)));
    } else {
      body.append(make('p', 'chat-notice chat-notice--bad', esc(item.error || (item.status === 'interrupted' ? '這一題沒有回完（網站重新啟動過）' : '這一題沒有回完'))));
    }
    root.append(body);
    return root;
  }

  function hello() {
    const box = make('div', 'chat-hello');
    box.append(make('p', '', '問研究知識庫：試過哪些策略、結果怎樣、當初為什麼這樣決定。AI 只根據知識庫的紀錄回答，每句附出處。'));
    box.append(make('p', 'chat-hello__small', '後端做到哪一步會一步一步寫在這裡，包括「已等幾秒」；出錯也寫在這裡。'
      + '換到別的頁面，這一題照樣在後端跑，回來這裡就看得到。'));
    if (examples.length) {
      const list = make('div', 'chat-examples');
      examples.forEach((text) => {
        const button = make('button', 'chat-example', esc(text));
        button.type = 'button';
        button.dataset.chatExample = text;
        list.append(button);
      });
      box.append(list);
    }
    thread.append(box);
  }

  // ---- talking to the backend ----
  function handleBlock(block, ui) {
    let event = 'message', data = '', id = null;
    block.split('\n').forEach((line) => {
      if (line.startsWith(':')) return;                       // a keep-alive comment
      if (line.startsWith('id: ')) id = Number(line.slice(4));
      else if (line.startsWith('event: ')) event = line.slice(7);
      else if (line.startsWith('data: ')) data += line.slice(6);
    });
    if (!data) return;
    let parsed;
    try { parsed = JSON.parse(data); } catch (error) { return; }
    if (id !== null && Number.isFinite(id)) ui.next = Math.max(ui.next, id + 1);
    ui.event(event, parsed);
  }

  async function readStream(response, ui) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      ui.last = performance.now();                            // any bytes, a keep-alive too, say the backend is there
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        handleBlock(block, ui);
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) handleBlock(buffer, ui);
  }

  async function fromThread(ui) {                             // the turn is no longer in the server's memory: its record says how it ended
    try {
      const data = await (await fetch('/research/chat/thread')).json();
      const item = (data.turns || []).find((turn) => turn.id === ui.id);
      if (item && item.status === 'done' && item.answer) {
        ui.finished = true;
        ui.stopClock();
        (item.steps || []).forEach((step) => stepRow(ui.root.querySelector('.chat-steps') || make('ol'), ui.rows, step));
        ui.show(item.answer, item.seconds);
        tab.del('chatTurn');
        return;
      }
      ui.fail((item && item.error) || '這一題的紀錄找不到，可能是網站重新啟動過；再問一次（同一題 AI 已經答過的不會重複計費）');
    } catch (error) {
      ui.fail('連線斷了，而且對話紀錄也讀不到。後端可能還在處理，稍後打開這個視窗再看。');
    }
  }

  async function track(ui, open) {
    let attempts = 0;
    for (;;) {
      try {
        const response = await open();
        if (!response.ok || !response.body) {
          const fault = await response.json().catch(() => ({}));
          if (response.status === 404 && ui.id) { await fromThread(ui); return; }
          ui.fail(fault.error || `後端回 HTTP ${response.status}`);
          return;
        }
        await readStream(response, ui);
        if (ui.finished) { await ui.done; return; }
        throw new Error('the connection closed before the answer');
      } catch (error) {
        if (ui.finished) { await ui.done; return; }
        attempts += 1;
        if (!ui.id || attempts > 5) {
          ui.fail('連線斷了，接不回來。後端如果還在處理，答案會留在對話紀錄裡，稍後打開這個視窗再看。');
          return;
        }
        ui.reconnecting(attempts);
        await sleep(1000 * attempts);
        open = () => fetch(`/research/chat/stream/${encodeURIComponent(ui.id)}?after=${ui.next}`);
      }
    }
  }

  const setBusy = (on) => {
    launcher.classList.toggle('is-busy', on);
    send.disabled = on;
    send.textContent = on ? '回答中…' : '送出';
    newButton.disabled = on;
    if (on) { stopPolling(); dot.hidden = true; }
  };

  function markUnread() {
    tab.set('chatUnread', '1');
    dot.hidden = false;
  }

  async function follow(ui, open) {
    running = ui;
    setBusy(true);
    try { await track(ui, open); } finally {
      running = null;
      setBusy(false);
      tab.del('chatTurn');
      if (!isOpen()) markUnread();
    }
  }

  async function restore() {
    if (restoring) return restoring;
    restoring = (async () => {
      thread.setAttribute('aria-busy', 'true');
      try {
        const response = await fetch('/research/chat/thread', { headers: { Accept: 'application/json' } });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        thread.textContent = '';
        const turns = data.turns || [];
        if (!turns.length) hello();
        let live = null;
        turns.forEach((item) => {
          if (item.status === 'running' && data.running && data.running.id === item.id) { live = item; return; }
          thread.append(renderSaved(item));
        });
        if (live && !running) {
          const ui = turnUi(live.q);
          ui.id = live.id;
          thread.append(ui.root);
          follow(ui, () => fetch(`/research/chat/stream/${encodeURIComponent(live.id)}?after=0`));
        } else if (!data.running) {
          tab.del('chatTurn');
          if (!running) setBusy(false);
        }
      } catch (error) {
        thread.textContent = '';
        hello();
        thread.append(make('p', 'chat-notice chat-notice--bad', `對話紀錄讀不到（${esc(error.message)}）。還是可以問新的問題。`));
      } finally {
        thread.removeAttribute('aria-busy');
        scrollDown(true);
      }
    })();
    return restoring;
  }

  async function ask(raw) {
    const text = String(raw || '').replace(/\s+/g, ' ').trim();
    if (!text) return;
    open(false);
    await restore();
    if (running) {
      const note = make('p', 'chat-notice', '上一題還在處理，等它回完再問。');
      thread.append(note);
      window.setTimeout(() => note.remove(), 5000);
      scrollDown(true);
      return;
    }
    thread.querySelector('.chat-hello')?.remove();
    const ui = turnUi(text);
    thread.append(ui.root);
    scrollDown(true);
    ui.startClock();
    await follow(ui, () => fetch('/research/chat', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ question: text }) }));
  }

  // ---- the window ----
  const isOpen = () => !win.hidden;
  function open(focus = true, instant = false) {
    if (!isOpen()) {
      win.classList.toggle('no-anim', instant);              // a page that reopens the window on load does not animate it again
      win.hidden = false;
    }
    launcher.setAttribute('aria-expanded', 'true');
    launcher.setAttribute('aria-label', '收合 AI 對話');
    tab.set('chatOpen', '1');
    tab.del('chatUnread');
    dot.hidden = true;
    stopPolling();
    restore();
    if (focus && !phone()) input.focus({ preventScroll: true });          // a phone's keyboard would cover the window
    scrollDown(true);
  }
  function close(returnFocus = true) {
    win.hidden = true;
    launcher.setAttribute('aria-expanded', 'false');
    launcher.setAttribute('aria-label', '開啟 AI 對話');
    tab.del('chatOpen');
    if (returnFocus) launcher.focus({ preventScroll: true });
    if (!running && tab.get('chatTurn')) startPolling();
  }

  // a question running while the window is shut (or on the page before): ask the thread until it is over, then light the dot
  async function poll() {
    if (isOpen() || running) { stopPolling(); return; }
    try {
      const data = await (await fetch('/research/chat/thread')).json();
      if (!data.running) { tab.del('chatTurn'); markUnread(); setBusy(false); stopPolling(); }
    } catch (error) { /* the next round */ }
  }
  function startPolling() { if (!poller) poller = window.setInterval(poll, 3000); }
  function stopPolling() { window.clearInterval(poller); poller = null; }

  const large = () => browser.get('chatLarge') === '1';
  function setLarge(on) {
    browser.set('chatLarge', on ? '1' : '0');
    win.classList.toggle('is-large', on);
    sizeButton.title = on ? '縮小' : '放大';
    sizeButton.setAttribute('aria-label', sizeButton.title);
    sizeButton.textContent = on ? '⤡' : '⤢';
  }
  setLarge(large());

  const grow = () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight + 2, 120)}px`; };

  launcher.addEventListener('click', () => (isOpen() ? close() : open()));
  win.querySelector('[data-chat-close]').addEventListener('click', () => close());
  sizeButton.addEventListener('click', () => setLarge(!win.classList.contains('is-large')));
  newButton.addEventListener('click', async () => {
    if (running) return;
    try {
      const response = await fetch('/research/chat/thread', { method: 'DELETE' });
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || `HTTP ${response.status}`);
      thread.textContent = '';
      hello();
    } catch (error) {
      thread.append(make('p', 'chat-notice chat-notice--bad', `開新對話失敗：${esc(error.message)}`));
    }
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const text = input.value;
    if (!text.trim() || running) return;
    input.value = '';
    grow();
    ask(text);
  });
  input.addEventListener('input', grow);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); form.requestSubmit(); }
  });
  win.addEventListener('click', (event) => {
    const example = event.target.closest('[data-chat-example]');
    if (example) ask(example.dataset.chatExample);
  });
  document.addEventListener('keydown', (event) => {
    const here = win.contains(document.activeElement) || document.activeElement === launcher || document.activeElement === document.body;
    if (event.key === 'Escape' && isOpen() && here && !document.querySelector('dialog[open]')) close();
  });
  // a button anywhere that asks the AI (the knowledge page's 「問 AI」, a recent question): the window opens and asks
  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-chat-ask]');
    if (!button) return;
    event.preventDefault();
    const source = button.dataset.chatSource ? document.querySelector(button.dataset.chatSource) : null;
    const question = button.dataset.chatQuestion || (source ? source.value : '');
    if (question && question.trim()) ask(question); else { open(); input.focus(); }
  });

  window.pimiChat = { open, close, ask };

  // the page before may have left a question running or an answer unseen
  if (tab.get('chatUnread') === '1') dot.hidden = false;
  if (tab.get('chatTurn')) setBusy(true);
  if (tab.get('chatOpen') === '1' && !phone()) open(false, true);
  else if (tab.get('chatTurn')) startPolling();
})();
