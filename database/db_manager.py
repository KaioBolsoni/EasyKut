"""
Módulo de Gerenciamento do Banco de Dados SQLite (EasyKut)
Responsável pela inicialização das tabelas e operações CRUD de vídeos e cortes.
"""

from contextlib import contextmanager
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Diretório base do projeto e pasta de dados (caminhos absolutos determinísticos)
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = (BASE_DIR / "data").resolve()
DEFAULT_DB_PATH = (DATA_DIR / "cortador.db").resolve()

SCHEMA_VERSION = 4
VALID_LAYOUT_MODES = {
    "horizontal_16_9",
    "vertical_blur",
    "manual_crop",
    "split_screen",
    "face_tracking_9_16",
    "square_1_1",
}


def _validate_corte_bounds(
    start_time: float,
    end_time: float,
    virality_score: Optional[float] = None
) -> None:
    """Valida invariantes de domínio de um corte antes de persistir no SQLite."""
    s = float(start_time)
    e = float(end_time)
    if s < 0.0:
        raise ValueError(f"start_time não pode ser negativo: {s}")
    if e <= s:
        raise ValueError(f"end_time ({e}) deve ser estritamente maior que start_time ({s}).")
    if virality_score is not None:
        sc = float(virality_score)
        if sc < 0.0 or sc > 100.0:
            raise ValueError(f"virality_score deve estar entre 0 e 100: {sc}")


def _serialize_layout_params(layout_params: Any) -> str:
    """Serializa layout_params (dict ou string JSON) com segurança para armazenamento TEXT."""
    if layout_params is None:
        return "{}"
    if isinstance(layout_params, str):
        stripped = layout_params.strip()
        if not stripped:
            return "{}"
        json.loads(stripped)  # valida JSON
        return stripped
    if isinstance(layout_params, dict):
        return json.dumps(layout_params, ensure_ascii=False)
    raise ValueError("layout_params deve ser um dicionário ou string JSON válida.")


def _deserialize_corte_row(row: sqlite3.Row) -> Dict[str, Any]:
    """Converte sqlite3.Row de 'cortes' em dict com layout_params desserializado e burn_subtitles bool."""
    item = dict(row)
    raw_lp = item.get("layout_params")
    if isinstance(raw_lp, str):
        try:
            item["layout_params"] = json.loads(raw_lp) if raw_lp.strip() else {}
        except Exception:
            item["layout_params"] = {}
    elif raw_lp is None:
        item["layout_params"] = {}
    if "burn_subtitles" in item:
        item["burn_subtitles"] = bool(item["burn_subtitles"])
    raw_seo = item.get("seo_kit_json")
    if isinstance(raw_seo, str) and raw_seo.strip() and raw_seo.strip() != "{}":
        try:
            item["seo_kit"] = json.loads(raw_seo)
        except Exception:
            item["seo_kit"] = None
    else:
        item["seo_kit"] = None
    return item


def _resolve_db_path(db_path: Optional[Path] = None) -> Path:
    """Garante que todas as operações utilizem exatamente o mesmo caminho absoluto de banco SQLite."""
    return Path(db_path).resolve() if db_path is not None else DEFAULT_DB_PATH


@contextmanager
def get_connection(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """
    Context manager para conexão SQLite com fechamento explícito garantido no Windows,
    evitando vazamento de handles e bloqueios 'database is locked'.
    """
    path = _resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 15000;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _run_migrations(conn: sqlite3.Connection) -> None:
    """
    Executa migrations idempotentes baseadas em PRAGMA user_version:
    - v2: colunas V3 em 'cortes' (tipo, layout_mode, layout_params, burn_subtitles, updated_at).
    - v3: índices secundários de performance em 'videos' e 'cortes'.
    """
    cursor = conn.cursor()
    current_version = int(cursor.execute("PRAGMA user_version;").fetchone()[0])

    if current_version < 2:
        existing_cols = {
            row["name"]
            for row in cursor.execute("PRAGMA table_info(cortes);").fetchall()
        }
        migrations_v2 = [
            ("tipo", "TEXT DEFAULT 'short'"),
            ("layout_mode", "TEXT DEFAULT 'vertical_blur'"),
            ("layout_params", "TEXT DEFAULT '{}'"),
            ("burn_subtitles", "INTEGER DEFAULT 1"),
            ("updated_at", "TIMESTAMP"),
        ]
        added_updated_at = False
        for col_name, col_def in migrations_v2:
            if col_name not in existing_cols:
                cursor.execute(f"ALTER TABLE cortes ADD COLUMN {col_name} {col_def};")
                if col_name == "updated_at":
                    added_updated_at = True
        if added_updated_at:
            cursor.execute("UPDATE cortes SET updated_at = COALESCE(created_at, CURRENT_TIMESTAMP) WHERE updated_at IS NULL;")

    if current_version < 3:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cortes_video_id ON cortes (video_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cortes_virality_score ON cortes (virality_score);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_created_at ON videos (created_at DESC, id DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_url ON videos (url);")

    if current_version < 4:
        existing_cols = {
            row["name"]
            for row in cursor.execute("PRAGMA table_info(cortes);").fetchall()
        }
        if "seo_kit_json" not in existing_cols:
            cursor.execute("ALTER TABLE cortes ADD COLUMN seo_kit_json TEXT DEFAULT '{}';")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS brand_kits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                logo_path TEXT DEFAULT '',
                logo_position TEXT DEFAULT 'top_right',
                logo_opacity REAL DEFAULT 0.85 CHECK (logo_opacity >= 0.0 AND logo_opacity <= 1.0),
                logo_scale REAL DEFAULT 0.14 CHECK (logo_scale >= 0.05 AND logo_scale <= 0.40),
                subtitle_preset TEXT DEFAULT 'hormozi',
                progress_bar_enabled INTEGER DEFAULT 1,
                progress_bar_color TEXT DEFAULT '#6366F1',
                hook_banner_enabled INTEGER DEFAULT 0,
                hook_banner_duration REAL DEFAULT 3.0 CHECK (hook_banner_duration >= 0.5 AND hook_banner_duration <= 10.0),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS job_queue (
                job_id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                step TEXT DEFAULT '',
                progress INTEGER DEFAULT 0,
                result_json TEXT DEFAULT '',
                error TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                corte_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                title TEXT NOT NULL,
                caption TEXT DEFAULT '',
                hashtags TEXT DEFAULT '',
                scheduled_at TEXT NOT NULL,
                status TEXT DEFAULT 'scheduled',
                external_id TEXT DEFAULT '',
                error TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (corte_id) REFERENCES cortes (id) ON DELETE CASCADE
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_queue_status ON job_queue (status, updated_at DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_posts_status ON scheduled_posts (status, scheduled_at ASC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_posts_corte_id ON scheduled_posts (corte_id);")

    if current_version < SCHEMA_VERSION:
        cursor.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")


def init_db(db_path: Optional[Path] = None) -> None:
    """
    Inicializa o banco de dados criando as tabelas necessárias e aplicando migrations de schema.
    """
    with get_connection(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        cursor = conn.cursor()

        # Tabela de vídeos principais
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT DEFAULT '',
                duration REAL DEFAULT 0.0 CHECK (duration >= 0.0),
                file_path TEXT DEFAULT '',
                audio_path TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Tabela de cortes/clipes gerados
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cortes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER NOT NULL,
                title TEXT DEFAULT '',
                start_time REAL NOT NULL CHECK (start_time >= 0.0),
                end_time REAL NOT NULL CHECK (end_time > start_time),
                duration REAL GENERATED ALWAYS AS (end_time - start_time) VIRTUAL,
                virality_score REAL DEFAULT 0.0 CHECK (virality_score >= 0.0 AND virality_score <= 100.0),
                justification TEXT DEFAULT '',
                tipo TEXT DEFAULT 'short',
                layout_mode TEXT DEFAULT 'vertical_blur',
                layout_params TEXT DEFAULT '{}',
                burn_subtitles INTEGER DEFAULT 1,
                output_path TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (video_id) REFERENCES videos (id) ON DELETE CASCADE
            );
        """)

        _run_migrations(conn)
        conn.commit()


def add_video(
    url: str,
    title: str = "",
    duration: float = 0.0,
    file_path: str = "",
    audio_path: str = "",
    status: str = "pending",
    db_path: Optional[Path] = None
) -> int:
    """
    Insere um novo vídeo na base com commit imediato e retorna o ID inteiro gerado (cursor.lastrowid).
    """
    target_db = _resolve_db_path(db_path)
    with get_connection(target_db) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO videos (url, title, duration, file_path, audio_path, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(url or ""), str(title or ""), float(duration or 0.0), str(file_path or ""), str(audio_path or ""), str(status or "pending")))
        conn.commit()
        last_id = cursor.lastrowid
        if last_id is None or int(last_id) <= 0:
            raise RuntimeError("Falha ao obter cursor.lastrowid válido após inserção na tabela 'videos'.")
        return int(last_id)


def get_video(video_id: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """
    Busca um vídeo pelo seu ID. Retorna um dicionário ou None se não encontrado.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_video_status(
    video_id: int,
    status: str,
    title: Optional[str] = None,
    duration: Optional[float] = None,
    file_path: Optional[str] = None,
    audio_path: Optional[str] = None,
    db_path: Optional[Path] = None
) -> bool:
    """
    Atualiza o status e metadados opcionais de um vídeo.
    """
    updates = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
    params: List[Any] = [status]

    if title is not None:
        updates.append("title = ?")
        params.append(title)
    if duration is not None:
        updates.append("duration = ?")
        params.append(duration)
    if file_path is not None:
        updates.append("file_path = ?")
        params.append(file_path)
    if audio_path is not None:
        updates.append("audio_path = ?")
        params.append(audio_path)

    params.append(video_id)
    query = f"UPDATE videos SET {', '.join(updates)} WHERE id = ?"

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor.rowcount > 0


def list_videos(
    search: Optional[str] = None,
    db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """
    Lista vídeos cadastrados ordenados pelo mais recente.
    Suporta busca parcial por título ou URL.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        if search and search.strip():
            term = f"%{search.strip()}%"
            cursor.execute(
                "SELECT * FROM videos WHERE title LIKE ? OR url LIKE ? ORDER BY created_at DESC, id DESC",
                (term, term)
            )
        else:
            cursor.execute("SELECT * FROM videos ORDER BY created_at DESC, id DESC")
        return [dict(row) for row in cursor.fetchall()]


def list_videos_with_cortes(
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db_path: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Lista vídeos paginados junto com todos os seus cortes em 1 única conexão (sem N+1),
    executando 1 consulta paginada em 'videos' + 1 consulta em lote (WHERE video_id IN (...)) em 'cortes'.
    Retorna (lista_de_videos_com_cortes, total_de_videos_correspondentes).
    """
    safe_limit = max(1, min(200, int(limit)))
    safe_offset = max(0, int(offset))

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        if search and search.strip():
            term = f"%{search.strip()}%"
            total_count = int(
                cursor.execute(
                    "SELECT COUNT(*) FROM videos WHERE title LIKE ? OR url LIKE ?",
                    (term, term),
                ).fetchone()[0]
            )
            video_rows = cursor.execute(
                "SELECT * FROM videos WHERE title LIKE ? OR url LIKE ? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (term, term, safe_limit, safe_offset),
            ).fetchall()
        else:
            total_count = int(cursor.execute("SELECT COUNT(*) FROM videos").fetchone()[0])
            video_rows = cursor.execute(
                "SELECT * FROM videos ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (safe_limit, safe_offset),
            ).fetchall()

        videos = [dict(row) for row in video_rows]
        if not videos:
            return [], total_count

        video_ids = [int(v["id"]) for v in videos]
        placeholders = ",".join("?" for _ in video_ids)
        corte_rows = cursor.execute(
            f"SELECT * FROM cortes WHERE video_id IN ({placeholders}) ORDER BY video_id ASC, start_time ASC",  # nosec B608
            video_ids,
        ).fetchall()

        cortes_by_video: Dict[int, List[Dict[str, Any]]] = {vid: [] for vid in video_ids}
        for row in corte_rows:
            item = _deserialize_corte_row(row)
            cortes_by_video[int(item["video_id"])].append(item)

        for v in videos:
            v["cortes"] = cortes_by_video.get(int(v["id"]), [])

        return videos, total_count


def delete_video(video_id: int, delete_files: bool = False, db_path: Optional[Path] = None) -> bool:
    """
    Remove um vídeo e seus cortes associados (via cascade no SQLite).
    Se delete_files=True, remove também os arquivos físicos confinados a DATA_DIR após a confirmação do DELETE SQL.
    """
    target_db = _resolve_db_path(db_path)
    files_to_delete: List[Path] = []

    with get_connection(target_db) as conn:
        cursor = conn.cursor()

        if delete_files:
            # Coleta caminhos de mídias brutas associadas
            cursor.execute("SELECT file_path, audio_path FROM videos WHERE id = ?", (video_id,))
            vid_row = cursor.fetchone()
            if vid_row:
                if vid_row["file_path"]:
                    files_to_delete.append(Path(vid_row["file_path"]))
                if vid_row["audio_path"]:
                    files_to_delete.append(Path(vid_row["audio_path"]))

            # Coleta caminhos de cortes renderizados
            cursor.execute("SELECT output_path FROM cortes WHERE video_id = ?", (video_id,))
            for c_row in cursor.fetchall():
                if c_row["output_path"]:
                    files_to_delete.append(Path(c_row["output_path"]))

            # Inclui caches de transcrição (atual em data/cache e legado em data/temp) associados ao vídeo
            files_to_delete.append(DATA_DIR / "cache" / f"transcript_{video_id}.json")
            files_to_delete.append(DATA_DIR / "temp" / f"transcript_{video_id}.json")

        cursor.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        conn.commit()
        deleted = cursor.rowcount > 0

    if deleted and delete_files:
        for f in files_to_delete:
            try:
                resolved = f.resolve()
                if not resolved.is_relative_to(DATA_DIR):
                    logger.warning("Exclusão física bloqueada por segurança (caminho fora de DATA_DIR): %s", f)
                    continue
                if resolved == target_db:
                    logger.warning("Exclusão física bloqueada: tentativa de remover o arquivo do banco SQLite.")
                    continue
                if resolved.exists() and resolved.is_file():
                    resolved.unlink()
            except Exception as exc:
                logger.warning("Falha ao remover arquivo físico '%s': %s", f.name, exc)

    return deleted


def add_corte(
    video_id: int,
    start_time: float,
    end_time: float,
    title: str = "",
    virality_score: float = 0.0,
    justification: str = "",
    output_path: str = "",
    status: str = "pending",
    db_path: Optional[Path] = None,
    tipo: str = "short",
    layout_mode: str = "vertical_blur",
    layout_params: Optional[Dict[str, Any]] = None,
    burn_subtitles: bool = True,
) -> int:
    """
    Insere um corte associado a um vídeo com validação de domínio e retorna o ID inteiro do corte.
    """
    if isinstance(video_id, bool) or video_id is None:
        raise ValueError(f"video_id inválido ao inserir corte: {video_id!r}")
    try:
        vid_int = int(video_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"video_id deve ser inteiro válido, recebido: {video_id!r}") from exc
    if vid_int <= 0:
        raise ValueError(f"video_id deve ser positivo, recebido: {vid_int}")

    _validate_corte_bounds(start_time, end_time, virality_score)
    lp_json = _serialize_layout_params(layout_params)
    mode_clean = str(layout_mode or "vertical_blur").strip() or "vertical_blur"
    tipo_clean = str(tipo or "short").strip().lower()
    if tipo_clean not in ("short", "longo"):
        tipo_clean = "short" if (float(end_time) - float(start_time)) <= 180.0 else "longo"

    target_db = _resolve_db_path(db_path)
    with get_connection(target_db) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO cortes (
                video_id, title, start_time, end_time, virality_score, justification,
                tipo, layout_mode, layout_params, burn_subtitles, output_path, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            vid_int, str(title or ""), float(start_time), float(end_time), float(virality_score or 0.0),
            str(justification or ""), tipo_clean, mode_clean, lp_json, 1 if burn_subtitles else 0,
            str(output_path or ""), str(status or "pending")
        ))
        conn.commit()
        return int(cursor.lastrowid)


def get_corte(corte_id: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """
    Busca um corte específico pelo ID. Retorna dicionário com layout_params desserializado ou None.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cortes WHERE id = ?", (corte_id,))
        row = cursor.fetchone()
        return _deserialize_corte_row(row) if row else None


def list_cortes(
    video_id: Optional[int] = None,
    min_virality_score: Optional[float] = None,
    search: Optional[str] = None,
    db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """
    Lista cortes cadastrados. Suporta filtros opcionais por video_id, nota mínima de viralidade e busca por título.
    """
    conditions = []
    params: List[Any] = []

    if video_id is not None:
        conditions.append("video_id = ?")
        params.append(video_id)

    if min_virality_score is not None and min_virality_score > 0:
        conditions.append("virality_score >= ?")
        params.append(min_virality_score)

    if search and search.strip():
        conditions.append("title LIKE ?")
        params.append(f"%{search.strip()}%")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    order_clause = "ORDER BY start_time ASC" if video_id is not None else "ORDER BY created_at DESC, id DESC"
    query = f"SELECT * FROM cortes {where_clause} {order_clause}"

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [_deserialize_corte_row(row) for row in cursor.fetchall()]


def update_corte(
    corte_id: int,
    title: Optional[str] = None,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    virality_score: Optional[float] = None,
    justification: Optional[str] = None,
    tipo: Optional[str] = None,
    layout_mode: Optional[str] = None,
    layout_params: Optional[Dict[str, Any]] = None,
    burn_subtitles: Optional[bool] = None,
    output_path: Optional[str] = None,
    status: Optional[str] = None,
    seo_kit: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """
    Atualiza parcialmente (PATCH) metadados, tempos, layout e status de um corte existente,
    validando invariantes (start_time >= 0 e end_time > start_time) antes do UPDATE.
    """
    target_db = _resolve_db_path(db_path)
    with get_connection(target_db) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT start_time, end_time, virality_score FROM cortes WHERE id = ?", (corte_id,))
        existing = cursor.fetchone()
        if not existing:
            return False

        eff_start = float(start_time) if start_time is not None else float(existing["start_time"])
        eff_end = float(end_time) if end_time is not None else float(existing["end_time"])
        eff_score = float(virality_score) if virality_score is not None else float(existing["virality_score"])
        _validate_corte_bounds(eff_start, eff_end, eff_score)

        updates = ["updated_at = CURRENT_TIMESTAMP"]
        params: List[Any] = []

        if title is not None:
            updates.append("title = ?")
            params.append(str(title))
        if start_time is not None:
            updates.append("start_time = ?")
            params.append(eff_start)
        if end_time is not None:
            updates.append("end_time = ?")
            params.append(eff_end)
        if virality_score is not None:
            updates.append("virality_score = ?")
            params.append(eff_score)
        if justification is not None:
            updates.append("justification = ?")
            params.append(str(justification))
        if tipo is not None:
            t_clean = str(tipo).strip().lower()
            if t_clean not in ("short", "longo"):
                raise ValueError(f"tipo inválido: {tipo!r}")
            updates.append("tipo = ?")
            params.append(t_clean)
        if layout_mode is not None:
            m_clean = str(layout_mode).strip()
            if m_clean not in VALID_LAYOUT_MODES:
                raise ValueError(f"layout_mode inválido: {layout_mode!r}")
            updates.append("layout_mode = ?")
            params.append(m_clean)
        if layout_params is not None:
            updates.append("layout_params = ?")
            params.append(_serialize_layout_params(layout_params))
        if burn_subtitles is not None:
            updates.append("burn_subtitles = ?")
            params.append(1 if burn_subtitles else 0)
        if output_path is not None:
            updates.append("output_path = ?")
            params.append(str(output_path))
        if status is not None:
            updates.append("status = ?")
            params.append(str(status))
        if seo_kit is not None:
            updates.append("seo_kit_json = ?")
            params.append(json.dumps(seo_kit, ensure_ascii=False))

        params.append(corte_id)
        query = f"UPDATE cortes SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, params)
        conn.commit()
        return cursor.rowcount > 0


def update_corte_status(
    corte_id: int,
    status: str,
    output_path: Optional[str] = None,
    db_path: Optional[Path] = None
) -> bool:
    """
    Atualiza o status e caminho de saída do corte processado.
    """
    return update_corte(corte_id=corte_id, status=status, output_path=output_path, db_path=db_path)


def delete_corte(corte_id: int, delete_file: bool = False, db_path: Optional[Path] = None) -> bool:
    """
    Remove um corte individual pelo ID.
    Se delete_file=True, remove o arquivo .mp4 associado confinado a DATA_DIR após o DELETE SQL.
    """
    target_db = _resolve_db_path(db_path)
    out_file: Optional[Path] = None

    with get_connection(target_db) as conn:
        cursor = conn.cursor()
        if delete_file:
            cursor.execute("SELECT output_path FROM cortes WHERE id = ?", (corte_id,))
            row = cursor.fetchone()
            if row and row["output_path"]:
                out_file = Path(row["output_path"])

        cursor.execute("DELETE FROM cortes WHERE id = ?", (corte_id,))
        conn.commit()
        deleted = cursor.rowcount > 0

    if deleted and delete_file and out_file is not None:
        try:
            resolved = out_file.resolve()
            if not resolved.is_relative_to(DATA_DIR) or resolved == target_db:
                logger.warning("Exclusão física de corte bloqueada por segurança: %s", out_file)
            elif resolved.exists() and resolved.is_file():
                resolved.unlink()
        except Exception as exc:
            logger.warning("Falha ao remover arquivo físico do corte '%s': %s", out_file.name, exc)

    return deleted


def save_analysis(
    video_id: Optional[int],
    transcript: Any,
    analysis_data: Any,
    db_path: Optional[Path] = None,
    url: str = "",
    title: str = "",
    duration: float = 0.0,
    file_path: str = "",
    audio_path: str = "",
) -> List[int]:
    """
    Salva a análise de cortes gerada pela IA para um determinado vídeo de forma atômica.
    Valida se o video_id inteiro existe na tabela 'videos' usando o mesmo db_path;
    caso não exista (ou seja inválido), recupera pela URL ou cria o registro pai antes
    de inserir em 'cortes', prevenindo 'FOREIGN KEY constraint failed'.
    Retorna a lista de IDs inteiros dos cortes gerados.
    """
    target_db = _resolve_db_path(db_path)

    # Normaliza lista de cortes
    cortes = []
    if isinstance(analysis_data, dict):
        cortes = analysis_data.get("cortes", []) or analysis_data.get("cuts", [])
    elif isinstance(analysis_data, list):
        cortes = analysis_data

    # Tenta converter video_id para inteiro válido (evitando None, bool ou string URL)
    effective_video_id: Optional[int] = None
    if not isinstance(video_id, bool) and video_id is not None:
        try:
            parsed_id = int(video_id)
            if parsed_id > 0:
                effective_video_id = parsed_id
        except (TypeError, ValueError):
            if not url and isinstance(video_id, str) and video_id.strip():
                url = video_id.strip()

    with get_connection(target_db) as conn:
        cursor = conn.cursor()

        # 1. Verifica se o registro pai existe na tabela 'videos'
        parent_exists = False
        if effective_video_id is not None:
            cursor.execute("SELECT id FROM videos WHERE id = ?", (effective_video_id,))
            row = cursor.fetchone()
            if row is not None:
                effective_video_id = int(row["id"])
                parent_exists = True

        # 2. Caso não exista, tenta recuperar pela URL ou recria o registro pai no mesmo banco
        if not parent_exists:
            if url:
                cursor.execute(
                    "SELECT id FROM videos WHERE url = ? ORDER BY id DESC LIMIT 1",
                    (url,)
                )
                row = cursor.fetchone()
                if row is not None:
                    effective_video_id = int(row["id"])
                    parent_exists = True

        if not parent_exists:
            fallback_url = url.strip() if isinstance(url, str) and url.strip() else "unknown://recovered"
            cursor.execute("""
                INSERT INTO videos (url, title, duration, file_path, audio_path, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                fallback_url,
                str(title or ""),
                float(duration or 0.0),
                str(file_path or ""),
                str(audio_path or ""),
                "analyzed",
            ))
            conn.commit()
            effective_video_id = int(cursor.lastrowid)
        else:
            cursor.execute(
                "UPDATE videos SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                ("analyzed", effective_video_id)
            )

        # 3. Insere os cortes vinculados ao effective_video_id garantido na tabela 'videos'
        inserted_ids: List[int] = []
        for c in cortes:
            if isinstance(c, dict):
                c_start = max(0.0, float(c.get("start_time", 0.0)))
                c_end = max(c_start + 0.5, float(c.get("end_time", 0.0)))
                c_score = min(100.0, max(0.0, float(c.get("virality_score", 0.0))))
                c_tipo = str(c.get("tipo", "short" if (c_end - c_start) <= 180.0 else "longo")).strip().lower()
                if c_tipo not in ("short", "longo"):
                    c_tipo = "short" if (c_end - c_start) <= 180.0 else "longo"
                c_mode = str(c.get("layout_mode", "horizontal_16_9" if (c_tipo == "longo" or (c_end - c_start) >= 180.0) else "vertical_blur")).strip()
                c_lp = _serialize_layout_params(c.get("layout_params") or ({"zoom_factor": 1.0} if c_mode == "vertical_blur" else {}))
                c_subs = 1 if c.get("burn_subtitles", True) else 0

                cursor.execute("""
                    INSERT INTO cortes (
                        video_id, title, start_time, end_time, virality_score, justification,
                        tipo, layout_mode, layout_params, burn_subtitles, output_path, status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    effective_video_id,
                    str(c.get("title", "")),
                    c_start,
                    c_end,
                    c_score,
                    str(c.get("justification", "")),
                    c_tipo,
                    c_mode,
                    c_lp,
                    c_subs,
                    "",
                    "pending",
                ))
                inserted_ids.append(int(cursor.lastrowid))

        conn.commit()

    return inserted_ids
