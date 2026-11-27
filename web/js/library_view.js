/**
 * Aba Biblioteca (Histórico paginado com X-Total-Count, reabertura no Estúdio e exclusões seguras).
 */

import { apiDeleteCorte, apiDeleteVideo, apiGetHistory, apiGetWorkspace } from './api.js';
import { openHighlightReelModal, openPublishModal, openSeoKitModal } from './pro_panels.js';
import { state } from './state.js';
import { getSafeCutDownloadUrl, showToast } from './utils.js';

let _onReopenWorkspace = () => {};

export function initLibraryView({ onReopenWorkspace }) {
  _onReopenWorkspace = onReopenWorkspace;
}

export async function loadLibraryHistory(append = false) {
  const search = document.getElementById('lib-search-input')?.value || '';
  const minScore = parseFloat(document.getElementById('lib-score-slider')?.value) || 0;

  if (!append) {
    state.libraryOffset = 0;
    state.libraryVideos = [];
  }

  try {
    const { data: videos, totalCount } = await apiGetHistory(
      search,
      minScore,
      state.libraryLimit,
      state.libraryOffset
    );

    const batch = Array.isArray(videos) ? videos : [];
    state.libraryVideos = append ? state.libraryVideos.concat(batch) : batch;
    state.libraryTotalCount = totalCount || state.libraryVideos.length;
    state.libraryOffset = state.libraryVideos.length;

    renderLibraryCards();
    updateLibraryPaginationUI();
  } catch (err) {
    console.error('Erro ao carregar histórico:', err);
  }
}

function updateLibraryPaginationUI() {
  const summaryEl = document.getElementById('library-pagination-summary');
  const loadMoreBtn = document.getElementById('btn-library-load-more');
  const shown = state.libraryVideos.length;
  const total = state.libraryTotalCount;

  if (summaryEl) {
    summaryEl.textContent = total > 0 ? `Mostrando ${shown} de ${total} vídeo(s)` : '';
  }
  if (loadMoreBtn) {
    loadMoreBtn.style.display = shown < total ? 'inline-flex' : 'none';
  }
}

function renderLibraryCards() {
  const container = document.getElementById('library-cards-container');
  container.replaceChildren();

  if (!state.libraryVideos || state.libraryVideos.length === 0) {
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

  state.libraryVideos.forEach((v) => {
    const card = document.createElement('div');
    card.className = 'video-card-hist';

    const header = document.createElement('div');
    header.className = 'video-card-hist-header';

    const displayTitle = String(v.title || v.url || '');
    const titleDiv = document.createElement('div');
    titleDiv.className = 'video-card-hist-title';
    titleDiv.title = displayTitle;
    titleDiv.textContent = `📹 ${displayTitle}`;

    const headerActions = document.createElement('div');
    headerActions.style.display = 'flex';
    headerActions.style.alignItems = 'center';
    headerActions.style.gap = '6px';

    const reopenBtn = document.createElement('button');
    reopenBtn.className = 'action-btn';
    reopenBtn.style.height = '28px';
    reopenBtn.style.padding = '0 10px';
    reopenBtn.style.fontSize = '0.75rem';
    reopenBtn.title = 'Reabrir este projeto no Estúdio para editar cortes';
    reopenBtn.textContent = '🎬 Editar';
    reopenBtn.addEventListener('click', () => reopenVideoInStudio(Number(v.id)));

    const renderedCount = (v.cortes || []).filter((c) => c.file_exists).length;
    if (renderedCount > 0) {
      const zipLink = document.createElement('a');
      zipLink.className = 'action-btn';
      zipLink.style.height = '28px';
      zipLink.style.padding = '0 8px';
      zipLink.style.fontSize = '0.72rem';
      zipLink.href = `/api/videos/${Number(v.id)}/export-zip`;
      zipLink.setAttribute('download', '');
      zipLink.title = 'Baixar todos os cortes renderizados deste vídeo em um arquivo .zip';
      zipLink.textContent = '📦 ZIP';
      headerActions.appendChild(zipLink);
    }

    if (renderedCount >= 2) {
      const reelBtn = document.createElement('button');
      reelBtn.className = 'action-btn';
      reelBtn.style.height = '28px';
      reelBtn.style.padding = '0 8px';
      reelBtn.style.fontSize = '0.72rem';
      reelBtn.title = 'Compilar melhores momentos em 1 vídeo com transições xfade';
      reelBtn.textContent = '🎞️ Reel';
      reelBtn.addEventListener('click', () => openHighlightReelModal(v));
      headerActions.appendChild(reelBtn);
    }

    const delBtn = document.createElement('button');
    delBtn.className = 'icon-btn';
    delBtn.style.color = 'var(--accent-red)';
    delBtn.style.width = '30px';
    delBtn.style.height = '30px';
    delBtn.title = 'Excluir vídeo';
    delBtn.addEventListener('click', () => deleteVideoItem(Number(v.id)));
    delBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';

    headerActions.append(reopenBtn, delBtn);
    header.append(titleDiv, headerActions);

    const body = document.createElement('div');
    body.className = 'video-card-hist-body';

    const metaDiv = document.createElement('div');
    metaDiv.style.fontSize = '0.8rem';
    metaDiv.style.color = 'var(--text-dim)';
    const sourceBadge = String(v.url || '').startsWith('local://') ? 'Arquivo Local' : 'Remoto';
    metaDiv.textContent = `ID: ${v.id} | ${sourceBadge} | Duração: ${Math.round(Number(v.duration) || 0)}s | Status: ${v.status}`;
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
        cutMetaSpan.textContent = `⭐ ${c.virality_score}/100 | ${c.start_time}s - ${c.end_time}s | ${c.layout_mode || 'vertical_blur'}`;

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
          missingSpan.textContent = 'Não renderizado';
          actionsDiv.appendChild(missingSpan);
        }

        if (c.id) {
          const seoBtn = document.createElement('button');
          seoBtn.className = 'action-btn';
          seoBtn.style.height = '28px';
          seoBtn.style.padding = '0 8px';
          seoBtn.style.fontSize = '0.72rem';
          seoBtn.title = 'Gerar 3 títulos A/B, descrição e hashtags com IA';
          seoBtn.textContent = '✨ SEO';
          seoBtn.addEventListener('click', () => openSeoKitModal(c));
          actionsDiv.appendChild(seoBtn);

          if (safeVideoUrl) {
            const pubBtn = document.createElement('button');
            pubBtn.className = 'action-btn';
            pubBtn.style.height = '28px';
            pubBtn.style.padding = '0 8px';
            pubBtn.style.fontSize = '0.72rem';
            pubBtn.title = 'Agendar ou publicar em YouTube Shorts, TikTok ou Instagram Reels';
            pubBtn.textContent = '🚀 Publicar';
            pubBtn.addEventListener('click', () => openPublishModal(c));
            actionsDiv.appendChild(pubBtn);
          }

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
}

async function reopenVideoInStudio(videoId) {
  showToast('Carregando projeto no Estúdio...');
  try {
    const { res, data } = await apiGetWorkspace(videoId);
    if (!res.ok) {
      throw new Error(data.detail || `Erro ${res.status}`);
    }
    _onReopenWorkspace(data);
    showToast(`Projeto "${data.title}" reaberto no Estúdio!`);
  } catch (err) {
    alert('Erro ao reabrir projeto no Estúdio: ' + err.message);
  }
}

async function deleteVideoItem(videoId) {
  const deleteFiles = confirm('Deseja também apagar os arquivos físicos (.mp4/.mp3) do disco?');
  try {
    const { data } = await apiDeleteVideo(videoId, deleteFiles);
    if (data.success) {
      showToast('Registro excluído com sucesso!');
      loadLibraryHistory(false);
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
      loadLibraryHistory(false);
    } else {
      alert('Erro ao excluir corte: ' + (data.detail || res.status));
    }
  } catch (err) {
    alert('Erro ao excluir corte: ' + err.message);
  }
}
