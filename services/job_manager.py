"""
Gerenciador thread-safe de jobs assíncronos e cache em memória (analyses_cache)
com limites de concorrência, TTL/LRU, persistência em SQLite (Onda 3) e cancelamento cooperativo.
"""

from collections import OrderedDict
import copy
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set
import uuid

from database import platform_repo

logger = logging.getLogger(__name__)


class JobManager:
    """Centraliza o ciclo de vida dos jobs, semáforo FFmpeg, processos ativos e cache LRU/TTL."""

    def __init__(
        self,
        max_concurrent_analyze: int = 2,
        max_concurrent_render: int = 2,
        max_preview_ffmpeg: int = 2,
        job_ttl_seconds: float = 3600.0,
        max_jobs: int = 200,
        cache_ttl_seconds: float = 7200.0,
        max_cache_entries: int = 50,
        persist_to_db: bool = True,
    ) -> None:
        self.max_concurrent_analyze = max_concurrent_analyze
        self.max_concurrent_render = max_concurrent_render
        self.job_ttl_seconds = job_ttl_seconds
        self.max_jobs = max_jobs
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_cache_entries = max_cache_entries
        self.persist_to_db = persist_to_db

        self.lock = threading.Lock()
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.analyses_cache: OrderedDict[int, Dict[str, Any]] = OrderedDict()
        self.preview_ffmpeg_semaphore = threading.Semaphore(max_preview_ffmpeg)
        self._cancelled_jobs: Set[str] = set()
        self._active_processes: Dict[str, List[Any]] = {}

    def _persist_snapshot(self, job: Dict[str, Any]) -> None:
        if self.persist_to_db:
            platform_repo.save_job_snapshot(job)

    def recover_orphaned_jobs(self) -> int:
        """Marca jobs deixados em 'running' por reinício anterior como 'interrupted'."""
        if not self.persist_to_db:
            return 0
        return platform_repo.mark_orphaned_jobs_interrupted()

    def _prune_locked(self, now: Optional[float] = None) -> Dict[str, int]:
        """Remove jobs finalizados expirados/excedentes e entradas vencidas do cache LRU."""
        ts = time.time() if now is None else float(now)
        pruned_jobs = 0
        pruned_cache = 0

        expired_job_ids = [
            jid
            for jid, j in self.jobs.items()
            if j.get("status") != "running"
            and (ts - float(j.get("updated_at", j.get("created_at", ts)))) > self.job_ttl_seconds
        ]
        for jid in expired_job_ids:
            self.jobs.pop(jid, None)
            self._cancelled_jobs.discard(jid)
            self._active_processes.pop(jid, None)
            pruned_jobs += 1

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
                self._cancelled_jobs.discard(jid)
                self._active_processes.pop(jid, None)
                pruned_jobs += 1

        expired_cache_keys = [
            vid
            for vid, entry in self.analyses_cache.items()
            if (ts - float(entry.get("_cached_at", ts))) > self.cache_ttl_seconds
        ]
        for vid in expired_cache_keys:
            self.analyses_cache.pop(vid, None)
            pruned_cache += 1

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

            job_entry = {
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
            self.jobs[job_id] = job_entry
            snapshot = copy.deepcopy(job_entry)

        self._persist_snapshot(snapshot)
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
        snapshot: Optional[Dict[str, Any]] = None
        with self.lock:
            if job_id not in self.jobs:
                return
            if self.jobs[job_id].get("status") == "cancelled" and status != "cancelled":
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
            snapshot = copy.deepcopy(self.jobs[job_id])
        if snapshot:
            self._persist_snapshot(snapshot)

    def mark_job_error(self, job_id: str, job_type: str, step: str, error: str) -> None:
        """Grava status='error' sob lock mesmo se o registro do job ainda não existir."""
        now = time.time()
        with self.lock:
            if job_id not in self.jobs:
                self.jobs[job_id] = {"job_id": job_id, "type": job_type, "created_at": now}
            if self.jobs[job_id].get("status") == "cancelled":
                return
            self.jobs[job_id]["status"] = "error"
            self.jobs[job_id]["step"] = step
            self.jobs[job_id]["progress"] = 100
            self.jobs[job_id]["error"] = str(error)
            self.jobs[job_id]["updated_at"] = now
            snapshot = copy.deepcopy(self.jobs[job_id])
        self._persist_snapshot(snapshot)

    def register_process(self, job_id: str, proc: Any) -> None:
        with self.lock:
            self._active_processes.setdefault(job_id, []).append(proc)

    def unregister_process(self, job_id: str, proc: Any) -> None:
        with self.lock:
            procs = self._active_processes.get(job_id)
            if procs and proc in procs:
                procs.remove(proc)
            if procs == []:
                self._active_processes.pop(job_id, None)

    def is_cancelled(self, job_id: str) -> bool:
        with self.lock:
            return job_id in self._cancelled_jobs or self.jobs.get(job_id, {}).get("status") == "cancelled"

    def cancel_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Marca um job como cancelado e encerra com segurança qualquer subprocesso FFmpeg ativo."""
        procs_to_kill: List[Any] = []
        snapshot: Optional[Dict[str, Any]] = None
        now = time.time()
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            self._cancelled_jobs.add(job_id)
            if job.get("status") == "running":
                job["status"] = "cancelled"
                job["step"] = "Job cancelado pelo usuário."
                job["error"] = "Cancelado pelo usuário."
                job["updated_at"] = now
            procs_to_kill = list(self._active_processes.pop(job_id, []))
            snapshot = copy.deepcopy(job)

        for proc in procs_to_kill:
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception as exc:
                logger.warning("Erro ao encerrar processo do job '%s': %s", job_id, exc)

        if snapshot:
            self._persist_snapshot(snapshot)
        return snapshot

    def get_job_snapshot(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retorna cópia profunda segura (deepcopy) do estado de um job sob lock."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is not None:
                return copy.deepcopy(job)
        if self.persist_to_db:
            for item in platform_repo.list_persisted_jobs(limit=100):
                if item["job_id"] == job_id:
                    return item
        return None

    def list_jobs(self, limit: int = 30) -> List[Dict[str, Any]]:
        """Lista jobs ativos em memória mesclados com histórico persistido no SQLite."""
        merged: Dict[str, Dict[str, Any]] = {}
        if self.persist_to_db:
            try:
                for p in platform_repo.list_persisted_jobs(limit=limit):
                    merged[p["job_id"]] = p
            except Exception:
                pass
        with self.lock:
            for jid, j in self.jobs.items():
                merged[jid] = copy.deepcopy(j)
        ordered = sorted(merged.values(), key=lambda x: float(x.get("updated_at", 0.0)), reverse=True)
        return ordered[:max(1, min(100, int(limit)))]

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
                if j.get("type") in ("render", "reel") and j.get("status") == "running"
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
            self._cancelled_jobs.clear()
            self._active_processes.clear()


job_manager = JobManager()
