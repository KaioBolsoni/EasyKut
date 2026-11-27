/**
 * Painéis Pro V4.0 (Ondas 2, 3 e 4):
 * - Brand Kit (logo PNG, barra de progresso, hook banner, preset Karaoke)
 * - Biblioteca de Trilhas (Auto-Ducking)
 * - B-Roll Automático (Pexels / Pixabay / Local)
 * - Kit de Publicação SEO IA (3 títulos A/B + descrição + hashtags)
 * - Compilação Highlight Reel (xfade)
 * - Publicação & Agendamento (YouTube Shorts / TikTok / Instagram Reels)
 * 100% DOM seguro (createElement + textContent + addEventListener).
 */

import {
  apiDeleteBrandKit,
  apiDeleteMusicTrack,
  apiDeleteScheduledPost,
  apiGenerateSeoKit,
  apiGetBrandKits,
  apiGetMusicTracks,
  apiGetPublishProviders,
  apiGetPublishSchedule,
  apiSaveBrandKit,
  apiSchedulePublish,
  apiStartHighlightReel,
  apiSuggestBroll,
  apiUploadBrandLogo,
  apiUploadMusicTrack,
} from './api.js';
import { getActiveCut, getCutSegments, pollJob, state } from './state.js';
import { showToast } from './utils.js';

let _uploadedLogoFilename = '';
let _activeSeoCorteId = null;
let _activePublishCorte = null;
let _activeReelVideo = null;

export async function refreshProResources() {
  try {
    const [kitsRes, musicRes] = await Promise.all([
      apiGetBrandKits().catch(() => ({ data: { brand_kits: [] } })),
      apiGetMusicTracks().catch(() => ({ data: { tracks: [] } })),
    ]);
    state.brandKits = kitsRes.data?.brand_kits || [];
    state.musicTracks = musicRes.data?.tracks || [];
    syncStudioProSelectors();
    renderBrandKitsList();
    renderMusicTracksList();
  } catch (err) {
    console.warn('Erro ao carregar recursos Pro:', err);
  }
}

export function syncStudioProSelectors() {
  const bkSelect = document.getElementById('select-brand-kit');
  if (bkSelect) {
    const prev = bkSelect.value;
    bkSelect.replaceChildren();
    const noneOpt = document.createElement('option');
    noneOpt.value = '';
    noneOpt.textContent = 'Sem Brand Kit';
    bkSelect.appendChild(noneOpt);
    state.brandKits.forEach((kit) => {
      const opt = document.createElement('option');
      opt.value = String(kit.id);
      opt.textContent = `🎨 ${kit.name}`;
      bkSelect.appendChild(opt);
    });
    if (prev && state.brandKits.some((k) => String(k.id) === prev)) {
      bkSelect.value = prev;
    }
  }

  const musSelect = document.getElementById('select-bgm-track');
  if (musSelect) {
    const prev = musSelect.value;
    musSelect.replaceChildren();
    const noneOpt = document.createElement('option');
    noneOpt.value = '';
    noneOpt.textContent = 'Sem trilha';
    musSelect.appendChild(noneOpt);
    state.musicTracks.forEach((tr) => {
      const opt = document.createElement('option');
      opt.value = tr.filename;
      opt.textContent = `🎵 ${tr.filename}`;
      musSelect.appendChild(opt);
    });
    if (prev && state.musicTracks.some((t) => t.filename === prev)) {
      musSelect.value = prev;
    }
  }
}

// ==========================================
// HUB PRO (BRAND KIT + MÚSICA + B-ROLL)
// ==========================================
export function openProHubModal() {
  refreshProResources();
  const modal = document.getElementById('pro-hub-modal');
  if (modal) modal.style.display = 'flex';
}

export function closeProHubModal() {
  const modal = document.getElementById('pro-hub-modal');
  if (modal) modal.style.display = 'none';
}

function renderBrandKitsList() {
  const container = document.getElementById('brand-kits-list');
  if (!container) return;
  container.replaceChildren();

  if (state.brandKits.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'pro-empty-hint';
    empty.textContent = 'Nenhum Brand Kit salvo ainda.';
    container.appendChild(empty);
    return;
  }

  state.brandKits.forEach((kit) => {
    const row = document.createElement('div');
    row.className = 'pro-list-row';

    const info = document.createElement('div');
    const nameEl = document.createElement('strong');
    nameEl.textContent = kit.name;
    const meta = document.createElement('div');
    meta.className = 'pro-row-meta';
    meta.textContent = `Preset: ${kit.subtitle_preset} · Barra: ${kit.progress_bar_enabled ? 'Sim' : 'Não'} · Hook: ${kit.hook_banner_enabled ? 'Sim' : 'Não'}`;
    info.append(nameEl, meta);

    const delBtn = document.createElement('button');
    delBtn.className = 'btn-ghost-sm';
    delBtn.type = 'button';
    delBtn.style.color = 'var(--accent-red)';
    delBtn.textContent = 'Excluir';
    delBtn.addEventListener('click', async () => {
      const { res } = await apiDeleteBrandKit(kit.id);
      if (res.ok) {
        if (state.brandKitId === kit.id) state.brandKitId = null;
        showToast('Brand Kit removido.');
        refreshProResources();
      }
    });

    row.append(info, delBtn);
    container.appendChild(row);
  });
}

function renderMusicTracksList() {
  const container = document.getElementById('music-tracks-list');
  if (!container) return;
  container.replaceChildren();

  if (state.musicTracks.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'pro-empty-hint';
    empty.textContent = 'Nenhuma trilha enviada. Envie .mp3/.wav para Auto-Ducking.';
    container.appendChild(empty);
    return;
  }

  state.musicTracks.forEach((tr) => {
    const row = document.createElement('div');
    row.className = 'pro-list-row';

    const lbl = document.createElement('span');
    lbl.textContent = `🎵 ${tr.filename} (${tr.size_kb} KB)`;

    const delBtn = document.createElement('button');
    delBtn.className = 'btn-ghost-sm';
    delBtn.type = 'button';
    delBtn.style.color = 'var(--accent-red)';
    delBtn.textContent = 'Remover';
    delBtn.addEventListener('click', async () => {
      const { res } = await apiDeleteMusicTrack(tr.filename);
      if (res.ok) {
        if (state.musicFilename === tr.filename) state.musicFilename = '';
        showToast('Trilha removida.');
        refreshProResources();
      }
    });

    row.append(lbl, delBtn);
    container.appendChild(row);
  });
}

export async function handleUploadLogoFile(file) {
  if (!file) return;
  const { res, data } = await apiUploadBrandLogo(file);
  if (res.ok && data.success) {
    _uploadedLogoFilename = data.logo_path;
    const statusEl = document.getElementById('bk-logo-status');
    if (statusEl) statusEl.textContent = `Logo pronta: ${data.logo_path}`;
    showToast('Logo enviada com sucesso!');
  } else {
    alert('Erro ao enviar logo: ' + (data.detail || res.status));
  }
}

export async function handleSaveBrandKitForm() {
  const name = (document.getElementById('bk-name-input')?.value || '').trim();
  if (!name) {
    alert('Informe um nome para o Brand Kit.');
    return;
  }
  const payload = {
    name,
    logo_path: _uploadedLogoFilename,
    logo_position: document.getElementById('bk-logo-pos')?.value || 'top_right',
    logo_opacity: parseFloat(document.getElementById('bk-logo-opacity')?.value || '0.85'),
    logo_scale: 0.14,
    subtitle_preset: document.getElementById('bk-sub-preset')?.value || 'hormozi',
    progress_bar_enabled: Boolean(document.getElementById('bk-progress-bar')?.checked),
    progress_bar_color: document.getElementById('bk-progress-color')?.value || '#6366F1',
    hook_banner_enabled: Boolean(document.getElementById('bk-hook-banner')?.checked),
    hook_banner_duration: 3.0,
  };
  const { res, data } = await apiSaveBrandKit(payload);
  if (res.ok && data.success) {
    showToast(`Brand Kit "${name}" salvo!`);
    document.getElementById('bk-name-input').value = '';
    _uploadedLogoFilename = '';
    const statusEl = document.getElementById('bk-logo-status');
    if (statusEl) statusEl.textContent = 'Opcional (.png/.webp)';
    await refreshProResources();
  } else {
    alert('Erro ao salvar Brand Kit: ' + (data.detail || res.status));
  }
}

export async function handleUploadMusicFile(file) {
  if (!file) return;
  const { res, data } = await apiUploadMusicTrack(file);
  if (res.ok && data.success) {
    showToast(`Trilha "${data.filename}" adicionada!`);
    await refreshProResources();
  } else {
    alert('Erro ao enviar trilha: ' + (data.detail || res.status));
  }
}

export async function handleSearchBroll() {
  const queryInput = document.getElementById('broll-query-input');
  const resultsEl = document.getElementById('broll-results-box');
  if (!resultsEl) return;

  const cut = getActiveCut();
  const excerpt = cut ? getCutSegments(cut).map((s) => s.text).join(' ') : '';
  const query = (queryInput?.value || '').trim() || null;

  resultsEl.replaceChildren();
  const loading = document.createElement('div');
  loading.className = 'pro-empty-hint';
  loading.textContent = 'Buscando sugestões de B-Roll...';
  resultsEl.appendChild(loading);

  try {
    const { res, data } = await apiSuggestBroll({
      query,
      transcript_excerpt: excerpt || null,
      max_results: 6,
      orientation: 'portrait',
    });
    resultsEl.replaceChildren();
    if (!res.ok) throw new Error(data.detail || `Erro ${res.status}`);

    const kwDiv = document.createElement('div');
    kwDiv.className = 'pro-row-meta';
    kwDiv.style.marginBottom = '0.5rem';
    kwDiv.textContent = `Palavras-chave: ${(data.keywords || []).join(', ')} · Fonte: ${data.provider}`;
    resultsEl.appendChild(kwDiv);

    if (!data.items || data.items.length === 0) {
      const hint = document.createElement('div');
      hint.className = 'pro-empty-hint';
      hint.textContent = 'Configure PEXELS_API_KEY ou PIXABAY_API_KEY no .env (ou adicione clipes em data/broll/) para visualizar mídias.';
      resultsEl.appendChild(hint);
      return;
    }

    data.items.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'pro-list-row';
      const span = document.createElement('span');
      span.textContent = `🎬 ${item.title}`;
      const link = document.createElement('a');
      link.className = 'btn-ghost-sm';
      link.textContent = 'Abrir';
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      const url = String(item.source_url || item.preview_url || '');
      if (url.startsWith('https://') || url.startsWith('/data/broll/')) {
        link.href = url;
      }
      row.append(span, link);
      resultsEl.appendChild(row);
    });
  } catch (err) {
    resultsEl.replaceChildren();
    const errEl = document.createElement('div');
    errEl.className = 'pro-empty-hint';
    errEl.textContent = `Erro ao buscar B-Roll: ${err.message}`;
    resultsEl.appendChild(errEl);
  }
}

// ==========================================
// SEO KIT IA (ONDA 3)
// ==========================================
export async function openSeoKitModal(corte) {
  _activeSeoCorteId = corte.id;
  const modal = document.getElementById('seo-kit-modal');
  const body = document.getElementById('seo-kit-content');
  if (!modal || !body) return;
  modal.style.display = 'flex';
  body.replaceChildren();

  const loading = document.createElement('div');
  loading.className = 'pro-empty-hint';
  loading.textContent = 'Gerando 3 títulos A/B, descrição e hashtags com IA...';
  body.appendChild(loading);

  try {
    const { res, data } = await apiGenerateSeoKit(corte.id, state.defaultProvider);
    if (!res.ok) throw new Error(data.detail || `Erro ${res.status}`);
    renderSeoKitContent(body, data.seo_kit);
  } catch (err) {
    body.replaceChildren();
    const errDiv = document.createElement('div');
    errDiv.className = 'pro-empty-hint';
    errDiv.textContent = `Falha ao gerar Kit SEO: ${err.message}`;
    body.appendChild(errDiv);
  }
}

function renderSeoKitContent(container, kit) {
  container.replaceChildren();
  if (!kit) return;

  const hTitles = document.createElement('div');
  hTitles.className = 'pro-section-label';
  hTitles.textContent = 'Opções de Título Viral (A/B):';
  container.appendChild(hTitles);

  (kit.titles || []).forEach((t, i) => {
    const row = document.createElement('div');
    row.className = 'pro-list-row';
    const txt = document.createElement('span');
    txt.textContent = `${String.fromCharCode(65 + i)}) ${t}`;
    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'btn-ghost-sm';
    copyBtn.textContent = 'Copiar';
    copyBtn.addEventListener('click', () => {
      navigator.clipboard?.writeText(t);
      showToast('Título copiado!');
    });
    row.append(txt, copyBtn);
    container.appendChild(row);
  });

  const hDesc = document.createElement('div');
  hDesc.className = 'pro-section-label';
  hDesc.style.marginTop = '0.75rem';
  hDesc.textContent = 'Descrição / Legenda Pronta:';
  const descArea = document.createElement('textarea');
  descArea.className = 'pro-textarea';
  descArea.rows = 4;
  descArea.readOnly = true;
  descArea.value = `${kit.description || ''}\n\n${(kit.hashtags || []).join(' ')}`.trim();

  const copyAllBtn = document.createElement('button');
  copyAllBtn.type = 'button';
  copyAllBtn.className = 'action-btn';
  copyAllBtn.style.marginTop = '0.5rem';
  copyAllBtn.textContent = '📋 Copiar Descrição + Hashtags';
  copyAllBtn.addEventListener('click', () => {
    navigator.clipboard?.writeText(descArea.value);
    showToast('Descrição e hashtags copiadas!');
  });

  container.append(hDesc, descArea, copyAllBtn);
}

export function closeSeoKitModal() {
  const modal = document.getElementById('seo-kit-modal');
  if (modal) modal.style.display = 'none';
  _activeSeoCorteId = null;
}

// ==========================================
// HIGHLIGHT REEL (XFADE — ONDA 4)
// ==========================================
export function openHighlightReelModal(video) {
  _activeReelVideo = video;
  const modal = document.getElementById('highlight-reel-modal');
  const listEl = document.getElementById('reel-cuts-checklist');
  if (!modal || !listEl) return;

  listEl.replaceChildren();
  const rendered = (video.cortes || []).filter((c) => c.file_exists && c.id);
  if (rendered.length < 2) {
    alert('Este vídeo precisa de pelo menos 2 cortes já renderizados (.mp4) para compilar um Highlight Reel.');
    return;
  }

  rendered.forEach((c) => {
    const label = document.createElement('label');
    label.className = 'pro-list-row';
    label.style.cursor = 'pointer';
    const chk = document.createElement('input');
    chk.type = 'checkbox';
    chk.checked = true;
    chk.value = String(c.id);
    chk.className = 'reel-cut-chk';
    const txt = document.createElement('span');
    txt.textContent = `${c.title} (${c.start_time}s → ${c.end_time}s)`;
    label.append(chk, txt);
    listEl.appendChild(label);
  });

  modal.style.display = 'flex';
}

export function closeHighlightReelModal() {
  const modal = document.getElementById('highlight-reel-modal');
  if (modal) modal.style.display = 'none';
  _activeReelVideo = null;
}

export async function handleStartHighlightReel() {
  if (!_activeReelVideo) return;
  const ids = Array.from(document.querySelectorAll('.reel-cut-chk:checked')).map((el) => Number(el.value));
  if (ids.length < 2) {
    alert('Marque pelo menos 2 cortes renderizados.');
    return;
  }
  const transition = document.getElementById('reel-transition-select')?.value || 'fade';
  const transDur = parseFloat(document.getElementById('reel-transition-dur')?.value || '0.5');

  closeHighlightReelModal();
  const overlay = document.getElementById('render-progress-overlay');
  if (overlay) overlay.style.display = 'flex';

  try {
    const { res, data } = await apiStartHighlightReel(_activeReelVideo.id, {
      corte_ids: ids,
      transition,
      transition_duration: transDur,
      title: `Highlight Reel - ${_activeReelVideo.title || _activeReelVideo.id}`,
      use_gpu: state.useGpu,
    });
    if (!res.ok || !data.job_id) throw new Error(data.detail || `Erro ${res.status}`);
    pollJob(
      data.job_id,
      (result) => {
        if (overlay) overlay.style.display = 'none';
        showToast('Highlight Reel compilado com sucesso!');
        if (result?.video_url && result.video_url.startsWith('/data/cortes/')) {
          const a = document.createElement('a');
          a.href = result.video_url;
          a.setAttribute('download', '');
          a.click();
        }
      },
      (errMsg) => {
        if (overlay) overlay.style.display = 'none';
        alert('Erro no Highlight Reel: ' + errMsg);
      }
    );
  } catch (err) {
    if (overlay) overlay.style.display = 'none';
    alert('Erro ao iniciar Highlight Reel: ' + err.message);
  }
}

// ==========================================
// PUBLICAÇÃO & AGENDAMENTO (ONDA 4)
// ==========================================
export async function openPublishModal(corte = null) {
  _activePublishCorte = corte;
  const modal = document.getElementById('publish-modal');
  if (!modal) return;
  modal.style.display = 'flex';

  const titleInput = document.getElementById('pub-title-input');
  const capInput = document.getElementById('pub-caption-input');
  const tagsInput = document.getElementById('pub-hashtags-input');
  const dateInput = document.getElementById('pub-date-input');

  if (corte) {
    if (titleInput) titleInput.value = corte.seo_kit?.titles?.[0] || corte.title || '';
    if (capInput) capInput.value = corte.seo_kit?.description || corte.justification || '';
    if (tagsInput) tagsInput.value = (corte.seo_kit?.hashtags || ['#shorts', '#reels', '#tiktok']).join(' ');
  }
  if (dateInput && !dateInput.value) {
    const nextHour = new Date(Date.now() + 3600 * 1000);
    dateInput.value = nextHour.toISOString().slice(0, 16);
  }

  await refreshPublishModalData();
}

export function closePublishModal() {
  const modal = document.getElementById('publish-modal');
  if (modal) modal.style.display = 'none';
  _activePublishCorte = null;
}

async function refreshPublishModalData() {
  const provBox = document.getElementById('pub-providers-status');
  const listBox = document.getElementById('pub-schedule-list');
  try {
    const [pRes, sRes] = await Promise.all([apiGetPublishProviders(), apiGetPublishSchedule()]);
    if (provBox) {
      provBox.replaceChildren();
      (pRes.data?.providers || []).forEach((p) => {
        const badge = document.createElement('span');
        badge.className = p.connected ? 'status-tag-ok' : 'status-tag-off';
        badge.textContent = `${p.name}: ${p.connected ? 'OAuth OK' : 'Fila Local'}`;
        provBox.appendChild(badge);
      });
    }
    if (listBox) {
      listBox.replaceChildren();
      const posts = sRes.data?.posts || [];
      if (posts.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'pro-empty-hint';
        empty.textContent = 'Nenhuma publicação agendada.';
        listBox.appendChild(empty);
      } else {
        posts.forEach((post) => {
          const row = document.createElement('div');
          row.className = 'pro-list-row';
          const info = document.createElement('div');
          const strong = document.createElement('strong');
          strong.textContent = `[${post.platform}] ${post.title}`;
          const meta = document.createElement('div');
          meta.className = 'pro-row-meta';
          meta.textContent = `${post.scheduled_at} · Status: ${post.status}`;
          info.append(strong, meta);

          const delBtn = document.createElement('button');
          delBtn.type = 'button';
          delBtn.className = 'btn-ghost-sm';
          delBtn.style.color = 'var(--accent-red)';
          delBtn.textContent = 'Cancelar';
          delBtn.addEventListener('click', async () => {
            await apiDeleteScheduledPost(post.id);
            showToast('Agendamento cancelado.');
            refreshPublishModalData();
          });
          row.append(info, delBtn);
          listBox.appendChild(row);
        });
      }
    }
  } catch (err) {
    console.warn('Falha ao atualizar agendamentos:', err);
  }
}

export async function handleSubmitPublish(publishNow = false) {
  if (!_activePublishCorte || !_activePublishCorte.id) {
    alert('Abra esta janela a partir de um corte renderizado na Biblioteca.');
    return;
  }
  const payload = {
    corte_id: Number(_activePublishCorte.id),
    platform: document.getElementById('pub-platform-select')?.value || 'youtube_shorts',
    title: (document.getElementById('pub-title-input')?.value || '').trim(),
    caption: (document.getElementById('pub-caption-input')?.value || '').trim(),
    hashtags: (document.getElementById('pub-hashtags-input')?.value || '').trim(),
    scheduled_at: document.getElementById('pub-date-input')?.value || new Date().toISOString().slice(0, 16),
    publish_now: Boolean(publishNow),
  };
  if (!payload.title) {
    alert('Informe o título da publicação.');
    return;
  }
  const { res, data } = await apiSchedulePublish(payload);
  if (res.ok && data.success) {
    showToast(data.message || 'Agendado com sucesso!');
    await refreshPublishModalData();
  } else {
    alert('Erro ao agendar/publicar: ' + (data.detail || res.status));
  }
}

export function bindProPanelsEvents() {
  document.getElementById('btn-pro-hub-modal')?.addEventListener('click', openProHubModal);
  document.getElementById('pro-hub-close-btn')?.addEventListener('click', closeProHubModal);
  document.getElementById('btn-save-brand-kit')?.addEventListener('click', handleSaveBrandKitForm);

  const logoInput = document.getElementById('bk-logo-file');
  document.getElementById('btn-upload-logo')?.addEventListener('click', () => logoInput?.click());
  logoInput?.addEventListener('change', (e) => {
    const f = e.target.files?.[0];
    if (f) handleUploadLogoFile(f);
  });

  const musicInput = document.getElementById('music-file-input');
  document.getElementById('btn-upload-music')?.addEventListener('click', () => musicInput?.click());
  musicInput?.addEventListener('change', (e) => {
    const f = e.target.files?.[0];
    if (f) handleUploadMusicFile(f);
  });

  document.getElementById('btn-search-broll')?.addEventListener('click', handleSearchBroll);
  document.getElementById('seo-kit-close-btn')?.addEventListener('click', closeSeoKitModal);
  document.getElementById('reel-close-btn')?.addEventListener('click', closeHighlightReelModal);
  document.getElementById('btn-confirm-reel')?.addEventListener('click', handleStartHighlightReel);
  document.getElementById('pub-close-btn')?.addEventListener('click', closePublishModal);
  document.getElementById('btn-submit-schedule')?.addEventListener('click', () => handleSubmitPublish(false));
  document.getElementById('btn-submit-publish-now')?.addEventListener('click', () => handleSubmitPublish(true));
}
