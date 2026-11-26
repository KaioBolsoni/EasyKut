/**
 * Utilitários puros e sanitizadores defensivos do EasyKut Studio (ES Module).
 */

export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function getSafeCutDownloadUrl(url) {
  if (typeof url !== 'string') return null;
  const trimmed = url.trim();
  if (!trimmed.startsWith('/data/cortes/')) return null;
  if (trimmed.includes('..') || /[\r\n\x00]/.test(trimmed)) return null;
  return trimmed;
}

export function makeEven(val) {
  const n = Math.round(val);
  return n % 2 === 0 ? n : n - 1;
}

export function clamp(val, min, max) {
  return Math.max(min, Math.min(max, val));
}

export function formatSecondsMMSS(sec) {
  const s = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, '0')}`;
}

export function formatTimeRange(start, end) {
  const sMin = Math.floor(start / 60);
  const sSec = Math.floor(start % 60);
  const eMin = Math.floor(end / 60);
  const eSec = Math.floor(end % 60);
  return `${sMin}:${sSec.toString().padStart(2, '0')} - ${eMin}:${eSec.toString().padStart(2, '0')}`;
}

export function showToast(msg) {
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

export function debounce(func, wait) {
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
