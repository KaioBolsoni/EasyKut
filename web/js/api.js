/**
 * Cliente REST modular para a API FastAPI do EasyKut Studio (V3.2).
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
  const res = await fetch('/api/upload', {
    method: 'POST',
    body: form,
  });
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
    body: JSON.stringify({
      video_id: videoId,
      source,
      start_time: startTime,
      end_time: endTime,
    }),
  });
  return { res, data: await res.json() };
}

export async function apiStartRender(videoId, cuts, maxResolution = 1080) {
  const res = await fetch('/api/render', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      video_id: videoId,
      cuts,
      max_resolution: Number(maxResolution) === 720 ? 720 : 1080,
    }),
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
  const res = await fetch(`/api/history/${videoId}?delete_files=${Boolean(deleteFiles)}`, {
    method: 'DELETE',
  });
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
  const res = await fetch(`/api/cortes/${corteId}?delete_file=${Boolean(deleteFile)}`, {
    method: 'DELETE',
  });
  return { res, data: await res.json() };
}
