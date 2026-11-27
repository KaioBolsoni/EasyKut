"""
Repositório SQLite para entidades da plataforma V4.0:
- brand_kits (Identidade Visual Reutilizável — Onda 2)
- job_queue (Persistência e recuperação de fila de jobs — Onda 3)
- scheduled_posts (Agendamento e publicação multiplataforma — Onda 4)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from database import db_manager

logger = logging.getLogger(__name__)

VALID_LOGO_POSITIONS = {"top_left", "top_right", "bottom_left", "bottom_right"}
VALID_PLATFORMS = {"youtube_shorts", "tiktok", "instagram_reels"}


# ==========================================
# BRAND KITS (ONDA 2)
# ==========================================
def _deserialize_brand_kit(row: Any) -> Dict[str, Any]:
    item = dict(row)
    item["progress_bar_enabled"] = bool(item.get("progress_bar_enabled", 1))
    item["hook_banner_enabled"] = bool(item.get("hook_banner_enabled", 0))
    lp = item.get("logo_path") or ""
    item["logo_url"] = f"/data/brands/{Path(lp).name}" if (lp and Path(lp).exists()) else None
    return item


def upsert_brand_kit(
    name: str,
    logo_path: str = "",
    logo_position: str = "top_right",
    logo_opacity: float = 0.85,
    logo_scale: float = 0.14,
    subtitle_preset: str = "hormozi",
    progress_bar_enabled: bool = True,
    progress_bar_color: str = "#6366F1",
    hook_banner_enabled: bool = False,
    hook_banner_duration: float = 3.0,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Cria ou atualiza um Brand Kit pelo nome e retorna o registro completo."""
    clean_name = str(name or "").strip()[:80]
    if not clean_name:
        raise ValueError("Nome do Brand Kit é obrigatório.")
    pos = logo_position if logo_position in VALID_LOGO_POSITIONS else "top_right"
    opacity = max(0.0, min(1.0, float(logo_opacity)))
    scale = max(0.05, min(0.40, float(logo_scale)))
    banner_dur = max(0.5, min(10.0, float(hook_banner_duration)))

    with db_manager.get_connection(db_path) as conn:
        cur = conn.cursor()
        existing = cur.execute("SELECT id FROM brand_kits WHERE name = ?", (clean_name,)).fetchone()
        if existing:
            kit_id = int(existing["id"])
            cur.execute(
                """
                UPDATE brand_kits
                SET logo_path = ?, logo_position = ?, logo_opacity = ?, logo_scale = ?,
                    subtitle_preset = ?, progress_bar_enabled = ?, progress_bar_color = ?,
                    hook_banner_enabled = ?, hook_banner_duration = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    str(logo_path or ""),
                    pos,
                    opacity,
                    scale,
                    str(subtitle_preset or "hormozi"),
                    1 if progress_bar_enabled else 0,
                    str(progress_bar_color or "#6366F1")[:16],
                    1 if hook_banner_enabled else 0,
                    banner_dur,
                    kit_id,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO brand_kits (
                    name, logo_path, logo_position, logo_opacity, logo_scale,
                    subtitle_preset, progress_bar_enabled, progress_bar_color,
                    hook_banner_enabled, hook_banner_duration
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_name,
                    str(logo_path or ""),
                    pos,
                    opacity,
                    scale,
                    str(subtitle_preset or "hormozi"),
                    1 if progress_bar_enabled else 0,
                    str(progress_bar_color or "#6366F1")[:16],
                    1 if hook_banner_enabled else 0,
                    banner_dur,
                ),
            )
            kit_id = int(cur.lastrowid)
        row = cur.execute("SELECT * FROM brand_kits WHERE id = ?", (kit_id,)).fetchone()
        return _deserialize_brand_kit(row)


def list_brand_kits(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    with db_manager.get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM brand_kits ORDER BY updated_at DESC, id DESC").fetchall()
        return [_deserialize_brand_kit(r) for r in rows]


def get_brand_kit(kit_id: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    with db_manager.get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM brand_kits WHERE id = ?", (kit_id,)).fetchone()
        return _deserialize_brand_kit(row) if row else None


def delete_brand_kit(kit_id: int, db_path: Optional[Path] = None) -> bool:
    with db_manager.get_connection(db_path) as conn:
        cur = conn.execute("DELETE FROM brand_kits WHERE id = ?", (kit_id,))
        return cur.rowcount > 0


# ==========================================
# PERSISTÊNCIA DA FILA DE JOBS (ONDA 3)
# ==========================================
def save_job_snapshot(job: Dict[str, Any], db_path: Optional[Path] = None) -> None:
    """Persiste snapshot do job em SQLite para sobreviver a reinicializações."""
    try:
        res_json = json.dumps(job.get("result"), ensure_ascii=False) if job.get("result") is not None else ""
    except Exception:
        res_json = ""
    try:
        with db_manager.get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO job_queue (job_id, job_type, status, step, progress, result_json, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    step = excluded.step,
                    progress = excluded.progress,
                    result_json = excluded.result_json,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    str(job["job_id"]),
                    str(job.get("type", "analyze")),
                    str(job.get("status", "running")),
                    str(job.get("step", "")),
                    int(job.get("progress", 0)),
                    res_json,
                    str(job.get("error") or ""),
                    float(job.get("created_at", 0.0)),
                    float(job.get("updated_at", 0.0)),
                ),
            )
    except Exception as exc:
        logger.debug("Falha não-crítica ao persistir job '%s': %s", job.get("job_id"), exc)


def list_persisted_jobs(limit: int = 50, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    with db_manager.get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM job_queue ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(200, int(limit))),),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            result_val = None
            if item.get("result_json"):
                try:
                    result_val = json.loads(item["result_json"])
                except Exception:
                    result_val = None
            out.append({
                "job_id": item["job_id"],
                "type": item["job_type"],
                "status": item["status"],
                "step": item["step"],
                "progress": int(item["progress"]),
                "result": result_val,
                "error": item["error"] or None,
                "created_at": float(item["created_at"]),
                "updated_at": float(item["updated_at"]),
            })
        return out


def mark_orphaned_jobs_interrupted(db_path: Optional[Path] = None) -> int:
    """Marca jobs que ficaram em 'running' após reinício abrupto como 'interrupted'."""
    try:
        with db_manager.get_connection(db_path) as conn:
            cur = conn.execute(
                """
                UPDATE job_queue
                SET status = 'interrupted',
                    step = 'Interrompido por reinicialização do servidor',
                    error = 'Servidor reiniciado durante a execução do job.'
                WHERE status = 'running'
                """
            )
            return int(cur.rowcount)
    except Exception as exc:
        logger.debug("Falha ao recuperar jobs órfãos: %s", exc)
        return 0


# ==========================================
# AGENDAMENTO DE PUBLICAÇÕES (ONDA 4)
# ==========================================
def create_scheduled_post(
    corte_id: int,
    platform: str,
    title: str,
    caption: str,
    hashtags: str,
    scheduled_at: str,
    status: str = "scheduled",
    external_id: str = "",
    error: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    plat = str(platform or "").strip().lower()
    if plat not in VALID_PLATFORMS:
        raise ValueError(f"Plataforma não suportada: {platform}")
    with db_manager.get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO scheduled_posts (corte_id, platform, title, caption, hashtags, scheduled_at, status, external_id, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(corte_id),
                plat,
                str(title or "").strip()[:150],
                str(caption or "").strip()[:2200],
                str(hashtags or "").strip()[:500],
                str(scheduled_at or "").strip(),
                str(status or "scheduled"),
                str(external_id or ""),
                str(error or ""),
            ),
        )
        post_id = int(cur.lastrowid)
        row = cur.execute("SELECT * FROM scheduled_posts WHERE id = ?", (post_id,)).fetchone()
        return dict(row)


def list_scheduled_posts(
    corte_id: Optional[int] = None,
    status: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    conds: List[str] = []
    params: List[Any] = []
    if corte_id is not None:
        conds.append("corte_id = ?")
        params.append(int(corte_id))
    if status:
        conds.append("status = ?")
        params.append(str(status))
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    query = f"SELECT * FROM scheduled_posts {where} ORDER BY scheduled_at ASC, id DESC"  # nosec B608
    with db_manager.get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def update_scheduled_post_status(
    post_id: int,
    status: str,
    external_id: str = "",
    error: str = "",
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    with db_manager.get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE scheduled_posts
            SET status = ?, external_id = ?, error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (str(status), str(external_id or ""), str(error or ""), int(post_id)),
        )
        if cur.rowcount <= 0:
            return None
        row = cur.execute("SELECT * FROM scheduled_posts WHERE id = ?", (int(post_id),)).fetchone()
        return dict(row) if row else None


def delete_scheduled_post(post_id: int, db_path: Optional[Path] = None) -> bool:
    with db_manager.get_connection(db_path) as conn:
        cur = conn.execute("DELETE FROM scheduled_posts WHERE id = ?", (int(post_id),))
        return cur.rowcount > 0
