(() => {
  'use strict';

  const seed = JSON.parse(document.getElementById('review-seed').textContent);
  const rows = Array.isArray(seed.rows) ? seed.rows : [];
  const artifactId = seed.artifact_id || '';
  const endpoint = `/convert/artifact/${encodeURIComponent(artifactId)}/chess_evidence_review_progress`;
  const byId = id => document.getElementById(id);
  const elements = {
    list: byId('queue-list'), search: byId('queue-search'), form: byId('review-form'),
    image: byId('evidence-image'), imageStage: byId('image-stage'), empty: byId('image-empty'),
    bbox: byId('bbox'), title: byId('diagram-title'), kicker: byId('diagram-kicker'),
    side: byId('side-to-move'), complete: byId('crop-complete'), reviewer: byId('reviewer'),
    notes: byId('notes'), validation: byId('validation'), assetKind: byId('asset-kind'),
    saveState: byId('save-state'), progressLabel: byId('progress-label'), progressBar: byId('progress-bar'),
  };
  let currentIndex = Math.max(0, rows.findIndex(row => (row.label_status || 'open') === 'open'));
  let currentFilter = 'all';
  let drawing = null;
  const reviewerKey = `kindlemaster.evidence-review.reviewer.${artifactId}`;
  elements.reviewer.value = localStorage.getItem(reviewerKey) || '';

  const current = () => rows[currentIndex] || null;
  const terminal = status => ['verified_visible', 'verified_absence', 'unclear', 'excluded'].includes(status);
  const clamp = value => Math.max(0, Math.min(1, value));
  const shapeInput = () => elements.form.querySelector('input[name="marker_shape"]:checked');

  function setSaveState(state, message) {
    elements.saveState.dataset.state = state;
    elements.saveState.textContent = message;
  }

  function metrics() {
    const counts = {open: 0, verified_visible: 0, verified_absence: 0, unclear: 0, excluded: 0};
    rows.forEach(row => { counts[row.label_status || 'open'] = (counts[row.label_status || 'open'] || 0) + 1; });
    const closed = rows.length - counts.open;
    elements.progressLabel.textContent = `${closed} / ${rows.length}`;
    elements.progressBar.style.width = `${rows.length ? (closed / rows.length) * 100 : 0}%`;
    byId('metric-open').textContent = counts.open;
    byId('metric-visible').textContent = counts.verified_visible;
    byId('metric-absence').textContent = counts.verified_absence;
    byId('metric-unclear').textContent = counts.unclear + counts.excluded;
  }

  function filteredRows() {
    const query = elements.search.value.trim().toLowerCase();
    return rows.map((row, index) => ({row, index})).filter(({row}) => {
      const status = row.label_status || 'open';
      const matchesStatus = currentFilter === 'all' || (currentFilter === 'open' ? status === 'open' : terminal(status));
      const haystack = `${row.canonical_diagram_id || ''} ${row.legacy_intake_diagram_id || ''} ${row.page || ''}`.toLowerCase();
      return matchesStatus && (!query || haystack.includes(query));
    });
  }

  function renderQueue() {
    elements.list.replaceChildren();
    const matches = filteredRows();
    const activePosition = Math.max(0, matches.findIndex(item => item.index === currentIndex));
    const windowSize = 80;
    const start = Math.max(0, Math.min(activePosition - Math.floor(windowSize / 2), matches.length - windowSize));
    const visible = matches.slice(start, start + windowSize);
    visible.forEach(({row, index}) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `queue-item${index === currentIndex ? ' is-active' : ''}`;
      button.dataset.status = row.label_status || 'open';
      button.innerHTML = '<span class="queue-index"></span><span class="queue-copy"><b></b><small></small></span><span class="queue-dot" aria-hidden="true"></span>';
      button.querySelector('.queue-index').textContent = String(row.queue_index || index + 1).padStart(3, '0');
      button.querySelector('b').textContent = row.canonical_diagram_id || row.legacy_intake_diagram_id || 'Diagram';
      button.querySelector('small').textContent = `strona ${row.page || '?'} / ${row.label_status || 'open'}`;
      button.addEventListener('click', () => { currentIndex = index; render(); });
      elements.list.append(button);
    });
    if (matches.length > visible.length) {
      const note = document.createElement('p');
      note.className = 'queue-window-note';
      note.textContent = `Pokazano ${visible.length} z ${matches.length}. Uzyj wyszukiwania, aby przejsc dalej.`;
      elements.list.append(note);
    }
  }

  function renderBBox() {
    const row = current();
    const box = row && Array.isArray(row.marker_bbox) ? row.marker_bbox : null;
    if (!box) { elements.bbox.hidden = true; return; }
    const rect = imageRect();
    if (!rect) { elements.bbox.hidden = true; return; }
    const stageRect = elements.imageStage.getBoundingClientRect();
    const [x0, y0, x1, y1] = box;
    Object.assign(elements.bbox.style, {
      left: `${rect.left - stageRect.left + x0 * rect.width}px`,
      top: `${rect.top - stageRect.top + y0 * rect.height}px`,
      width: `${(x1 - x0) * rect.width}px`,
      height: `${(y1 - y0) * rect.height}px`,
    });
    elements.bbox.hidden = false;
  }

  function renderCurrent() {
    const row = current();
    if (!row) { elements.title.textContent = 'Brak rekordow'; return; }
    elements.kicker.textContent = `Diagram ${row.queue_index || currentIndex + 1} / ${rows.length} / strona ${row.page}`;
    elements.title.textContent = row.canonical_diagram_id || row.legacy_intake_diagram_id || 'Diagram';
    const hasAsset = row.asset_kind !== 'unavailable' && Boolean(row.asset_rel_path);
    elements.image.hidden = !hasAsset;
    elements.empty.hidden = hasAsset;
    if (hasAsset) elements.image.src = row.asset_rel_path;
    else elements.image.removeAttribute('src');
    elements.assetKind.textContent = hasAsset ? `${row.asset_kind}: ${row.asset_rel_path}` : 'brak zasobu';
    for (const input of elements.form.querySelectorAll('input[name="marker_shape"]')) {
      input.checked = input.value === (row.marker_shape || row.suggested_marker_shape || '');
    }
    elements.side.value = row.side_to_move || row.suggested_side_to_move || '';
    elements.complete.checked = row.crop_complete === true;
    elements.notes.value = row.notes || '';
    elements.validation.textContent = row.asset_kind === 'unavailable' ? 'Brak cropa: wybierz "Do wyjasnienia" albo "Wyklucz".' : '';
    elements.validation.classList.toggle('is-error', row.asset_kind === 'unavailable');
    renderBBox();
  }

  function render() {
    metrics();
    renderQueue();
    renderCurrent();
  }

  function imageRect() {
    if (elements.image.hidden || !elements.image.naturalWidth || !elements.image.naturalHeight) return null;
    const stage = elements.imageStage.getBoundingClientRect();
    const scale = Math.min(stage.width / elements.image.naturalWidth, stage.height / elements.image.naturalHeight);
    const width = elements.image.naturalWidth * scale;
    const height = elements.image.naturalHeight * scale;
    return {
      left: stage.left + (stage.width - width) / 2,
      top: stage.top + (stage.height - height) / 2,
      width,
      height,
    };
  }

  function pointerPosition(event) {
    const rect = imageRect();
    if (!rect) return null;
    if (event.clientX < rect.left || event.clientX > rect.left + rect.width || event.clientY < rect.top || event.clientY > rect.top + rect.height) return null;
    return [clamp((event.clientX - rect.left) / rect.width), clamp((event.clientY - rect.top) / rect.height)];
  }

  elements.imageStage.addEventListener('pointerdown', event => {
    const row = current();
    if (!row || row.asset_kind === 'unavailable') return;
    drawing = pointerPosition(event);
    if (!drawing) return;
    elements.imageStage.setPointerCapture(event.pointerId);
    row.marker_bbox = [drawing[0], drawing[1], drawing[0] + 0.001, drawing[1] + 0.001];
    renderBBox();
  });
  elements.imageStage.addEventListener('pointermove', event => {
    if (!drawing) return;
    const row = current();
    const position = pointerPosition(event);
    if (!position) return;
    const [x, y] = position;
    row.marker_bbox = [Math.min(drawing[0], x), Math.min(drawing[1], y), Math.max(drawing[0], x), Math.max(drawing[1], y)];
    renderBBox();
  });
  const finishDrawing = () => { drawing = null; };
  elements.imageStage.addEventListener('pointerup', finishDrawing);
  elements.imageStage.addEventListener('pointercancel', finishDrawing);
  elements.image.addEventListener('load', renderBBox);
  window.addEventListener('resize', renderBBox);

  function submission(status) {
    const row = current();
    const markerShape = shapeInput()?.value || '';
    return {
      ...row,
      canonical_diagram_fingerprint: row.canonical_diagram_fingerprint,
      label_status: status,
      marker_shape: markerShape,
      side_to_move: elements.side.value,
      marker_bbox: row.marker_bbox || null,
      crop_complete: elements.complete.checked,
      verified_by: elements.reviewer.value.trim(),
      notes: elements.notes.value.trim(),
    };
  }

  function inferredStatus() {
    const shape = shapeInput()?.value || '';
    if (shape === 'none_confirmed') return 'verified_absence';
    if (shape === 'unclear') return 'unclear';
    return 'verified_visible';
  }

  async function save(status, moveNext) {
    const row = current();
    if (!row) return;
    localStorage.setItem(reviewerKey, elements.reviewer.value.trim());
    setSaveState('saving', 'Zapisywanie...');
    elements.validation.textContent = '';
    elements.validation.classList.remove('is-error');
    try {
      const response = await fetch(endpoint, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json', Accept: 'application/json'},
        body: JSON.stringify({expected_revision: Number(row.revision || 0), row: submission(status)}),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.success !== true) throw new Error(payload.error || payload.message || `HTTP ${response.status}`);
      const saved = payload.row || payload.result?.row || submission(status);
      rows[currentIndex] = {...row, ...saved, revision: Number(payload.revision ?? payload.result?.revision ?? row.revision + 1)};
      setSaveState('saved', 'Zapisano');
      if (moveNext) nextOpen(); else render();
    } catch (error) {
      elements.validation.textContent = error.message || 'Nie udalo sie zapisac.';
      elements.validation.classList.add('is-error');
      setSaveState('error', 'Blad zapisu');
    }
  }

  function navigate(delta) {
    if (!rows.length) return;
    currentIndex = (currentIndex + delta + rows.length) % rows.length;
    render();
  }
  function nextOpen() {
    const next = rows.findIndex((row, index) => index > currentIndex && (row.label_status || 'open') === 'open');
    const wrapped = rows.findIndex(row => (row.label_status || 'open') === 'open');
    currentIndex = next >= 0 ? next : wrapped >= 0 ? wrapped : Math.min(currentIndex + 1, rows.length - 1);
    render();
  }

  elements.form.addEventListener('submit', event => { event.preventDefault(); save(inferredStatus(), true); });
  elements.form.addEventListener('change', event => {
    if (event.target.name === 'marker_shape') {
      if (event.target.value === 'outline_triangle') elements.side.value = 'w';
      if (event.target.value === 'filled_triangle') elements.side.value = 'b';
      if (event.target.value === 'none_confirmed') elements.side.value = '';
    }
  });
  elements.form.querySelectorAll('[data-decision]').forEach(button => button.addEventListener('click', () => save(button.dataset.decision, true)));
  const shapeSymbols = {outline_triangle: '\u25b3', filled_triangle: '\u25bd', none_confirmed: '\u2205', unclear: '?'};
  elements.form.querySelectorAll('input[name="marker_shape"]').forEach(input => {
    const symbol = input.nextElementSibling?.querySelector('b');
    if (symbol) symbol.textContent = shapeSymbols[input.value] || '?';
  });
  byId('clear-bbox').addEventListener('click', () => { const row = current(); if (row) { row.marker_bbox = null; renderBBox(); } });
  byId('previous-item').addEventListener('click', () => navigate(-1));
  byId('next-item').addEventListener('click', () => navigate(1));
  elements.search.addEventListener('input', renderQueue);
  document.querySelectorAll('.filter').forEach(button => button.addEventListener('click', () => {
    currentFilter = button.dataset.filter;
    document.querySelectorAll('.filter').forEach(item => item.classList.toggle('is-active', item === button));
    renderQueue();
  }));
  window.addEventListener('keydown', event => {
    if (event.target.matches('input, textarea, select')) return;
    if (event.key === ']') navigate(1);
    if (event.key === '[') navigate(-1);
  });

  render();
})();
