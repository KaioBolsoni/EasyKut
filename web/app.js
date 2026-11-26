/**
 * EasyKut Studio V3.0 — Application & Canvas Engine
 * HTML5 Canvas 60fps Interactive Framing & Live Phone Mockup Mirror
 */

// Estado Global da Aplicação
const state = {
  activeTab: 'studio',        // 'studio' | 'library'
  config: null,
  isAnalyzing: false,
  isRendering: false,
  activeJobId: null,
  jobPoller: null,
  
  // Dados do Vídeo Analisado
  currentVideo: null,
  cuts: [],
  activeCutIndex: 0,

  // Configurações do Usuário
  defaultProvider: 'gemini',
  maxCuts: 5,
};

// Canvas Engine State
const canvasState = {
  editorCanvas: null,
  editorCtx: null,
  mockupCanvas: null,
  mockupCtx: null,
  
  currentImg: null,
  imgLoaded: false,
  imgNaturalW: 1920,
  imgNaturalH: 1080,

  // Player de Vídeo Ao Vivo Integrado ao Canvas 60fps
  videoEl: null,
  isVideoReady: false,
  isVideoPlaying: false,
  isLoadingClip: false,
  animFrameId: null,
  loadedClipKey: null,

  // Interação do Mouse
  isDragging: false,
  dragTarget: null,       // 'manual_box' | 'manual_handle' | 'top_box' | 'top_handle' | 'bot_box' | 'bot_handle'
  activeHandle: null,     // 'nw' | 'ne' | 'sw' | 'se'
  dragStartX: 0,
  dragStartY: 0,
  initialBox: null,
  handleSize: 12,
};

// =========================================================
// INICIALIZAÇÃO
// =========================================================
window.addEventListener('DOMContentLoaded', () => {
  initDOM();
  initCanvas();
  fetchConfig();
});

function initDOM() {
  // Tabs
  document.getElementById('tab-btn-studio').addEventListener('click', () => switchTab('studio'));
  document.getElementById('tab-btn-library').addEventListener('click', () => switchTab('library'));

  // Topbar
  document.getElementById('btn-open-folder').addEventListener('click', openCutsFolder);
  document.getElementById('btn-settings-modal').addEventListener('click', openSettingsModal);
  document.getElementById('modal-close-btn').addEventListener('click', closeSettingsModal);
  document.getElementById('modal-save-btn').addEventListener('click', saveSettingsModal);

  // Hero Input
  document.getElementById('btn-analyze').addEventListener('click', startAnalysis);
  document.getElementById('input-yt-url').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') startAnalysis();
  });

  // Workspace
  document.getElementById('btn-new-video').addEventListener('click', resetToHero);
  document.getElementById('btn-render-selected').addEventListener('click', startRender);

  // Toolbar do Corte
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

  // Layout Buttons
  document.querySelectorAll('.layout-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const mode = btn.dataset.mode;
      setLayoutMode(mode);
    });
  });

  // Library Filters
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

  // Dimensões do Canvas do Mockup 9:16 (HD 1080x1920 nativo)
  canvasState.mockupCanvas.width = 1080;
  canvasState.mockupCanvas.height = 1920;

  // Inicializa Elemento de Vídeo HTML5 para reprodução 60fps no Canvas
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

  // Eventos de Mouse no Editor Canvas
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
    const res = await fetch('/api/config');
    const data = await res.json();
    state.config = data;
    
    // Atualiza modal de status
    updateConfigModalUI(data);

    // Ajusta provedor padrão
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

  if (data.keys.GROQ_API_KEY) {
    gTag.className = 'status-tag-ok'; gTag.textContent = 'Conectado';
  } else {
    gTag.className = 'status-tag-off'; gTag.textContent = 'Ausente';
  }

  if (data.keys.GEMINI_API_KEY) {
    mTag.className = 'status-tag-ok'; mTag.textContent = 'Conectado';
  } else {
    mTag.className = 'status-tag-off'; mTag.textContent = 'Ausente';
  }

  if (data.keys.FFMPEG_AVAILABLE) {
    fTag.className = 'status-tag-ok'; fTag.textContent = 'Pronto';
  } else {
    fTag.className = 'status-tag-off'; fTag.textContent = 'Ausente';
  }

  if (cTag) {
    if (data.keys.COOKIES_TXT) {
      cTag.className = 'status-tag-ok'; cTag.textContent = 'Carregado';
    } else {
      cTag.className = 'status-tag-off'; cTag.textContent = 'Opcional (Não detectado)';
    }
  }
}

async function openCutsFolder() {
  try {
    const res = await fetch('/api/open-folder', { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      showToast('Pasta data/cortes/ aberta no Explorador de Arquivos!');
    }
  } catch (err) {
    alert('Erro ao abrir pasta: ' + err);
  }
}

// =========================================================
// NAVEGAÇÃO DE ABAS
// =========================================================
function switchTab(tabName) {
  state.activeTab = tabName;
  if (tabName !== 'studio' && canvasState.videoEl && !canvasState.videoEl.paused) {
    canvasState.videoEl.pause();
  }
  
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));

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
  state.maxCuts = parseInt(document.getElementById('modal-maxcuts-input').value) || 5;
  document.getElementById('select-hero-provider').value = state.defaultProvider;
  closeSettingsModal();
  showToast('Configurações salvas!');
}

// =========================================================
// ESTEIRA DE ANÁLISE (HERO / INPUT)
// =========================================================
async function startAnalysis() {
  if (state.isAnalyzing) {
    return; // Impede pedidos duplicados em paralelo
  }

  const urlInput = document.getElementById('input-yt-url');
  let url = urlInput ? urlInput.value.trim() : '';
  const provider = document.getElementById('select-hero-provider')?.value || 'gemini';

  if (!url) {
    alert('Por favor, cole a URL de um vídeo do YouTube.');
    return;
  }

  // Previne erros caso a URL venha sem http(s)://
  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    url = 'https://' + url;
    if (urlInput) urlInput.value = url;
  }

  // Desativa imediatamente o botão para impedir envios simultâneos
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
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, provider, max_cuts: state.maxCuts })
    });
    const data = await res.json();

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
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) {
        if (res.status === 404) {
          stopPolling();
          onError('O job não foi encontrado no servidor (o servidor pode ter sido reiniciado).');
          return;
        }
        throw new Error(`Erro na comunicação com o servidor (${res.status})`);
      }

      consecutiveErrors = 0;
      const job = await res.json();
      if (isStopped) return;

      // Exibe na barra de progresso (Análise e Renderização) o texto real de job.step retornado pelo backend
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

      // Atualiza passos visuais
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
        const errMsg = job.error || job.step || 'Erro durante o processamento.';
        onError(errMsg);
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

  // Reativa o botão para futuras análises caso o usuário volte
  const btnAnalyze = document.getElementById('btn-analyze');
  if (btnAnalyze) {
    btnAnalyze.disabled = false;
    btnAnalyze.style.opacity = '1';
    btnAnalyze.style.cursor = 'pointer';
  }
  const progressCard = document.getElementById('progress-card');
  if (progressCard) progressCard.style.display = 'none';

  // Transição suave para o Workspace Master-Detail
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
  // Inicializa parâmetros geométricos para cada corte caso não existam
  state.cuts.forEach((c, idx) => {
    if (!c.layout_params) c.layout_params = {};
    if (!c.layout_mode) c.layout_mode = (c.tipo === 'longo' || c.duration >= 180) ? 'horizontal_16_9' : 'vertical_blur';
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
  const count = state.cuts.filter(c => c.selected).length;
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

  // Atualiza classe ativa na sidebar
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

  // Atualiza botões de Layout
  document.querySelectorAll('.layout-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === c.layout_mode);
  });

  updateSubcontrolsBar(c);
}

function updateSubcontrolsBar(cut) {
  const bar = document.getElementById('subcontrols-bar');
  const mode = cut.layout_mode;

  if (mode === 'vertical_blur') {
    const zoom = cut.layout_params?.zoom_factor || 1.0;
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
    // horizontal_16_9
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

  // Inicializa caixas se necessário
  initCutGeometry(c);

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
// CARREGAMENTO E ATUALIZAÇÃO DE FRAME
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

  // Se o frame_url for vazio ou nulo, utiliza thumbnail do vídeo como fallback antes de exibir placeholder
  if (!cut.frame_url) {
    if (state.currentVideo && state.currentVideo.thumbnail) {
      cut.frame_url = state.currentVideo.thumbnail;
    } else {
      canvasState.currentImg = null;
      canvasState.imgNaturalW = 1920;
      canvasState.imgNaturalH = 1080;
      canvasState.editorCanvas.width = 1920;
      canvasState.editorCanvas.height = 1080;
      initCutGeometry(cut);
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

    // Ajusta resolução do Editor Canvas
    canvasState.editorCanvas.width = canvasState.imgNaturalW;
    canvasState.editorCanvas.height = canvasState.imgNaturalH;

    // Inicializa geometria se necessário
    initCutGeometry(cut);

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
    initCutGeometry(cut);
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
    const res = await fetch('/api/frame', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_id: videoId, source, timestamp_sec: ts })
    });
    const data = await res.json();
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

function initCutGeometry(cut) {
  const W = canvasState.imgNaturalW || 1920;
  const H = canvasState.imgNaturalH || 1080;

  if (!cut.layout_params) cut.layout_params = {};

  // Se já existia uma referência de resolução diferente (ex: placeholder 1920x1080 -> frame/vídeo 1280x720), reescala as caixas proporcionalmente
  const prevW = cut.layout_params.ref_w;
  const prevH = cut.layout_params.ref_h;
  if (prevW && prevH && (prevW !== W || prevH !== H)) {
    const sx = W / prevW;
    const sy = H / prevH;
    if (cut.layout_params.crop_w && cut.layout_params.crop_h) {
      cut.layout_params.crop_h = makeEven(Math.min(H, Math.round(cut.layout_params.crop_h * sy)));
      cut.layout_params.crop_w = makeEven(Math.min(W, Math.round(cut.layout_params.crop_h * (9 / 16))));
      cut.layout_params.crop_x = makeEven(clamp(Math.round((cut.layout_params.crop_x || 0) * sx), 0, W - cut.layout_params.crop_w));
      cut.layout_params.crop_y = makeEven(clamp(Math.round((cut.layout_params.crop_y || 0) * sy), 0, H - cut.layout_params.crop_h));
    }
    if (cut.layout_params.box_top && cut.layout_params.box_bottom) {
      const target_ar = 1080 / 960;
      ['box_top', 'box_bottom'].forEach((k) => {
        const b = cut.layout_params[k];
        const nh = makeEven(Math.min(H, Math.round(b.h * sy)));
        const nw = makeEven(Math.min(W, Math.round(nh * target_ar)));
        b.w = nw;
        b.h = nh;
        b.x = makeEven(clamp(Math.round(b.x * sx), 0, W - nw));
        b.y = makeEven(clamp(Math.round(b.y * sy), 0, H - nh));
      });
    }
  }

  cut.layout_params.ref_w = W;
  cut.layout_params.ref_h = H;

  // 1. Manual Crop: 9:16
  if (!cut.layout_params.crop_w || !cut.layout_params.crop_h) {
    const crop_h = Math.round(H * 0.96);
    const crop_w = makeEven(Math.round(crop_h * (9 / 16)));
    const crop_x = makeEven(Math.round((W - crop_w) / 2));
    const crop_y = makeEven(Math.round((H - crop_h) / 2));
    cut.layout_params.crop_w = crop_w;
    cut.layout_params.crop_h = crop_h;
    cut.layout_params.crop_x = crop_x;
    cut.layout_params.crop_y = crop_y;
  }

  // 2. Split Screen: 9:8 (1080/960 = 1.125)
  if (!cut.layout_params.box_top || !cut.layout_params.box_bottom) {
    const target_ar = 1080 / 960; // 1.125
    const max_h = Math.min(H, Math.round(W / target_ar));
    const bh = makeEven(Math.round(max_h * 0.85));
    const bw = makeEven(Math.round(bh * target_ar));

    // Topo à esquerda, Base à direita
    const tx = makeEven(Math.max(0, Math.round(W * 0.1)));
    const ty = makeEven(Math.round((H - bh) / 2));
    const bx = makeEven(Math.min(W - bw, Math.round(W * 0.5)));
    const by = ty;

    cut.layout_params.box_top = { x: tx, y: ty, w: bw, h: bh };
    cut.layout_params.box_bottom = { x: bx, y: by, w: bw, h: bh };
  }
}

// =========================================================
// PLAYER DE VÍDEO AO VIVO DO CORTE & PAINEL DE CONTEXTO
// =========================================================
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

  if (!cut) {
    return;
  }

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

  // Atualiza display de tempo inicial na barra do player
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

  // Se o vídeo deste corte já está carregado, apenas alterna Play / Pause
  if (canvasState.isVideoReady && canvasState.loadedClipKey === clipKey && vid) {
    if (vid.paused) {
      vid.play().catch((err) => console.warn('Falha ao reproduzir vídeo:', err));
    } else {
      vid.pause();
    }
    return;
  }

  // Caso contrário, solicita a geração rápida do clipe de prévia (.mp4 com áudio) ao backend
  canvasState.isLoadingClip = true;
  updateClipPlayerButtonsUI();
  showToast('Preparando prévia em vídeo e áudio do corte...');

  try {
    const res = await fetch('/api/preview-clip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_id: state.currentVideo.video_id,
        source: state.currentVideo.url,
        start_time: cut.start_time,
        end_time: cut.end_time,
      }),
    });

    const data = await res.json();
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

    // Se ainda não tínhamos frame estático carregado, ajusta dimensões do canvas pelo vídeo
    if (!canvasState.imgLoaded && vid.videoWidth && vid.videoHeight) {
      canvasState.imgNaturalW = vid.videoWidth;
      canvasState.imgNaturalH = vid.videoHeight;
      canvasState.editorCanvas.width = vid.videoWidth;
      canvasState.editorCanvas.height = vid.videoHeight;
      initCutGeometry(cut);
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

  // Destaca a frase ativa na caixa de transcrição lateral
  const absSec = cut.start_time + cur;
  const segs = getCutSegments(cut);
  const items = document.querySelectorAll('.cut-seg-item');
  items.forEach((el, idx) => {
    const s = segs[idx];
    const isActive = s && absSec >= s.start && absSec <= s.end;
    el.classList.toggle('active-seg', !!isActive);
  });
}

function formatSecondsMMSS(sec) {
  const s = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, '0')}`;
}

function getActiveMediaSource() {
  if (canvasState.isVideoReady && canvasState.videoEl && canvasState.videoEl.readyState >= 2) {
    return {
      el: canvasState.videoEl,
      srcW: canvasState.videoEl.videoWidth || canvasState.imgNaturalW || 1920,
      srcH: canvasState.videoEl.videoHeight || canvasState.imgNaturalH || 1080,
    };
  }
  if (canvasState.imgLoaded && canvasState.currentImg) {
    return {
      el: canvasState.currentImg,
      srcW: canvasState.currentImg.naturalWidth || canvasState.imgNaturalW || 1920,
      srcH: canvasState.currentImg.naturalHeight || canvasState.imgNaturalH || 1080,
    };
  }
  return null;
}

// =========================================================
// RENDERIZAÇÃO 60FPS NOS CANVASES
// =========================================================
function renderAllCanvases() {
  renderEditorCanvas();
  renderMockupCanvas();
}

// 1. PALCO PRINCIPAL (EDITOR)
function renderEditorCanvas() {
  const ctx = canvasState.editorCtx;
  const cut = getActiveCut();
  const media = getActiveMediaSource();

  // Se nem o vídeo nem o frame estiverem carregados, desenha placeholder limpo no Canvas
  if (!media) {
    const W = canvasState.editorCanvas.width || 1920;
    const H = canvasState.editorCanvas.height || 1080;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#10141D';
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = '#232938';
    ctx.lineWidth = 4;
    ctx.strokeRect(20, 20, W - 40, H - 40);

    ctx.fillStyle = '#6B7280';
    ctx.font = 'bold 34px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Frame de prévia pendente', W / 2, H / 2 - 25);

    ctx.fillStyle = '#6366F1';
    ctx.font = '22px Inter, sans-serif';
    ctx.fillText('Clique em "Assistir Corte" ou "Carregar Frame" para visualizar', W / 2, H / 2 + 25);
    return;
  }

  const { el: srcEl, srcW, srcH } = media;
  const W = canvasState.imgNaturalW || 1920;
  const H = canvasState.imgNaturalH || 1080;
  const scaleX = srcW / W;
  const scaleY = srcH / H;

  ctx.clearRect(0, 0, W, H);

  // Desenha imagem ou frame do vídeo ao vivo
  ctx.drawImage(srcEl, 0, 0, W, H);

  if (!cut) return;
  const mode = cut.layout_mode;

  if (mode === 'manual_crop') {
    const p = cut.layout_params;
    const { crop_x: x, crop_y: y, crop_w: w, crop_h: h } = p;

    // Máscara escura fora do corte
    ctx.fillStyle = 'rgba(11, 13, 18, 0.7)';
    ctx.fillRect(0, 0, W, y);                     // Topo
    ctx.fillRect(0, y + h, W, H - (y + h));         // Base
    ctx.fillRect(0, y, x, h);                     // Esquerda
    ctx.fillRect(x + w, y, W - (x + w), h);         // Direita

    // Borda da Caixa 9:16
    ctx.strokeStyle = '#6366F1';
    ctx.lineWidth = 4;
    ctx.strokeRect(x, y, w, h);

    // Alças nos 4 cantos
    drawHandle(ctx, x, y);
    drawHandle(ctx, x + w, y);
    drawHandle(ctx, x, y + h);
    drawHandle(ctx, x + w, y + h);

    // Badge de proporção
    drawBadge(ctx, `9:16 (${w}×${h})`, x + 10, y + 26, '#6366F1');

  } else if (mode === 'split_screen') {
    const top = cut.layout_params.box_top;
    const bot = cut.layout_params.box_bottom;

    // Escurecimento suave geral
    ctx.fillStyle = 'rgba(11, 13, 18, 0.55)';
    ctx.fillRect(0, 0, W, H);

    // Redesenha as duas regiões nítidas escalando da fonte (vídeo ou imagem)
    ctx.drawImage(srcEl, top.x * scaleX, top.y * scaleY, top.w * scaleX, top.h * scaleY, top.x, top.y, top.w, top.h);
    ctx.drawImage(srcEl, bot.x * scaleX, bot.y * scaleY, bot.w * scaleX, bot.h * scaleY, bot.x, bot.y, bot.w, bot.h);

    // Caixa Topo (Índigo)
    ctx.strokeStyle = '#6366F1';
    ctx.lineWidth = 4;
    ctx.strokeRect(top.x, top.y, top.w, top.h);
    drawHandle(ctx, top.x, top.y, '#6366F1');
    drawHandle(ctx, top.x + top.w, top.y, '#6366F1');
    drawHandle(ctx, top.x, top.y + top.h, '#6366F1');
    drawHandle(ctx, top.x + top.w, top.y + top.h, '#6366F1');
    drawBadge(ctx, 'TOPO (1080×960)', top.x + 10, top.y + 26, '#6366F1');

    // Caixa Base (Âmbar)
    ctx.strokeStyle = '#F59E0B';
    ctx.lineWidth = 4;
    ctx.strokeRect(bot.x, bot.y, bot.w, bot.h);
    drawHandle(ctx, bot.x, bot.y, '#F59E0B');
    drawHandle(ctx, bot.x + bot.w, bot.y, '#F59E0B');
    drawHandle(ctx, bot.x, bot.y + bot.h, '#F59E0B');
    drawHandle(ctx, bot.x + bot.w, bot.y + bot.h, '#F59E0B');
    drawBadge(ctx, 'BASE (1080×960)', bot.x + 10, bot.y + 26, '#F59E0B');

  } else if (mode === 'vertical_blur') {
    // Guia central suave 9:16 indicando a área central
    const centerW = Math.round(H * (9 / 16));
    const centerX = Math.round((W - centerW) / 2);
    ctx.strokeStyle = 'rgba(99, 102, 241, 0.4)';
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 8]);
    ctx.strokeRect(centerX, 0, centerW, H);
    ctx.setLineDash([]);
  }
}

function drawHandle(ctx, x, y, color = '#6366F1') {
  const size = 16;
  ctx.fillStyle = '#FFFFFF';
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.rect(x - size / 2, y - size / 2, size, size);
  ctx.fill();
  ctx.stroke();
}

function drawBadge(ctx, text, x, y, bgColor = '#6366F1') {
  ctx.save();
  ctx.font = 'bold 16px Inter, sans-serif';
  const textW = ctx.measureText(text).width;
  ctx.fillStyle = bgColor;
  ctx.beginPath();
  ctx.roundRect(x, y - 18, textW + 16, 26, 4);
  ctx.fill();
  ctx.fillStyle = '#FFFFFF';
  ctx.fillText(text, x + 8, y);
  ctx.restore();
}

// 2. PRÉVIA FINAL AO VIVO (MOCKUP 9:16 EM TEMPO REAL)
function renderMockupCanvas() {
  const ctx = canvasState.mockupCtx;
  const mW = canvasState.mockupCanvas.width || 1080;
  const mH = canvasState.mockupCanvas.height || 1920;
  const cut = getActiveCut();
  const media = getActiveMediaSource();

  ctx.clearRect(0, 0, mW, mH);
  if (!cut) return;

  if (!media) {
    ctx.fillStyle = '#10141D';
    ctx.fillRect(0, 0, mW, mH);
    ctx.strokeStyle = '#232938';
    ctx.lineWidth = 4;
    ctx.strokeRect(16, 16, mW - 32, mH - 32);

    ctx.fillStyle = '#4B5563';
    ctx.font = 'bold 36px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Prévia Pendente', mW / 2, mH / 2);
    return;
  }

  const { el: srcEl, srcW, srcH } = media;
  const W = canvasState.imgNaturalW || 1920;
  const H = canvasState.imgNaturalH || 1080;
  const scaleX = srcW / W;
  const scaleY = srcH / H;
  const mode = cut.layout_mode;

  if (mode === 'manual_crop') {
    const { crop_x: x, crop_y: y, crop_w: w, crop_h: h } = cut.layout_params;
    // Espelha o recorte do canvas exatamente para 1080x1920
    ctx.drawImage(srcEl, x * scaleX, y * scaleY, w * scaleX, h * scaleY, 0, 0, mW, mH);

  } else if (mode === 'split_screen') {
    const top = cut.layout_params.box_top;
    const bot = cut.layout_params.box_bottom;

    // Metade Superior (0 -> 960)
    ctx.drawImage(srcEl, top.x * scaleX, top.y * scaleY, top.w * scaleX, top.h * scaleY, 0, 0, mW, 960);
    // Metade Inferior (960 -> 1920)
    ctx.drawImage(srcEl, bot.x * scaleX, bot.y * scaleY, bot.w * scaleX, bot.h * scaleY, 0, 960, mW, 960);

    // Divisória sutil entre as duas metades
    ctx.fillStyle = '#232938';
    ctx.fillRect(0, 958, mW, 4);

  } else if (mode === 'vertical_blur') {
    const zoom = cut.layout_params.zoom_factor || 1.0;

    // Fundo desfocado cobrindo todo o 1080x1920
    ctx.save();
    ctx.filter = 'blur(25px) brightness(0.8)';
    const scale = Math.max(mW / srcW, mH / srcH);
    const bgW = srcW * scale;
    const bgH = srcH * scale;
    ctx.drawImage(srcEl, (mW - bgW) / 2, (mH - bgH) / 2, bgW, bgH);
    ctx.restore();

    // Primeiro plano 16:9 nítido centralizado com zoom_factor
    const fgW = mW * zoom;
    const fgH = (mW * (srcH / srcW)) * zoom;
    const fgX = (mW - fgW) / 2;
    const fgY = (mH - fgH) / 2;
    ctx.drawImage(srcEl, fgX, fgY, fgW, fgH);

  } else {
    // horizontal_16_9 no mockup 9:16 (letterbox limpo)
    ctx.fillStyle = '#0B0D12';
    ctx.fillRect(0, 0, mW, mH);
    const scale = mW / srcW;
    const h = srcH * scale;
    ctx.drawImage(srcEl, 0, (mH - h) / 2, mW, h);
  }

  // Legendas sincronizadas ao vivo caso ativadas
  if (cut.burn_subtitles) {
    drawMockupSubtitles(ctx, mW, mH, cut);
  }
}

function drawMockupSubtitles(ctx, mW, mH, cut) {
  let activeText = 'EXEMPLO DE LEGENDA DINÂMICA';
  const segs = getCutSegments(cut);

  if (segs.length > 0) {
    if (canvasState.isVideoReady && canvasState.videoEl) {
      const absSec = cut.start_time + (canvasState.videoEl.currentTime || 0);
      const found = segs.find((s) => absSec >= s.start && absSec <= s.end);
      activeText = found ? found.text : '';
    } else {
      activeText = segs[0].text;
    }
  }

  if (!activeText) return;

  ctx.save();
  ctx.font = 'bold 38px Inter, Arial, sans-serif';
  ctx.textAlign = 'center';
  ctx.strokeStyle = '#000000';
  ctx.lineWidth = 9;
  ctx.fillStyle = '#FFFFFF';

  // Quebra automática em até 2 linhas para caber perfeitamente na largura 1080px do Mockup
  const words = activeText.toUpperCase().split(/\s+/);
  const lines = [];
  let currentLine = '';
  const maxWidth = mW - 120;

  for (const word of words) {
    const testLine = currentLine ? `${currentLine} ${word}` : word;
    if (ctx.measureText(testLine).width > maxWidth && currentLine) {
      lines.push(currentLine);
      currentLine = word;
    } else {
      currentLine = testLine;
    }
  }
  if (currentLine) lines.push(currentLine);

  const visibleLines = lines.slice(0, 3);
  const baseY = mH - 220;
  const lineHeight = 48;

  visibleLines.forEach((line, i) => {
    const y = baseY + i * lineHeight;
    ctx.strokeText(line, mW / 2, y);
    ctx.fillText(line, mW / 2, y);
  });

  ctx.restore();
}

// =========================================================
// INTERAÇÃO DO MOUSE NO CANVAS (DRAG & RESIZE 60FPS)
// =========================================================
function getCanvasMouseCoords(e) {
  const canvas = canvasState.editorCanvas;
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  return {
    x: (e.clientX - rect.left) * scaleX,
    y: (e.clientY - rect.top) * scaleY,
  };
}

function onCanvasMouseDown(e) {
  if (!canvasState.imgLoaded && !canvasState.isVideoReady) return;
  const cut = getActiveCut();
  if (!cut) return;

  const mode = cut.layout_mode;
  if (mode !== 'manual_crop' && mode !== 'split_screen') return;

  const { x, y } = getCanvasMouseCoords(e);
  canvasState.dragStartX = x;
  canvasState.dragStartY = y;

  if (mode === 'manual_crop') {
    const p = cut.layout_params;
    const box = { x: p.crop_x, y: p.crop_y, w: p.crop_w, h: p.crop_h };

    // Checa clique nas 4 alças
    const handle = checkHandleHit(x, y, box);
    if (handle) {
      canvasState.isDragging = true;
      canvasState.dragTarget = 'manual_handle';
      canvasState.activeHandle = handle;
      canvasState.initialBox = { ...box };
      return;
    }

    // Checa clique dentro da caixa para arrastar
    if (isPointInsideBox(x, y, box)) {
      canvasState.isDragging = true;
      canvasState.dragTarget = 'manual_box';
      canvasState.initialBox = { ...box };
    }

  } else if (mode === 'split_screen') {
    const top = cut.layout_params.box_top;
    const bot = cut.layout_params.box_bottom;

    // Alças do Topo
    const topHandle = checkHandleHit(x, y, top);
    if (topHandle) {
      canvasState.isDragging = true;
      canvasState.dragTarget = 'top_handle';
      canvasState.activeHandle = topHandle;
      canvasState.initialBox = { ...top };
      return;
    }

    // Alças da Base
    const botHandle = checkHandleHit(x, y, bot);
    if (botHandle) {
      canvasState.isDragging = true;
      canvasState.dragTarget = 'bot_handle';
      canvasState.activeHandle = botHandle;
      canvasState.initialBox = { ...bot };
      return;
    }

    // Arrastar caixa do Topo
    if (isPointInsideBox(x, y, top)) {
      canvasState.isDragging = true;
      canvasState.dragTarget = 'top_box';
      canvasState.initialBox = { ...top };
      return;
    }

    // Arrastar caixa da Base
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
  const { x, y } = getCanvasMouseCoords(e);

  // Atualização do Cursor quando não estiver arrastando
  if (!canvasState.isDragging) {
    updateCanvasCursor(x, y, mode, cut);
    return;
  }

  // Execução do Arraste / Redimensionamento
  const dx = x - canvasState.dragStartX;
  const dy = y - canvasState.dragStartY;
  const init = canvasState.initialBox;
  const W = canvasState.imgNaturalW;
  const H = canvasState.imgNaturalH;

  if (canvasState.dragTarget === 'manual_box') {
    // Move a caixa 9:16 dentro dos limites do frame
    const newX = clamp(init.x + dx, 0, W - init.w);
    const newY = clamp(init.y + dy, 0, H - init.h);
    cut.layout_params.crop_x = makeEven(Math.round(newX));
    cut.layout_params.crop_y = makeEven(Math.round(newY));

  } else if (canvasState.dragTarget === 'manual_handle') {
    // Redimensiona mantendo a proporção 9:16 travada
    resizeBoxWithAspectRatio(cut.layout_params, init, dx, dy, 9 / 16, canvasState.activeHandle, W, H, true);

  } else if (canvasState.dragTarget === 'top_box') {
    const newX = clamp(init.x + dx, 0, W - init.w);
    const newY = clamp(init.y + dy, 0, H - init.h);
    cut.layout_params.box_top.x = makeEven(Math.round(newX));
    cut.layout_params.box_top.y = makeEven(Math.round(newY));

  } else if (canvasState.dragTarget === 'top_handle') {
    resizeBoxWithAspectRatio(cut.layout_params.box_top, init, dx, dy, 1080 / 960, canvasState.activeHandle, W, H, false);

  } else if (canvasState.dragTarget === 'bot_box') {
    const newX = clamp(init.x + dx, 0, W - init.w);
    const newY = clamp(init.y + dy, 0, H - init.h);
    cut.layout_params.box_bottom.x = makeEven(Math.round(newX));
    cut.layout_params.box_bottom.y = makeEven(Math.round(newY));

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

// Utilitário de redimensionamento proporcional
function resizeBoxWithAspectRatio(targetObj, init, dx, dy, aspectWtoH, handle, maxW, maxH, isCropCoords = false) {
  let newW = init.w;
  let newH = init.h;
  let newX = init.x;
  let newY = init.y;

  if (handle === 'se') {
    newW = Math.max(120, Math.min(maxW - init.x, init.w + dx));
    newH = Math.round(newW / aspectWtoH);
    if (init.y + newH > maxH) {
      newH = maxH - init.y;
      newW = Math.round(newH * aspectWtoH);
    }
  } else if (handle === 'nw') {
    newW = Math.max(120, Math.min(init.x + init.w, init.w - dx));
    newH = Math.round(newW / aspectWtoH);
    newX = init.x + (init.w - newW);
    newY = init.y + (init.h - newH);
    if (newX < 0 || newY < 0) return;
  } else if (handle === 'ne') {
    newW = Math.max(120, Math.min(maxW - init.x, init.w + dx));
    newH = Math.round(newW / aspectWtoH);
    newY = init.y + (init.h - newH);
    if (newY < 0) return;
  } else if (handle === 'sw') {
    newW = Math.max(120, Math.min(init.x + init.w, init.w - dx));
    newH = Math.round(newW / aspectWtoH);
    newX = init.x + (init.w - newW);
    if (newX < 0 || init.y + newH > maxH) return;
  }

  newW = makeEven(Math.round(newW));
  newH = makeEven(Math.round(newH));
  newX = makeEven(Math.round(newX));
  newY = makeEven(Math.round(newY));

  if (isCropCoords) {
    targetObj.crop_w = newW;
    targetObj.crop_h = newH;
    targetObj.crop_x = newX;
    targetObj.crop_y = newY;
  } else {
    targetObj.w = newW;
    targetObj.h = newH;
    targetObj.x = newX;
    targetObj.y = newY;
  }
}

function updateCanvasCursor(x, y, mode, cut) {
  const canvas = canvasState.editorCanvas;
  if (mode === 'manual_crop') {
    const p = cut.layout_params;
    const box = { x: p.crop_x, y: p.crop_y, w: p.crop_w, h: p.crop_h };
    const h = checkHandleHit(x, y, box);
    if (h === 'nw' || h === 'se') canvas.style.cursor = 'nwse-resize';
    else if (h === 'ne' || h === 'sw') canvas.style.cursor = 'nesw-resize';
    else if (isPointInsideBox(x, y, box)) canvas.style.cursor = 'move';
    else canvas.style.cursor = 'default';
  } else if (mode === 'split_screen') {
    const top = cut.layout_params.box_top;
    const bot = cut.layout_params.box_bottom;
    const ht = checkHandleHit(x, y, top);
    const hb = checkHandleHit(x, y, bot);
    if (ht || hb) canvas.style.cursor = 'pointer';
    else if (isPointInsideBox(x, y, top) || isPointInsideBox(x, y, bot)) canvas.style.cursor = 'move';
    else canvas.style.cursor = 'default';
  } else {
    canvas.style.cursor = 'default';
  }
}

function checkHandleHit(x, y, box) {
  const hitDist = 24;
  if (Math.hypot(x - box.x, y - box.y) <= hitDist) return 'nw';
  if (Math.hypot(x - (box.x + box.w), y - box.y) <= hitDist) return 'ne';
  if (Math.hypot(x - box.x, y - (box.y + box.h)) <= hitDist) return 'sw';
  if (Math.hypot(x - (box.x + box.w), y - (box.y + box.h)) <= hitDist) return 'se';
  return null;
}

function isPointInsideBox(px, py, box) {
  return px >= box.x && px <= box.x + box.w && py >= box.y && py <= box.y + box.h;
}

// =========================================================
// RENDERIZAÇÃO DE CORTES (POST /api/render)
// =========================================================
async function startRender() {
  if (state.isRendering) return;
  if (canvasState.videoEl && !canvasState.videoEl.paused) {
    canvasState.videoEl.pause();
  }
  const selecionados = state.cuts.filter(c => c.selected);
  if (selecionados.length === 0) {
    alert('Selecione pelo menos um corte para renderizar.');
    return;
  }

  // Garante que cada corte leve a resolução de referência do canvas para reescala exata no FFmpeg
  selecionados.forEach(c => {
    if (!c.layout_params) c.layout_params = {};
    if (!c.layout_params.ref_w) c.layout_params.ref_w = canvasState.imgNaturalW || 1920;
    if (!c.layout_params.ref_h) c.layout_params.ref_h = canvasState.imgNaturalH || 1080;
  });

  state.isRendering = true;
  updateRenderButtonState();

  // Exibe o modal dedicado de progresso da renderização visível sobre o Workspace
  const overlay = document.getElementById('render-progress-overlay');
  if (overlay) overlay.style.display = 'flex';
  const rStep = document.getElementById('render-progress-step');
  const rPct = document.getElementById('render-progress-pct');
  const rFill = document.getElementById('render-progress-fill');
  if (rStep) rStep.textContent = 'Iniciando download e renderização com FFmpeg...';
  if (rPct) rPct.textContent = '5%';
  if (rFill) rFill.style.width = '5%';

  try {
    const res = await fetch('/api/render', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_id: state.currentVideo.video_id,
        cuts: selecionados,
        max_resolution: 1080
      })
    });
    const data = await res.json();

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

function onRenderSuccess(result) {
  state.isRendering = false;
  updateRenderButtonState();
  const overlay = document.getElementById('render-progress-overlay');
  if (overlay) overlay.style.display = 'none';

  showToast('Cortes renderizados com sucesso!');
  // Transfere o usuário diretamente para a Biblioteca para visualizar os clipes
  switchTab('library');
}

function onRenderError(errorMsg) {
  state.isRendering = false;
  updateRenderButtonState();
  const overlay = document.getElementById('render-progress-overlay');
  if (overlay) overlay.style.display = 'none';
  alert('Erro durante a renderização: ' + errorMsg);
}

// =========================================================
// BIBLIOTECA (HISTÓRICO)
// =========================================================
async function loadLibraryHistory() {
  const search = document.getElementById('lib-search-input')?.value || '';
  const minScore = parseFloat(document.getElementById('lib-score-slider')?.value) || 0;

  try {
    const res = await fetch(`/api/history?search=${encodeURIComponent(search)}&min_score=${minScore}`);
    const videos = await res.json();
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

    videos.forEach(v => {
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
        v.cortes.forEach(c => {
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
    const res = await fetch(`/api/history/${videoId}?delete_files=${deleteFiles}`, { method: 'DELETE' });
    const data = await res.json();
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
    const res = await fetch(`/api/cortes/${corteId}?delete_file=${deleteFile}`, { method: 'DELETE' });
    const data = await res.json();
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
    await fetch(`/api/cortes/${cut.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: cut.title,
        start_time: cut.start_time,
        end_time: cut.end_time,
        layout_mode: cut.layout_mode,
        layout_params: cut.layout_params || {},
        burn_subtitles: Boolean(cut.burn_subtitles),
      }),
    });
  } catch (err) {
    console.warn('Falha ao salvar edição do corte em segundo plano:', err);
  }
}, 500);

// =========================================================
// HELPERS & UTILITÁRIOS
// =========================================================
function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function getSafeCutDownloadUrl(url) {
  if (typeof url !== 'string') return null;
  const trimmed = url.trim();
  if (!trimmed.startsWith('/data/cortes/')) return null;
  if (trimmed.includes('..') || /[\r\n\x00]/.test(trimmed)) return null;
  return trimmed;
}

function makeEven(val) {
  const n = Math.round(val);
  return n % 2 === 0 ? n : n - 1;
}

function clamp(val, min, max) {
  return Math.max(min, Math.min(max, val));
}

function formatTimeRange(start, end) {
  const sMin = Math.floor(start / 60);
  const sSec = Math.floor(start % 60);
  const eMin = Math.floor(end / 60);
  const eSec = Math.floor(end % 60);
  return `${sMin}:${sSec.toString().padStart(2, '0')} - ${eMin}:${eSec.toString().padStart(2, '0')}`;
}

function showToast(msg) {
  const existing = document.getElementById('easykut-toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.id = 'easykut-toast';
  toast.style.cssText = `
    position: fixed;
    bottom: 24px;
    right: 24px;
    background: #141821;
    color: #F3F4F6;
    border: 1px solid #6366F1;
    padding: 10px 18px;
    border-radius: 8px;
    font-size: 0.85rem;
    font-weight: 600;
    box-shadow: 0 10px 30px rgba(0,0,0,0.5);
    z-index: 999;
    transition: opacity 0.3s;
  `;
  toast.textContent = msg;
  document.body.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

function debounce(func, wait) {
  let timeout;
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout);
      func(...args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
}
