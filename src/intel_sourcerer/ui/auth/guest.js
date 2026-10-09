"use strict";
const guestForm = document.getElementById('guest-form');
guestForm.addEventListener('submit', async event => {
  event.preventDefault();
  const button = document.getElementById('guest-start'), error = document.getElementById('guest-error');
  button.disabled = true; error.hidden = true;
  const records = [];
  async function prepare(body, type) {
    const response = await fetch('/guest/prepare', {method:'POST',headers:{'Content-Type':type,'X-Guest-Request':'1'},body});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not prepare these articles.');
    return result.articles;
  }
  try {
    const files = [...document.getElementById('guest-files').files];
    if (files.length > 100) throw new Error('Choose up to 100 articles.');
    for (const file of files) {
      if (file.size > 20000000) throw new Error('Files must be smaller than 20 MB.');
      const name = file.name.toLowerCase();
      if (name.endsWith('.pdf')) {
        records.push(...await prepare(file, 'application/pdf'));
      } else if (name.endsWith('.json')) {
        const supplied = JSON.parse(await file.text());
        const articles = Array.isArray(supplied) ? supplied : supplied.articles;
        if (!Array.isArray(articles)) throw new Error('JSON must contain an articles list.');
        records.push(...articles);
      } else {
        const contents = await file.text();
        const key = /\.html?$/.test(name) ? 'html' : name.endsWith('.txt') ? 'text' : 'markdown';
        records.push({title:file.name,[key]:contents,url:'https://temporary.invalid/'+crypto.randomUUID()});
      }
    }
    const text = document.getElementById('guest-text').value.trim();
    if (text) records.push({title:document.getElementById('guest-title').value.trim() || 'Untitled article',markdown:text,url:document.getElementById('guest-url').value.trim() || 'https://temporary.invalid/'+crypto.randomUUID()});
    if (!records.length) throw new Error('Choose article files or paste an article to start.');
    const articles = await prepare(JSON.stringify({articles:records}), 'application/json');
    sessionStorage.setItem('intel-guest-input',JSON.stringify({id:crypto.randomUUID(),articles}));
    location.assign('/guest/read');
  } catch (problem) { error.textContent = problem.message; error.hidden = false; button.disabled = false; }
});
