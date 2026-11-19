'use strict';

(() => {
  const byId = id => document.getElementById(id);
  const panel = byId('anssi-panel');
  const filters = byId('anssi-filters');
  const dialog = byId('anssi-dialog');
  const detail = byId('anssi-detail');
  const sync = byId('anssi-sync');
  const jobStatus = byId('anssi-job');
  const mutationHeaders = {'X-CSIRT-Client': 'observatoire'};
  const types = {alerte: 'Alerte', avis: 'Avis', cti: 'Rapport', ioc: 'IOC'};
  const severities = {critical: 'Critique', high: 'Élevée', medium: 'Moyenne', low: 'Faible', none: 'Nulle'};
  const signals = {patch: 'Correctif disponible', active: 'Exploitation active signalée', poc: 'Preuve de concept publique signalée'};
  const changeLabels = {
    title: 'titre', description: 'résumé', published_at: 'publication', updated_at: 'mise à jour',
    risks: 'risques', revisions: 'révisions', products: 'produits', products_text: 'systèmes affectés',
    cves: 'CVE', remediation: 'remédiation', evidence: 'confirmations', evidence_cves: 'confirmations par CVE',
    official_severity: 'criticité officielle',
    official_severity_reason: 'justification officielle',
  };
  const pageSize = 25;
  let offset = 0;
  let conditions = new URLSearchParams();
  let listRequest = 0;
  let detailRequest = 0;
  let pollTimer;
  let busyJob = false;
  let syncRequested = false;
  let nextManualAt = 0;

  async function request(path, options = {}) {
    const response = await fetch('/anssi' + path, {
      cache: 'no-store', signal: AbortSignal.timeout(15000), ...options,
    });
    const payload = await response.json();
    if (!response.ok) {
      const message = typeof payload.detail === 'string' ? payload.detail : 'Vérifiez les champs saisis.';
      throw new Error(message);
    }
    return payload;
  }

  function displayDate(value, withTime = false) {
    if (!value) return '—';
    if (!withTime) return value.slice(0, 10).split('-').reverse().join('/');
    return new Date(value).toLocaleString('fr-FR', {dateStyle: 'short', timeStyle: 'short'});
  }

  function updateSummary(data) {
    byId('anssi-last-sync').textContent = data.last_sync_at ? 'Dernière synchronisation · ' + displayDate(data.last_sync_at, true) : '';
    const jobs = {
      queued: 'Synchronisation demandée.',
      running: 'Synchronisation en cours…',
      success: 'Dernière synchronisation terminée.',
      partial: 'Dernière synchronisation partielle.',
      failed: 'Échec de la dernière synchronisation.',
    };
    const job = data.job;
    busyJob = ['queued', 'running'].includes(job?.status);
    nextManualAt = job?.next_manual_at ? Date.parse(job.next_manual_at) : 0;
    sync.disabled = busyJob || syncRequested || Date.now() < nextManualAt;
    const collector = data.collector.active ? '' : ' Collecteur inactif. Les demandes attendent son redémarrage.';
    const cooldown = Date.now() < nextManualAt
      ? ' Prochaine demande possible à ' + new Date(nextManualAt).toLocaleTimeString('fr-FR') + '.' : '';
    jobStatus.textContent = (jobs[job?.status] || 'Première synchronisation en attente.') + cooldown + collector;
    const errorDetails = byId('anssi-job-errors');
    errorDetails.hidden = !job?.errors.length;
    errorDetails.querySelector('ul').innerHTML = (job?.errors || []).map(error => `<li>${escapeHtml(error)}</li>`).join('');
    clearTimeout(pollTimer);
    if (busyJob && !panel.hidden) pollTimer = setTimeout(pollJob, 5000);
  }

  async function pollJob(refreshOnFinish = true) {
    if (panel.hidden) return;
    try {
      const previouslyBusy = busyJob;
      updateSummary(await request('/summary'));
      if (refreshOnFinish && previouslyBusy && !busyJob) await loadList();
    } catch {
      jobStatus.textContent = 'État de la synchronisation temporairement indisponible.';
      pollTimer = setTimeout(pollJob, 10000);
    }
  }

  function renderList(data) {
    const rows = data.items.map(item => {
      const products = [...new Set(item.products.map(product => product.name || product.affected || product.vendor))];
      const summary = products.slice(0, 2).join(' · ') || item.products_text.slice(0, 160) || '—';
      const extraCves = item.cves.length > 3 ? ` +${item.cves.length - 3}` : '';
      return `<tr>
        <td><button class="title-button" data-anssi-ref="${escapeHtml(item.reference)}">${escapeHtml(item.title)}</button>
          <small class="anssi-meta">${escapeHtml(item.reference)}</small></td>
        <td>${types[item.kind]}</td><td>${displayDate(item.published_at)}</td><td>${displayDate(item.updated_at)}</td>
        <td>${severities[item.severity] || '—'}<small class="anssi-meta">${escapeHtml(item.severity_source)}</small></td>
        <td>${escapeHtml(item.cves.slice(0, 3).join(', ') || '—')}${extraCves}</td>
        <td>${escapeHtml(summary)}${products.length > 2 ? ` +${products.length - 2}` : ''}</td>
      </tr>`;
    });
    byId('anssi-rows').innerHTML = rows.join('') || '<tr><td colspan="7">Aucune publication pour ces critères.</td></tr>';
    byId('anssi-count').textContent = `${data.total.toLocaleString('fr-FR')} publication(s)`;
    byId('anssi-page').textContent = data.total ? `${offset + 1}–${offset + data.items.length} sur ${data.total}` : '';
    byId('anssi-previous').disabled = offset === 0;
    byId('anssi-next').disabled = offset + pageSize >= data.total;
  }

  async function loadList() {
    const id = ++listRequest;
    const params = new URLSearchParams(conditions);
    params.set('offset', offset);
    params.set('limit', pageSize);
    try {
      const data = await request('/publications?' + params);
      if (id !== listRequest || panel.hidden) return;
      if (offset && offset >= data.total) {
        offset = Math.floor(Math.max(0, data.total - 1) / pageSize) * pageSize;
        return loadList();
      }
      renderList(data);
    } catch (error) {
      if (id === listRequest) byId('anssi-count').textContent = error.message;
    }
  }

  window.refreshAnssi = async () => {
    await Promise.all([pollJob(false), loadList()]);
  };

  function list(values) {
    return values.length ? '<ul>' + values.map(value => `<li>${escapeHtml(value)}</li>`).join('') + '</ul>' : '';
  }

  function sourceBlock(title, record, html) {
    return `<p><strong>${title}</strong> · Vérifié le ${displayDate(record.retrieved_at, true)}</p>${html}`;
  }

  function vulnerability(cve, records, official, verifiedAt) {
    const parts = [];
    const nvd = records.NVD;
    if (nvd) {
      const values = nvd.data.scores.map(score => `CVSS ${score.version} · ${score.score}/10${score.issuer ? ' · ' + score.issuer : ''}`);
      const summary = nvd.data.description ? `<p>${escapeHtml(nvd.data.description)}</p>` : '';
      parts.push(sourceBlock('NVD', nvd, list(values) + summary || '<p>Aucun score ni résumé renseigné.</p>'));
    }
    const cveRecord = records.CVE;
    if (cveRecord) {
      const products = cveRecord.data.affected.map(product => {
        const affected = product.versions.filter(version => version.status === 'affected').map(version => {
          const bound = version.lessThan ? ' à < ' + version.lessThan :
            version.lessThanOrEqual ? ' à ≤ ' + version.lessThanOrEqual : '';
          return version.version + bound;
        });
        return [product.vendor, product.product, affected.join(', ')].filter(Boolean).join(' · ');
      });
      const url = 'https://www.cve.org/CVERecord?id=' + encodeURIComponent(cve);
      parts.push(sourceBlock('CVE Program', cveRecord,
        list([...cveRecord.data.types, ...products].filter(Boolean)) +
        `<p><a href="${url}" target="_blank" rel="noopener noreferrer">Consulter la fiche CVE</a></p>`));
    }
    const kev = records['CISA KEV'];
    if (kev) {
      const message = kev.data.listed
        ? `Exploitation connue. Inscription au catalogue le ${displayDate(kev.data.date_added)}.`
        : 'Absente du catalogue lors de la vérification.';
      parts.push(sourceBlock('CISA KEV', kev, `<p>${message}</p>`));
    }
    const epss = records.EPSS;
    if (epss) {
      const score = epss.data.score;
      const message = score === undefined ? 'Score indisponible.' :
        `Probabilité estimée à 30 jours · ${score.toLocaleString('fr-FR', {
          style: 'percent', maximumFractionDigits: 2,
        })} · Score du ${displayDate(epss.data.date)}`;
      parts.push(sourceBlock('FIRST EPSS', epss, `<p>${message}</p>`));
    }
    const cert = official.length
      ? sourceBlock('CERT-FR', {retrieved_at: verifiedAt}, list(official.map(key => signals[key]))) : '';
    return `<article class="publication"><h4>${escapeHtml(cve)}</h4>${cert}${parts.join('') || '<p>Enrichissement indisponible.</p>'}</article>`;
  }

  function renderDetail(item) {
    const products = item.products.map(product =>
      [product.vendor, product.name, product.affected].filter(Boolean).join(' · '));
    const verified = Object.keys(signals).filter(key => item.evidence[key]).map(key => signals[key]);
    const remediation = {patch: 'Solution et versions corrigées', workaround: 'Contournements', recommendations: 'Recommandations'};
    const advice = Object.entries(remediation).filter(([key]) => item.remediation[key].length)
      .map(([key, label]) => `<h4>${label}</h4>${list(item.remediation[key])}`).join('');
    const revisions = item.revisions.map(revision =>
      [displayDate(revision.date), revision.description].filter(Boolean).join(' · '));
    const changes = item.changes.map(change => `${displayDate(change.detected_at, true)} · ${change.fields
      .map(field => changeLabels[field] || field).join(', ')}`);
    detail.innerHTML = `
      <p class="muted">${escapeHtml(item.reference)} · ${types[item.kind]}</p>
      <h2 id="anssi-detail-title">${escapeHtml(item.title)}</h2>
      <p>Publié le ${displayDate(item.published_at)} · Dernière révision ${displayDate(item.updated_at)}</p>
      <p><a href="${escapeHtml(safeUrl(item.url))}" target="_blank" rel="noopener noreferrer">Lire sur le site du CERT-FR</a></p>
      <p><strong>Criticité</strong> · ${severities[item.severity] || 'Non renseignée'}
        ${item.severity_source ? ' · ' + escapeHtml(item.severity_source) : ''}</p>
      ${item.severity_reason ? `<p>${escapeHtml(item.severity_reason)}</p>` : ''}
      <h3>Informations du CERT-FR</h3>
      <p class="muted">Vérifiées le ${displayDate(item.verified_at, true)}</p>
      <p>${escapeHtml(item.description || 'Résumé indisponible.')}</p>
      ${item.risks.length ? '<h4>Risques</h4>' + list(item.risks) : ''}
      <h4>Produits et versions affectés</h4>
      ${item.products_text ? `<p>${escapeHtml(item.products_text)}</p>` : ''}
      ${list(products) || (item.products_text ? '' : '<p>Non renseignés.</p>')}
      ${verified.length ? '<h4>Confirmations au niveau du bulletin</h4>' + list(verified) : ''}
      <h4>Remédiation</h4>${advice || '<p>Consultez la publication officielle pour les préconisations du CERT-FR.</p>'}
      ${revisions.length ? '<h4>Révisions officielles</h4>' + list(revisions) : ''}
      <h3>Vulnérabilités et enrichissements</h3>
      ${item.cves.map(cve => vulnerability(cve, item.enrichment[cve], item.official_evidence_by_cve[cve], item.verified_at)).join('') || '<p>Aucune CVE référencée.</p>'}
      <h3>Modifications détectées</h3>${list(changes) || '<p>Aucune modification détectée depuis la collecte initiale.</p>'}`;
  }

  async function openDetail(reference) {
    const id = ++detailRequest;
    detail.innerHTML = '<h2 id="anssi-detail-title">Chargement du bulletin…</h2>';
    if (!dialog.open) dialog.showModal();
    try {
      const item = await request('/publications/' + encodeURIComponent(reference));
      if (id === detailRequest && dialog.open) renderDetail(item);
    } catch (error) {
      if (id === detailRequest) detail.innerHTML = `<h2 id="anssi-detail-title">${escapeHtml(error.message)}</h2>`;
    }
  }

  filters.addEventListener('submit', event => {
    event.preventDefault();
    conditions = new URLSearchParams([...new FormData(filters)].filter(([, value]) => value.trim()));
    offset = 0;
    loadList();
  });
  filters.addEventListener('reset', () => {
    conditions = new URLSearchParams();
    offset = 0;
    loadList();
  });
  byId('anssi-previous').addEventListener('click', () => { offset = Math.max(0, offset - pageSize); loadList(); });
  byId('anssi-next').addEventListener('click', () => { offset += pageSize; loadList(); });
  byId('anssi-rows').addEventListener('click', event => {
    const button = event.target.closest('[data-anssi-ref]');
    if (button) openDetail(button.dataset.anssiRef);
  });
  dialog.addEventListener('close', () => { detailRequest++; });
  sync.addEventListener('click', async () => {
    syncRequested = true;
    sync.disabled = true;
    try {
      await request('/sync', {method: 'POST', headers: mutationHeaders});
      await pollJob();
    } catch (error) {
      jobStatus.textContent = error.message;
    } finally {
      syncRequested = false;
      sync.disabled = busyJob || Date.now() < nextManualAt;
    }
  });
})();
