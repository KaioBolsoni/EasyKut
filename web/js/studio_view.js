/**
 * Aba Estúdio: Hero (URL + Upload Local), Workspace Master-Detail,
 * Criação/Duplicação de cortes, Timeline Visual de Trim, Atalhos de Teclado e Renderização.
 */

import {
  apiCreateCorte,
  apiExtractFrame,
  apiStartAnalyze,
  apiStartRender,
  apiUploadVideo,
} from './api.js';
import {
  initCutGeometry,
  invalidateBlurCache,
  renderEditorCanvas,
  renderMockupCanvas,
} from './canvas_engine.js';
import {
  seekClipByDelta,
  seekClipToRelativeSec,
  stopAndResetClipPreview,
  togglePlayClipPreview,
} from './clip_player.js';
import {
  canvasState,
  getActiveCut,
  getCutSegments,
  pollJob,
  schedulePersistCutEdits,
  state,
  updateStepTracker,
} from './state.js';
import { clamp, formatSecondsMMSS, formatTimeRange, showToast } from './utils.js';

let _switchTab = () => {};

export function initStudioView({ switchTab }) {
  _switchTab = switchTab;
  bindTrimTimelineInteractions();
  bindKeyboardShortcuts();
}

export function renderAllCanvases() {
  const cut = getActiveCut();
  renderEditorCanvas(canvasState, cut);
  renderMockupCanvas(canvasState, cut, getCutSegments);
}

export function updateCanvasPlaceholderUI(show) {
  const overlay = document.getElementById('canvas-placeholder-overlay');
  if (overlay) {
    overlay.style.display = show ? 'flex' : 'none';
  }
}

// =========================================================
// INGESTÃO: URL REMOTA & UPLOAD LOCAL
// =========================================================
function setHeroAnalyzingUI(stepMessage) {
  state.isAnalyzing = true;
  const btnAnalyze = document.getElementById('btn-analyze');
  const btnUpload = document.getElementById('btn-upload-local');
  if (btnAnalyze) {
    btnAnalyze.disabled = true;
    btnAnalyze.style.opacity = '0.55';
    btnAnalyze.style.cursor = 'not-allowed';
  }
  if (btnUpload) btnUpload.disabled = true;

  const progressCard = document.getElementById('progress-card');
  if (progressCard) progressCard.style.display = 'block';

  const stepText = document.getElementById('progress-step-text');
  const pctText = document.getElementById('progress-pct-text');
  const lineFill = document.getElementById('progress-line-fill');
  if (stepText) stepText.textContent = stepMessage;
  if (pctText) pctText.textContent = '5%';
  if (lineFill) lineFill.style.width = '5%';

  updateStepTracker(1);
}

export function resetHeroUI() {
  state.isAnalyzing = false;
  const btnAnalyze = document.getElementById('btn-analyze');
  const btnUpload = document.getElementById('btn-upload-local');
  if (btnAnalyze) {
    btnAnalyze.disabled = false;
    btnAnalyze.style.opacity = '1';
    btnAnalyze.style.cursor = 'pointer';
  }
  if (btnUpload) btnUpload.disabled = false;
  const progressCard = document.getElementById('progress-card');
  if (progressCard) progressCard.style.display = 'none';
}

export function resetToHero() {
  stopAndResetClipPreview();
  state.currentVideo = null;
  state.cuts = [];
  const urlInput = document.getElementById('input-yt-url');
  if (urlInput) urlInput.value = '';
  const fileInput = document.getElementById('input-local-video');
  if (fileInput) fileInput.value = '';
  document.getElementById('workspace-section').style.display = 'none';
  document.getElementById('hero-section').style.display = 'flex';
  resetHeroUI();
}

export async function startAnalysis() {
  if (state.isAnalyzing) return;

  const urlInput = document.getElementById('input-yt-url');
  let url = urlInput ? urlInput.value.trim() : '';
  const provider = document.getElementById('select-hero-provider')?.value || 'gemini';

  if (!url) {
    alert('Por favor, cole a URL de um vídeo do YouTube, Vimeo ou Twitch (ou envie um arquivo local).');
    return;
  }

  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    url = 'https://' + url;
    if (urlInput) urlInput.value = url;
  }

  setHeroAnalyzingUI('Enviando link para o estúdio...');

  try {
    const { res, data } = await apiStartAnalyze(url, provider, state.maxCuts);
    if (res.ok && data.job_id) {
      state.activeJobId = data.job_id;
      pollJob(data.job_id, openWorkspaceFromData, onAnalysisError);
    } else {
      throw new Error(data.detail || 'Falha ao iniciar análise no servidor.');
    }
  } catch (err) {
    alert('Erro ao analisar vídeo: ' + err.message);
    resetHeroUI();
  }
}

export async function handleLocalVideoUpload(file) {
  if (!file || state.isAnalyzing) return;

  const provider = document.getElementById('select-hero-provider')?.value || 'gemini';
  setHeroAnalyzingUI(`Enviando arquivo local "${file.name}"...`);

  try {
    const { res, data } = await apiUploadVideo(file, provider, state.maxCuts);
    if (res.ok && data.job_id) {
      state.activeJobId = data.job_id;
      pollJob(data.job_id, openWorkspaceFromData, onAnalysisError);
    } else {
      throw new Error(data.detail || 'Falha ao enviar arquivo local.');
    }
  } catch (err) {
    alert('Erro ao processar vídeo local: ' + err.message);
    resetHeroUI();
  }
}

function onAnalysisError(errorMsg) {
  resetHeroUI();
  alert('Erro na esteira de IA: ' + errorMsg);
}

// =========================================================
// WORKSPACE MASTER-DETAIL (ANÁLISE OU REABERTURA)
// =========================================================
export function openWorkspaceFromData(result) {
  state.currentVideo = result;
  state.cuts = (result.cortes || []).map((c, idx) => ({
    ...c,
    index: idx,
    start_time: Number(c.start_time) || 0,
    end_time: Number(c.end_time) || 30,
    duration: Number(c.duration) || Math.max(0.5, (Number(c.end_time) || 30) - (Number(c.start_time) || 0)),
    layout_params: c.layout_params ? { ...c.layout_params } : {},
    layout_mode: c.layout_mode || ((c.tipo === 'longo' || Number(c.duration) >= 180) ? 'horizontal_16_9' : 'vertical_blur'),
    selected: c.selected !== undefined ? Boolean(c.selected) : idx < state.maxCuts,
    burn_subtitles: c.burn_subtitles !== undefined ? Boolean(c.burn_subtitles) : true,
  }));
  state.activeCutIndex = 0;
  resetHeroUI();

  document.getElementById('hero-section').style.display = 'none';
  document.getElementById('workspace-section').style.display = 'grid';

  const resSel = document.getElementById('select-render-resolution');
  if (resSel) resSel.value = String(state.renderResolution);

  updateCutsSidebarUI();
  if (state.cuts.length > 0) {
    selectCut(0);
  } else {
    renderAllCanvases();
    updateTrimTimelineUI();
  }
}

export function updateCutsSidebarUI() {
  const container = document.getElementById('cuts-list-container');
  container.replaceChildren();

  const totalCuts = state.cuts.length;
  document.getElementById('cuts-count-header').textContent = `Cortes (${totalCuts})`;

  state.cuts.forEach((c, idx) => {
    const card = document.createElement('div');
    card.className = `cut-item-card ${idx === state.activeCutIndex ? 'active' : ''}`;
    card.addEventListener('click', () => selectCut(idx));

    const score = Math.round(Number(c.virality_score) || 80);
    const scoreBadgeClass = score >= 75 ? 'cut-score-badge' : 'cut-score-badge med';

    const topRow = document.createElement('div');
    topRow.className = 'cut-item-top';

    const checkLabel = document.createElement('label');
    checkLabel.className = 'cut-check-label';
    checkLabel.addEventListener('click', (e) => e.stopPropagation());

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = Boolean(c.selected);
    checkbox.addEventListener('change', (e) => {
      c.selected = e.target.checked;
      updateRenderButtonState();
    });

    const idxSpan = document.createElement('span');
    idxSpan.textContent = `#${idx + 1}`;
    checkLabel.append(checkbox, idxSpan);

    const actionsGroup = document.createElement('div');
    actionsGroup.style.display = 'flex';
    actionsGroup.style.alignItems = 'center';
    actionsGroup.style.gap = '4px';

    const dupBtn = document.createElement('button');
    dupBtn.className = 'cut-card-dup-mini';
    dupBtn.title = 'Duplicar este corte';
    dupBtn.textContent = '⧉';
    dupBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      duplicateCut(idx);
    });

    const playBtn = document.createElement('button');
    playBtn.className = 'cut-card-play-mini';
    playBtn.title = 'Assistir este corte no Estúdio';
    playBtn.textContent = '▶ Ver';
    playBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (state.activeCutIndex !== idx) selectCut(idx);
      togglePlayClipPreview();
    });

    const scoreBadge = document.createElement('span');
    scoreBadge.className = scoreBadgeClass;
    scoreBadge.textContent = `⭐ ${score}`;
    actionsGroup.append(dupBtn, playBtn, scoreBadge);

    topRow.append(checkLabel, actionsGroup);

    const titleEl = document.createElement('div');
    titleEl.className = 'cut-title-text';
    const safeTitle = String(c.title || '');
    titleEl.title = safeTitle;
    titleEl.textContent = safeTitle;

    const metaRow = document.createElement('div');
    metaRow.className = 'cut-meta-text';
    const durSpan = document.createElement('span');
    durSpan.textContent = `⏱️ ${(Number(c.duration) || 0).toFixed(1)}s`;
    const rangeSpan = document.createElement('span');
    rangeSpan.textContent = formatTimeRange(Number(c.start_time) || 0, Number(c.end_time) || 0);
    metaRow.append(durSpan, rangeSpan);

    card.append(topRow, titleEl, metaRow);
    container.appendChild(card);
  });

  updateRenderButtonState();
}

export async function createManualCut() {
  if (!state.currentVideo || !state.currentVideo.video_id) return;

  const totalDur = Math.max(30, Number(state.currentVideo.duration) || 60);
  const active = getActiveCut();
  const baseStart = active ? Math.min(totalDur - 5, active.end_time) : 0;
  const startTime = Number(Math.max(0, baseStart).toFixed(1));
  const endTime = Number(Math.min(totalDur, startTime + 30).toFixed(1));
  const effectiveEnd = endTime > startTime + 1 ? endTime : startTime + 15;

  await persistNewCut({
    video_id: state.currentVideo.video_id,
    title: `Corte Manual #${state.cuts.length + 1}`,
    start_time: startTime,
    end_time: effectiveEnd,
    virality_score: 80.0,
    justification: 'Corte criado manualmente no Estúdio.',
    layout_mode: 'vertical_blur',
    layout_params: { zoom_factor: 1.0 },
    burn_subtitles: true,
  });
}

async function duplicateCut(idx) {
  const src = state.cuts[idx];
  if (!src || !state.currentVideo?.video_id) return;

  await persistNewCut({
    video_id: state.currentVideo.video_id,
    title: `${src.title} (Cópia)`.slice(0, 140),
    start_time: src.start_time,
    end_time: src.end_time,
    virality_score: Number(src.virality_score) || 80.0,
    justification: src.justification || '',
    layout_mode: src.layout_mode || 'vertical_blur',
    layout_params: src.layout_params ? { ...src.layout_params } : {},
    burn_subtitles: Boolean(src.burn_subtitles),
  });
}

async function persistNewCut(payload) {
  try {
    const { res, data } = await apiCreateCorte(payload);
    if (!res.ok || !data.success || !data.corte) {
      throw new Error(data.detail || `Erro ${res.status}`);
    }
    const saved = data.corte;
    const newCut = {
      index: state.cuts.length,
      id: saved.id,
      title: saved.title,
      start_time: Number(saved.start_time),
      end_time: Number(saved.end_time),
      duration: round1(Number(saved.end_time) - Number(saved.start_time)),
      virality_score: Number(saved.virality_score) || 80,
      tipo: saved.tipo || 'short',
      justification: saved.justification || '',
      frame_url: '',
      selected: true,
      layout_mode: saved.layout_mode || 'vertical_blur',
      layout_params: saved.layout_params || {},
      burn_subtitles: Boolean(saved.burn_subtitles),
      segments: [],
    };
    state.cuts.push(newCut);
    updateCutsSidebarUI();
    selectCut(state.cuts.length - 1);
    showToast(`Corte "${newCut.title}" criado!`);
  } catch (err) {
    alert('Erro ao criar corte: ' + err.message);
  }
}

function round1(val) {
  return Math.round(val * 10) / 10;
}

export function updateRenderButtonState() {
  const count = state.cuts.filter((c) => c.selected).length;
  const btn = document.getElementById('btn-render-selected');
  if (!btn) return;
  btn.disabled = (count === 0 || state.isRendering);
  btn.innerHTML = `
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
    Renderizar ${count} Selecionado${count !== 1 ? 's' : ''}
  `;
}

export function selectCut(idx) {
  if (idx < 0 || idx >= state.cuts.length) return;
  state.activeCutIndex = idx;

  stopAndResetClipPreview();

  document.querySelectorAll('.cut-item-card').forEach((c, i) => {
    c.classList.toggle('active', i === idx);
  });

  const activeCut = getActiveCut();
  updateActiveCutToolbar();
  updateCutContextPanel(activeCut);
  updateTrimTimelineUI();
  loadCutFrame(activeCut);
}

export function updateActiveCutToolbar() {
  const c = getActiveCut();
  if (!c) return;

  document.getElementById('active-cut-title').value = c.title || '';
  document.getElementById('active-cut-start').value = c.start_time.toFixed(1);
  document.getElementById('active-cut-end').value = c.end_time.toFixed(1);
  document.getElementById('toggle-burn-subs').checked = Boolean(c.burn_subtitles);

  document.querySelectorAll('.layout-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.mode === c.layout_mode);
  });

  updateSubcontrolsBar(c);
}

function updateSubcontrolsBar(cut) {
  const bar = document.getElementById('subcontrols-bar');
  if (!bar) return;
  bar.replaceChildren();
  const mode = cut.layout_mode;

  const row = document.createElement('div');
  row.style.display = 'flex';
  row.style.alignItems = 'center';
  row.style.gap = '0.85rem';
  row.style.width = '100%';

  if (mode === 'vertical_blur') {
    const zoom = Number(cut.layout_params?.zoom_factor) || 1.0;
    const desc = document.createElement('span');
    desc.textContent = 'Canvas 9:16 com fundo desfocado e vídeo 16:9 nítido centralizado.';

    const rightBox = document.createElement('div');
    rightBox.style.display = 'flex';
    rightBox.style.alignItems = 'center';
    rightBox.style.gap = '0.5rem';
    rightBox.style.marginLeft = 'auto';

    const zoomLbl = document.createElement('span');
    zoomLbl.append(document.createTextNode('Zoom: '));
    const zoomVal = document.createElement('strong');
    zoomVal.id = 'lbl-zoom-val';
    zoomVal.className = 'highlight-indigo';
    zoomVal.textContent = `${zoom.toFixed(2)}x`;
    zoomLbl.appendChild(zoomVal);

    const slider = document.createElement('input');
    slider.type = 'range';
    slider.min = '1.0';
    slider.max = '1.3';
    slider.step = '0.05';
    slider.value = String(zoom);
    slider.style.width = '140px';
    slider.addEventListener('input', (e) => {
      const val = parseFloat(e.target.value);
      cut.layout_params.zoom_factor = val;
      zoomVal.textContent = `${val.toFixed(2)}x`;
      renderAllCanvases();
      schedulePersistCutEdits(cut);
    });

    rightBox.append(zoomLbl, slider);
    row.append(desc, rightBox);
  } else if (mode === 'manual_crop') {
    const badge = document.createElement('span');
    badge.className = 'highlight-indigo';
    badge.textContent = 'Caixa 9:16 Arrastável:';
    const desc = document.createElement('span');
    desc.textContent = 'Arraste dentro da caixa para mover ou puxe os cantos para redimensionar com proporção travada.';
    row.append(badge, desc);
  } else if (mode === 'split_screen') {
    const topB = document.createElement('strong');
    topB.className = 'highlight-indigo';
    topB.textContent = '■ Topo';
    const botB = document.createElement('strong');
    botB.className = 'highlight-amber';
    botB.textContent = '■ Base';
    const desc = document.createElement('span');
    desc.textContent = 'Arraste ou redimensione cada metade de forma independente (1080×960 cada).';
    row.append(topB, botB, desc);
  } else if (mode === 'face_tracking_9_16') {
    const badge = document.createElement('span');
    badge.style.color = 'var(--accent-green)';
    badge.style.fontWeight = '700';
    badge.textContent = '🤖 Auto Face Tracking IA (OpenCV):';
    const desc = document.createElement('span');
    desc.textContent = 'Detecta automaticamente o falante principal no trecho e estabiliza o recorte 9:16 com Média Móvel.';
    row.append(badge, desc);
  } else {
    const desc = document.createElement('span');
    desc.textContent = 'Modo Widescreen 16:9 Nativo. Sem corte ou distorção (Stream Copy instantâneo sem legendas).';
    row.appendChild(desc);
  }

  bar.appendChild(row);
}

export function setLayoutMode(mode) {
  const c = getActiveCut();
  if (!c) return;

  c.layout_mode = mode;
  if (!c.layout_params) c.layout_params = {};
  if (mode === 'vertical_blur' && !c.layout_params.zoom_factor) {
    c.layout_params.zoom_factor = 1.0;
  }

  initCutGeometry(c, canvasState);
  updateActiveCutToolbar();
  renderAllCanvases();
  schedulePersistCutEdits(c);
}

// =========================================================
// TIMELINE VISUAL INTERATIVA DE TRIM & ATALHOS DE TECLADO
// =========================================================
export function applyCutTimeBounds(startTime, endTime, { persist = true, resetClip = true } = {}) {
  const c = getActiveCut();
  if (!c) return;
  const maxDur = Math.max(10, Number(state.currentVideo?.duration) || Math.max(60, endTime));
  const cleanStart = round1(clamp(startTime, 0, Math.max(0, maxDur - 1)));
  const cleanEnd = round1(clamp(endTime, cleanStart + 1, maxDur));

  c.start_time = cleanStart;
  c.end_time = cleanEnd;
  c.duration = round1(Math.max(0.5, cleanEnd - cleanStart));

  if (resetClip) stopAndResetClipPreview();
  updateActiveCutToolbar();
  updateCutsSidebarUI();
  updateCutContextPanel(c);
  updateTrimTimelineUI();
  if (persist) schedulePersistCutEdits(c);
}

export function updateTrimTimelineUI() {
  const track = document.getElementById('trim-timeline-track');
  const rangeEl = document.getElementById('trim-timeline-range');
  const markersEl = document.getElementById('trim-timeline-markers');
  const labelEl = document.getElementById('trim-timeline-summary');
  if (!track || !rangeEl) return;

  const cut = getActiveCut();
  const totalDur = Math.max(1, Number(state.currentVideo?.duration) || (cut ? Math.max(60, cut.end_time) : 60));

  if (markersEl) {
    markersEl.replaceChildren();
    state.cuts.forEach((c, idx) => {
      if (idx === state.activeCutIndex) return;
      const m = document.createElement('div');
      m.className = 'trim-other-marker';
      const leftPct = clamp((c.start_time / totalDur) * 100, 0, 100);
      const widthPct = clamp(((c.end_time - c.start_time) / totalDur) * 100, 0.5, 100 - leftPct);
      m.style.left = `${leftPct}%`;
      m.style.width = `${widthPct}%`;
      m.title = `#${idx + 1}: ${c.title}`;
      markersEl.appendChild(m);
    });
  }

  if (!cut) {
    rangeEl.style.display = 'none';
    if (labelEl) labelEl.textContent = '0:00 — 0:00';
    return;
  }

  rangeEl.style.display = 'block';
  const leftPct = clamp((cut.start_time / totalDur) * 100, 0, 99);
  const rightPct = clamp((cut.end_time / totalDur) * 100, leftPct + 1, 100);
  rangeEl.style.left = `${leftPct}%`;
  rangeEl.style.width = `${Math.max(1, rightPct - leftPct)}%`;

  if (labelEl) {
    labelEl.textContent = `${formatSecondsMMSS(cut.start_time)} → ${formatSecondsMMSS(cut.end_time)} (${cut.duration.toFixed(1)}s de ${formatSecondsMMSS(totalDur)})`;
  }
}

function bindTrimTimelineInteractions() {
  const track = document.getElementById('trim-timeline-track');
  const handleStart = document.getElementById('trim-handle-start');
  const handleEnd = document.getElementById('trim-handle-end');
  if (!track || !handleStart || !handleEnd) return;

  let activeEdge = null; // 'start' | 'end'

  const startDrag = (edge) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    activeEdge = edge;
  };

  handleStart.addEventListener('mousedown', startDrag('start'));
  handleEnd.addEventListener('mousedown', startDrag('end'));

  window.addEventListener('mousemove', (e) => {
    if (!activeEdge) return;
    const cut = getActiveCut();
    if (!cut) return;

    const rect = track.getBoundingClientRect();
    if (rect.width <= 0) return;
    const ratio = clamp((e.clientX - rect.left) / rect.width, 0, 1);
    const totalDur = Math.max(1, Number(state.currentVideo?.duration) || Math.max(60, cut.end_time));
    const targetSec = round1(ratio * totalDur);

    if (activeEdge === 'start') {
      const nextStart = Math.min(targetSec, cut.end_time - 1);
      applyCutTimeBounds(nextStart, cut.end_time, { persist: false, resetClip: false });
    } else if (activeEdge === 'end') {
      const nextEnd = Math.max(targetSec, cut.start_time + 1);
      applyCutTimeBounds(cut.start_time, nextEnd, { persist: false, resetClip: false });
    }
  });

  window.addEventListener('mouseup', () => {
    if (!activeEdge) return;
    activeEdge = null;
    stopAndResetClipPreview();
    schedulePersistCutEdits(getActiveCut());
  });
}

function bindKeyboardShortcuts() {
  window.addEventListener('keydown', (e) => {
    if (state.activeTab !== 'studio' || !state.currentVideo) return;
    const tag = (e.target?.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target?.isContentEditable) {
      return;
    }

    const cut = getActiveCut();
    if (!cut) return;

    if (e.code === 'Space') {
      e.preventDefault();
      togglePlayClipPreview();
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      seekClipByDelta(-1);
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      seekClipByDelta(1);
    } else if (e.key === '[') {
      if (canvasState.isVideoReady && canvasState.videoEl) {
        e.preventDefault();
        const absSec = cut.start_time + (canvasState.videoEl.currentTime || 0);
        if (absSec < cut.end_time - 1) {
          applyCutTimeBounds(absSec, cut.end_time);
          showToast(`Início ajustado para ${formatSecondsMMSS(absSec)}`);
        }
      }
    } else if (e.key === ']') {
      if (canvasState.isVideoReady && canvasState.videoEl) {
        e.preventDefault();
        const absSec = cut.start_time + (canvasState.videoEl.currentTime || 0);
        if (absSec > cut.start_time + 1) {
          applyCutTimeBounds(cut.start_time, absSec);
          showToast(`Fim ajustado para ${formatSecondsMMSS(absSec)}`);
        }
      }
    }
  });
}

// =========================================================
// FRAMES & PAINEL DE CONTEXTO
// =========================================================
export function loadCutFrame(cut) {
  if (!cut) return;

  canvasState.imgLoaded = false;
  invalidateBlurCache(canvasState);

  if (!cut.frame_url) {
    if (state.currentVideo && state.currentVideo.thumbnail) {
      cut.frame_url = state.currentVideo.thumbnail;
    } else {
      canvasState.currentImg = null;
      canvasState.imgNaturalW = 1920;
      canvasState.imgNaturalH = 1080;
      canvasState.editorCanvas.width = 1920;
      canvasState.editorCanvas.height = 1080;
      initCutGeometry(cut, canvasState);
      updateCanvasPlaceholderUI(true);
      renderAllCanvases();
      return;
    }
  }

  updateCanvasPlaceholderUI(false);
  const img = new Image();
  img.crossOrigin = 'anonymous';

  img.onload = () => {
    canvasState.currentImg = img;
    canvasState.imgNaturalW = img.naturalWidth || 1920;
    canvasState.imgNaturalH = img.naturalHeight || 1080;
    canvasState.imgLoaded = true;
    invalidateBlurCache(canvasState);

    canvasState.editorCanvas.width = canvasState.imgNaturalW;
    canvasState.editorCanvas.height = canvasState.imgNaturalH;

    initCutGeometry(cut, canvasState);
    updateCanvasPlaceholderUI(false);
    renderAllCanvases();
  };

  img.onerror = () => {
    console.warn('Erro ao carregar frame:', cut.frame_url);
    canvasState.imgLoaded = false;
    canvasState.currentImg = null;
    canvasState.imgNaturalW = 1920;
    canvasState.imgNaturalH = 1080;
    canvasState.editorCanvas.width = 1920;
    canvasState.editorCanvas.height = 1080;
    initCutGeometry(cut, canvasState);
    updateCanvasPlaceholderUI(true);
    renderAllCanvases();
  };

  img.src = cut.frame_url;
}

export async function refreshCutFrame() {
  const c = getActiveCut();
  if (!c || !state.currentVideo) return;

  const btnToolbar = document.getElementById('btn-refresh-frame');
  const btnOverlay = document.getElementById('btn-load-frame-placeholder');
  if (btnToolbar && btnToolbar.disabled) return;

  if (btnToolbar) btnToolbar.disabled = true;
  if (btnOverlay) btnOverlay.disabled = true;

  showToast('Capturando frame no tempo exato...');
  const videoId = state.currentVideo.video_id;
  const source = state.currentVideo.url;
  const ts = Math.max(0, c.start_time + 0.5);

  try {
    const { res, data } = await apiExtractFrame(videoId, source, ts);
    if (res.ok && data.success && data.frame_url) {
      c.frame_url = data.frame_url;
      loadCutFrame(c);
      showToast('Frame atualizado com sucesso!');
    } else {
      showToast(data.detail || 'Não foi possível capturar o frame. Tente novamente.');
    }
  } catch (err) {
    alert('Erro ao atualizar frame: ' + err.message);
  } finally {
    if (btnToolbar) btnToolbar.disabled = false;
    if (btnOverlay) btnOverlay.disabled = false;
  }
}

export function updateCutContextPanel(cut) {
  const justBox = document.getElementById('cut-justification-box');
  const transBox = document.getElementById('cut-transcript-box');
  if (!justBox || !transBox) return;

  justBox.replaceChildren();
  transBox.replaceChildren();

  if (!cut) return;

  const labelStrong = document.createElement('strong');
  if (cut.justification) {
    labelStrong.textContent = 'Motivo Viral: ';
    justBox.append(labelStrong, document.createTextNode(String(cut.justification)));
  } else {
    labelStrong.textContent = 'Intervalo: ';
    const infoText = `${formatTimeRange(Number(cut.start_time) || 0, Number(cut.end_time) || 0)} (${(Number(cut.duration) || 0).toFixed(1)}s)`;
    justBox.append(labelStrong, document.createTextNode(infoText));
  }

  const segs = getCutSegments(cut);
  if (segs.length === 0) {
    const emptyHint = document.createElement('span');
    emptyHint.style.color = 'var(--text-dim)';
    emptyHint.textContent = 'Clique em "Assistir Corte" (ou barra de Espaço) para ver e ouvir o clipe no editor.';
    transBox.appendChild(emptyHint);
    return;
  }

  segs.forEach((s, i) => {
    const relSec = Math.max(0, (Number(s.start) || 0) - (Number(cut.start_time) || 0));
    const item = document.createElement('div');
    item.className = 'cut-seg-item';
    item.dataset.segIdx = String(i);
    item.dataset.relSec = relSec.toFixed(2);
    item.title = 'Clique para pular o vídeo · Clique no lápis para corrigir a legenda';

    const timeSpan = document.createElement('span');
    timeSpan.className = 'cut-seg-time';
    timeSpan.textContent = `[${formatSecondsMMSS(s.start)}]`;
    timeSpan.addEventListener('click', () => seekClipToRelativeSec(Number(relSec.toFixed(2))));

    const textSpan = document.createElement('span');
    textSpan.style.flex = '1';
    textSpan.textContent = String(s.text || '');
    textSpan.addEventListener('click', () => seekClipToRelativeSec(Number(relSec.toFixed(2))));

    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'cut-seg-edit-btn';
    editBtn.title = 'Editar texto desta legenda antes de renderizar';
    editBtn.textContent = '✏️';
    editBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'cut-seg-inline-input';
      input.value = String(s.text || '');
      const commitEdit = () => {
        const nextText = input.value.trim();
        const updatedSegs = segs.map((segItem, idx) =>
          idx === i ? { ...segItem, text: nextText || segItem.text } : { ...segItem }
        );
        if (!cut.layout_params) cut.layout_params = {};
        cut.layout_params.subtitle_overrides = updatedSegs;
        schedulePersistCutEdits(cut);
        updateCutContextPanel(cut);
        renderAllCanvases();
        showToast('Legenda corrigida para renderização!');
      };
      input.addEventListener('blur', commitEdit);
      input.addEventListener('keydown', (ke) => {
        if (ke.key === 'Enter') input.blur();
        if (ke.key === 'Escape') updateCutContextPanel(cut);
      });
      item.replaceChildren(timeSpan, input);
      input.focus();
    });

    item.append(timeSpan, document.createTextNode(' '), textSpan, editBtn);
    transBox.appendChild(item);
  });

  const timeDisp = document.getElementById('clip-time-display');
  if (timeDisp && !canvasState.isVideoReady) {
    timeDisp.textContent = `0:00 / ${formatSecondsMMSS(cut.duration)}`;
  }
}

// =========================================================
// RENDERIZAÇÃO DE CORTES (720p / 1080p)
// =========================================================
export async function startRender() {
  if (state.isRendering) return;
  if (canvasState.videoEl && !canvasState.videoEl.paused) {
    canvasState.videoEl.pause();
  }
  const selecionados = state.cuts.filter((c) => c.selected);
  if (selecionados.length === 0) {
    alert('Selecione pelo menos um corte para renderizar.');
    return;
  }

  const cutsPayload = selecionados.map((c) => {
    if (!c.layout_params) c.layout_params = {};
    if (!c.layout_params.ref_w) c.layout_params.ref_w = canvasState.imgNaturalW || 1920;
    if (!c.layout_params.ref_h) c.layout_params.ref_h = canvasState.imgNaturalH || 1080;
    return {
      ...c,
      subtitle_preset: state.subtitlePreset || 'hormozi',
      karaoke_subtitles: Boolean(state.karaokeSubtitles),
      dynamic_face_tracking: true,
      remove_silences: Boolean(state.removeSilences),
      punch_in_zoom: Boolean(state.punchInZoom),
      subtitle_overrides: c.layout_params.subtitle_overrides || null,
    };
  });

  const exportFormats = Array.from(document.querySelectorAll('.chk-export-format:checked')).map((el) => el.value);

  const resSel = document.getElementById('select-render-resolution');
  const targetRes = parseInt(resSel?.value || String(state.renderResolution), 10) === 720 ? 720 : 1080;
  state.renderResolution = targetRes;

  state.isRendering = true;
  updateRenderButtonState();

  const overlay = document.getElementById('render-progress-overlay');
  if (overlay) overlay.style.display = 'flex';
  const rStep = document.getElementById('render-progress-step');
  const rPct = document.getElementById('render-progress-pct');
  const rFill = document.getElementById('render-progress-fill');
  if (rStep) rStep.textContent = `Iniciando renderização (${targetRes}p) com FFmpeg...`;
  if (rPct) rPct.textContent = '5%';
  if (rFill) rFill.style.width = '5%';

  try {
    const { res, data } = await apiStartRender(state.currentVideo.video_id, cutsPayload, targetRes, {
      use_gpu: state.useGpu,
      brand_kit_id: state.brandKitId,
      normalize_audio: state.normalizeAudio,
      denoise_audio: state.denoiseAudio,
      music_filename: state.musicFilename || null,
      music_volume: state.musicVolume,
      export_formats: exportFormats,
    });
    if (res.ok && data.job_id) {
      pollJob(data.job_id, onRenderSuccess, onRenderError);
    } else {
      throw new Error(data.detail || 'Falha ao iniciar renderização.');
    }
  } catch (err) {
    alert('Erro ao renderizar: ' + err.message);
    state.isRendering = false;
    updateRenderButtonState();
    if (overlay) overlay.style.display = 'none';
  }
}

function onRenderSuccess() {
  state.isRendering = false;
  updateRenderButtonState();
  const overlay = document.getElementById('render-progress-overlay');
  if (overlay) overlay.style.display = 'none';

  showToast('Cortes renderizados com sucesso!');
  _switchTab('library');
}

function onRenderError(errorMsg) {
  state.isRendering = false;
  updateRenderButtonState();
  const overlay = document.getElementById('render-progress-overlay');
  if (overlay) overlay.style.display = 'none';
  alert('Erro durante a renderização: ' + errorMsg);
}
