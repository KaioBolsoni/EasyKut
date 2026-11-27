/**
 * EasyKut Studio V4.0 — Bootstrap Modular (ES Module Entry Point)
 * Conecta eventos do DOM aos sub-módulos especializados sem poluir `window`.
 */

import { apiCancelJob, apiOpenFolder } from './js/api.js';
import { initBlurBuffer } from './js/canvas_engine.js';
import { bindCanvasInteractions } from './js/canvas_interactions.js';
import {
  initClipPlayer,
  onClipSeekInput,
  toggleMuteClipPreview,
  togglePlayClipPreview,
} from './js/clip_player.js';
import { initLibraryView, loadLibraryHistory } from './js/library_view.js';
import { bindProPanelsEvents, refreshProResources } from './js/pro_panels.js';
import {
  closeSettingsModal,
  fetchConfigAndHealth,
  openSettingsModal,
  runManualCleanup,
  saveSettingsModal,
} from './js/settings_modal.js';
import { canvasState, getActiveCut, schedulePersistCutEdits, state } from './js/state.js';
import {
  applyCutTimeBounds,
  createManualCut,
  handleLocalVideoUpload,
  initStudioView,
  openWorkspaceFromData,
  refreshCutFrame,
  renderAllCanvases,
  resetToHero,
  setLayoutMode,
  startAnalysis,
  startRender,
  updateCanvasPlaceholderUI,
  updateCutsSidebarUI,
} from './js/studio_view.js';
import { debounce, showToast } from './js/utils.js';

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
    loadLibraryHistory(false);
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

async function cancelActiveRenderJob() {
  if (!state.activeJobId) return;
  try {
    const { res } = await apiCancelJob(state.activeJobId);
    if (res.ok) {
      showToast('Solicitação de cancelamento enviada ao FFmpeg...');
    }
  } catch (err) {
    console.warn('Erro ao cancelar job:', err);
  }
}

function initCanvas() {
  canvasState.editorCanvas = document.getElementById('editor-canvas');
  canvasState.editorCtx = canvasState.editorCanvas.getContext('2d');

  canvasState.mockupCanvas = document.getElementById('mockup-canvas');
  canvasState.mockupCtx = canvasState.mockupCanvas.getContext('2d');
  canvasState.mockupCanvas.width = 1080;
  canvasState.mockupCanvas.height = 1920;

  initBlurBuffer(canvasState);
  initClipPlayer({ renderAllCanvases, updateCanvasPlaceholderUI });
  bindCanvasInteractions(renderAllCanvases);
}

function bindProControls() {
  document.getElementById('select-sub-preset')?.addEventListener('change', (e) => {
    state.subtitlePreset = e.target.value || 'hormozi';
  });
  document.getElementById('toggle-jump-cuts')?.addEventListener('change', (e) => {
    state.removeSilences = e.target.checked;
  });
  document.getElementById('toggle-punch-zoom')?.addEventListener('change', (e) => {
    state.punchInZoom = e.target.checked;
  });
  document.getElementById('toggle-loudnorm')?.addEventListener('change', (e) => {
    state.normalizeAudio = e.target.checked;
  });
  document.getElementById('toggle-use-gpu')?.addEventListener('change', (e) => {
    state.useGpu = e.target.checked;
  });
  document.getElementById('select-brand-kit')?.addEventListener('change', (e) => {
    state.brandKitId = e.target.value ? Number(e.target.value) : null;
  });
  document.getElementById('select-bgm-track')?.addEventListener('change', (e) => {
    state.musicFilename = e.target.value || '';
  });
  document.getElementById('btn-cancel-active-job')?.addEventListener('click', cancelActiveRenderJob);
}

function bindDOMEvents() {
  // Brand & Tabs
  document.getElementById('brand-home-btn')?.addEventListener('click', resetToHero);
  document.getElementById('tab-btn-studio').addEventListener('click', () => switchTab('studio'));
  document.getElementById('tab-btn-library').addEventListener('click', () => switchTab('library'));

  // Topbar & Modal de Configurações
  document.getElementById('btn-open-folder').addEventListener('click', openCutsFolder);
  document.getElementById('btn-settings-modal').addEventListener('click', openSettingsModal);
  document.getElementById('modal-close-btn').addEventListener('click', closeSettingsModal);
  document.getElementById('modal-save-btn').addEventListener('click', saveSettingsModal);
  document.getElementById('btn-run-cleanup')?.addEventListener('click', runManualCleanup);

  // Hero: URL + Upload Local
  document.getElementById('btn-analyze').addEventListener('click', startAnalysis);
  document.getElementById('input-yt-url').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') startAnalysis();
  });

  const fileInput = document.getElementById('input-local-video');
  document.getElementById('btn-upload-local')?.addEventListener('click', () => fileInput?.click());
  fileInput?.addEventListener('change', (e) => {
    const f = e.target.files?.[0];
    if (f) handleLocalVideoUpload(f);
  });

  // Workspace Sidebar & Render
  document.getElementById('btn-new-video').addEventListener('click', resetToHero);
  document.getElementById('btn-add-manual-cut')?.addEventListener('click', createManualCut);
  document.getElementById('btn-render-selected').addEventListener('click', startRender);
  document.getElementById('select-render-resolution')?.addEventListener('change', (e) => {
    state.renderResolution = parseInt(e.target.value, 10) === 720 ? 720 : 1080;
  });

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
      applyCutTimeBounds(parseFloat(e.target.value) || 0, c.end_time);
    }
  });

  document.getElementById('active-cut-end').addEventListener('change', (e) => {
    const c = getActiveCut();
    if (c) {
      applyCutTimeBounds(c.start_time, parseFloat(e.target.value) || 0);
    }
  });

  document.getElementById('btn-refresh-frame').addEventListener('click', refreshCutFrame);
  document.getElementById('btn-load-frame-placeholder')?.addEventListener('click', refreshCutFrame);
  document.getElementById('toggle-burn-subs').addEventListener('change', (e) => {
    const c = getActiveCut();
    if (c) {
      c.burn_subtitles = e.target.checked;
      renderAllCanvases();
      schedulePersistCutEdits(c);
    }
  });

  // 6 Modos de Layout (inclui Face Tracking IA e 1:1 Quadrado)
  document.querySelectorAll('.layout-btn').forEach((btn) => {
    btn.addEventListener('click', () => setLayoutMode(btn.dataset.mode));
  });

  // Controles do Player de Clipe ao Vivo
  document.getElementById('btn-play-clip')?.addEventListener('click', togglePlayClipPreview);
  document.getElementById('clip-seek-slider')?.addEventListener('input', (e) => onClipSeekInput(e.target.value));
  document.getElementById('btn-mute-clip')?.addEventListener('click', toggleMuteClipPreview);

  // Biblioteca: Filtros & Paginação
  document.getElementById('lib-search-input').addEventListener('input', debounce(() => loadLibraryHistory(false), 300));
  document.getElementById('lib-score-slider').addEventListener('input', (e) => {
    document.getElementById('lib-score-val').textContent = e.target.value;
    loadLibraryHistory(false);
  });
  document.getElementById('btn-library-load-more')?.addEventListener('click', () => loadLibraryHistory(true));

  bindProControls();
  bindProPanelsEvents();
}

function boot() {
  initCanvas();
  initStudioView({ switchTab });
  initLibraryView({
    onReopenWorkspace: (workspaceData) => {
      switchTab('studio');
      openWorkspaceFromData(workspaceData);
    },
  });
  bindDOMEvents();
  fetchConfigAndHealth();
  refreshProResources();
}

if (document.readyState === 'loading') {
  window.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
