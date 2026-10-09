"use strict";
// A frontend-only adapter for the reader's existing API contract.
(() => {
  const nativeFetch = window.fetch.bind(window);
  let model, storageKey;
  const guest = ['/guest', '/guest/read'].includes(location.pathname);
  let guestInput = null;
  if (guest) {
    try { guestInput = JSON.parse(sessionStorage.getItem('intel-guest-input')); } catch {}
    if (!guestInput?.id || !Array.isArray(guestInput.articles)) {
      guestInput = {id: crypto.randomUUID(), articles: []};
      sessionStorage.setItem('intel-guest-input', JSON.stringify(guestInput));
    }
    document.querySelector('meta[name="user-id"]').content = 'temporary-' + guestInput.id;
  }
  let activeID = null;
  if (!guest) {
    try { activeID = sessionStorage.getItem('intel-temporary-active'); } catch {}
    if (activeID) document.querySelector('meta[name="user-id"]').content = 'temporary-' + activeID;
  }
  const parseEndpoint = guest ? '/guest/prepare' : '/temporary/parse';
  async function parseSources(records, body = null, type = 'application/json') {
    const response = await nativeFetch(parseEndpoint, {method:'POST',headers:{'Content-Type':type,'X-Guest-Request':'1'},body:body || JSON.stringify({articles:records})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not parse session articles.');
    return result.articles;
  }
  const ready = (guest ? Promise.resolve(guestInput) : nativeFetch('/temporary-session.json').then(response => response.json())).then(async supplied => {
    if (!guest && !activeID && typeof sessionStorage.key === 'function') {
      for (let index = 0; index < sessionStorage.length; index++) {
        const key = sessionStorage.key(index);
        if (!key.startsWith('intel-temporary:')) continue;
        try {
          const previous = JSON.parse(sessionStorage.getItem(key));
          if (supplied.articles.every(article => previous.articles.some(item => item.url === article.url))) { activeID = previous.id; break; }
        } catch {}
      }
    }
    if (!guest && activeID) supplied.id = activeID;
    storageKey = 'intel-temporary:' + supplied.id;
    try { model = JSON.parse(sessionStorage.getItem(storageKey)); } catch {}
    if (!model || model.id !== supplied.id) {
      model = supplied;
      model.queue = {id: supplied.id, cursor: 0, finished: false, urls: supplied.articles.map(a => a.url)};
    }
    model.sources ||= model.articles.map(article => article.source || article);
    const parsed = model.sources.length ? await parseSources(model.sources) : [];
    model.articles = parsed.map(article => {
      const previous = model.articles.find(item => item.url === article.url);
      return previous ? {...article,...Object.fromEntries(['notes','notes_format','notes_revision','is_read','has_notes','notes_updated_at'].map(key => [key,previous[key]]))} : article;
    });
    remember();
  });
  function remember() { sessionStorage.setItem(storageKey, JSON.stringify(model)); if (!guest) sessionStorage.setItem('intel-temporary-active', model.id); }
  async function addSources(records, body = null, type = 'application/json') {
    const articles = await parseSources(records, body, type);
    const old = model;
    const next = {...model,articles:[...model.articles],sources:[...model.sources]};
    for (const article of articles) {
      const index = next.articles.findIndex(item => item.url === article.url);
      if (index < 0) { next.articles.push(article); next.sources.push(article.source || article); }
    }
    if (next.articles.length > 100) throw new Error('Temporary sessions support up to 100 articles.');
    model = next;
    try { remember(); } catch { model = old; throw new Error('The browser session is full. Export your notes and start a new session.'); }
    return {url:articles[0].url};
  }
  function timerState() {
    const timer = (model.timers || []).findLast(item => ['running', 'paused'].includes(item.status));
    if (!timer) return null;
    return {...timer, elapsed_seconds: timer.elapsed_seconds + (timer.status === 'running' ? Math.max(0, (Date.now() - Date.parse(timer.resumed_at)) / 1000) : 0)};
  }
  function timerAction(data) {
    model.timers ||= [];
    const current = timerState(), stamp = new Date().toISOString();
    if (data.action === 'start') {
      if (current) throw new Error('End your current timer before starting another.');
      if (!Number.isInteger(data.target_seconds) || data.target_seconds < 30 || data.target_seconds > 14400) throw new Error('Choose a target between 0.5 and 240 minutes.');
      const article = model.articles.find(item => item.url === data.url);
      if (!article) throw new Error('Article not found.');
      if (['audio', 'video'].includes(article.content_format)) throw new Error('Use playback controls for audio/video.');
      const body = document.createElement('div');
      body.innerHTML = article.body_html || '';
      body.querySelectorAll('[data-reading-exclude], figure, video, iframe').forEach(element => element.remove());
      const words = (body.textContent || '').trim().split(/\s+/).filter(Boolean).length;
      if (!words) throw new Error('This article has no saved text to time.');
      model.timers.push({id: crypto.randomUUID(), article_url: article.url, title: article.title, word_count: words, target_seconds: data.target_seconds, estimated_seconds: Math.max(1, Math.ceil(words / 200)) * 60, started_at: stamp, resumed_at: stamp, elapsed_seconds: 0, status: 'running'});
      return timerState();
    }
    if (!current || current.id !== data.id) {
      const completed = model.timers.find(item => item.id === data.id && item.status === 'completed');
      if (data.action === 'finish' && completed) return {...completed};
      throw new Error('This timer has changed. Refresh and try again.');
    }
    if (!['pause', 'resume', 'finish', 'abandon'].includes(data.action)) throw new Error('Unknown timer action.');
    const timer = model.timers.find(item => item.id === current.id);
    if (data.action === 'resume') {
      if (timer.status === 'paused') { timer.status = 'running'; timer.resumed_at = stamp; }
      return timerState();
    }
    timer.elapsed_seconds = current.elapsed_seconds;
    timer.resumed_at = null;
    timer.status = {pause:'paused', finish:'completed', abandon:'abandoned'}[data.action];
    timer.finished_at = data.action === 'pause' ? null : stamp;
    if (data.action === 'finish') model.articles.find(item => item.url === timer.article_url).is_read = true;
    return {...timer};
  }
  function queue() {
    const items = model.queue.urls.map(url => model.articles.find(a => a.url === url));
    return {...model.queue, items, read_count: items.filter(a => a.is_read).length};
  }
  window.fetch = async (resource, options = {}) => {
    const url = new URL(typeof resource === 'string' ? resource : resource.url, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) return nativeFetch(resource, options);
    let result, status = 200;
    try {
      await ready;
      const path = url.pathname;
      if (path === '/api/file-upload') {
        const file = options.body, name = decodeURIComponent(options.headers['X-Filename']).toLowerCase();
        if (name.endsWith('.pdf')) result = await addSources(null, file, 'application/pdf');
        else {
          const text = await file.text();
          if (name.endsWith('.json')) {
            const input = JSON.parse(text);
            result = await addSources(Array.isArray(input) ? input : input.articles);
          } else {
            const key = /\.html?$/.test(name) ? 'html' : name.endsWith('.txt') ? 'text' : 'markdown';
            result = await addSources([{title:decodeURIComponent(options.headers['X-Filename']),url:'https://temporary.invalid/'+crypto.randomUUID(),[key]:text}]);
          }
        }
        return new Response(JSON.stringify(result), {headers:{'Content-Type':'application/json'}});
      }
      const data = options.body ? JSON.parse(options.body) : null;
      if (path === '/api/link' && data) result = await addSources([{url:data.url}]);
      else if (path === '/api/state' && !data) result = {articles: model.articles, queue: queue()};
      else if (path === '/api/timer') result = {session: data ? timerAction(data) : timerState()};
      else if (path === '/api/article' && !data) {
        result = model.articles.find(a => a.url === url.searchParams.get('url'));
        if (!result) throw new Error('Article not in this temporary session.');
      } else if (path === '/api/notes' && data) {
        const article = model.articles.find(a => a.url === data.url);
        if (!article || article.notes_revision !== data.revision) throw new Error('Notes changed. Refresh before editing.');
        if (typeof data.text !== 'string' || data.text.length > 100000) throw new Error('Invalid notes.');
        const container = document.createElement('div');
        container.innerHTML = data.text;
        for (const element of [...container.querySelectorAll('*')]) {
          if (!['P','DIV','BR','STRONG','B','EM','I','MARK','BLOCKQUOTE','UL','OL','LI'].includes(element.tagName)) element.replaceWith(...element.childNodes);
          else for (const attribute of [...element.attributes]) element.removeAttribute(attribute.name);
        }
        article.notes = container.innerHTML;
        article.notes_format = 'html';
        article.has_notes = !!container.textContent.trim();
        article.notes_updated_at = new Date().toISOString();
        result = {revision: ++article.notes_revision};
      } else if (path === '/api/delete-article' && data) {
        if (!model.articles.some(article => article.url === data.url)) throw new Error('Article not found.');
        const removed = model.queue.urls.indexOf(data.url);
        model.timers = (model.timers || []).filter(timer => timer.article_url !== data.url);
        const articleIndex = model.articles.findIndex(article => article.url === data.url);
        model.sources = model.sources.filter((source, index) => index !== articleIndex);
        model.articles = model.articles.filter(article => article.url !== data.url);
        model.queue.urls = model.queue.urls.filter(url => url !== data.url);
        if (removed >= 0 && removed < model.queue.cursor) model.queue.cursor--;
        model.queue.cursor = Math.max(0, Math.min(model.queue.cursor, model.queue.urls.length - 1));
        if (!model.queue.urls.length) model.queue.finished = true;
        result = {deleted: data.url};
      } else if (path === '/api/queue' && data) {
        if (!Array.isArray(data.urls) || !data.urls.length || new Set(data.urls).size !== data.urls.length || data.urls.some(url => !model.articles.some(a => a.url === url))) throw new Error('Choose articles from this session.');
        model.queue = {id: model.id, cursor: 0, finished: false, urls: data.urls}; result = queue();
      } else if (path === '/api/navigate' && data) {
        const q = model.queue;
        if (data.queue_id !== q.id || data.cursor !== q.cursor || q.finished) throw new Error('The queue changed. Refresh to continue.');
        const article = model.articles.find(a => a.url === q.urls[q.cursor]);
        if (data.action === 'unread') article.is_read = false;
        else if (data.action === 'previous') q.cursor = Math.max(0, q.cursor - 1);
        else if (['next','mark_next'].includes(data.action)) {
          if (data.action === 'mark_next') article.is_read = true;
          if (q.cursor + 1 === q.urls.length) q.finished = true;
          else q.cursor++;
        } else throw new Error('Unknown navigation action.');
        result = queue();
      } else throw new Error('This action requires the full app. Temporary sessions use only the supplied articles.');
      remember();
    } catch (error) { result = {error: error.message}; status = 400; }
    return new Response(JSON.stringify(result), {status, headers: {'Content-Type':'application/json'}});
  };
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('article-type').value = 'All';
    document.getElementById('timeframe').value = '0';
    document.getElementById('export-temporary').onclick = async () => {
      await ready;
      await window.saveNow?.();
      const file = new Blob([JSON.stringify({articles: model.articles, timed_reading_sessions: model.timers || []}, null, 2)], {type:'application/json'});
      const url = URL.createObjectURL(file), link = document.createElement('a');
      link.href = url; link.download = 'intel-sourcerer-session.json'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
  });
})();
