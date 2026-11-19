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
    if (c) c.title = e.target.value;
    updateCutsSidebarUI();
  });

  document.getElementById('active-cut-start').addEventListener('change', (e) => {
    const c = getActiveCut();
    if (c) {
      c.start_time = Math.max(0, parseFloat(e.target.value) || 0);
      c.duration = Math.max(0.5, c.end_time - c.start_time);
      updateActiveCutToolbar();
      updateCutsSidebarUI();
    }
  });

  document.getElementById('active-cut-end').addEventListener('change', (e) => {
    const c = getActiveCut();
    if (c) {
      c.end_time = Math.max(c.start_time + 0.5, parseFloat(e.target.value) || 0);
      c.duration = Math.max(0.5, c.end_time - c.start_time);
      updateActiveCutToolbar();
      updateCutsSidebarUI();
    }
  });

  document.getElementById('btn-refresh-frame').addEventListener('click', refreshCutFrame);
  document.getElementById('toggle-burn-subs').addEventListener('change', (e) => {
    const c = getActiveCut();
    if (c) {
      c.burn_subtitles = e.target.checked;
      renderAllCanvases();
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
  const urlInput = document.getElementById('input-yt-url');
  const url = urlInput.value.trim();
  const provider = document.getElementById('select-hero-provider').value;

  if (!url) {
    alert('Por favor, cole a URL de um vídeo do YouTube.');
    return;
  }

  state.isAnalyzing = true;
  document.getElementById('btn-analyze').disabled = true;
  document.getElementById('progress-card').style.display = 'block';

  updateStepTracker(1, 'Extraindo áudio e metadados...', 15);

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
      throw new Error(data.detail || 'Falha ao iniciar análise.');
    }
  } catch (err) {
    alert('Erro ao analisar vídeo: ' + err.message);
    resetHeroUI();
  }
}

function pollJob(jobId, onSuccess, onError) {
  if (state.jobPoller) clearInterval(state.jobPoller);

  state.jobPoller = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) {
        throw new Error(`Erro na comunicação com o servidor (${res.status})`);
      }
      const job = await res.json();

      // Exibe na barra de progresso o texto real de job.step retornado pelo backend
      if (job.step) {
        document.getElementById('progress-step-text').textContent = job.step;
      }
      const pct = typeof job.progress === 'number' ? job.progress : 0;
      document.getElementById('progress-pct-text').textContent = `${pct}%`;
      document.getElementById('progress-line-fill').style.width = `${pct}%`;

      // Atualiza passos visuais
      if (pct < 30) updateStepTracker(1);
      else if (pct < 55) updateStepTracker(2);
      else if (pct < 80) updateStepTracker(3);
      else updateStepTracker(4);

      const status = (job.status || '').toLowerCase().trim();
      if (status === 'completed' || status === 'done') {
        clearInterval(state.jobPoller);
        state.jobPoller = null;
        onSuccess(job.result);
      } else if (status === 'failed' || status === 'error') {
        clearInterval(state.jobPoller);
        state.jobPoller = null;
        const errMsg = job.error || job.step || 'Erro durante o processamento.';
        onError(errMsg);
      }
    } catch (err) {
      console.error('Erro de polling:', err);
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

  // Transição suave para o Workspace Master-Detail
  document.getElementById('hero-section').style.display = 'none';
  document.getElementById('workspace-section').style.display = 'grid';

  initWorkspaceData();
}

function onAnalysisError(errorMsg) {
  alert('Erro na esteira de IA: ' + errorMsg);
  resetHeroUI();
}

function resetHeroUI() {
  state.isAnalyzing = false;
  document.getElementById('btn-analyze').disabled = false;
  document.getElementById('progress-card').style.display = 'none';
}

function resetToHero() {
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
  container.innerHTML = '';

  const totalCuts = state.cuts.length;
  document.getElementById('cuts-count-header').textContent = `Cortes Sugeridos (${totalCuts})`;

  state.cuts.forEach((c, idx) => {
    const card = document.createElement('div');
    card.className = `cut-item-card ${idx === state.activeCutIndex ? 'active' : ''}`;
    card.onclick = () => selectCut(idx);

    const score = Math.round(c.virality_score || 80);
    const scoreBadgeClass = score >= 75 ? 'cut-score-badge' : 'cut-score-badge med';

    card.innerHTML = `
      <div class="cut-item-top">
        <label class="cut-check-label" onclick="event.stopPropagation()">
          <input type="checkbox" ${c.selected ? 'checked' : ''} onchange="toggleCutSelection(${idx}, this.checked)">
          <span>#${idx + 1}</span>
        </label>
        <span class="${scoreBadgeClass}">⭐ ${score}</span>
      </div>
      <div class="cut-title-text" title="${c.title}">${c.title}</div>
      <div class="cut-meta-text">
        <span>⏱️ ${c.duration.toFixed(1)}s</span>
        <span>${formatTimeRange(c.start_time, c.end_time)}</span>
      </div>
    `;

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

  // Atualiza classe ativa na sidebar
  const cards = document.querySelectorAll('.cut-item-card');
  cards.forEach((c, i) => {
    c.classList.toggle('active', i === idx);
  });

  updateActiveCutToolbar();
  loadCutFrame(getActiveCut());
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
}

function onZoomSliderChange(val) {
  const c = getActiveCut();
  if (c && c.layout_mode === 'vertical_blur') {
    c.layout_params.zoom_factor = parseFloat(val);
    document.getElementById('lbl-zoom-val').textContent = `${parseFloat(val).toFixed(2)}x`;
    renderAllCanvases();
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

  // Se o frame_url for vazio ou nulo, não quebra o editor: exibe placeholder no canvas
  if (!cut.frame_url) {
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

  showToast('Capturando frame no tempo exato...');
  const source = state.currentVideo.url;
  const ts = Math.max(0, c.start_time + 0.5);

  try {
    const res = await fetch('/api/frame', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, timestamp_sec: ts })
    });
    const data = await res.json();
    if (data.success && data.frame_url) {
      c.frame_url = data.frame_url;
      c.frame_path = data.frame_path;
      loadCutFrame(c);
      showToast('Frame atualizado com sucesso!');
    } else {
      showToast('Não foi possível capturar o frame. Tente novamente.');
    }
  } catch (err) {
    alert('Erro ao atualizar frame: ' + err.message);
  }
}

function initCutGeometry(cut) {
  const W = canvasState.imgNaturalW || 1920;
  const H = canvasState.imgNaturalH || 1080;

  if (!cut.layout_params) cut.layout_params = {};

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
// RENDERIZAÇÃO 60FPS NOS CANVASES
// =========================================================
function renderAllCanvases() {
  renderEditorCanvas();
  renderMockupCanvas();
}

// 1. PALCO PRINCIPAL (EDITOR)
function renderEditorCanvas() {
  const ctx = canvasState.editorCtx;
  const W = canvasState.editorCanvas.width || 1920;
  const H = canvasState.editorCanvas.height || 1080;
  const cut = getActiveCut();

  ctx.clearRect(0, 0, W, H);

  // Se o frame não estiver carregado, desenha placeholder limpo no Canvas
  if (!canvasState.imgLoaded || !canvasState.currentImg) {
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
    ctx.fillText('Clique em "Carregar Frame" para capturar a imagem deste corte', W / 2, H / 2 + 25);
    return;
  }

  const img = canvasState.currentImg;
  const W = canvasState.imgNaturalW;
  const H = canvasState.imgNaturalH;
  const cut = getActiveCut();

  ctx.clearRect(0, 0, W, H);

  // Desenha imagem original base
  ctx.drawImage(img, 0, 0, W, H);

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

    // Redesenha as duas regiões nítidas
    ctx.drawImage(img, top.x, top.y, top.w, top.h, top.x, top.y, top.w, top.h);
    ctx.drawImage(img, bot.x, bot.y, bot.w, bot.h, bot.x, bot.y, bot.w, bot.h);

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

  ctx.clearRect(0, 0, mW, mH);
  if (!cut) return;

  if (!canvasState.imgLoaded || !canvasState.currentImg) {
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

  const img = canvasState.currentImg;
  const mode = cut.layout_mode;

  if (mode === 'manual_crop') {
    const { crop_x: x, crop_y: y, crop_w: w, crop_h: h } = cut.layout_params;
    // Espelha o recorte do canvas exatamente para 1080x1920
    ctx.drawImage(img, x, y, w, h, 0, 0, mW, mH);

  } else if (mode === 'split_screen') {
    const top = cut.layout_params.box_top;
    const bot = cut.layout_params.box_bottom;

    // Metade Superior (0 -> 960)
    ctx.drawImage(img, top.x, top.y, top.w, top.h, 0, 0, mW, 960);
    // Metade Inferior (960 -> 1920)
    ctx.drawImage(img, bot.x, bot.y, bot.w, bot.h, 0, 960, mW, 960);

    // Divisória sutil entre as duas metades
    ctx.fillStyle = '#232938';
    ctx.fillRect(0, 958, mW, 4);

  } else if (mode === 'vertical_blur') {
    const zoom = cut.layout_params.zoom_factor || 1.0;

    // Fundo desfocado cobrindo todo o 1080x1920
    ctx.save();
    ctx.filter = 'blur(25px) brightness(0.8)';
    const scale = Math.max(mW / img.naturalWidth, mH / img.naturalHeight);
    const bgW = img.naturalWidth * scale;
    const bgH = img.naturalHeight * scale;
    ctx.drawImage(img, (mW - bgW) / 2, (mH - bgH) / 2, bgW, bgH);
    ctx.restore();

    // Primeiro plano 16:9 nítido centralizado com zoom_factor
    const fgW = mW * zoom;
    const fgH = (mW * (img.naturalHeight / img.naturalWidth)) * zoom;
    const fgX = (mW - fgW) / 2;
    const fgY = (mH - fgH) / 2;
    ctx.drawImage(img, fgX, fgY, fgW, fgH);

  } else {
    // horizontal_16_9 no mockup 9:16 (letterbox limpo)
    ctx.fillStyle = '#0B0D12';
    ctx.fillRect(0, 0, mW, mH);
    const scale = mW / img.naturalWidth;
    const h = img.naturalHeight * scale;
    ctx.drawImage(img, 0, (mH - h) / 2, mW, h);
  }

  // Simulação de Legendas caso ativadas
  if (cut.burn_subtitles) {
    drawMockupSubtitles(ctx, mW, mH);
  }
}

function drawMockupSubtitles(ctx, mW, mH) {
  ctx.save();
  ctx.font = 'bold 36px Inter, Arial, sans-serif';
  ctx.textAlign = 'center';
  const text = 'EXEMPLO DE LEGENDA DINÂMICA';
  const y = mH - 180;

  // Sombra / Contorno preto espesso
  ctx.strokeStyle = '#000000';
  ctx.lineWidth = 8;
  ctx.strokeText(text, mW / 2, y);

  // Texto branco brilhante
  ctx.fillStyle = '#FFFFFF';
  ctx.fillText(text, mW / 2, y);
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
  if (!canvasState.imgLoaded) return;
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
  if (!canvasState.imgLoaded) return;
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
  canvasState.isDragging = false;
  canvasState.dragTarget = null;
  canvasState.activeHandle = null;
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
  const selecionados = state.cuts.filter(c => c.selected);
  if (selecionados.length === 0) {
    alert('Selecione pelo menos um corte para renderizar.');
    return;
  }

  state.isRendering = true;
  updateRenderButtonState();

  // Exibe barra de progresso no topo da tela
  document.getElementById('progress-card').style.display = 'block';
  document.getElementById('hero-section').style.display = 'none';

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
    document.getElementById('progress-card').style.display = 'none';
  }
}

function onRenderSuccess(result) {
  state.isRendering = false;
  updateRenderButtonState();
  document.getElementById('progress-card').style.display = 'none';

  showToast('Cortes renderizados com sucesso!');
  // Transfere o usuário diretamente para a Biblioteca para visualizar os clipes
  switchTab('library');
}

function onRenderError(errorMsg) {
  alert('Erro durante a renderização: ' + errorMsg);
  state.isRendering = false;
  updateRenderButtonState();
  document.getElementById('progress-card').style.display = 'none';
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
    container.innerHTML = '';

    if (!videos || videos.length === 0) {
      container.innerHTML = `
        <div style="grid-column: 1 / -1; padding: 3rem; text-align: center; color: var(--text-dim);">
          <p style="font-size: 1.1rem; font-weight: 500;">Nenhum vídeo processado encontrado na biblioteca.</p>
        </div>
      `;
      return;
    }

    videos.forEach(v => {
      const card = document.createElement('div');
      card.className = 'video-card-hist';

      let cutsHTML = '';
      if (v.cortes && v.cortes.length > 0) {
        v.cortes.forEach(c => {
          cutsHTML += `
            <div class="cut-row-hist">
              <div class="cut-row-info">
                <strong>${c.title}</strong>
                <span style="font-size: 0.75rem; color: var(--text-muted);">⭐ ${c.virality_score}/100 | ${c.start_time}s - ${c.end_time}s</span>
              </div>
              <div style="display: flex; gap: 0.5rem; align-items: center;">
                ${c.video_url ? `
                  <a class="action-btn" style="height: 30px; font-size: 0.75rem;" href="${c.video_url}" download>⬇️ Baixar (.mp4)</a>
                ` : '<span style="font-size: 0.75rem; color: var(--accent-red);">Arquivo ausente</span>'}
              </div>
            </div>
          `;
        });
      } else {
        cutsHTML = `<p style="font-size: 0.8rem; color: var(--text-dim); padding: 0.5rem 0;">Nenhum corte deste vídeo atingiu a nota mínima.</p>`;
      }

      card.innerHTML = `
        <div class="video-card-hist-header">
          <div class="video-card-hist-title" title="${v.title || v.url}">
            📹 ${v.title || v.url}
          </div>
          <button class="icon-btn" style="color: var(--accent-red); width: 30px; height: 30px;" onclick="deleteVideoItem(${v.id})" title="Excluir vídeo">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
          </button>
        </div>
        <div class="video-card-hist-body">
          <div style="font-size: 0.8rem; color: var(--text-dim);">ID: ${v.id} | Duração: ${v.duration}s | Status: ${v.status}</div>
          ${cutsHTML}
        </div>
      `;

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

// =========================================================
// HELPERS & UTILITÁRIOS
// =========================================================
function makeEven(val) {
  return val % 2 === 0 ? val : val - 1;
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
