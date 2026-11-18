"""
Módulo de Gerenciamento do Banco de Dados SQLite (EasyKut)
Responsável pela inicialização das tabelas e operações CRUD de vídeos e cortes.
"""

from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

# Diretório base do projeto e pasta de dados
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "cortador.db"


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """
    Cria e retorna uma conexão com o banco de dados SQLite.
    Habilita suporte a chaves estrangeiras, WAL mode para melhor concorrência no Streamlit,
    e define sqlite3.Row como fábrica de linhas para acesso por chave.
    """
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Configurações de concorrência e integridade referencial
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """
    Inicializa o banco de dados criando as tabelas necessárias caso não existam.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Tabela de vídeos principais
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT DEFAULT '',
                duration REAL DEFAULT 0.0,
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
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                duration REAL GENERATED ALWAYS AS (end_time - start_time) VIRTUAL,
                virality_score REAL DEFAULT 0.0,
                justification TEXT DEFAULT '',
                output_path TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (video_id) REFERENCES videos (id) ON DELETE CASCADE
            );
        """)

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
    Insere um novo vídeo na base e retorna o ID gerado.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO videos (url, title, duration, file_path, audio_path, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (url, title, duration, file_path, audio_path, status))
        conn.commit()
        return cursor.lastrowid


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


def list_videos(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """
    Lista todos os vídeos cadastrados ordenados pelo mais recente.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM videos ORDER BY created_at DESC, id DESC")
        return [dict(row) for row in cursor.fetchall()]


def delete_video(video_id: int, delete_files: bool = True, db_path: Optional[Path] = None) -> bool:
    """
    Remove um vídeo e seus cortes associados (via cascade no SQLite).
    Se delete_files=True, remove também os arquivos físicos do disco (vídeos, áudios e cortes).
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        if delete_files:
            # Coleta caminhos de mídias brutas associadas
            cursor.execute("SELECT file_path, audio_path FROM videos WHERE id = ?", (video_id,))
            vid_row = cursor.fetchone()
            files_to_delete = []
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

            # Exclui arquivos físicos com segurança
            for f in files_to_delete:
                try:
                    if f.exists() and f.is_file():
                        f.unlink()
                except Exception:
                    pass

        cursor.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        conn.commit()
        return cursor.rowcount > 0


def add_corte(
    video_id: int,
    start_time: float,
    end_time: float,
    title: str = "",
    virality_score: float = 0.0,
    justification: str = "",
    output_path: str = "",
    status: str = "pending",
    db_path: Optional[Path] = None
) -> int:
    """
    Insere um corte associado a um vídeo e retorna o ID do corte.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO cortes (video_id, title, start_time, end_time, virality_score, justification, output_path, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (video_id, title, start_time, end_time, virality_score, justification, output_path, status))
        conn.commit()
        return cursor.lastrowid


def list_cortes(video_id: Optional[int] = None, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """
    Lista cortes cadastrados. Se video_id for fornecido, filtra por ele.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        if video_id is not None:
            cursor.execute("SELECT * FROM cortes WHERE video_id = ? ORDER BY start_time ASC", (video_id,))
        else:
            cursor.execute("SELECT * FROM cortes ORDER BY created_at DESC, id DESC")
        return [dict(row) for row in cursor.fetchall()]


def update_corte_status(
    corte_id: int,
    status: str,
    output_path: Optional[str] = None,
    db_path: Optional[Path] = None
) -> bool:
    """
    Atualiza o status e caminho de saída do corte processado.
    """
    updates = ["status = ?"]
    params: List[Any] = [status]

    if output_path is not None:
        updates.append("output_path = ?")
        params.append(output_path)

    params.append(corte_id)
    query = f"UPDATE cortes SET {', '.join(updates)} WHERE id = ?"

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor.rowcount > 0


def save_analysis(
    video_id: int,
    transcript: Any,
    analysis_data: Any,
    db_path: Optional[Path] = None
) -> List[int]:
    """
    Salva a análise de cortes gerada pela IA para um determinado vídeo.
    Compatível com listas de dicionários ou objetos contendo a chave 'cortes'.
    Atualiza o status do vídeo para 'analyzed' e insere cada corte na tabela 'cortes'.
    Retorna a lista de IDs dos cortes gerados.
    """
    # Atualiza o status do vídeo
    update_video_status(video_id=video_id, status="analyzed", db_path=db_path)

    # Normaliza lista de cortes
    cortes = []
    if isinstance(analysis_data, dict):
        cortes = analysis_data.get("cortes", []) or analysis_data.get("cuts", [])
    elif isinstance(analysis_data, list):
        cortes = analysis_data

    inserted_ids: List[int] = []
    for c in cortes:
        if isinstance(c, dict):
            c_id = add_corte(
                video_id=video_id,
                start_time=float(c.get("start_time", 0.0)),
                end_time=float(c.get("end_time", 0.0)),
                title=str(c.get("title", "")),
                virality_score=float(c.get("virality_score", 0.0)),
                justification=str(c.get("justification", "")),
                status="pending",
                db_path=db_path
            )
            inserted_ids.append(c_id)

    return inserted_ids
