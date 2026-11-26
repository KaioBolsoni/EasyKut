/**
 * EasyKut Studio V3.1 — Aplicação Principal (ES Module)
 * Coordena estado do Estúdio, polling de jobs, player de clipe, biblioteca e auto-save,
 * delegando utilitários (./js/utils.js), chamadas REST (./js/api.js) e Canvas 60fps (./js/canvas_engine.js).
 */

import {
  apiDeleteCorte,
  apiDeleteVideo,
  apiExtractFrame,
  apiExtractPreviewClip,
  apiGetConfig,
  apiGetHistory,
  apiGetJob,
  apiOpenFolder,
  apiPatchCorte,
  apiStartAnalyze,
  apiStartRender,
} from './js/api.js';
import {
  checkHandleHit,
  createCanvasState,
  getCanvasMouseCoords,
  initBlurBuffer,
  initCutGeometry,
  invalidateBlurCache,
  isPointInsideBox,
  renderEditorCanvas,
  renderMockupCanvas,
  resizeBoxWithAspectRatio,
  updateCanvasCursor,
} from './js/canvas_engine.js';
import {
  clamp,
  debounce,
  escapeHtml,
  formatSecondsMMSS,
  formatTimeRange,
  getSafeCutDownloadUrl,
  makeEven,
  showToast,
} from './js/utils.js';

// Estado Global da Aplicação
const state = {
  activeTab: 'studio',        // 'studio' | 'library'
  config: null,
  isAnalyzing: false,
  isRendering: false,
  activeJobId: null,
  jobPoller: null,

  currentVideo: null,
  cuts: [],
  activeCutIndex: 0,

  defaultProvider: 'gemini',
  maxCuts: 5,
};

const canvasState = createCanvasState();

// =========================================================
// INICIALIZAÇÃO
// =========================================================
function boot() {
  initDOM();
  initCanvas();
  fetchConfig();
}

if (document.readyState === 'loading') {
  window.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}

function initDOM() {
  document.getElementById('tab-btn-studio').addEventListener('click', () => switchTab('studio'));
  document.getElementById('tab-btn-library').addEventListener('click', () => switchTab('library'));

  document.getElementById('btn-open-folder').addEventListener('click', openCutsFolder);
  document.getElementById('btn-settings-modal').addEventListener('click', openSettingsModal);
  document.getElementById('modal-close-btn').addEventListener('click', closeSettingsModal);
  document.getElementById('modal-save-btn').addEventListener('click', saveSettingsModal);

  document.getElementById('btn-analyze').addEventListener('click', startAnalysis);
  document.getElementById('input-yt-url').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') startAnalysis();
  });

  document.getElementById('btn-new-video').addEventListener('click', resetToHero);
  document.getElementById('btn-render-selected').addEventListener('click', startRender);

  document.getElementById('active-cut-title').addEventListener('input', (e) => {
    const c = getActiveCut();
    if (c) {
      c.title = e.target.value;
      schedulePersistCutEdits(c);
    }
    updateCutsSidebarUI();
  });

  document.getElementById('active-cut-start').addEventListener('change', (e) => {
    const c = getActiveCut();
    if (c) {
      c.start_time = Math.max(0, parseFloat(e.target.value) || 0);
      c.duration = Math.max(0.5, c.end_time - c.start_time);
      stopAndResetClipPreview();
      updateActiveCutToolbar();
      updateCutsSidebarUI();
      updateCutContextPanel(c);
      schedulePersistCutEdits(c);
    }
  });

  document.getElementById('active-cut-end').addEventListener('change', (e) => {
    const c = getActiveCut();
    if (c) {
      c.end_time = Math.max(c.start_time + 0.5, parseFloat(e.target.value) || 0);
      c.duration = Math.max(0.5, c.end_time - c.start_time);
      stopAndResetClipPreview();
      updateActiveCutToolbar();
      updateCutsSidebarUI();
      updateCutContextPanel(c);
      schedulePersistCutEdits(c);
    }
  });

  document.getElementById('btn-refresh-frame').addEventListener('click', refreshCutFrame);
  document.getElementById('toggle-burn-subs').addEventListener('change', (e) => {
    const c = getActiveCut();
    if (c) {
      c.burn_subtitles = e.target.checked;
      renderAllCanvases();
      schedulePersistCutEdits(c);
    }
  });

  document.querySelectorAll('.layout-btn').forEach((btn) => {
    btn.addEventListener('click', () => setLayoutMode(btn.dataset.mode));
  });

  document.getElementById('lib-search-input').addEventListener('input', debounce(loadLibraryHistory, 300));
  document.getElementById('lib-score-slider').addEventListener('input', (e) => {
    document.getElementById('lib-score-val').textContent = e.target.value;
    loadLibraryHistory();
  });
}

function initCanvas() {
  canvasState.editorCanvas = document.getElementById('editor-canvas');
  canvasState.editorCtx = canvasState.editorCanvas.getContext('2d');

  canvasState.mockupCanvas = document.getElementById('mockup-canvas');
  canvasState.mockupCtx = canvasState.mockupCanvas.getContext('2d');
  canvasState.mockupCanvas.width = 1080;
  canvasState.mockupCanvas.height = 1920;

  initBlurBuffer(canvasState);

  const vid = document.createElement('video');
  vid.crossOrigin = 'anonymous';
  vid.playsInline = true;
  vid.preload = 'auto';
  vid.loop = true;

  vid.addEventListener('timeupdate', () => {
    updateClipPlayerTimelineUI();
    if (!canvasState.isVideoPlaying) {
      renderAllCanvases();
    }
  });

  vid.addEventListener('play', () => {
    canvasState.isVideoPlaying = true;
    updateClipPlayerButtonsUI();
    startVideoCanvasLoop();
  });

  vid.addEventListener('pause', () => {
    canvasState.isVideoPlaying = false;
    updateClipPlayerButtonsUI();
    stopVideoCanvasLoop();
    renderAllCanvases();
  });

  vid.addEventListener('ended', () => {
    canvasState.isVideoPlaying = false;
    updateClipPlayerButtonsUI();
    stopVideoCanvasLoop();
    renderAllCanvases();
  });

  canvasState.videoEl = vid;

  const canvas = canvasState.editorCanvas;
  canvas.addEventListener('mousedown', onCanvasMouseDown);
  window.addEventListener('mousemove', onCanvasMouseMove);
  window.addEventListener('mouseup', onCanvasMouseUp);
}

// =========================================================
// API & CONFIGURAÇÃO
// =========================================================
async function fetchConfig() {
  try {
    const { data } = await apiGetConfig();
    state.config = data;
    updateConfigModalUI(data);

    if (data.default_provider) {
      state.defaultProvider = data.default_provider;
      const select = document.getElementById('select-hero-provider');
      if (select) select.value = data.default_provider;
    }
  } catch (err) {
    console.error('Falha ao obter configuração:', err);
  }
}

function updateConfigModalUI(data) {
  const gTag = document.getElementById('cfg-status-groq');
  const mTag = document.getElementById('cfg-status-gemini');
  const fTag = document.getElementById('cfg-status-ffmpeg');
  const cTag = document.getElementById('cfg-status-cookies');

  gTag.className = data.keys.GROQ_API_KEY ? 'status-tag-ok' : 'status-tag-off';
  gTag.textContent = data.keys.GROQ_API_KEY ? 'Conectado' : 'Ausente';

  mTag.className = data.keys.GEMINI_API_KEY ? 'status-tag-ok' : 'status-tag-off';
  mTag.textContent = data.keys.GEMINI_API_KEY ? 'Conectado' : 'Ausente';

  fTag.className = data.keys.FFMPEG_AVAILABLE ? 'status-tag-ok' : 'status-tag-off';
  fTag.textContent = data.keys.FFMPEG_AVAILABLE ? 'Pronto' : 'Ausente';

  if (cTag) {
    cTag.className = data.keys.COOKIES_TXT ? 'status-tag-ok' : 'status-tag-off';
    cTag.textContent = data.keys.COOKIES_TXT ? 'Carregado' : 'Opcional (Não detectado)';
  }
}

async function openCutsFolder() {
  try {
    const { data } = await apiOpenFolder();
    if (data.success) {
      showToast('Pasta data/cortes/ aberta no Explorador de Arquivos!');
    }
  } catch (err) {
    alert('Erro ao abrir pasta: ' + err);
  }
}

// =========================================================
// NAVEGAÇÃO DE ABAS & MODAL
// =========================================================
function switchTab(tabName) {
  state.activeTab = tabName;
  if (tabName !== 'studio' && canvasState.videoEl && !canvasState.videoEl.paused) {
    canvasState.videoEl.pause();
  }

  document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach((p) => p.classList.remove('active'));

  if (tabName === 'studio') {
    document.getElementById('tab-btn-studio').classList.add('active');
    document.getElementById('tab-studio').classList.add('active');
  } else {
    document.getElementById('tab-btn-library').classList.add('active');
    document.getElementById('tab-library').classList.add('active');
    loadLibraryHistory();
  }
}

function openSettingsModal() {
  fetchConfig();
  document.getElementById('modal-provider-select').value = state.defaultProvider;
  document.getElementById('modal-maxcuts-input').value = state.maxCuts;
  document.getElementById('settings-modal').style.display = 'flex';
}

function closeSettingsModal() {
  document.getElementById('settings-modal').style.display = 'none';
}

function saveSettingsModal() {
  state.defaultProvider = document.getElementById('modal-provider-select').value;
  state.maxCuts = parseInt(document.getElementById('modal-maxcuts-input').value, 10) || 5;
  document.getElementById('select-hero-provider').value = state.defaultProvider;
  closeSettingsModal();
  showToast('Configurações salvas!');
}

// =========================================================
// ESTEIRA DE ANÁLISE & POLLING
// =========================================================
async function startAnalysis() {
  if (state.isAnalyzing) return;

  const urlInput = document.getElementById('input-yt-url');
  let url = urlInput ? urlInput.value.trim() : '';
  const provider = document.getElementById('select-hero-provider')?.value || 'gemini';

  if (!url) {
    alert('Por favor, cole a URL de um vídeo do YouTube.');
    return;
  }

  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    url = 'https://' + url;
    if (urlInput) urlInput.value = url;
  }

  state.isAnalyzing = true;
  const btnAnalyze = document.getElementById('btn-analyze');
  if (btnAnalyze) {
    btnAnalyze.disabled = true;
    btnAnalyze.style.opacity = '0.55';
    btnAnalyze.style.cursor = 'not-allowed';
  }

  const progressCard = document.getElementById('progress-card');
  if (progressCard) progressCard.style.display = 'block';

  const stepText = document.getElementById('progress-step-text');
  if (stepText) stepText.textContent = 'Enviando link para o estúdio...';
  const pctText = document.getElementById('progress-pct-text');
  if (pctText) pctText.textContent = '5%';
  const lineFill = document.getElementById('progress-line-fill');
  if (lineFill) lineFill.style.width = '5%';

  updateStepTracker(1);

  try {
    const { res, data } = await apiStartAnalyze(url, provider, state.maxCuts);
    if (res.ok && data.job_id) {
      state.activeJobId = data.job_id;
      pollJob(data.job_id, onAnalysisSuccess, onAnalysisError);
    } else {
      throw new Error(data.detail || 'Falha ao iniciar análise no servidor.');
    }
  } catch (err) {
    alert('Erro ao analisar vídeo: ' + err.message);
    resetHeroUI();
  }
}

function pollJob(jobId, onSuccess, onError) {
  if (state.jobPoller) {
    clearInterval(state.jobPoller);
    state.jobPoller = null;
  }

  let isStopped = false;
  let consecutiveErrors = 0;
  const MAX_CONSECUTIVE_ERRORS = 5;

  const stopPolling = () => {
    isStopped = true;
    if (state.jobPoller) {
      clearInterval(state.jobPoller);
      state.jobPoller = null;
    }
  };

  state.jobPoller = setInterval(async () => {
    if (isStopped) return;
    try {
      const { res, data: job } = await apiGetJob(jobId);
      if (!res.ok) {
        if (res.status === 404) {
          stopPolling();
          onError('O job não foi encontrado no servidor (o servidor pode ter sido reiniciado).');
          return;
        }
        throw new Error(`Erro na comunicação com o servidor (${res.status})`);
      }

      consecutiveErrors = 0;
      if (isStopped || !job) return;

      const stepLabel = job.step || 'Processando...';
      const pct = typeof job.progress === 'number' ? job.progress : 0;

      const heroStep = document.getElementById('progress-step-text');
      const heroPct = document.getElementById('progress-pct-text');
      const heroFill = document.getElementById('progress-line-fill');
      if (heroStep) heroStep.textContent = stepLabel;
      if (heroPct) heroPct.textContent = `${pct}%`;
      if (heroFill) heroFill.style.width = `${pct}%`;

      const renderStep = document.getElementById('render-progress-step');
      const renderPct = document.getElementById('render-progress-pct');
      const renderFill = document.getElementById('render-progress-fill');
      if (renderStep) renderStep.textContent = stepLabel;
      if (renderPct) renderPct.textContent = `${pct}%`;
      if (renderFill) renderFill.style.width = `${pct}%`;

      if (pct < 30) updateStepTracker(1);
      else if (pct < 55) updateStepTracker(2);
      else if (pct < 80) updateStepTracker(3);
      else updateStepTracker(4);

      const status = (job.status || '').toLowerCase().trim();
      if (status === 'completed' || status === 'done') {
        stopPolling();
        onSuccess(job.result);
      } else if (status === 'error' || status === 'failed') {
        stopPolling();
        onError(job.error || job.step || 'Erro durante o processamento.');
      }
    } catch (err) {
      consecutiveErrors += 1;
      console.error('Erro de polling:', err);
      if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
        stopPolling();
        onError(`Falha persistente de comunicação com o servidor: ${err.message}`);
      }
    }
  }, 1000);
}

function updateStepTracker(activeStepNum) {
  for (let i = 1; i <= 4; i++) {
    const item = document.getElementById(`step-track-${i}`);
    if (!item) continue;
    item.classList.remove('active', 'done');
    if (i < activeStepNum) item.classList.add('done');
    else if (i === activeStepNum) item.classList.add('active');
  }
}

function onAnalysisSuccess(result) {
  state.currentVideo = result;
  state.cuts = result.cortes || [];
  state.activeCutIndex = 0;
  state.isAnalyzing = false;

  const btnAnalyze = document.getElementById('btn-analyze');
  if (btnAnalyze) {
    btnAnalyze.disabled = false;
    btnAnalyze.style.opacity = '1';
    btnAnalyze.style.cursor = 'pointer';
  }
  const progressCard = document.getElementById('progress-card');
  if (progressCard) progressCard.style.display = 'none';

  document.getElementById('hero-section').style.display = 'none';
  document.getElementById('workspace-section').style.display = 'grid';

  initWorkspaceData();
}

function onAnalysisError(errorMsg) {
  resetHeroUI();
  alert('Erro na esteira de IA: ' + errorMsg);
}

function resetHeroUI() {
  state.isAnalyzing = false;
  const btnAnalyze = document.getElementById('btn-analyze');
  if (btnAnalyze) {
    btnAnalyze.disabled = false;
    btnAnalyze.style.opacity = '1';
    btnAnalyze.style.cursor = 'pointer';
  }
  const progressCard = document.getElementById('progress-card');
  if (progressCard) progressCard.style.display = 'none';
}

function resetToHero() {
  stopAndResetClipPreview();
  state.currentVideo = null;
  state.cuts = [];
  document.getElementById('input-yt-url').value = '';
  document.getElementById('workspace-section').style.display = 'none';
  document.getElementById('hero-section').style.display = 'flex';
  resetHeroUI();
}

// =========================================================
// WORKSPACE MASTER-DETAIL
// =========================================================
function initWorkspaceData() {
  state.cuts.forEach((c) => {
    if (!c.layout_params) c.layout_params = {};
    if (!c.layout_mode) {
      c.layout_mode = (c.tipo === 'longo' || c.duration >= 180) ? 'horizontal_16_9' : 'vertical_blur';
    }
  });

  updateCutsSidebarUI();
  selectCut(0);
}

function updateCutsSidebarUI() {
  const container = document.getElementById('cuts-list-container');
  container.replaceChildren();

  const totalCuts = state.cuts.length;
  document.getElementById('cuts-count-header').textContent = `Cortes Sugeridos (${totalCuts})`;

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
    checkbox.addEventListener('change', (e) => toggleCutSelection(idx, e.target.checked));

    const idxSpan = document.createElement('span');
    idxSpan.textContent = `#${idx + 1}`;
    checkLabel.append(checkbox, idxSpan);

    const actionsGroup = document.createElement('div');
    actionsGroup.style.display = 'flex';
    actionsGroup.style.alignItems = 'center';
    actionsGroup.style.gap = '4px';

    const playBtn = document.createElement('button');
    playBtn.className = 'cut-card-play-mini';
    playBtn.title = 'Assistir este corte no Estúdio';
    playBtn.textContent = '▶ Ver';
    playBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      selectAndPlayCut(idx);
    });

    const scoreBadge = document.createElement('span');
    scoreBadge.className = scoreBadgeClass;
    scoreBadge.textContent = `⭐ ${score}`;
    actionsGroup.append(playBtn, scoreBadge);

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

function toggleCutSelection(idx, isSelected) {
  if (state.cuts[idx]) {
    state.cuts[idx].selected = isSelected;
    updateRenderButtonState();
  }
}

function updateRenderButtonState() {
  const count = state.cuts.filter((c) => c.selected).length;
  const btn = document.getElementById('btn-render-selected');
  btn.disabled = (count === 0 || state.isRendering);
  btn.innerHTML = `
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
    Renderizar ${count} Selecionado${count !== 1 ? 's' : ''}
  `;
}

function selectCut(idx) {
  if (idx < 0 || idx >= state.cuts.length) return;
  state.activeCutIndex = idx;

  stopAndResetClipPreview();

  const cards = document.querySelectorAll('.cut-item-card');
  cards.forEach((c, i) => {
    c.classList.toggle('active', i === idx);
  });

  const activeCut = getActiveCut();
  updateActiveCutToolbar();
  updateCutContextPanel(activeCut);
  loadCutFrame(activeCut);
}

function selectAndPlayCut(idx) {
  if (idx < 0 || idx >= state.cuts.length) return;
  if (state.activeCutIndex !== idx) {
    selectCut(idx);
  }
  togglePlayClipPreview();
}

function getActiveCut() {
  return state.cuts[state.activeCutIndex] || null;
}

function updateActiveCutToolbar() {
  const c = getActiveCut();
  if (!c) return;

  document.getElementById('active-cut-title').value = c.title || '';
  document.getElementById('active-cut-start').value = c.start_time.toFixed(1);
  document.getElementById('active-cut-end').value = c.end_time.toFixed(1);
  document.getElementById('toggle-burn-subs').checked = !!c.burn_subtitles;

  document.querySelectorAll('.layout-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.mode === c.layout_mode);
  });

  updateSubcontrolsBar(c);
}

function updateSubcontrolsBar(cut) {
  const bar = document.getElementById('subcontrols-bar');
  const mode = cut.layout_mode;

  if (mode === 'vertical_blur') {
    const zoom = Number(cut.layout_params?.zoom_factor) || 1.0;
    bar.innerHTML = `
      <div style="display: flex; align-items: center; gap: 1rem; width: 100%;">
        <span>Canvas 9:16 com fundo desfocado e vídeo 16:9 nítido centralizado.</span>
        <div style="display: flex; align-items: center; gap: 0.5rem; margin-left: auto;">
          <span>Zoom: <strong id="lbl-zoom-val" class="highlight-indigo">${zoom.toFixed(2)}x</strong></span>
          <input type="range" min="1.0" max="1.3" step="0.05" value="${zoom}" style="width: 140px;" oninput="onZoomSliderChange(this.value)">
        </div>
      </div>
    `;
  } else if (mode === 'manual_crop') {
    bar.innerHTML = `
      <div style="display: flex; align-items: center; gap: 0.5rem;">
        <span class="highlight-indigo">Caixa 9:16 Arrastável:</span>
        <span>Clique e arraste dentro da caixa para mover; puxe os cantos para redimensionar com proporção travada.</span>
      </div>
    `;
  } else if (mode === 'split_screen') {
    bar.innerHTML = `
      <div style="display: flex; align-items: center; gap: 1rem;">
        <span><strong class="highlight-indigo">■ Topo (Índigo)</strong> e <strong class="highlight-amber">■ Base (Âmbar)</strong>:</span>
        <span>Arraste ou redimensione cada metade de forma independente. Proporção 9:8 (1080×960 cada).</span>
      </div>
    `;
  } else {
    bar.innerHTML = `
      <span>Modo Widescreen 16:9 Nativo. Sem corte ou distorção. Renderização limpa e ultrarrápida.</span>
    `;
  }
}

function setLayoutMode(mode) {
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

function onZoomSliderChange(val) {
  const c = getActiveCut();
  if (c && c.layout_mode === 'vertical_blur') {
    c.layout_params.zoom_factor = parseFloat(val);
    document.getElementById('lbl-zoom-val').textContent = `${parseFloat(val).toFixed(2)}x`;
    renderAllCanvases();
    schedulePersistCutEdits(c);
  }
}

// =========================================================
// FRAME & PLAYER DE CLIPE AO VIVO
// =========================================================
function updateCanvasPlaceholderUI(show) {
  const overlay = document.getElementById('canvas-placeholder-overlay');
  if (overlay) {
    overlay.style.display = show ? 'flex' : 'none';
  }
}

function loadCutFrame(cut) {
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

async function refreshCutFrame() {
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

function getCutSegments(cut) {
  if (!cut) return [];
  const allSegs = state.currentVideo?.transcript_segments || cut.segments || [];
  return allSegs.filter(
    (s) => s.end > cut.start_time && s.start < cut.end_time && (s.text || '').trim().length > 0
  );
}

function updateCutContextPanel(cut) {
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
    emptyHint.textContent = 'Clique em "Assistir Corte" para ver e ouvir o clipe no editor.';
    transBox.appendChild(emptyHint);
    return;
  }

  segs.forEach((s, i) => {
    const relSec = Math.max(0, (Number(s.start) || 0) - (Number(cut.start_time) || 0));
    const item = document.createElement('div');
    item.className = 'cut-seg-item';
    item.dataset.segIdx = String(i);
    item.dataset.relSec = relSec.toFixed(2);
    item.title = 'Clique para pular o vídeo para este trecho';
    item.addEventListener('click', () => seekClipToRelativeSec(Number(relSec.toFixed(2))));

    const timeSpan = document.createElement('span');
    timeSpan.className = 'cut-seg-time';
    timeSpan.textContent = `[${formatSecondsMMSS(s.start)}]`;

    const textSpan = document.createElement('span');
    textSpan.textContent = String(s.text || '');

    item.append(timeSpan, document.createTextNode(' '), textSpan);
    transBox.appendChild(item);
  });

  const timeDisp = document.getElementById('clip-time-display');
  if (timeDisp && !canvasState.isVideoReady) {
    timeDisp.textContent = `0:00 / ${formatSecondsMMSS(cut.duration)}`;
  }
}

function stopAndResetClipPreview() {
  stopVideoCanvasLoop();
  if (canvasState.videoEl) {
    canvasState.videoEl.pause();
    canvasState.videoEl.removeAttribute('src');
    canvasState.videoEl.load();
  }
  canvasState.isVideoReady = false;
  canvasState.isVideoPlaying = false;
  canvasState.isLoadingClip = false;
  canvasState.loadedClipKey = null;

  updateClipPlayerButtonsUI();
  const slider = document.getElementById('clip-seek-slider');
  if (slider) {
    slider.value = 0;
    slider.disabled = true;
  }
  const c = getActiveCut();
  const timeDisp = document.getElementById('clip-time-display');
  if (timeDisp) {
    timeDisp.textContent = `0:00 / ${c ? formatSecondsMMSS(c.duration) : '0:00'}`;
  }
}

async function togglePlayClipPreview() {
  const cut = getActiveCut();
  if (!cut || !state.currentVideo || canvasState.isLoadingClip) return;

  const vid = canvasState.videoEl;
  const clipKey = `${state.currentVideo.video_id}_${cut.start_time.toFixed(1)}_${cut.end_time.toFixed(1)}`;

  if (canvasState.isVideoReady && canvasState.loadedClipKey === clipKey && vid) {
    if (vid.paused) {
      vid.play().catch((err) => console.warn('Falha ao reproduzir vídeo:', err));
    } else {
      vid.pause();
    }
    return;
  }

  canvasState.isLoadingClip = true;
  updateClipPlayerButtonsUI();
  showToast('Preparando prévia em vídeo e áudio do corte...');

  try {
    const { res, data } = await apiExtractPreviewClip(
      state.currentVideo.video_id,
      state.currentVideo.url,
      cut.start_time,
      cut.end_time
    );

    if (!res.ok || !data.success || !data.clip_url) {
      throw new Error(data.detail || 'Não foi possível carregar o vídeo de prévia deste corte.');
    }

    await new Promise((resolve, reject) => {
      const onLoaded = () => {
        vid.removeEventListener('loadeddata', onLoaded);
        vid.removeEventListener('error', onError);
        resolve();
      };
      const onError = () => {
        vid.removeEventListener('loadeddata', onLoaded);
        vid.removeEventListener('error', onError);
        reject(new Error('Erro ao decodificar stream de vídeo no navegador.'));
      };
      vid.addEventListener('loadeddata', onLoaded);
      vid.addEventListener('error', onError);
      vid.src = data.clip_url;
      vid.load();
    });

    canvasState.loadedClipKey = clipKey;
    canvasState.isVideoReady = true;
    canvasState.isLoadingClip = false;

    if (!canvasState.imgLoaded && vid.videoWidth && vid.videoHeight) {
      canvasState.imgNaturalW = vid.videoWidth;
      canvasState.imgNaturalH = vid.videoHeight;
      canvasState.editorCanvas.width = vid.videoWidth;
      canvasState.editorCanvas.height = vid.videoHeight;
      initCutGeometry(cut, canvasState);
      updateCanvasPlaceholderUI(false);
    }

    const slider = document.getElementById('clip-seek-slider');
    if (slider) {
      slider.max = vid.duration || cut.duration || 30;
      slider.value = 0;
      slider.disabled = false;
    }

    updateClipPlayerButtonsUI();
    await vid.play();
  } catch (err) {
    canvasState.isLoadingClip = false;
    updateClipPlayerButtonsUI();
    alert('Erro ao reproduzir prévia do corte: ' + err.message);
  }
}

function startVideoCanvasLoop() {
  stopVideoCanvasLoop();
  const loop = () => {
    if (!canvasState.isVideoPlaying) return;
    renderAllCanvases();
    updateClipPlayerTimelineUI();
    canvasState.animFrameId = requestAnimationFrame(loop);
  };
  canvasState.animFrameId = requestAnimationFrame(loop);
}

function stopVideoCanvasLoop() {
  if (canvasState.animFrameId) {
    cancelAnimationFrame(canvasState.animFrameId);
    canvasState.animFrameId = null;
  }
}

function onClipSeekInput(val) {
  const vid = canvasState.videoEl;
  if (!vid || !canvasState.isVideoReady) return;
  vid.currentTime = parseFloat(val) || 0;
  updateClipPlayerTimelineUI();
  renderAllCanvases();
}

async function seekClipToRelativeSec(relSec) {
  const cut = getActiveCut();
  if (!cut) return;
  const clipKey = `${state.currentVideo?.video_id}_${cut.start_time.toFixed(1)}_${cut.end_time.toFixed(1)}`;
  if (!canvasState.isVideoReady || canvasState.loadedClipKey !== clipKey) {
    await togglePlayClipPreview();
  }
  if (canvasState.videoEl && canvasState.isVideoReady) {
    canvasState.videoEl.currentTime = Math.max(0, Math.min(canvasState.videoEl.duration || cut.duration, relSec));
    renderAllCanvases();
  }
}

function toggleMuteClipPreview() {
  const vid = canvasState.videoEl;
  if (!vid) return;
  vid.muted = !vid.muted;
  const btn = document.getElementById('btn-mute-clip');
  if (btn) btn.textContent = vid.muted ? '🔇' : '🔊';
}

function updateClipPlayerButtonsUI() {
  const btn = document.getElementById('btn-play-clip');
  const icon = document.getElementById('btn-play-clip-icon');
  const label = document.getElementById('btn-play-clip-label');
  if (!btn || !icon || !label) return;

  if (canvasState.isLoadingClip) {
    btn.disabled = true;
    icon.textContent = '⏳';
    label.textContent = 'Carregando Clipe...';
  } else if (canvasState.isVideoPlaying) {
    btn.disabled = false;
    icon.textContent = '⏸️';
    label.textContent = 'Pausar Corte';
  } else {
    btn.disabled = false;
    icon.textContent = '▶️';
    label.textContent = canvasState.isVideoReady ? 'Continuar Corte' : 'Assistir Corte';
  }
}

function updateClipPlayerTimelineUI() {
  const vid = canvasState.videoEl;
  const cut = getActiveCut();
  if (!vid || !cut) return;

  const cur = vid.currentTime || 0;
  const tot = vid.duration || cut.duration || 0;

  const slider = document.getElementById('clip-seek-slider');
  if (slider && !slider.disabled) {
    slider.max = tot;
    slider.value = cur;
  }

  const timeDisp = document.getElementById('clip-time-display');
  if (timeDisp) {
    timeDisp.textContent = `${formatSecondsMMSS(cur)} / ${formatSecondsMMSS(tot)}`;
  }

  const absSec = cut.start_time + cur;
  const segs = getCutSegments(cut);
  const items = document.querySelectorAll('.cut-seg-item');
  items.forEach((el, idx) => {
    const s = segs[idx];
    const isActive = s && absSec >= s.start && absSec <= s.end;
    el.classList.toggle('active-seg', !!isActive);
  });
}

function renderAllCanvases() {
  const cut = getActiveCut();
  renderEditorCanvas(canvasState, cut);
  renderMockupCanvas(canvasState, cut, getCutSegments);
}

// =========================================================
// INTERAÇÃO DO MOUSE NO CANVAS
// =========================================================
function onCanvasMouseDown(e) {
  if (!canvasState.imgLoaded && !canvasState.isVideoReady) return;
  const cut = getActiveCut();
  if (!cut) return;

  const mode = cut.layout_mode;
  if (mode !== 'manual_crop' && mode !== 'split_screen') return;

  const { x, y } = getCanvasMouseCoords(canvasState.editorCanvas, e);
  canvasState.dragStartX = x;
  canvasState.dragStartY = y;

  if (mode === 'manual_crop') {
    const p = cut.layout_params;
    const box = { x: p.crop_x, y: p.crop_y, w: p.crop_w, h: p.crop_h };

    const handle = checkHandleHit(x, y, box);
    if (handle) {
      canvasState.isDragging = true;
      canvasState.dragTarget = 'manual_handle';
      canvasState.activeHandle = handle;
      canvasState.initialBox = { ...box };
      return;
    }

    if (isPointInsideBox(x, y, box)) {
      canvasState.isDragging = true;
      canvasState.dragTarget = 'manual_box';
      canvasState.initialBox = { ...box };
    }
  } else if (mode === 'split_screen') {
    const top = cut.layout_params.box_top;
    const bot = cut.layout_params.box_bottom;

    const topHandle = checkHandleHit(x, y, top);
    if (topHandle) {
      canvasState.isDragging = true;
      canvasState.dragTarget = 'top_handle';
      canvasState.activeHandle = topHandle;
      canvasState.initialBox = { ...top };
      return;
    }

    const botHandle = checkHandleHit(x, y, bot);
    if (botHandle) {
      canvasState.isDragging = true;
      canvasState.dragTarget = 'bot_handle';
      canvasState.activeHandle = botHandle;
      canvasState.initialBox = { ...bot };
      return;
    }

    if (isPointInsideBox(x, y, top)) {
      canvasState.isDragging = true;
      canvasState.dragTarget = 'top_box';
      canvasState.initialBox = { ...top };
      return;
    }

    if (isPointInsideBox(x, y, bot)) {
      canvasState.isDragging = true;
      canvasState.dragTarget = 'bot_box';
      canvasState.initialBox = { ...bot };
    }
  }
}

function onCanvasMouseMove(e) {
  if (!canvasState.imgLoaded && !canvasState.isVideoReady) return;
  const cut = getActiveCut();
  if (!cut) return;

  const mode = cut.layout_mode;
  const { x, y } = getCanvasMouseCoords(canvasState.editorCanvas, e);

  if (!canvasState.isDragging) {
    updateCanvasCursor(canvasState.editorCanvas, x, y, mode, cut);
    return;
  }

  const dx = x - canvasState.dragStartX;
  const dy = y - canvasState.dragStartY;
  const init = canvasState.initialBox;
  const W = canvasState.imgNaturalW;
  const H = canvasState.imgNaturalH;

  if (canvasState.dragTarget === 'manual_box') {
    cut.layout_params.crop_x = makeEven(Math.round(clamp(init.x + dx, 0, W - init.w)));
    cut.layout_params.crop_y = makeEven(Math.round(clamp(init.y + dy, 0, H - init.h)));
  } else if (canvasState.dragTarget === 'manual_handle') {
    resizeBoxWithAspectRatio(cut.layout_params, init, dx, dy, 9 / 16, canvasState.activeHandle, W, H, true);
  } else if (canvasState.dragTarget === 'top_box') {
    cut.layout_params.box_top.x = makeEven(Math.round(clamp(init.x + dx, 0, W - init.w)));
    cut.layout_params.box_top.y = makeEven(Math.round(clamp(init.y + dy, 0, H - init.h)));
  } else if (canvasState.dragTarget === 'top_handle') {
    resizeBoxWithAspectRatio(cut.layout_params.box_top, init, dx, dy, 1080 / 960, canvasState.activeHandle, W, H, false);
  } else if (canvasState.dragTarget === 'bot_box') {
    cut.layout_params.box_bottom.x = makeEven(Math.round(clamp(init.x + dx, 0, W - init.w)));
    cut.layout_params.box_bottom.y = makeEven(Math.round(clamp(init.y + dy, 0, H - init.h)));
  } else if (canvasState.dragTarget === 'bot_handle') {
    resizeBoxWithAspectRatio(cut.layout_params.box_bottom, init, dx, dy, 1080 / 960, canvasState.activeHandle, W, H, false);
  }

  renderAllCanvases();
}

function onCanvasMouseUp() {
  const wasDragging = canvasState.isDragging;
  canvasState.isDragging = false;
  canvasState.dragTarget = null;
  canvasState.activeHandle = null;
  if (wasDragging) {
    schedulePersistCutEdits(getActiveCut());
  }
}

// =========================================================
// RENDERIZAÇÃO & BIBLIOTECA
// =========================================================
async function startRender() {
  if (state.isRendering) return;
  if (canvasState.videoEl && !canvasState.videoEl.paused) {
    canvasState.videoEl.pause();
  }
  const selecionados = state.cuts.filter((c) => c.selected);
  if (selecionados.length === 0) {
    alert('Selecione pelo menos um corte para renderizar.');
    return;
  }

  selecionados.forEach((c) => {
    if (!c.layout_params) c.layout_params = {};
    if (!c.layout_params.ref_w) c.layout_params.ref_w = canvasState.imgNaturalW || 1920;
    if (!c.layout_params.ref_h) c.layout_params.ref_h = canvasState.imgNaturalH || 1080;
  });

  state.isRendering = true;
  updateRenderButtonState();

  const overlay = document.getElementById('render-progress-overlay');
  if (overlay) overlay.style.display = 'flex';
  const rStep = document.getElementById('render-progress-step');
  const rPct = document.getElementById('render-progress-pct');
  const rFill = document.getElementById('render-progress-fill');
  if (rStep) rStep.textContent = 'Iniciando download e renderização com FFmpeg...';
  if (rPct) rPct.textContent = '5%';
  if (rFill) rFill.style.width = '5%';

  try {
    const { res, data } = await apiStartRender(state.currentVideo.video_id, selecionados, 1080);
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
  switchTab('library');
}

function onRenderError(errorMsg) {
  state.isRendering = false;
  updateRenderButtonState();
  const overlay = document.getElementById('render-progress-overlay');
  if (overlay) overlay.style.display = 'none';
  alert('Erro durante a renderização: ' + errorMsg);
}

async function loadLibraryHistory() {
  const search = document.getElementById('lib-search-input')?.value || '';
  const minScore = parseFloat(document.getElementById('lib-score-slider')?.value) || 0;

  try {
    const { data: videos } = await apiGetHistory(search, minScore, 50, 0);
    const container = document.getElementById('library-cards-container');
    container.replaceChildren();

    if (!videos || videos.length === 0) {
      const emptyBox = document.createElement('div');
      emptyBox.style.gridColumn = '1 / -1';
      emptyBox.style.padding = '3rem';
      emptyBox.style.textAlign = 'center';
      emptyBox.style.color = 'var(--text-dim)';
      const emptyP = document.createElement('p');
      emptyP.style.fontSize = '1.1rem';
      emptyP.style.fontWeight = '500';
      emptyP.textContent = 'Nenhum vídeo processado encontrado na biblioteca.';
      emptyBox.appendChild(emptyP);
      container.appendChild(emptyBox);
      return;
    }

    videos.forEach((v) => {
      const card = document.createElement('div');
      card.className = 'video-card-hist';

      const header = document.createElement('div');
      header.className = 'video-card-hist-header';

      const displayTitle = String(v.title || v.url || '');
      const titleDiv = document.createElement('div');
      titleDiv.className = 'video-card-hist-title';
      titleDiv.title = displayTitle;
      titleDiv.textContent = `📹 ${displayTitle}`;

      const delBtn = document.createElement('button');
      delBtn.className = 'icon-btn';
      delBtn.style.color = 'var(--accent-red)';
      delBtn.style.width = '30px';
      delBtn.style.height = '30px';
      delBtn.title = 'Excluir vídeo';
      delBtn.addEventListener('click', () => deleteVideoItem(Number(v.id)));
      delBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';

      header.append(titleDiv, delBtn);

      const body = document.createElement('div');
      body.className = 'video-card-hist-body';

      const metaDiv = document.createElement('div');
      metaDiv.style.fontSize = '0.8rem';
      metaDiv.style.color = 'var(--text-dim)';
      metaDiv.textContent = `ID: ${v.id} | Duração: ${v.duration}s | Status: ${v.status}`;
      body.appendChild(metaDiv);

      if (v.cortes && v.cortes.length > 0) {
        v.cortes.forEach((c) => {
          const cutRow = document.createElement('div');
          cutRow.className = 'cut-row-hist';

          const infoDiv = document.createElement('div');
          infoDiv.className = 'cut-row-info';

          const cutTitleStrong = document.createElement('strong');
          cutTitleStrong.textContent = String(c.title || '');

          const cutMetaSpan = document.createElement('span');
          cutMetaSpan.style.fontSize = '0.75rem';
          cutMetaSpan.style.color = 'var(--text-muted)';
          cutMetaSpan.textContent = `⭐ ${c.virality_score}/100 | ${c.start_time}s - ${c.end_time}s`;

          infoDiv.append(cutTitleStrong, cutMetaSpan);

          const actionsDiv = document.createElement('div');
          actionsDiv.style.display = 'flex';
          actionsDiv.style.gap = '0.5rem';
          actionsDiv.style.alignItems = 'center';

          const safeVideoUrl = getSafeCutDownloadUrl(c.video_url);
          if (safeVideoUrl) {
            const dlLink = document.createElement('a');
            dlLink.className = 'action-btn';
            dlLink.style.height = '30px';
            dlLink.style.fontSize = '0.75rem';
            dlLink.href = safeVideoUrl;
            dlLink.setAttribute('download', '');
            dlLink.textContent = '⬇️ Baixar (.mp4)';
            actionsDiv.appendChild(dlLink);
          } else {
            const missingSpan = document.createElement('span');
            missingSpan.style.fontSize = '0.75rem';
            missingSpan.style.color = 'var(--accent-red)';
            missingSpan.textContent = 'Arquivo ausente';
            actionsDiv.appendChild(missingSpan);
          }

          if (c.id) {
            const delCutBtn = document.createElement('button');
            delCutBtn.className = 'icon-btn';
            delCutBtn.style.color = 'var(--accent-red)';
            delCutBtn.style.width = '28px';
            delCutBtn.style.height = '28px';
            delCutBtn.title = 'Excluir apenas este corte';
            delCutBtn.addEventListener('click', () => deleteCorteItem(Number(c.id)));
            delCutBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
            actionsDiv.appendChild(delCutBtn);
          }

          cutRow.append(infoDiv, actionsDiv);
          body.appendChild(cutRow);
        });
      } else {
        const noCutsP = document.createElement('p');
        noCutsP.style.fontSize = '0.8rem';
        noCutsP.style.color = 'var(--text-dim)';
        noCutsP.style.padding = '0.5rem 0';
        noCutsP.textContent = 'Nenhum corte deste vídeo atingiu a nota mínima.';
        body.appendChild(noCutsP);
      }

      card.append(header, body);
      container.appendChild(card);
    });
  } catch (err) {
    console.error('Erro ao carregar histórico:', err);
  }
}

async function deleteVideoItem(videoId) {
  const deleteFiles = confirm('Deseja também apagar os arquivos físicos (.mp4/.mp3) do disco?');
  try {
    const { data } = await apiDeleteVideo(videoId, deleteFiles);
    if (data.success) {
      showToast('Registro excluído com sucesso!');
      loadLibraryHistory();
    } else {
      alert('Erro ao excluir: ' + data.detail);
    }
  } catch (err) {
    alert('Erro ao excluir: ' + err.message);
  }
}

async function deleteCorteItem(corteId) {
  const deleteFile = confirm('Excluir este corte? Clique em OK para apagar também o arquivo .mp4 do disco (ou Cancelar para manter o arquivo).');
  try {
    const { res, data } = await apiDeleteCorte(corteId, deleteFile);
    if (res.ok && data.success) {
      showToast('Corte excluído com sucesso!');
      loadLibraryHistory();
    } else {
      alert('Erro ao excluir corte: ' + (data.detail || res.status));
    }
  } catch (err) {
    alert('Erro ao excluir corte: ' + err.message);
  }
}

const schedulePersistCutEdits = debounce(async (cut) => {
  if (!cut || !cut.id) return;
  if (typeof cut.end_time !== 'number' || typeof cut.start_time !== 'number' || cut.end_time <= cut.start_time) {
    return;
  }
  try {
    await apiPatchCorte(cut.id, {
      title: cut.title,
      start_time: cut.start_time,
      end_time: cut.end_time,
      layout_mode: cut.layout_mode,
      layout_params: cut.layout_params || {},
      burn_subtitles: Boolean(cut.burn_subtitles),
    });
  } catch (err) {
    console.warn('Falha ao salvar edição do corte em segundo plano:', err);
  }
}, 500);

// Expõe handlers chamados por atributos inline no HTML (retrocompatibilidade com index.html)
Object.assign(window, {
  resetToHero,
  startAnalysis,
  refreshCutFrame,
  togglePlayClipPreview,
  onClipSeekInput,
  toggleMuteClipPreview,
  onZoomSliderChange,
  escapeHtml,
  getSafeCutDownloadUrl,
});
