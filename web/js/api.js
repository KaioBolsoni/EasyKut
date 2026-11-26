/**
 * Cliente REST modular para a API FastAPI do EasyKut Studio.
 */

export async function apiGetConfig() {
  const res = await fetch('/api/config');
  return { res, data: await res.json() };
}

export async function apiOpenFolder() {
  const res = await fetch('/api/open-folder', { method: 'POST' });
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
      max_resolution: maxResolution,
    }),
  });
  return { res, data: await res.json() };
}

export async function apiGetHistory(search = '', minScore = 0, limit = 50, offset = 0) {
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
