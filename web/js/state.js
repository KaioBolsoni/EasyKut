/**
 * Estado centralizado, polling resiliente de jobs e auto-save com debounce.
 */

import { apiGetJob, apiPatchCorte } from './api.js';
import { createCanvasState } from './canvas_engine.js';
import { debounce } from './utils.js';

export const state = {
  activeTab: 'studio',
  config: null,
  health: null,
  isAnalyzing: false,
  isRendering: false,
  activeJobId: null,
  jobPoller: null,

  currentVideo: null,
  cuts: [],
  activeCutIndex: 0,

  defaultProvider: 'gemini',
  maxCuts: 5,
  renderResolution: 1080,

  // Paginação da Biblioteca
  libraryVideos: [],
  libraryTotalCount: 0,
  libraryOffset: 0,
  libraryLimit: 12,
};

export const canvasState = createCanvasState();

export function getActiveCut() {
  return state.cuts[state.activeCutIndex] || null;
}

export function getCutSegments(cut) {
  if (!cut) return [];
  const allSegs = state.currentVideo?.transcript_segments || cut.segments || [];
  return allSegs.filter(
    (s) => s.end > cut.start_time && s.start < cut.end_time && (s.text || '').trim().length > 0
  );
}

export function updateStepTracker(activeStepNum) {
  for (let i = 1; i <= 4; i++) {
    const item = document.getElementById(`step-track-${i}`);
    if (!item) continue;
    item.classList.remove('active', 'done');
    if (i < activeStepNum) item.classList.add('done');
    else if (i === activeStepNum) item.classList.add('active');
  }
}

export function pollJob(jobId, onSuccess, onError) {
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

export const schedulePersistCutEdits = debounce(async (cut) => {
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
