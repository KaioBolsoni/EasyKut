"""
Fixtures compartilhadas para execução determinística e 100% isolada da suíte pytest.
Garante que nenhum teste leia ou modifique data/cortador.db ou mídias reais de produção.
"""

from pathlib import Path
import threading
from typing import Any, Dict, Iterator

from fastapi.testclient import TestClient
import pytest

from core import media_utils, video_engine
from database import db_manager
import server
from services.job_manager import job_manager


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Dict[str, Path]]:
    """
    Cria um diretório de dados temporário isolado (SQLite + temp + cortes + downloads + cache + logs)
    e redireciona todas as referências globais de db_manager, core e server.
    """
    data_dir = (tmp_path / "data").resolve()
    temp_dir = data_dir / "temp"
    cuts_dir = data_dir / "cortes"
    downloads_dir = data_dir / "downloads"
    cache_dir = data_dir / "cache"
    logs_dir = data_dir / "logs"
    db_path = data_dir / "cortador.db"

    for d in (data_dir, temp_dir, cuts_dir, downloads_dir, cache_dir, logs_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Redireciona camada de banco de dados
    monkeypatch.setattr(db_manager, "DATA_DIR", data_dir)
    monkeypatch.setattr(db_manager, "DEFAULT_DB_PATH", db_path)

    # Redireciona camada de utilitários e fachada do motor de vídeo
    for mod in (media_utils, video_engine):
        monkeypatch.setattr(mod, "DATA_DIR", data_dir)
        monkeypatch.setattr(mod, "DATA_DIR_RESOLVED", data_dir)
        monkeypatch.setattr(mod, "TEMP_DIR", temp_dir)
        monkeypatch.setattr(mod, "CUTS_DIR", cuts_dir)
        monkeypatch.setattr(mod, "DOWNLOADS_DIR", downloads_dir)

    # Redireciona servidor FastAPI e limpa estado em memória
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "TEMP_DIR", temp_dir)
    monkeypatch.setattr(server, "CUTS_DIR", cuts_dir)
    monkeypatch.setattr(server, "DOWNLOADS_DIR", downloads_dir)
    monkeypatch.setattr(server, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(server, "preview_ffmpeg_semaphore", threading.Semaphore(2))

    job_manager.clear()
    db_manager.init_db(db_path=db_path)

    yield {
        "data_dir": data_dir,
        "temp_dir": temp_dir,
        "cuts_dir": cuts_dir,
        "downloads_dir": downloads_dir,
        "cache_dir": cache_dir,
        "logs_dir": logs_dir,
        "db_path": db_path,
    }

    job_manager.clear()


@pytest.fixture()
def client(isolated_env: Dict[str, Path]) -> Iterator[TestClient]:
    """Cliente HTTP FastAPI apontando para o ambiente temporário isolado."""
    with TestClient(server.app) as test_client:
        yield test_client


@pytest.fixture()
def sample_video_and_cut(isolated_env: Dict[str, Path]) -> Dict[str, Any]:
    """Popula um vídeo e um corte válidos no banco isolado."""
    video_id = db_manager.add_video(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Vídeo de Teste Isolado",
        duration=300.0,
        status="analyzed",
    )
    corte_id = db_manager.add_corte(
        video_id=video_id,
        title="Gancho Inicial",
        start_time=10.0,
        end_time=45.0,
        virality_score=88.5,
        justification="Alta retenção nos primeiros segundos.",
        tipo="short",
        layout_mode="vertical_blur",
        layout_params={"zoom_factor": 1.15},
        burn_subtitles=True,
        status="pending",
    )
    return {
        "video_id": video_id,
        "corte_id": corte_id,
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    }
