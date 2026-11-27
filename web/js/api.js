/**
 * Cliente REST modular para a API FastAPI do EasyKut Studio (V4.0).
 */

export async function apiGetConfig() {
  const res = await fetch('/api/config');
  return { res, data: await res.json() };
}

export async function apiGetHealth() {
  const res = await fetch('/api/health');
  return { res, data: await res.json() };
}

export async function apiOpenFolder() {
  const res = await fetch('/api/open-folder', { method: 'POST' });
  return { res, data: await res.json() };
}

export async function apiCleanup(removeRawDownloads = false, maxAgeHours = null) {
  const payload = { remove_raw_downloads: Boolean(removeRawDownloads) };
  if (maxAgeHours !== null) payload.max_age_hours = maxAgeHours;
  const res = await fetch('/api/cleanup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return { res, data: await res.json() };
}

export async function apiStartAnalyze(url, provider, maxCuts) {
  const res = await fetch('/api/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, provider, max_cuts: maxCuts }),
  });
  return { res, data: await res.json() };
}

export async function apiUploadVideo(file, provider, maxCuts) {
  const form = new FormData();
  form.append('file', file);
  form.append('provider', provider);
  form.append('max_cuts', String(maxCuts));
  const res = await fetch('/api/upload', { method: 'POST', body: form });
  return { res, data: await res.json() };
}

export async function apiGetWorkspace(videoId) {
  const res = await fetch(`/api/videos/${videoId}/workspace`);
  return { res, data: await res.json() };
}

export async function apiGetJob(jobId) {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  return { res, data };
}

export async function apiCancelJob(jobId) {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
  return { res, data: await res.json() };
}

export async function apiExtractFrame(videoId, source, timestampSec) {
  const res = await fetch('/api/frame', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ video_id: videoId, source, timestamp_sec: timestampSec }),
  });
  return { res, data: await res.json() };
}

export async function apiExtractPreviewClip(videoId, source, startTime, endTime) {
  const res = await fetch('/api/preview-clip', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ video_id: videoId, source, start_time: startTime, end_time: endTime }),
  });
  return { res, data: await res.json() };
}

export async function apiStartRender(videoId, cuts, maxResolution = 1080, options = {}) {
  const payload = {
    video_id: videoId,
    cuts,
    max_resolution: Number(maxResolution) === 720 ? 720 : 1080,
    use_gpu: Boolean(options.use_gpu),
    brand_kit_id: options.brand_kit_id || null,
    normalize_audio: Boolean(options.normalize_audio),
    denoise_audio: Boolean(options.denoise_audio),
    music_filename: options.music_filename || null,
    music_volume: typeof options.music_volume === 'number' ? options.music_volume : 0.18,
    export_formats: Array.isArray(options.export_formats) && options.export_formats.length > 0 ? options.export_formats : null,
  };
  const res = await fetch('/api/render', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return { res, data: await res.json() };
}

export async function apiGetHistory(search = '', minScore = 0, limit = 12, offset = 0) {
  const params = new URLSearchParams({
    search,
    min_score: String(minScore),
    limit: String(limit),
    offset: String(offset),
  });
  const res = await fetch(`/api/history?${params.toString()}`);
  const totalCount = Number(res.headers.get('X-Total-Count') || 0);
  return { res, data: await res.json(), totalCount };
}

export async function apiDeleteVideo(videoId, deleteFiles = false) {
  const res = await fetch(`/api/history/${videoId}?delete_files=${Boolean(deleteFiles)}`, { method: 'DELETE' });
  return { res, data: await res.json() };
}

export async function apiCreateCorte(payload) {
  const res = await fetch('/api/cortes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return { res, data: await res.json() };
}

export async function apiPatchCorte(corteId, payload) {
  const res = await fetch(`/api/cortes/${corteId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return { res, data: await res.json() };
}

export async function apiDeleteCorte(corteId, deleteFile = false) {
  const res = await fetch(`/api/cortes/${corteId}?delete_file=${Boolean(deleteFile)}`, { method: 'DELETE' });
  return { res, data: await res.json() };
}

// ==========================================
// ENDPOINTS PRO V4.0 (ONDAS 2, 3 E 4)
// ==========================================
export async function apiGetBrandKits() {
  const res = await fetch('/api/brand-kits');
  return { res, data: await res.json() };
}

export async function apiSaveBrandKit(payload) {
  const res = await fetch('/api/brand-kits', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return { res, data: await res.json() };
}

export async function apiDeleteBrandKit(kitId) {
  const res = await fetch(`/api/brand-kits/${kitId}`, { method: 'DELETE' });
  return { res, data: await res.json() };
}

export async function apiUploadBrandLogo(file) {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch('/api/brand-kits/upload-logo', { method: 'POST', body: form });
  return { res, data: await res.json() };
}

export async function apiGetMusicTracks() {
  const res = await fetch('/api/music');
  return { res, data: await res.json() };
}

export async function apiUploadMusicTrack(file) {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch('/api/music/upload', { method: 'POST', body: form });
  return { res, data: await res.json() };
}

export async function apiDeleteMusicTrack(filename) {
  const res = await fetch(`/api/music/${encodeURIComponent(filename)}`, { method: 'DELETE' });
  return { res, data: await res.json() };
}

export async function apiGenerateSeoKit(corteId, provider = 'gemini') {
  const res = await fetch(`/api/cortes/${corteId}/seo-kit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider }),
  });
  return { res, data: await res.json() };
}

export async function apiStartHighlightReel(videoId, payload) {
  const res = await fetch(`/api/videos/${videoId}/highlight-reel`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return { res, data: await res.json() };
}

export async function apiSuggestBroll(payload) {
  const res = await fetch('/api/broll/suggest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return { res, data: await res.json() };
}

export async function apiGetPublishProviders() {
  const res = await fetch('/api/publish/providers');
  return { res, data: await res.json() };
}

export async function apiGetPublishSchedule() {
  const res = await fetch('/api/publish/schedule');
  return { res, data: await res.json() };
}

export async function apiSchedulePublish(payload) {
  const res = await fetch('/api/publish/schedule', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return { res, data: await res.json() };
}

export async function apiDeleteScheduledPost(postId) {
  const res = await fetch(`/api/publish/schedule/${postId}`, { method: 'DELETE' });
  return { res, data: await res.json() };
}
