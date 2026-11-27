/**
 * Interações de mouse (drag & resize proporcional) sobre o Canvas do Estúdio.
 */

import {
  checkHandleHit,
  getCanvasMouseCoords,
  isPointInsideBox,
  resizeBoxWithAspectRatio,
  updateCanvasCursor,
} from './canvas_engine.js';
import { canvasState, getActiveCut, schedulePersistCutEdits } from './state.js';
import { clamp, makeEven } from './utils.js';

export function bindCanvasInteractions(renderAllCanvases) {
  const canvas = canvasState.editorCanvas;
  if (!canvas) return;

  canvas.addEventListener('mousedown', (e) => {
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
  });

  window.addEventListener('mousemove', (e) => {
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
  });

  window.addEventListener('mouseup', () => {
    const wasDragging = canvasState.isDragging;
    canvasState.isDragging = false;
    canvasState.dragTarget = null;
    canvasState.activeHandle = null;
    if (wasDragging) {
      schedulePersistCutEdits(getActiveCut());
    }
  });
}
