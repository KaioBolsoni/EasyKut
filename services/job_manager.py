"""
Gerenciador thread-safe de jobs assíncronos e cache em memória (analyses_cache)
com limites de concorrência, TTL e política LRU para prevenir vazamento de RAM.
"""

from collections import OrderedDict
import copy
import threading
import time
from typing import Any, Dict, Optional
import uuid


class JobManager:
    """Centraliza o ciclo de vida dos jobs, semáforo FFmpeg e cache LRU/TTL de análises."""

    def __init__(
        self,
        max_concurrent_analyze: int = 2,
        max_concurrent_render: int = 2,
        max_preview_ffmpeg: int = 2,
        job_ttl_seconds: float = 3600.0,
        max_jobs: int = 200,
        cache_ttl_seconds: float = 7200.0,
        max_cache_entries: int = 50,
    ) -> None:
        self.max_concurrent_analyze = max_concurrent_analyze
        self.max_concurrent_render = max_concurrent_render
        self.job_ttl_seconds = job_ttl_seconds
        self.max_jobs = max_jobs
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_cache_entries = max_cache_entries

        self.lock = threading.Lock()
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.analyses_cache: OrderedDict[int, Dict[str, Any]] = OrderedDict()
        self.preview_ffmpeg_semaphore = threading.Semaphore(max_preview_ffmpeg)

    def _prune_locked(self, now: Optional[float] = None) -> Dict[str, int]:
        """Remove jobs finalizados expirados/excedentes e entradas vencidas do cache LRU."""
        ts = time.time() if now is None else float(now)
        pruned_jobs = 0
        pruned_cache = 0

        # 1. TTL de jobs finalizados (nunca remove jobs 'running')
        expired_job_ids = [
            jid
            for jid, j in self.jobs.items()
            if j.get("status") != "running"
            and (ts - float(j.get("updated_at", j.get("created_at", ts)))) > self.job_ttl_seconds
        ]
        for jid in expired_job_ids:
            self.jobs.pop(jid, None)
            pruned_jobs += 1

        # 2. Cap de quantidade de jobs (evicta finalizados mais antigos primeiro)
        if len(self.jobs) > self.max_jobs:
            finished = sorted(
                (
                    (float(j.get("updated_at", j.get("created_at", 0.0))), jid)
                    for jid, j in self.jobs.items()
                    if j.get("status") != "running"
                ),
                key=lambda item: item[0],
            )
            excess = len(self.jobs) - self.max_jobs
            for _, jid in finished[:excess]:
                self.jobs.pop(jid, None)
                pruned_jobs += 1

        # 3. TTL do analyses_cache
        expired_cache_keys = [
            vid
            for vid, entry in self.analyses_cache.items()
            if (ts - float(entry.get("_cached_at", ts))) > self.cache_ttl_seconds
        ]
        for vid in expired_cache_keys:
            self.analyses_cache.pop(vid, None)
            pruned_cache += 1

        # 4. Cap LRU do analyses_cache
        while len(self.analyses_cache) > self.max_cache_entries:
            self.analyses_cache.popitem(last=False)
            pruned_cache += 1

        return {"pruned_jobs": pruned_jobs, "pruned_cache": pruned_cache}

    def prune(self, now: Optional[float] = None) -> Dict[str, int]:
        """Executa limpeza de TTL/LRU sob lock."""
        with self.lock:
            return self._prune_locked(now=now)

    def create_job(self, job_type: str, step: str, progress: int = 5) -> Optional[str]:
        """
        Registra um novo job se o limite de concorrência do tipo não foi atingido.
        Retorna `job_id` ou `None` quando saturado (HTTP 429).
        """
        limit = (
            self.max_concurrent_analyze
            if job_type == "analyze"
            else self.max_concurrent_render
        )
        now = time.time()
        job_id = f"job_{job_type}_{uuid.uuid4().hex[:10]}"

        with self.lock:
            self._prune_locked(now=now)
            active = sum(
                1 for j in self.jobs.values()
                if j.get("type") == job_type and j.get("status") == "running"
            )
            if active >= limit:
                return None

            self.jobs[job_id] = {
                "job_id": job_id,
                "type": job_type,
                "status": "running",
                "step": step,
                "progress": progress,
                "result": None,
                "error": None,
                "created_at": now,
                "updated_at": now,
            }
            return job_id

    def update_job(
        self,
        job_id: str,
        status: Optional[str] = None,
        step: Optional[str] = None,
        progress: Optional[int] = None,
        result: Optional[Any] = None,
        error: Optional[str] = None,
    ) -> None:
        with self.lock:
            if job_id not in self.jobs:
                return
            if status is not None:
                self.jobs[job_id]["status"] = status
            if step is not None:
                self.jobs[job_id]["step"] = step
            if progress is not None:
                self.jobs[job_id]["progress"] = progress
            if result is not None:
                self.jobs[job_id]["result"] = result
            if error is not None:
                self.jobs[job_id]["error"] = error
            self.jobs[job_id]["updated_at"] = time.time()

    def mark_job_error(self, job_id: str, job_type: str, step: str, error: str) -> None:
        """Grava status='error' sob lock mesmo se o registro do job ainda não existir."""
        now = time.time()
        with self.lock:
            if job_id not in self.jobs:
                self.jobs[job_id] = {"job_id": job_id, "type": job_type, "created_at": now}
            self.jobs[job_id]["status"] = "error"
            self.jobs[job_id]["step"] = step
            self.jobs[job_id]["progress"] = 100
            self.jobs[job_id]["error"] = str(error)
            self.jobs[job_id]["updated_at"] = now

    def get_job_snapshot(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retorna cópia profunda segura (deepcopy) do estado de um job sob lock."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            return copy.deepcopy(job)

    def active_counts(self) -> Dict[str, int]:
        """Retorna contagens de jobs em execução por tipo para observabilidade (/api/health)."""
        with self.lock:
            self._prune_locked()
            analyze_running = sum(
                1 for j in self.jobs.values()
                if j.get("type") == "analyze" and j.get("status") == "running"
            )
            render_running = sum(
                1 for j in self.jobs.values()
                if j.get("type") == "render" and j.get("status") == "running"
            )
            return {
                "analyze_running": analyze_running,
                "render_running": render_running,
                "total_tracked": len(self.jobs),
                "cached_analyses": len(self.analyses_cache),
            }

    def set_analysis_cache(self, video_id: int, payload: Dict[str, Any]) -> None:
        now = time.time()
        entry = dict(payload)
        entry["_cached_at"] = now
        with self.lock:
            self.analyses_cache[int(video_id)] = entry
            self.analyses_cache.move_to_end(int(video_id))
            self._prune_locked(now=now)

    def get_analysis_cache(self, video_id: int) -> Optional[Dict[str, Any]]:
        now = time.time()
        with self.lock:
            entry = self.analyses_cache.get(int(video_id))
            if entry is None:
                return None
            if (now - float(entry.get("_cached_at", now))) > self.cache_ttl_seconds:
                self.analyses_cache.pop(int(video_id), None)
                return None
            self.analyses_cache.move_to_end(int(video_id))
            return dict(entry)

    def find_analysis_cache_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        now = time.time()
        with self.lock:
            self._prune_locked(now=now)
            for entry in self.analyses_cache.values():
                if entry.get("url") == url:
                    return dict(entry)
        return None

    def pop_analysis_cache(self, video_id: int) -> None:
        with self.lock:
            self.analyses_cache.pop(int(video_id), None)

    def clear(self) -> None:
        with self.lock:
            self.jobs.clear()
            self.analyses_cache.clear()


job_manager = JobManager()
