/**
 * Modal de Configurações, Diagnóstico de Saúde (/api/health) e Limpeza de Disco (/api/cleanup).
 */

import { apiCleanup, apiGetConfig, apiGetHealth } from './api.js';
import { state } from './state.js';
import { showToast } from './utils.js';

export async function fetchConfigAndHealth() {
  try {
    const [{ data: cfg }, { data: health }] = await Promise.all([
      apiGetConfig(),
      apiGetHealth().catch(() => ({ data: null })),
    ]);

    state.config = cfg;
    state.health = health;

    updateConfigModalUI(cfg, health);

    if (cfg.default_provider) {
      state.defaultProvider = cfg.default_provider;
      const select = document.getElementById('select-hero-provider');
      if (select) select.value = cfg.default_provider;
    }
  } catch (err) {
    console.error('Falha ao obter configuração/saúde:', err);
  }
}

function setStatusTag(id, ok, okText, offText) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = ok ? 'status-tag-ok' : 'status-tag-off';
  el.textContent = ok ? okText : offText;
}

export function updateConfigModalUI(data, health) {
  if (data && data.keys) {
    setStatusTag('cfg-status-groq', data.keys.GROQ_API_KEY, 'Conectado', 'Ausente');
    setStatusTag('cfg-status-gemini', data.keys.GEMINI_API_KEY, 'Conectado', 'Ausente');
    setStatusTag('cfg-status-ffmpeg', data.keys.FFMPEG_AVAILABLE, 'Pronto', 'Ausente');
    setStatusTag('cfg-status-cookies', data.keys.COOKIES_TXT, 'Carregado', 'Opcional (Não detectado)');
  }

  if (health) {
    setStatusTag('cfg-status-db', Boolean(health.db_ok), 'Online (WAL v3)', 'Degradado');
    const diskEl = document.getElementById('cfg-status-disk');
    if (diskEl) {
      const mb = Number(health.disk_free_mb);
      if (mb >= 0) {
        diskEl.className = mb >= 1024 ? 'status-tag-ok' : 'status-tag-off';
        diskEl.textContent = mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB livres` : `${mb.toFixed(0)} MB livres`;
      } else {
        diskEl.className = 'status-tag-off';
        diskEl.textContent = 'Indisponível';
      }
    }
    const jobsEl = document.getElementById('cfg-status-jobs');
    if (jobsEl && health.active_jobs) {
      const active = (health.active_jobs.analyze_running || 0) + (health.active_jobs.render_running || 0);
      jobsEl.className = 'status-tag-ok';
      jobsEl.textContent = `${active} ativo(s) · ${health.active_jobs.cached_analyses || 0} em cache`;
    }
  }
}

export function openSettingsModal() {
  fetchConfigAndHealth();
  const prov = document.getElementById('modal-provider-select');
  const maxC = document.getElementById('modal-maxcuts-input');
  const resSel = document.getElementById('modal-resolution-select');
  if (prov) prov.value = state.defaultProvider;
  if (maxC) maxC.value = state.maxCuts;
  if (resSel) resSel.value = String(state.renderResolution);
  document.getElementById('settings-modal').style.display = 'flex';
}

export function closeSettingsModal() {
  document.getElementById('settings-modal').style.display = 'none';
}

export function saveSettingsModal() {
  state.defaultProvider = document.getElementById('modal-provider-select').value;
  state.maxCuts = parseInt(document.getElementById('modal-maxcuts-input').value, 10) || 5;
  const resVal = parseInt(document.getElementById('modal-resolution-select')?.value || '1080', 10);
  state.renderResolution = resVal === 720 ? 720 : 1080;

  const heroProv = document.getElementById('select-hero-provider');
  if (heroProv) heroProv.value = state.defaultProvider;

  const renderRes = document.getElementById('select-render-resolution');
  if (renderRes) renderRes.value = String(state.renderResolution);

  closeSettingsModal();
  showToast('Configurações salvas!');
}

export async function runManualCleanup() {
  const btn = document.getElementById('btn-run-cleanup');
  const removeRaw = Boolean(document.getElementById('chk-cleanup-downloads')?.checked);
  if (btn) btn.disabled = true;

  try {
    const { res, data } = await apiCleanup(removeRaw, null);
    if (res.ok && data.success) {
      showToast(`Limpeza concluída: ${data.deleted_count} arquivo(s) · ${data.freed_mb.toFixed(2)} MB liberados.`);
      fetchConfigAndHealth();
    } else {
      alert('Erro ao executar limpeza: ' + (data.detail || res.status));
    }
  } catch (err) {
    alert('Erro ao executar limpeza: ' + err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}
