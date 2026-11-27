/**
 * Engine de Canvas 2D 60fps do EasyKut Studio:
 * - Editor interativo de enquadramento (manual_crop, split_screen, vertical_blur, horizontal_16_9)
 * - Mockup 9:16 em tempo real com desfoque otimizado via canvas offscreen reduzido (270x480 -> ~16x menos pixels)
 * - Interação de arraste e redimensionamento proporcional via mouse
 */

import { clamp, makeEven } from './utils.js';

export function createCanvasState() {
  return {
    editorCanvas: null,
    editorCtx: null,
    mockupCanvas: null,
    mockupCtx: null,

    // Canvas offscreen reduzido (1/4 da escala linear = 1/16 da área) para blur 60fps ultraleve
    blurCanvas: null,
    blurCtx: null,
    blurStaticCacheKey: null,

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
}

export function initBlurBuffer(canvasState, width = 270, height = 480) {
  const off = document.createElement('canvas');
  off.width = width;
  off.height = height;
  canvasState.blurCanvas = off;
  canvasState.blurCtx = off.getContext('2d');
  canvasState.blurStaticCacheKey = null;
}

export function invalidateBlurCache(canvasState) {
  canvasState.blurStaticCacheKey = null;
}

export function getActiveMediaSource(canvasState) {
  if (canvasState.isVideoReady && canvasState.videoEl && canvasState.videoEl.readyState >= 2) {
    return {
      el: canvasState.videoEl,
      isVideo: true,
      srcW: canvasState.videoEl.videoWidth || canvasState.imgNaturalW || 1920,
      srcH: canvasState.videoEl.videoHeight || canvasState.imgNaturalH || 1080,
    };
  }
  if (canvasState.imgLoaded && canvasState.currentImg) {
    return {
      el: canvasState.currentImg,
      isVideo: false,
      srcW: canvasState.currentImg.naturalWidth || canvasState.imgNaturalW || 1920,
      srcH: canvasState.currentImg.naturalHeight || canvasState.imgNaturalH || 1080,
    };
  }
  return null;
}

export function initCutGeometry(cut, canvasState) {
  const W = canvasState.imgNaturalW || 1920;
  const H = canvasState.imgNaturalH || 1080;

  if (!cut.layout_params) cut.layout_params = {};

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
      const targetAr = 1080 / 960;
      ['box_top', 'box_bottom'].forEach((k) => {
        const b = cut.layout_params[k];
        const nh = makeEven(Math.min(H, Math.round(b.h * sy)));
        const nw = makeEven(Math.min(W, Math.round(nh * targetAr)));
        b.w = nw;
        b.h = nh;
        b.x = makeEven(clamp(Math.round(b.x * sx), 0, W - nw));
        b.y = makeEven(clamp(Math.round(b.y * sy), 0, H - nh));
      });
    }
  }

  cut.layout_params.ref_w = W;
  cut.layout_params.ref_h = H;

  if (!cut.layout_params.crop_w || !cut.layout_params.crop_h) {
    const cropH = Math.round(H * 0.96);
    const cropW = makeEven(Math.round(cropH * (9 / 16)));
    const cropX = makeEven(Math.round((W - cropW) / 2));
    const cropY = makeEven(Math.round((H - cropH) / 2));
    cut.layout_params.crop_w = cropW;
    cut.layout_params.crop_h = cropH;
    cut.layout_params.crop_x = cropX;
    cut.layout_params.crop_y = cropY;
  }

  if (!cut.layout_params.box_top || !cut.layout_params.box_bottom) {
    const targetAr = 1080 / 960;
    const maxH = Math.min(H, Math.round(W / targetAr));
    const bh = makeEven(Math.round(maxH * 0.85));
    const bw = makeEven(Math.round(bh * targetAr));

    const tx = makeEven(Math.max(0, Math.round(W * 0.1)));
    const ty = makeEven(Math.round((H - bh) / 2));
    const bx = makeEven(Math.min(W - bw, Math.round(W * 0.5)));
    const by = ty;

    cut.layout_params.box_top = { x: tx, y: ty, w: bw, h: bh };
    cut.layout_params.box_bottom = { x: bx, y: by, w: bw, h: bh };
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

export function renderEditorCanvas(canvasState, cut) {
  const ctx = canvasState.editorCtx;
  if (!ctx) return;
  const media = getActiveMediaSource(canvasState);

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
  ctx.drawImage(srcEl, 0, 0, W, H);

  if (!cut) return;
  const mode = cut.layout_mode;

  if (mode === 'manual_crop') {
    const { crop_x: x, crop_y: y, crop_w: w, crop_h: h } = cut.layout_params;

    ctx.fillStyle = 'rgba(11, 13, 18, 0.7)';
    ctx.fillRect(0, 0, W, y);
    ctx.fillRect(0, y + h, W, H - (y + h));
    ctx.fillRect(0, y, x, h);
    ctx.fillRect(x + w, y, W - (x + w), h);

    ctx.strokeStyle = '#6366F1';
    ctx.lineWidth = 4;
    ctx.strokeRect(x, y, w, h);

    drawHandle(ctx, x, y);
    drawHandle(ctx, x + w, y);
    drawHandle(ctx, x, y + h);
    drawHandle(ctx, x + w, y + h);
    drawBadge(ctx, `9:16 (${w}×${h})`, x + 10, y + 26, '#6366F1');
  } else if (mode === 'split_screen') {
    const top = cut.layout_params.box_top;
    const bot = cut.layout_params.box_bottom;

    ctx.fillStyle = 'rgba(11, 13, 18, 0.55)';
    ctx.fillRect(0, 0, W, H);

    ctx.drawImage(srcEl, top.x * scaleX, top.y * scaleY, top.w * scaleX, top.h * scaleY, top.x, top.y, top.w, top.h);
    ctx.drawImage(srcEl, bot.x * scaleX, bot.y * scaleY, bot.w * scaleX, bot.h * scaleY, bot.x, bot.y, bot.w, bot.h);

    ctx.strokeStyle = '#6366F1';
    ctx.lineWidth = 4;
    ctx.strokeRect(top.x, top.y, top.w, top.h);
    drawHandle(ctx, top.x, top.y, '#6366F1');
    drawHandle(ctx, top.x + top.w, top.y, '#6366F1');
    drawHandle(ctx, top.x, top.y + top.h, '#6366F1');
    drawHandle(ctx, top.x + top.w, top.y + top.h, '#6366F1');
    drawBadge(ctx, 'TOPO (1080×960)', top.x + 10, top.y + 26, '#6366F1');

    ctx.strokeStyle = '#F59E0B';
    ctx.lineWidth = 4;
    ctx.strokeRect(bot.x, bot.y, bot.w, bot.h);
    drawHandle(ctx, bot.x, bot.y, '#F59E0B');
    drawHandle(ctx, bot.x + bot.w, bot.y, '#F59E0B');
    drawHandle(ctx, bot.x, bot.y + bot.h, '#F59E0B');
    drawHandle(ctx, bot.x + bot.w, bot.y + bot.h, '#F59E0B');
    drawBadge(ctx, 'BASE (1080×960)', bot.x + 10, bot.y + 26, '#F59E0B');
  } else if (mode === 'face_tracking_9_16') {
    const centerW = Math.round(H * (9 / 16));
    const centerX = Math.round((W - centerW) / 2);
    ctx.fillStyle = 'rgba(11, 13, 18, 0.55)';
    ctx.fillRect(0, 0, centerX, H);
    ctx.fillRect(centerX + centerW, 0, W - (centerX + centerW), H);

    ctx.strokeStyle = '#10B981';
    ctx.lineWidth = 4;
    ctx.setLineDash([12, 8]);
    ctx.strokeRect(centerX, 0, centerW, H);
    ctx.setLineDash([]);
    drawBadge(ctx, '🤖 AUTO FACE TRACKING 9:16', centerX + 12, 34, '#10B981');
  } else if (mode === 'vertical_blur') {
    const centerW = Math.round(H * (9 / 16));
    const centerX = Math.round((W - centerW) / 2);
    ctx.strokeStyle = 'rgba(99, 102, 241, 0.4)';
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 8]);
    ctx.strokeRect(centerX, 0, centerW, H);
    ctx.setLineDash([]);
  }
}

function drawOptimizedBlurredBackground(ctx, canvasState, media, mW, mH) {
  const { el: srcEl, isVideo, srcW, srcH } = media;

  if (!canvasState.blurCanvas || !canvasState.blurCtx) {
    initBlurBuffer(canvasState);
  }

  const bCanvas = canvasState.blurCanvas;
  const bCtx = canvasState.blurCtx;
  const bW = bCanvas.width;
  const bH = bCanvas.height;

  // Para imagens estáticas, reutiliza o buffer offscreen já desfocado sem recomputar blur
  const staticKey = !isVideo ? `${srcEl.src}_${srcW}x${srcH}` : null;
  if (isVideo || canvasState.blurStaticCacheKey !== staticKey) {
    bCtx.save();
    bCtx.clearRect(0, 0, bW, bH);
    bCtx.filter = 'blur(6px) brightness(0.8)';
    const scale = Math.max(bW / srcW, bH / srcH);
    const bgW = srcW * scale;
    const bgH = srcH * scale;
    bCtx.drawImage(srcEl, (bW - bgW) / 2, (bH - bgH) / 2, bgW, bgH);
    bCtx.restore();
    canvasState.blurStaticCacheKey = staticKey;
  }

  ctx.save();
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(bCanvas, 0, 0, bW, bH, 0, 0, mW, mH);
  ctx.restore();
}

export function renderMockupCanvas(canvasState, cut, getCutSegmentsFn) {
  const ctx = canvasState.mockupCtx;
  if (!ctx) return;
  const mW = canvasState.mockupCanvas.width || 1080;
  const mH = canvasState.mockupCanvas.height || 1920;
  const media = getActiveMediaSource(canvasState);

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
    ctx.drawImage(srcEl, x * scaleX, y * scaleY, w * scaleX, h * scaleY, 0, 0, mW, mH);
  } else if (mode === 'face_tracking_9_16') {
    const cropW = Math.round(srcH * (9 / 16));
    const cropX = Math.max(0, Math.round((srcW - cropW) / 2));
    ctx.drawImage(srcEl, cropX, 0, cropW, srcH, 0, 0, mW, mH);
    drawBadge(ctx, '🤖 Face Tracking IA (OpenCV)', 28, 86, '#10B981');
  } else if (mode === 'split_screen') {
    const top = cut.layout_params.box_top;
    const bot = cut.layout_params.box_bottom;
    ctx.drawImage(srcEl, top.x * scaleX, top.y * scaleY, top.w * scaleX, top.h * scaleY, 0, 0, mW, 960);
    ctx.drawImage(srcEl, bot.x * scaleX, bot.y * scaleY, bot.w * scaleX, bot.h * scaleY, 0, 960, mW, 960);
    ctx.fillStyle = '#232938';
    ctx.fillRect(0, 958, mW, 4);
  } else if (mode === 'vertical_blur') {
    const zoom = cut.layout_params.zoom_factor || 1.0;
    drawOptimizedBlurredBackground(ctx, canvasState, media, mW, mH);

    const fgW = mW * zoom;
    const fgH = (mW * (srcH / srcW)) * zoom;
    const fgX = (mW - fgW) / 2;
    const fgY = (mH - fgH) / 2;
    ctx.drawImage(srcEl, fgX, fgY, fgW, fgH);
  } else {
    ctx.fillStyle = '#0B0D12';
    ctx.fillRect(0, 0, mW, mH);
    const scale = mW / srcW;
    const h = srcH * scale;
    ctx.drawImage(srcEl, 0, (mH - h) / 2, mW, h);
  }

  if (cut.burn_subtitles) {
    drawMockupSubtitles(ctx, mW, mH, cut, canvasState, getCutSegmentsFn);
  }
}

function drawMockupSubtitles(ctx, mW, mH, cut, canvasState, getCutSegmentsFn) {
  let activeText = 'EXEMPLO DE LEGENDA DINÂMICA';
  const segs = getCutSegmentsFn(cut);

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

export function getCanvasMouseCoords(canvas, e) {
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  return {
    x: (e.clientX - rect.left) * scaleX,
    y: (e.clientY - rect.top) * scaleY,
  };
}

export function checkHandleHit(x, y, box) {
  const hitDist = 24;
  if (Math.hypot(x - box.x, y - box.y) <= hitDist) return 'nw';
  if (Math.hypot(x - (box.x + box.w), y - box.y) <= hitDist) return 'ne';
  if (Math.hypot(x - box.x, y - (box.y + box.h)) <= hitDist) return 'sw';
  if (Math.hypot(x - (box.x + box.w), y - (box.y + box.h)) <= hitDist) return 'se';
  return null;
}

export function isPointInsideBox(px, py, box) {
  return px >= box.x && px <= box.x + box.w && py >= box.y && py <= box.y + box.h;
}

export function resizeBoxWithAspectRatio(targetObj, init, dx, dy, aspectWtoH, handle, maxW, maxH, isCropCoords = false) {
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

export function updateCanvasCursor(canvas, x, y, mode, cut) {
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
