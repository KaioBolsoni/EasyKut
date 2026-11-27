/**
 * Player de prévia de vídeo + áudio integrado ao Canvas 60fps.
 */

import { apiExtractPreviewClip } from './api.js';
import { initCutGeometry } from './canvas_engine.js';
import { canvasState, getActiveCut, getCutSegments, state } from './state.js';
import { formatSecondsMMSS, showToast } from './utils.js';

let _renderAllCanvases = () => {};
let _updatePlaceholderUI = () => {};

export function initClipPlayer({ renderAllCanvases, updateCanvasPlaceholderUI }) {
  _renderAllCanvases = renderAllCanvases;
  _updatePlaceholderUI = updateCanvasPlaceholderUI;

  const vid = document.createElement('video');
  vid.crossOrigin = 'anonymous';
  vid.playsInline = true;
  vid.preload = 'auto';
  vid.loop = true;

  vid.addEventListener('timeupdate', () => {
    updateClipPlayerTimelineUI();
    if (!canvasState.isVideoPlaying) {
      _renderAllCanvases();
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
    _renderAllCanvases();
  });

  vid.addEventListener('ended', () => {
    canvasState.isVideoPlaying = false;
    updateClipPlayerButtonsUI();
    stopVideoCanvasLoop();
    _renderAllCanvases();
  });

  canvasState.videoEl = vid;
}

export function stopVideoCanvasLoop() {
  if (canvasState.animFrameId) {
    cancelAnimationFrame(canvasState.animFrameId);
    canvasState.animFrameId = null;
  }
}

export function startVideoCanvasLoop() {
  stopVideoCanvasLoop();
  const loop = () => {
    if (!canvasState.isVideoPlaying) return;
    _renderAllCanvases();
    updateClipPlayerTimelineUI();
    canvasState.animFrameId = requestAnimationFrame(loop);
  };
  canvasState.animFrameId = requestAnimationFrame(loop);
}

export function stopAndResetClipPreview() {
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

export async function togglePlayClipPreview() {
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
      _updatePlaceholderUI(false);
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

export function onClipSeekInput(val) {
  const vid = canvasState.videoEl;
  if (!vid || !canvasState.isVideoReady) return;
  vid.currentTime = parseFloat(val) || 0;
  updateClipPlayerTimelineUI();
  _renderAllCanvases();
}

export function seekClipByDelta(deltaSec) {
  const vid = canvasState.videoEl;
  if (!vid || !canvasState.isVideoReady) return;
  const maxDur = vid.duration || 0;
  vid.currentTime = Math.max(0, Math.min(maxDur, (vid.currentTime || 0) + deltaSec));
  updateClipPlayerTimelineUI();
  _renderAllCanvases();
}

export async function seekClipToRelativeSec(relSec) {
  const cut = getActiveCut();
  if (!cut) return;
  const clipKey = `${state.currentVideo?.video_id}_${cut.start_time.toFixed(1)}_${cut.end_time.toFixed(1)}`;
  if (!canvasState.isVideoReady || canvasState.loadedClipKey !== clipKey) {
    await togglePlayClipPreview();
  }
  if (canvasState.videoEl && canvasState.isVideoReady) {
    canvasState.videoEl.currentTime = Math.max(0, Math.min(canvasState.videoEl.duration || cut.duration, relSec));
    _renderAllCanvases();
  }
}

export function toggleMuteClipPreview() {
  const vid = canvasState.videoEl;
  if (!vid) return;
  vid.muted = !vid.muted;
  const btn = document.getElementById('btn-mute-clip');
  if (btn) btn.textContent = vid.muted ? '🔇' : '🔊';
}

export function updateClipPlayerButtonsUI() {
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

export function updateClipPlayerTimelineUI() {
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
  document.querySelectorAll('.cut-seg-item').forEach((el, idx) => {
    const s = segs[idx];
    el.classList.toggle('active-seg', Boolean(s && absSec >= s.start && absSec <= s.end));
  });
}
