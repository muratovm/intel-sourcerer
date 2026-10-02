'use strict';
const $ = id => document.getElementById(id);
let standaloneURL=null;
let state, currentArticle, noteModel, timer, saving = Promise.resolve(), busy = false;
const token = document.querySelector('meta[name="csrf-token"]').content;
function notice(message) { $('notice').textContent = message; $('notice').hidden = !message; }
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':token}, body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Unable to save. Please try again.');
  return result;
}
function dateLabel(date) { return new Intl.DateTimeFormat('en-CA',{month:'short',day:'numeric',year:'numeric',timeZone:'America/Toronto'}).format(new Date(date)); }
function show(view) { $('setup').inert=false; for (const id of ['home','setup','reader','complete']) $(id).hidden = id !== view; }
function queueKey() { return `intel-scroll:${standaloneURL?'standalone':state.queue.id}:${currentArticle.url}`; }
function stashScroll() { if(currentArticle && (standaloneURL || state?.queue)) { try{localStorage.setItem(queueKey(),String($('article-pane').scrollTop));}catch{} } }
function draftKey(url) { return `intel-draft:${url}`; }
function stashDraft() { if(noteModel) { try{localStorage.setItem(draftKey(noteModel.url),JSON.stringify({text:notesText(),format:'html',revision:noteModel.revision}));}catch{} } }
function setBusy(value) { busy=value; $('notes').contentEditable=String(!value); document.querySelectorAll('[data-format]').forEach(b=>b.disabled=value); $('quote-selection').disabled=value; document.querySelectorAll('[data-action]').forEach(b => b.disabled=value || (b.dataset.action === 'previous' && state?.queue?.cursor===0)); }
let draftQueue=[], draggingURL=null;
function saveQueueDraft(){try{localStorage.setItem('intel-queue-draft',JSON.stringify(draftQueue));}catch{}}
function renderSetup(){
  show('setup');$('builder-date').textContent=dateLabel(new Date());
  try{const draft=JSON.parse(localStorage.getItem('intel-queue-draft'));if(Array.isArray(draft))draftQueue=[...new Set(draft.filter(url=>state.articles.some(a=>a.url===url)))].slice(0,100);}catch{}
  renderBuilder();
}
function renderHome(){
  currentArticle=null;noteModel=null;show('home');$('home-date').textContent=dateLabel(new Date());
  const q=state.queue,active=q&&!q.finished&&q.items.length;
  $('home-current').replaceChildren();
  if(active){const item=q.items[q.cursor],heading=document.createElement('h2'),meta=document.createElement('p');heading.textContent=item.title;meta.className='muted';meta.textContent=`${item.publisher} · ${dateLabel(item.published_at)}`;$('home-current').append(heading,meta);}
  else{const heading=document.createElement('h2');heading.textContent=q?'Your last queue is complete.':'Your first reading session starts here.';$('home-current').append(heading);}
  $('home-progress-text').textContent=q?`${q.read_count} of ${q.items.length} articles read · ${Math.round(q.read_count/q.items.length*100)}%`:'Choose a few articles to start your queue.';
  $('home-progress').hidden=!q;$('home-progress').max=q?.items.length||1;$('home-progress').value=q?.read_count||0;
  $('resume-reading').hidden=!active;
  $('recent-notes').replaceChildren();const noted=state.articles.filter(a=>a.has_notes).sort((a,b)=>(b.notes_updated_at||'').localeCompare(a.notes_updated_at||'')).slice(0,5);
  if(!noted.length){const empty=document.createElement('p');empty.className='muted';empty.textContent='Your saved takeaways will appear here as you read and take notes.';$('recent-notes').append(empty);}
  for(const article of noted){const row=document.createElement('article');row.className='home-note';const source=document.createElement('div'),publisher=document.createElement('strong'),date=document.createElement('p'),copy=document.createElement('div'),link=document.createElement('button'),preview=document.createElement('p');publisher.textContent=article.publisher;date.textContent=dateLabel(article.published_at);date.className='muted';source.append(publisher,date);link.className='note-article-link';link.textContent=article.title;link.onclick=async()=>{link.disabled=true;try{standaloneURL=article.url;await renderQueue();}catch(error){notice(error.message);link.disabled=false;}};preview.textContent=article.note_preview;preview.className='muted';copy.append(link,preview);row.append(source,copy);$('recent-notes').append(row);}
}
$('home-build').onclick=()=>renderSetup();$('resume-reading').onclick=()=>{standaloneURL=null;renderQueue().catch(error=>notice(error.message));};
function buildMeta(article){return `${article.publisher} · ${dateLabel(article.published_at)} · ${article.is_read?'Read':'Unread'}${article.has_notes?' · Notted':''}`;}
function styleArticleStatus(meta,article){
  const publisher=document.createElement('span');publisher.className='article-source';publisher.textContent=article.publisher;
  meta.replaceChildren(publisher,document.createTextNode(` · ${dateLabel(article.published_at)} · `));
  const status=document.createElement('span');status.textContent=article.is_read?'Read':'Unread';if(article.is_read)status.className='status-read';meta.append(status);
  if(article.has_notes){meta.append(document.createTextNode(' · '));const noted=document.createElement('span');noted.className='status-noted';noted.textContent='Notted';meta.append(noted);}
}
function addToDraft(url){if(draftQueue.includes(url))return;if(draftQueue.length>=100){notice('Choose up to 100 articles per queue.');return;}draftQueue.push(url);saveQueueDraft();renderBuilder();}
function moveDraft(url,index){const from=draftQueue.indexOf(url);if(from<0)return;draftQueue.splice(from,1);draftQueue.splice(Math.max(0,Math.min(index,draftQueue.length)),0,url);saveQueueDraft();renderBuilder();}
function renderBuilder(){
  const sourceInput=$('article-source');
  $('source-options').replaceChildren(...[...new Set(state.articles.map(article=>article.publisher))].sort((a,b)=>a.localeCompare(b)).map(publisher=>new Option(publisher,publisher)));
  const source=sourceInput.value.trim().toLowerCase();
  const type=$('article-type').value,days=Number($('timeframe').value),since=days===0?-Infinity:Date.now()-days*86400000;
  const hideRead=document.querySelector('input[value="hide-read"]').checked,onlyNoted=document.querySelector('input[value="only-noted"]').checked;
  const articles=state.articles.filter(a=>(!source||a.publisher.toLowerCase().includes(source))&&(type==='All'||(a.article_types||['Threat research']).includes(type))&&new Date(a.published_at).getTime()>=since && (!hideRead||!a.is_read)&&(!onlyNoted||a.has_notes));
  $('result-count').textContent=`${articles.length} results · newest first`;$('discovery-results').replaceChildren();
  for(const article of articles){const row=document.createElement('div');row.className='discovery-row';const copy=document.createElement('div'),title=document.createElement('strong'),meta=document.createElement('span'),tag=document.createElement('span'),button=document.createElement('button');title.textContent=article.title;if(article.is_read)title.classList.add('read-title');meta.className='choice-meta';styleArticleStatus(meta,article);copy.append(title,meta);tag.className='type-tag';tag.textContent=type==='All'?(article.article_types||['Threat research'])[0]:type;button.textContent=draftQueue.includes(article.url)?'Added':'Add';button.classList.toggle('is-added',draftQueue.includes(article.url));button.setAttribute('aria-pressed',String(draftQueue.includes(article.url)));button.setAttribute('aria-label',`${draftQueue.includes(article.url)?'Remove from queue':'Add'}: ${article.title}`);button.onclick=()=>{if(draftQueue.includes(article.url)){draftQueue=draftQueue.filter(url=>url!==article.url);saveQueueDraft();renderBuilder();}else addToDraft(article.url);};row.append(copy,tag,button);row.draggable=true;row.addEventListener('dragstart',e=>{e.dataTransfer.setData('text/uri-list',article.url);});$('discovery-results').append(row);}
  if(!articles.length){const empty=document.createElement('p');empty.className='muted';empty.textContent='No saved matches. Find latest articles or add a link.';$('discovery-results').append(empty);}
  $('queue-count').textContent=`· ${draftQueue.length} articles`;$('draft-queue').replaceChildren();
  draftQueue.forEach((url,index)=>{const article=state.articles.find(a=>a.url===url);if(!article)return;const row=document.createElement('li');row.draggable=true;const number=document.createElement('span');number.className='queue-number';number.textContent=String(index+1);const copy=document.createElement('div'),title=document.createElement('strong'),meta=document.createElement('span');title.textContent=article.title;if(article.is_read)title.classList.add('read-title');meta.className='choice-meta';styleArticleStatus(meta,article);copy.append(title,meta);const tools=document.createElement('div');tools.className='queue-row-tools';for(const [label,shift] of [['Move up',-1],['Move down',1],['Remove',null]]){const button=document.createElement('button');button.textContent=label;button.setAttribute('aria-label',`${label}: ${article.title}`);button.disabled=shift===-1&&index===0||shift===1&&index===draftQueue.length-1;button.onclick=()=>{if(shift===null){draftQueue=draftQueue.filter(u=>u!==url);saveQueueDraft();renderBuilder();}else moveDraft(url,index+shift);};tools.append(button);}row.append(number,copy,tools);row.addEventListener('dragstart',e=>{draggingURL=url;e.dataTransfer.setData('text/uri-list',url);});row.addEventListener('dragend',()=>draggingURL=null);row.addEventListener('dragover',e=>e.preventDefault());row.addEventListener('drop',e=>{e.preventDefault();e.stopPropagation();if(draggingURL)moveDraft(draggingURL,index);else importLinks(e.dataTransfer.getData('text/uri-list')||e.dataTransfer.getData('text/plain'));});$('draft-queue').append(row);});
  if(!draftQueue.length){const empty=document.createElement('p');empty.className='muted';empty.textContent='Your next reading session starts here.';$('draft-queue').append(empty);}
  $('start').disabled=!draftQueue.length;$('start').textContent=`Start reading${draftQueue.length?' · '+draftQueue.length+' articles':''}`;
}
document.querySelectorAll('input[name="article-status"]').forEach(input=>input.onchange=renderBuilder);
$('article-source').oninput=renderBuilder;$('article-type').onchange=renderBuilder;$('timeframe').onchange=renderBuilder;
$('discovery-form').onsubmit=async event=>{event.preventDefault();$('find-latest').disabled=true;$('discovery-status').textContent='Checking enabled publishers for fresh articles…';notice('');try{const report=await api('/api/discover',{days:Number($('timeframe').value)});state=await api('/api/state');renderBuilder();$('discovery-status').textContent=report.errors.length?'Updated with some source issues. Saved results are available.':'Updated just now · newest first';if(report.errors.length)notice(report.errors.join(' · '));}catch(error){$('discovery-status').textContent='Could not refresh. Saved articles remain available.';notice(error.message);}finally{$('find-latest').disabled=false;}};
async function importLinks(text){const urls=[...new Set(text.split(/\s+/).filter(u=>/^https?:\/\//i.test(u)))];if(!urls.length){notice('Drop or paste an HTTP or HTTPS article link.');return;}$('add-link').disabled=true;try{for(const url of urls.slice(0,20)){$('link-status').textContent='Fetching article details…';try{const result=await api('/api/link',{url});state=await api('/api/state');addToDraft(result.url);$('article-url').value='';}catch(error){notice(error.message);}}$('link-status').textContent='Links processed. Your queue is ready to edit.';}finally{$('add-link').disabled=false;}}
$('link-form').onsubmit=event=>{event.preventDefault();importLinks($('article-url').value);};
$('link-drop').addEventListener('dragover',event=>{event.preventDefault();$('link-drop').classList.add('drag-over');});
$('link-drop').addEventListener('dragleave',()=>$('link-drop').classList.remove('drag-over'));
$('link-drop').addEventListener('drop',event=>{event.preventDefault();$('link-drop').classList.remove('drag-over');importLinks(event.dataTransfer.getData('text/uri-list')||event.dataTransfer.getData('text/plain'));});
$('queue-form').onsubmit=async event=>{event.preventDefault();$('start').disabled=true;notice('');try{standaloneURL=null;state.queue=await api('/api/queue',{urls:draftQueue});draftQueue=[];saveQueueDraft();await renderQueue();}catch(error){notice(error.message);$('start').disabled=false;}};
async function renderQueue(){
  const q=standaloneURL?{id:'standalone',items:[{url:standaloneURL}],cursor:0,read_count:0}:state.queue;
  if(!q){renderSetup();return;}
  if(q.finished){currentArticle=null;noteModel=null;renderSetup();$('complete').hidden=false;$('setup').inert=true;$('new-queue').focus();$('completion-count').textContent=`${q.read_count} of ${q.items.length} articles marked read. ${q.items.length-q.read_count} remain unread.`;return;}
  const item=q.items[q.cursor];
  const article=await api('/api/article?'+new URLSearchParams({url:item.url}));
  currentArticle=article; selectedPassage=''; selectedPassageHTML='';
  noteModel={url:article.url,revision:article.notes_revision,savedText:article.notes};
  let text=article.notes, draftFormat=null;
  try {const draft=JSON.parse(localStorage.getItem(draftKey(article.url)));if(draft && draft.text!==article.notes){text=draft.text;draftFormat=draft.format || 'plain';noteModel.revision=draft.revision;notice(draft.revision===article.notes_revision?'Recovered your unsaved note draft.':'A local draft conflicts with newer saved notes. Copy your draft before reloading.');}}catch{}
  loadNotes(text, draftFormat || article.notes_format); noteModel.savedText=article.notes_format==='html'?sanitizeNotes(article.notes):plainHTML(article.notes);$('save-status').textContent=text===article.notes?'Saved':'Unsaved draft';
  $('article-title').textContent=article.title;
  $('article-meta').textContent=`${article.publisher} · ${dateLabel(article.published_at)} · ${article.is_read?'Read':'Unread'}${article.has_notes?' · Notted':''}`;
  const source=new URL(article.url);
  $('original').hidden=!['https:','http:'].includes(source.protocol);$('original').href=article.url;
  $('article-body').innerHTML=article.body_html; renderMedia();
  const next=q.items[q.cursor+1];
  document.querySelectorAll('.position').forEach(el=>el.textContent=`Article ${q.cursor+1} of ${q.items.length} · ${q.read_count} read`);
  document.querySelectorAll('.queue-progress').forEach(el=>{el.max=q.items.length;el.value=q.read_count;});
  document.querySelectorAll('.next-title').forEach(el=>el.textContent=next ? next.title:'Finish this queue');
  $('read-next').textContent=q.cursor===q.items.length-1?'Mark read & finish':'Mark read & next';
  document.querySelector('[data-action="next"]').textContent=q.cursor===q.items.length-1?'Finish':'Next';
  $('mark-unread').hidden=!article.is_read;
  show('reader'); setBusy(false);
  document.querySelectorAll('[data-action=previous],[data-action=next],[data-action=mark_next],[data-action=unread],.nav-bar .up-next,.nav-bar .progress-area').forEach(el=>el.hidden=!!standaloneURL);
  $('mark-unread').hidden=!!standaloneURL || !article.is_read;
  let scroll=0;try{scroll=Number(localStorage.getItem(queueKey()))||0;}catch{}
  $('article-pane').scrollTop=scroll;
  if(text!==article.notes && noteModel.revision===article.notes_revision)timer=setTimeout(()=>saveNow().catch(e=>notice(e.message)),600);
}
function saveNow(){
  clearTimeout(timer);
  if(!noteModel)return saving;
  const model=noteModel,text=notesText();
  saving=saving.catch(()=>{}).then(async()=>{
    if(text===model.savedText)return;
    if(noteModel===model)$('save-status').textContent='Saving…';
    try {
      const result=await api('/api/notes',{url:model.url,text,format:'html',revision:model.revision});
      model.revision=result.revision;model.savedText=text;
      if(noteModel===model){const container=document.createElement('div');container.innerHTML=text;currentArticle.has_notes=!!container.textContent.replace(/\u200b/g,'').trim();$('article-meta').textContent=`${currentArticle.publisher} · ${dateLabel(currentArticle.published_at)} · ${currentArticle.is_read?'Read':'Unread'}${currentArticle.has_notes?' · Notted':''}`;}
      if(noteModel===model){$('save-status').textContent=notesText()===text?'Saved':'Unsaved changes';if(notesText()===text){try{localStorage.removeItem(draftKey(model.url));}catch{}}else stashDraft();}
    }catch(error){if(noteModel===model){$('save-status').textContent='Not saved — retry by editing';stashDraft();}throw error;}
  });
  return saving;
}
$('notes').addEventListener('input',()=>{stashDraft();$('save-status').textContent='Unsaved changes';clearTimeout(timer);timer=setTimeout(()=>saveNow().catch(error=>notice(error.message)),600);});
$('article-pane').addEventListener('scroll',stashScroll,{passive:true});
window.addEventListener('beforeunload',event=>{stashDraft();if(noteModel && notesText()!==noteModel.savedText){event.preventDefault();event.returnValue='';}});
document.querySelectorAll('[data-action]').forEach(button=>button.onclick=async()=>{
  if(busy)return;setBusy(true);notice('');stashScroll();
  try{await saveNow();if(button.dataset.action==='home'){standaloneURL=null;state=await api('/api/state');renderHome();setBusy(false);return;}if(button.dataset.action==='exit'){const wasStandalone=!!standaloneURL;standaloneURL=null;state=await api('/api/state');if(!wasStandalone&&state.queue){draftQueue=state.queue.items.map(item=>item.url);saveQueueDraft();}currentArticle=null;noteModel=null;selectedPassage='';selectedPassageHTML='';renderSetup();setBusy(false);return;}state.queue=await api('/api/navigate',{queue_id:state.queue.id,cursor:state.queue.cursor,action:button.dataset.action});await renderQueue();}
  catch(error){notice(error.message);setBusy(false);}
});
let selectedPassage='', selectedPassageHTML='';
document.addEventListener('selectionchange',()=>{
  const selection=window.getSelection();
  const isArticleText=node=>$('article-body').contains(node)||$('article-title').contains(node);
  if(!selection || !selection.rangeCount || !isArticleText(selection.anchorNode) || !isArticleText(selection.focusNode))return;
  const text=selection.toString().trim();if(!text)return;
  selectedPassage=text;
  const range=selection.getRangeAt(0), holder=document.createElement('div');holder.append(range.cloneContents());
  for(const heading of holder.querySelectorAll('h1,h2,h3,h4,h5,h6')){const bold=document.createElement('strong');bold.append(...heading.childNodes);const paragraph=document.createElement('p');paragraph.append(bold);heading.replaceWith(paragraph);}
  const start=range.startContainer.nodeType===1?range.startContainer:range.startContainer.parentElement;
  const end=range.endContainer.nodeType===1?range.endContainer:range.endContainer.parentElement;
  const heading=start.closest('h1,h2,h3,h4,h5,h6');
  selectedPassageHTML=heading && heading===end.closest('h1,h2,h3,h4,h5,h6')?'<strong>'+plainHTML(text)+'</strong>':sanitizeNotes(holder.innerHTML);
});
$('quote-selection').onclick=()=>{if(!selectedPassage){notice('Select a passage in the article first.');return;}notice('');insertCallout(selectedPassage,selectedPassageHTML);selectedPassage='';selectedPassageHTML='';};

document.addEventListener('keydown',event=>{if($('complete').hidden)return;if(event.key==='Escape'){event.preventDefault();$('complete').hidden=true;$('setup').inert=false;$('article-type').focus();}else if(event.key==='Tab'){event.preventDefault();$('new-queue').focus();}});
$('new-queue').onclick=async()=>{try{state=await api('/api/state');renderSetup();}catch(error){notice(error.message);}};
(async()=>{try{state=await api('/api/state');const view=new URLSearchParams(location.search).get('view');const open=new URLSearchParams(location.search).get('open');if(open){standaloneURL=open;await renderQueue();}else if(view==='builder')renderSetup();else if(view==='reader')await renderQueue();else renderHome();}catch(error){notice(error.message);}})();

let mediaEnabled=false;
try {mediaEnabled=localStorage.getItem('intel-load-media')==='true';}catch{}
$('load-media').checked=mediaEnabled;
$('load-media').addEventListener('change',()=>{
  mediaEnabled=$('load-media').checked;
  try{localStorage.setItem('intel-load-media',String(mediaEnabled));}catch{}
  const scroll=$('article-pane').scrollTop;
  if(currentArticle){$('article-body').innerHTML=currentArticle.body_html;renderMedia();$('article-pane').scrollTop=scroll;}
});
function embedURL(url){
  const host=url.hostname.toLowerCase();
  if(['www.youtube.com','youtube.com','www.youtube-nocookie.com','youtube-nocookie.com','youtu.be'].includes(host)){
    const id=host==='youtu.be'?url.pathname.slice(1):url.pathname.startsWith('/embed/')?url.pathname.split('/')[2]:url.searchParams.get('v');
    if(id && /^[a-zA-Z0-9_-]{11}$/.test(id))return 'https://www.youtube-nocookie.com/embed/'+id;
  }
  if(['vimeo.com','www.vimeo.com','player.vimeo.com'].includes(host)){
    const id=url.pathname.split('/').filter(Boolean).pop();
    if(id && /^\d+$/.test(id))return 'https://player.vimeo.com/video/'+id;
  }
  return null;
}
function renderMedia(){
  if(!mediaEnabled)return;
  for(const block of $('article-body').querySelectorAll('blockquote[data-media-url]')){
    let url;try{url=new URL(block.dataset.mediaUrl);}catch{continue;}
    if(!['https:','http:'].includes(url.protocol))continue;
    let media;
    if(block.dataset.mediaKind==='image'){
      media=document.createElement('img');media.alt=block.dataset.mediaDescription;media.loading='lazy';media.decoding='async';
    }else{
      const embed=embedURL(url);
      if(embed){media=document.createElement('iframe');media.title=block.dataset.mediaDescription==='No description supplied'?'Article video':block.dataset.mediaDescription;media.loading='lazy';media.setAttribute('allow','fullscreen; picture-in-picture');media.setAttribute('sandbox','allow-scripts allow-same-origin allow-presentation');url=new URL(embed);}
      else if(/\.(mp4|webm|ogv|ogg)$/i.test(url.pathname)){media=document.createElement('video');media.controls=true;media.preload='none';}
      else continue;
    }
    media.className='article-media';media.referrerPolicy='no-referrer';
    media.addEventListener('error',()=>{media.remove();const message=document.createElement('p');message.textContent='Media could not load. Use the original link below.';block.prepend(message);},{once:true});
    media.src=url.href;block.prepend(media);
  }
}

const divider=$('pane-divider'), workspace=document.querySelector('.workspace');
let panePercent=50, resizingPointer=null;
function setPaneWidth(value,persist=true){
  panePercent=Math.max(30,Math.min(70,value));
  workspace.style.setProperty('--article-width',`${panePercent}fr`);
  workspace.style.setProperty('--notes-width',`${100-panePercent}fr`);
  divider.setAttribute('aria-valuenow',String(Math.round(panePercent)));
  divider.setAttribute('aria-valuetext',`Article ${Math.round(panePercent)} percent, notes ${100-Math.round(panePercent)} percent`);
  if(persist)try{localStorage.setItem('intel-pane-width',String(panePercent));}catch{}
}
try{const stored=Number(localStorage.getItem('intel-pane-width'));if(Number.isFinite(stored)&&stored>=30&&stored<=70)setPaneWidth(stored,false);}catch{}
divider.addEventListener('pointerdown',event=>{
  if(event.button!==0)return;
  event.preventDefault();resizingPointer=event.pointerId;divider.setPointerCapture(event.pointerId);divider.focus();workspace.classList.add('resizing-panes');
});
divider.addEventListener('pointermove',event=>{
  if(event.pointerId!==resizingPointer)return;
  const bounds=workspace.getBoundingClientRect();
  setPaneWidth((event.clientX-bounds.left-5)/(bounds.width-10)*100);
});
function stopResize(){resizingPointer=null;workspace.classList.remove('resizing-panes');}
divider.addEventListener('pointerup',stopResize);
divider.addEventListener('pointercancel',stopResize);
divider.addEventListener('lostpointercapture',stopResize);
divider.addEventListener('dblclick',()=>setPaneWidth(50));
divider.addEventListener('keydown',event=>{
  const changes={ArrowLeft:-2,ArrowRight:2};
  if(event.key in changes){event.preventDefault();setPaneWidth(panePercent+changes[event.key]);}
  else if(event.key==='Home'||event.key==='End'){event.preventDefault();setPaneWidth(event.key==='Home'?30:70);}
  else if(event.key==='Enter'){event.preventDefault();setPaneWidth(50);}
});

function plainHTML(text){const p=document.createElement('div');p.textContent=text;return p.innerHTML.replace(/\n/g,'<br>');}
function sanitizeNotes(html){
  const template=document.createElement('template');template.innerHTML=html;
  function clean(node){
    if(node.nodeType===3)return document.createTextNode(node.textContent);
    if(node.nodeType!==1)return document.createDocumentFragment();
    const tag=node.tagName.toLowerCase(), allowed=['p','div','br','strong','b','em','i','mark','blockquote','ul','ol','li'];
    const result=document.createElement(tag==='span' && node.style.backgroundColor?'mark':allowed.includes(tag)?tag:'span');
    for(const child of node.childNodes)result.append(clean(child));
    if(result.tagName==='SPAN'){const fragment=document.createDocumentFragment();fragment.append(...result.childNodes);return fragment;}
    if(node.style.backgroundColor && result.tagName!=='MARK'){const mark=document.createElement('mark');mark.append(result);return mark;}
    return result;
  }
  const holder=document.createElement('div');for(const node of template.content.childNodes)holder.append(clean(node));return holder.innerHTML;
}
function notesText(){return sanitizeNotes($('notes').innerHTML);}
function loadNotes(text,format){$('notes').innerHTML=format==='html'?sanitizeNotes(text):plainHTML(text);noteRange=null;clearTypingFormat=false;updateFormatButtons();}
let noteRange=null;
document.addEventListener('selectionchange',()=>{const sel=window.getSelection();if(sel.rangeCount && $('notes').contains(sel.anchorNode) && $('notes').contains(sel.focusNode))noteRange=sel.getRangeAt(0).cloneRange();});
function focusNotes(){$('notes').focus();const sel=window.getSelection();if(noteRange && $('notes').contains(noteRange.startContainer)){sel.removeAllRanges();sel.addRange(noteRange);}else{const range=document.createRange();range.selectNodeContents($('notes'));range.collapse(false);sel.removeAllRanges();sel.addRange(range);}}
function noteChanged(){$('notes').dispatchEvent(new Event('input'));}
function insertCallout(text,html=null){focusNotes();const selection=window.getSelection();if(selection.rangeCount)selection.collapseToEnd();document.execCommand('insertHTML',false,'<blockquote>'+(html?sanitizeNotes(html):plainHTML(text))+'</blockquote><div><br></div>');noteChanged();}
function selectionFormatState(kind){
  const selection=window.getSelection();
  if(!selection.rangeCount || !$('notes').contains(selection.anchorNode))return false;
  const selector=kind==='bold'?'b,strong':'mark';
  if(selection.isCollapsed){if(kind==='bold')return document.queryCommandState('bold');const node=selection.anchorNode.nodeType===1?selection.anchorNode:selection.anchorNode.parentElement;return !!node.closest(selector);}
  const range=selection.getRangeAt(0),walker=document.createTreeWalker($('notes'),NodeFilter.SHOW_TEXT);
  let node,found=false;
  while(node=walker.nextNode()){if(!range.intersectsNode(node) || !node.length)continue;if(node===range.startContainer && range.startOffset===node.length)continue;if(node===range.endContainer && range.endOffset===0)continue;found=true;if(!node.parentElement.closest(selector))return false;}
  return found;
}
function updateFormatButtons(){for(const kind of ['bold','highlight']){const button=document.querySelector(`[data-format="${kind}"]`);button.setAttribute('aria-pressed',String(selectionFormatState(kind)));}}
document.addEventListener('selectionchange',updateFormatButtons);
function formatSelection(kind){
  const selection=window.getSelection();
  if(selection.isCollapsed){if(kind==='bold')document.execCommand('bold');else notice('Select note text to highlight or unhighlight it.');updateFormatButtons();return;}
  const range=selection.getRangeAt(0),prefix=document.createRange();prefix.selectNodeContents($('notes'));prefix.setEnd(range.startContainer,range.startOffset);
  const start=prefix.toString().length,end=start+range.toString().length,enabled=!selectionFormatState(kind);
  let offset=0;
  function rebuild(node,bold=false,highlight=false,italic=false){
    const fragment=document.createDocumentFragment();
    if(node.nodeType===3){
      const text=node.textContent,base=offset;offset+=text.length;
      const cuts=[0,Math.max(0,Math.min(text.length,start-base)),Math.max(0,Math.min(text.length,end-base)),text.length].sort((a,b)=>a-b);
      for(let i=1;i<cuts.length;i++){const from=cuts[i-1],to=cuts[i];if(to===from)continue;const selected=base+from>=start && base+to<=end;let piece=document.createTextNode(text.slice(from,to));
        const flags={bold,highlight,italic};if(selected)flags[kind]=enabled;
        for(const [flag,tag] of [['italic','em'],['bold','strong'],['highlight','mark']])if(flags[flag]){const wrapper=document.createElement(tag);wrapper.append(piece);piece=wrapper;}
        fragment.append(piece);
      }return fragment;
    }
    if(node.nodeType!==1)return fragment;
    const tag=node.tagName.toLowerCase();bold=bold||['b','strong'].includes(tag);highlight=highlight||tag==='mark';italic=italic||['i','em'].includes(tag);
    const inline=['b','strong','mark','i','em','span'].includes(tag),target=inline?fragment:document.createElement(tag);
    for(const child of node.childNodes)target.append(rebuild(child,bold,highlight,italic));
    return target;
  }
  const result=document.createDocumentFragment();for(const node of Array.from($('notes').childNodes))result.append(rebuild(node));$('notes').replaceChildren(result);
  // Keep the selection so its formatting is visible in the toolbar.
  const walker=document.createTreeWalker($('notes'),NodeFilter.SHOW_TEXT);let node,total=0;const restored=document.createRange();let started=false;
  while(node=walker.nextNode()){if(!started && total+node.length>=start){restored.setStart(node,start-total);started=true;}if(total+node.length>=end){restored.setEnd(node,end-total);break;}total+=node.length;}
  selection.removeAllRanges();selection.addRange(restored);noteRange=restored.cloneRange();
  clearTypingFormat=true;noteChanged();updateFormatButtons();
}
let clearTypingFormat=false;
$('notes').addEventListener('beforeinput',event=>{
  if(!clearTypingFormat || !['insertText','insertParagraph','insertLineBreak'].includes(event.inputType))return;
  clearTypingFormat=false;
  const selection=window.getSelection();
  event.preventDefault();const range=selection.getRangeAt(0);range.deleteContents();range.collapse(true);const marker=document.createTextNode('');range.insertNode(marker);
  let parent=marker.parentElement;
  while(parent!==$('notes') && ['B','STRONG','MARK','I','EM','SPAN'].includes(parent.tagName)){
    const after=parent.cloneNode(false);while(marker.nextSibling)after.append(marker.nextSibling);
    parent.after(marker,after);if(!parent.hasChildNodes())parent.remove();if(!after.hasChildNodes())after.remove();parent=marker.parentElement;
  }
  marker.textContent=event.inputType==='insertText'?(event.data||''):'\n';range.setStartAfter(marker);range.collapse(true);selection.removeAllRanges();selection.addRange(range);noteRange=range.cloneRange();noteChanged();updateFormatButtons();
});
document.querySelectorAll('[data-format]').forEach(button=>{button.addEventListener('mousedown',e=>e.preventDefault());button.onclick=()=>{if(busy)return;focusNotes();const action=button.dataset.format;if(action==='callout'){document.execCommand('formatBlock',false,'blockquote');noteChanged();}else formatSelection(action);};});
$('notes').addEventListener('keydown',event=>{if((event.metaKey||event.ctrlKey) && event.key.toLowerCase()==='b'){event.preventDefault();formatSelection('bold');}});

$('notes').addEventListener('paste',event=>{event.preventDefault();document.execCommand('insertText',false,event.clipboardData.getData('text/plain'));noteChanged();});
