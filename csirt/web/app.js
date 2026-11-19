'use strict';
const $=id=>document.getElementById(id);
const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl=value=>{try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)?url.href:'#';}catch{return '#';}};
const date=value=>value?new Date(typeof value==='number'?value*1000:value).toLocaleString('fr-FR',{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}):'Pas encore collecté';
const STORAGE='csirt.alerts.seen.v1';
const SORTS={reference_at:'Date de publication',activity_seq:'Ajout ou mise à jour',last_changed_at:'Dernière modification',organization:'Nom de l’organisme',alert_status:'Statut de l’alerte',source_name:'Source'};
const defaultOrder=sort=>['organization','alert_status','source_name'].includes(sort)?'asc':'desc';
const state={view:'incidents',page:1,q:'',source:'',alert_status:'',sort:'reference_at',order:'desc',onlyNew:false,baseline:null,snapshot:null,revision:null,busy:false,queued:false,storageAvailable:true,detail:null,detailRequest:0};
function readBaseline(){try{const data=JSON.parse(localStorage.getItem(STORAGE));return data&&typeof data.generation==='string'&&Number.isSafeInteger(data.sequence)&&data.sequence>=0?data:null;}catch{state.storageAvailable=false;return null;}}
state.baseline=readBaseline();
function saveBaseline(){try{localStorage.setItem(STORAGE,JSON.stringify(state.baseline));}catch{state.storageAvailable=false;}}
async function getJson(url){const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),15000);try{const response=await fetch(url,{signal:controller.signal,cache:'no-store'});if(!response.ok)throw new Error(`Erreur HTTP ${response.status}`);return await response.json();}finally{clearTimeout(timer);}}
function route(){const [view,query='']=location.hash.slice(1).split('?');const params=new URLSearchParams(query);state.view=view==='anssi'?'cve':['incidents','sources','cve','memory'].includes(view)?view:'incidents';state.page=Math.max(1,Math.min(400,Math.trunc(Number(params.get('page')))||1));state.q=(params.get('q')||'').slice(0,128);state.source=params.get('source')||'';state.alert_status=params.get('alert_status')||'';state.sort=Object.hasOwn(SORTS,params.get('sort'))?params.get('sort'):'reference_at';state.order=params.get('order')==='asc'?'asc':'desc';state.onlyNew=params.get('new')==='1';setView();syncFilters();refresh(true);}
function navigate(){const params=new URLSearchParams();if(state.page>1)params.set('page',state.page);for(const key of ['q','source','alert_status'])if(state[key])params.set(key,state[key]);if(state.sort!=='reference_at')params.set('sort',state.sort);if(state.order!=='desc')params.set('order',state.order);if(state.onlyNew)params.set('new','1');const hash='#'+state.view+(params.size?'?'+params:'');if(location.hash===hash){syncFilters();refresh(true);}else location.hash=hash;}
function setView() {
  const names = {
    incidents: ['Incidents', 'Toutes les alertes.'],
    sources: ['Sources de veille', 'Une veille, plusieurs sources.'],
    cve: ['Alertes CVE', 'Alertes CVE'],
    memory: ['Gestion mémoire', 'Gestion mémoire'],
  };
  const descriptions = {
    cve: 'Consultez les publications du CERT-FR et les vulnérabilités associées.',
    memory: 'Suivez la RAM et le stockage utilisés sur le serveur de l’Observatoire.',
  };
  const {view} = state;
  $('breadcrumb').textContent = names[view][0];
  $('page-title').textContent = names[view][1];
  for (const link of document.querySelectorAll('nav a')) {
    const active = link.dataset.view === view;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  const showIncidents = view === 'incidents';
  $('reporting-period').hidden = !showIncidents;
  $('incidents-panel').hidden = !showIncidents;
  $('sources-panel').hidden = view !== 'sources';
  $('anssi-panel').hidden = view !== 'cve';
  $('memory-panel').hidden = view !== 'memory';
  if (view === 'memory') loadRetention();
  $('refresh').hidden = view === 'cve';
  document.querySelector('.page-header .description').textContent = descriptions[view] ||
    'Rassemblez les alertes, croisez les publications et suivez les nouveautés.';
}
function syncFilters(){$('search').value=state.q;$('filter-source').value=state.source;$('filter-status').value=state.alert_status;$('filter-sort').value=state.sort;$('filter-new').checked=state.onlyNew;syncSortHeaders();}
function syncSortHeaders(){for(const button of document.querySelectorAll('.sort-button')){const active=button.dataset.sort===state.sort;button.closest('th').setAttribute('aria-sort',active?(state.order==='asc'?'ascending':'descending'):'none');button.querySelector('.sort-indicator').textContent=active?(state.order==='asc'?'▲':'▼'):'↕';const next=active?(state.order==='asc'?'desc':'asc'):defaultOrder(button.dataset.sort);const direction=next==='asc'?'croissant':'décroissant';button.setAttribute('aria-label',SORTS[button.dataset.sort]+' : trier par ordre '+direction);button.title='Trier par '+SORTS[button.dataset.sort].toLocaleLowerCase('fr-FR')+' · ordre '+direction;}}
function renderNew(snapshot){const count=snapshot.new_count||0;$('new-title').textContent=count?`${count} incident${count>1?'s':''} nouveau${count>1?'x':''} ou mis à jour`:'Vous êtes à jour';$('new-description').textContent=state.storageAvailable?'Repère conservé dans ce navigateur, même après actualisation ou réouverture. Cliquez sur « Tout marquer comme vu » pour le déplacer.':'Le stockage du navigateur est indisponible : le repère reste valable pendant cette ouverture uniquement.';$('mark-seen').disabled=!count;$('show-new').disabled=!count;}
function renderCollector(collector){$('collector-status').textContent=collector.active?'Collecteur actif · '+date(collector.heartbeat_at):'Collecteur inactif · consultation de l’historique';}
function renderDashboard(data){const periodDate=value=>new Date(value).toLocaleString('fr-FR',{timeZone:'Europe/Paris',day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'});$('reporting-period').textContent=`Bilan : du ${periodDate(data.period.starts_at)} au ${periodDate(data.period.ends_at)} (fin exclue) · Heure de Paris · Bascule à 20 h`;}
function renderSources(sources){const activeElement=document.activeElement;const selected=state.source;$('filter-source').innerHTML='<option value="">Toutes les sources</option>'+sources.map(s=>`<option value="${escapeHtml(s.id)}">${escapeHtml(s.name)}${s.enabled?'':' (inactive)'}</option>`).join('');$('filter-source').value=selected;if(activeElement===$('filter-source'))activeElement.focus();$('source-cards').innerHTML=sources.map(s=>`<article class="source-card"><h3>${escapeHtml(s.name)}</h3><span class="badge ${s.failures?'claimed':'confirmed'}">${!s.enabled?'Désactivée':s.failures?'Collecte à réessayer':s.last_success_at?'Collecte opérationnelle':'En attente de collecte'}</span><p><a href="${escapeHtml(safeUrl(s.feed_url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.feed_url)}</a></p><dl><dt>Intervalle</dt><dd>${Math.round(s.poll_seconds/60)} minutes</dd><dt>Dernier succès</dt><dd>${date(s.last_success_at)}</dd><dt>Prochain passage</dt><dd>${s.enabled?date(s.next_poll_at):'—'}</dd></dl>${s.last_error?`<p class="muted">${escapeHtml(s.last_error)}</p>`:''}</article>`).join('');}
function assessment(alert){return `<span class="badge ${escapeHtml(alert.status)}">${escapeHtml(alert.status_label)}</span><div class="index-label">Indice : ${escapeHtml(alert.claim_index.label)}</div>`;}
function renderIncidents(data){$('sort-status').textContent='Tri par '+SORTS[state.sort]+', ordre '+(state.order==='asc'?'croissant':'décroissant')+'.';const focus=document.activeElement?.dataset?.incident;const baseline=state.baseline.sequence;$('incident-rows').innerHTML=data.items.map(item=>{const isNew=item.activity_seq>baseline;const name=item.organization||item.title;return `<tr${isNew?' class="new-row"':''}><td><div class="row-title"><span class="initial" aria-hidden="true">${escapeHtml(name.charAt(0).toUpperCase())}</span><div><button class="title-button" data-incident="${item.id}">${escapeHtml(name)}</button>${isNew?'<span class="badge new">Nouveau / mis à jour</span>':''}<div class="row-meta">${item.observation_count} publication${item.observation_count>1?'s':''}</div></div></div></td><td>${date(item.reference_at)}</td><td>${assessment(item.alert_assessment)}</td><td>${item.sources.map(s=>`<span class="source-tag">${escapeHtml(s.name)}</span>`).join('')}</td><td><button class="button" data-incident="${item.id}" aria-label="Consulter ${escapeHtml(name)}">→</button></td></tr>`;}).join('')||'<tr><td colspan="5">Aucune alerte pour ces filtres.</td></tr>';$('result-count').textContent=`${data.total} incident${data.total>1?'s':''} · ${state.onlyNew?'nouveautés depuis votre repère':'toutes les alertes'}`;const pages=Math.max(1,Math.ceil(data.total/25));$('page-info').textContent=`Page ${state.page} sur ${pages}`;$('previous').disabled=state.page<=1;$('next').disabled=state.page>=pages;if(focus)document.querySelector(`[data-incident="${focus}"]`)?.focus({preventScroll:true});}
async function refresh(force=false){if(state.view==='cve')return window.refreshAnssi();if(state.view==='memory')return refreshResources();if(state.busy){state.queued=true;return;}state.busy=true;$('refresh').disabled=true;try{let snapshot=await getJson('/updates?since='+(state.baseline?.sequence||0));if(!state.baseline||state.baseline.generation!==snapshot.generation||state.baseline.sequence>snapshot.sequence){state.baseline={generation:snapshot.generation,sequence:snapshot.sequence};saveBaseline();snapshot={...snapshot,new_count:0};}if(force||snapshot.revision!==state.revision){const params=new URLSearchParams({limit:25,offset:(state.page-1)*25,sort:state.sort,order:state.order});for(const key of ['q','source','alert_status'])if(state[key])params.set(key,state[key]);if(state.onlyNew)params.set('since_sequence',state.baseline.sequence);const [dashboard,sources,incidents]=await Promise.all([getJson('/dashboard'),getJson('/sources'),getJson('/incidents?'+params)]);if(state.queued)return;if(state.page>1&&incidents.total<=((state.page-1)*25)){state.page=Math.max(1,Math.ceil(incidents.total/25));navigate();return;}renderDashboard(dashboard);renderSources(sources);renderIncidents(incidents);if(state.detail&&!$('detail-dialog').hidden&&$('detail-dialog').open)await openDetail(state.detail,false);state.revision=snapshot.revision;}
state.snapshot=snapshot;renderNew(snapshot);renderCollector(snapshot.collector);$('updated').textContent='Vérifié à '+new Date().toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});$('global-error').hidden=true;}catch(error){$('global-error').textContent='Actualisation impossible. Les dernières données restent affichées. Nouvelle tentative automatique dans une minute. '+(error.name==='AbortError'?'Délai dépassé.':error.message);$('global-error').hidden=false;}finally{state.busy=false;$('refresh').disabled=false;if(state.queued){state.queued=false;refresh(true);}}}
async function openDetail(id,show=true){const request=++state.detailRequest;if(show){state.detail=id;$('detail-body').innerHTML='<h2 id="detail-title">Chargement…</h2>';if(!$('detail-dialog').open)$('detail-dialog').showModal();}try{const item=await getJson('/incidents/'+id);if(request!==state.detailRequest||state.detail!==id)return;$('detail-body').innerHTML=`<h2 id="detail-title">${escapeHtml(item.organization||item.title)}</h2>${assessment(item.alert_assessment)}<p class="muted">Première collecte : ${date(item.first_seen_at)}<br>Dernière évolution : ${date(item.last_changed_at)}</p>${item.organization_aliases.length?`<p class="muted">Également cité sous : ${item.organization_aliases.map(escapeHtml).join(', ')}</p>`:''}<p class="detail-note">Le statut et l’indice retranscrivent les indications des sources. Le regroupement tient compte des variantes du nom, des sigles explicitement cités, des dates et du type d’événement. L’écart total entre la première et la dernière publication doit être strictement inférieur à 48 heures.</p><h3>${item.observations.length} publication${item.observations.length>1?'s':''}</h3>${item.observations.map(o=>`<article class="publication"><small>${escapeHtml(o.source_name)} · ${date(o.published_at)}</small><p><a href="${escapeHtml(safeUrl(o.url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(o.title)} ↗</a></p>${assessment(o.alert_assessment)}<p>${escapeHtml(o.summary)}</p></article>`).join('')}`;}catch{if(request===state.detailRequest)$('detail-body').innerHTML='<h2 id="detail-title">Détail indisponible</h2><p>Réessayez dans quelques instants.</p>';}}
$('filters').addEventListener('submit',event=>{event.preventDefault();state.q=$('search').value.trim();state.source=$('filter-source').value;state.alert_status=$('filter-status').value;if(state.sort!==$('filter-sort').value){state.sort=$('filter-sort').value;state.order=defaultOrder(state.sort);}state.onlyNew=$('filter-new').checked;state.page=1;navigate();});
for(const id of ['filter-source','filter-status','filter-sort','filter-new'])$(id).addEventListener('change',()=>$('filters').requestSubmit());
document.querySelector('thead').addEventListener('click',event=>{const button=event.target.closest('.sort-button');if(!button)return;const sort=button.dataset.sort;state.order=state.sort===sort?(state.order==='asc'?'desc':'asc'):defaultOrder(sort);state.sort=sort;state.page=1;navigate();});
$('refresh').addEventListener('click',()=>refresh(true));
$('show-new').addEventListener('click',()=>{state.view='incidents';state.q='';state.source='';state.alert_status='';state.onlyNew=true;state.page=1;state.sort='activity_seq';state.order='desc';navigate();});
$('mark-seen').addEventListener('click',()=>{if(!state.snapshot)return;state.baseline={generation:state.snapshot.generation,sequence:state.snapshot.sequence};saveBaseline();refresh(true);});
$('previous').addEventListener('click',()=>{if(state.page>1){state.page--;navigate();}});
$('next').addEventListener('click',()=>{state.page++;navigate();});
$('incident-rows').addEventListener('click',event=>{const button=event.target.closest('[data-incident]');if(button)openDetail(Number(button.dataset.incident));});
$('close-detail').addEventListener('click',()=>$('detail-dialog').close());
$('detail-dialog').addEventListener('close',()=>{state.detail=null;state.detailRequest++;});
window.addEventListener('hashchange',route);
let lastResume=0;function resume(){if(document.visibilityState==='visible'&&Date.now()-lastResume>3000){lastResume=Date.now();refresh(true);}}
window.addEventListener('focus',resume);window.addEventListener('pageshow',event=>{if(event.persisted)resume();});document.addEventListener('visibilitychange',resume);
window.addEventListener('storage',event=>{if(event.key===STORAGE){state.baseline=readBaseline();refresh(true);}});
const units = ['o', 'Kio', 'Mio', 'Gio', 'Tio', 'Pio'];
function formatBytes(bytes) {
  if (bytes === null) return 'Indisponible';
  const exponent = bytes > 0 ? Math.min(units.length - 1, Math.floor(Math.log2(bytes) / 10)) : 0;
  return (bytes / (1024 ** exponent)).toLocaleString('fr-FR', {maximumFractionDigits: 1}) + ' ' + units[exponent];
}
function renderResource(name, data) {
  const row = $('resource-' + name);
  const meter = $('meter-' + name);
  row.hidden = name === 'swap' && !data?.total;
  meter.hidden = !data?.total;
  const value = row.querySelector('span');
  if (!data?.total) {
    value.textContent = 'Indisponible';
    return;
  }
  meter.value = 100 * data.used / data.total;
  value.textContent = `${formatBytes(data.used)} utilisés sur ${formatBytes(data.total)} · ${formatBytes(data.available)} disponibles`;
}
let readingResources = false;
const retentionForm = $('retention-form');
const retentionButton = retentionForm.querySelector('button');
const retentionFields = ['incidents_days', 'cve_days'];
const calendarDay = 86400000;
const utcMidnight = () => Math.floor(Date.now() / calendarDay) * calendarDay;
const calendarDate = value => new Date(value).toISOString().slice(0, 10);
let retentionBusy = false;

async function loadRetention() {
  if (retentionBusy) return;
  retentionBusy = true;
  retentionButton.disabled = true;
  try {
    const values = await getJson('/retention');
    const today = utcMidnight();
    for (const key of retentionFields) {
      const input = retentionForm.elements[key];
      input.min = calendarDate(today - 36500 * calendarDay);
      input.max = calendarDate(today - calendarDay);
      input.value = values[key] === null ? '' : calendarDate(today - values[key] * calendarDay);
    }
    retentionButton.disabled = false;
  } catch {
    $('retention-status').textContent = 'Impossible de charger les réglages. Rouvre cet onglet pour réessayer.';
  } finally {
    retentionBusy = false;
  }
}

retentionForm.addEventListener('submit', async event => {
  event.preventDefault();
  retentionBusy = true;
  retentionButton.disabled = true;
  const values = Object.fromEntries(retentionFields.map(key => {
    const input = retentionForm.elements[key];
    return [key, input.value === '' ? null : (utcMidnight() - input.valueAsNumber) / calendarDay];
  }));
  try {
    const response = await fetch('/retention', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json', 'X-CSIRT-Client': 'observatoire'},
      body: JSON.stringify(values),
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) throw new Error();
    $('retention-status').textContent = 'Réglages enregistrés. Le collecteur les appliquera au prochain passage.';
  } catch {
    $('retention-status').textContent = 'Impossible d’enregistrer les réglages.';
  } finally {
    retentionBusy = false;
    retentionButton.disabled = false;
  }
});

async function refreshResources() {
  if (readingResources) return;
  readingResources = true;
  try {
    const resources = await getJson('/resources');
    for (const name of ['ram', 'swap', 'disk']) renderResource(name, resources[name]);
    $('database-size').textContent = formatBytes(resources.database_bytes);
    $('memory-status').textContent = 'Mesuré le ' + date(resources.measured_at);
  } catch {
    $('memory-status').textContent = 'Mesures temporairement indisponibles. Nouvelle tentative dans dix secondes.';
  } finally {
    readingResources = false;
  }
}
setInterval(() => {
  if (state.view === 'memory' && document.visibilityState === 'visible') refreshResources();
}, 10000);
setInterval(()=>{if(state.view!=='memory'&&document.visibilityState==='visible')refresh();},60000);
route();
