"""
Testes unitários e de integridade da camada SQLite (database/db_manager.py).
Cobre migrations versionadas, constraints CHECK, integridade referencial (FK),
CRUD completo de videos e cortes, e confinamento de exclusão física em DATA_DIR.
"""

from pathlib import Path
import sqlite3

import pytest

from database import db_manager, platform_repo


def test_init_db_sets_schema_version_wal_mode_and_indexes(isolated_env):
    db_path = isolated_env["db_path"]
    with db_manager.get_connection(db_path) as conn:
        user_version = conn.execute("PRAGMA user_version;").fetchone()[0]
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        fk_enabled = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(cortes);").fetchall()}
        indexes = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index';").fetchall()
        }

        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';").fetchall()
        }

    assert user_version == db_manager.SCHEMA_VERSION == 4
    assert str(journal_mode).lower() == "wal"
    assert fk_enabled == 1
    assert {"tipo", "layout_mode", "layout_params", "burn_subtitles", "updated_at", "seo_kit_json"}.issubset(cols)
    assert {"brand_kits", "job_queue", "scheduled_posts"}.issubset(tables)
    assert {
        "idx_cortes_video_id",
        "idx_cortes_virality_score",
        "idx_videos_created_at",
        "idx_videos_url",
        "idx_job_queue_status",
        "idx_scheduled_posts_status",
    }.issubset(indexes)


def test_legacy_db_migration_v0_to_v2_preserves_rows_and_is_idempotent(tmp_path: Path):
    legacy_db = (tmp_path / "legacy_v0.db").resolve()

    # Cria manualmente schema legado v0 sem as colunas V3
    conn = sqlite3.connect(str(legacy_db))
    conn.executescript(
        """
        CREATE TABLE videos (
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
        CREATE TABLE cortes (
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
        INSERT INTO videos (url, title, duration, status)
        VALUES ('https://youtu.be/dQw4w9WgXcQ', 'Legado', 120.0, 'analyzed');
        INSERT INTO cortes (video_id, title, start_time, end_time, virality_score)
        VALUES (1, 'Corte Legado', 5.0, 25.0, 82.0);
        """
    )
    conn.commit()
    conn.close()

    # Executa migração duas vezes para provar idempotência (v0 -> v3)
    db_manager.init_db(db_path=legacy_db)
    db_manager.init_db(db_path=legacy_db)

    with db_manager.get_connection(legacy_db) as conn:
        assert conn.execute("PRAGMA user_version;").fetchone()[0] == 4
        indexes = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index';").fetchall()
        }
        assert "idx_cortes_video_id" in indexes
        assert "idx_videos_url" in indexes

    corte = db_manager.get_corte(1, db_path=legacy_db)
    assert corte is not None
    assert corte["title"] == "Corte Legado"
    assert corte["tipo"] == "short"
    assert corte["layout_mode"] == "vertical_blur"
    assert corte["layout_params"] == {}
    assert corte["burn_subtitles"] is True
    assert corte["updated_at"] is not None


def test_video_crud_and_cascade_delete(isolated_env):
    vid_id = db_manager.add_video(
        url="https://www.youtube.com/watch?v=abcdefghijk",
        title="Podcast Tech",
        duration=1800.0,
        status="pending",
    )
    assert isinstance(vid_id, int) and vid_id > 0

    fetched = db_manager.get_video(vid_id)
    assert fetched is not None
    assert fetched["title"] == "Podcast Tech"
    assert fetched["status"] == "pending"

    updated = db_manager.update_video_status(
        vid_id,
        status="ready",
        duration=1805.5,
        title="Podcast Tech Editado",
    )
    assert updated is True

    by_search = db_manager.list_videos(search="Editado")
    assert len(by_search) == 1
    assert by_search[0]["id"] == vid_id
    assert by_search[0]["status"] == "ready"
    assert by_search[0]["duration"] == pytest.approx(1805.5)

    corte_id = db_manager.add_corte(video_id=vid_id, start_time=10.0, end_time=40.0, title="Trecho 1")
    assert db_manager.get_corte(corte_id) is not None

    # DELETE em videos deve remover cortes associados em cascata
    assert db_manager.delete_video(vid_id, delete_files=False) is True
    assert db_manager.get_video(vid_id) is None
    assert db_manager.get_corte(corte_id) is None
    assert db_manager.delete_video(vid_id, delete_files=False) is False


def test_corte_full_crud_lifecycle(sample_video_and_cut):
    vid_id = sample_video_and_cut["video_id"]
    corte_id = sample_video_and_cut["corte_id"]

    corte = db_manager.get_corte(corte_id)
    assert corte is not None
    assert corte["video_id"] == vid_id
    assert corte["duration"] == pytest.approx(35.0)
    assert corte["layout_mode"] == "vertical_blur"
    assert corte["layout_params"] == {"zoom_factor": 1.15}
    assert corte["burn_subtitles"] is True

    # Atualização parcial (PATCH) preserva campos não informados
    ok = db_manager.update_corte(
        corte_id,
        title="Título Atualizado",
        start_time=12.5,
        end_time=52.5,
        layout_mode="manual_crop",
        layout_params={"crop_x": 200, "crop_y": 0, "crop_w": 608, "crop_h": 1080},
        burn_subtitles=False,
    )
    assert ok is True

    after = db_manager.get_corte(corte_id)
    assert after["title"] == "Título Atualizado"
    assert after["start_time"] == pytest.approx(12.5)
    assert after["end_time"] == pytest.approx(52.5)
    assert after["duration"] == pytest.approx(40.0)
    assert after["virality_score"] == pytest.approx(88.5)
    assert after["layout_mode"] == "manual_crop"
    assert after["layout_params"]["crop_x"] == 200
    assert after["burn_subtitles"] is False

    # update_corte_status delega corretamente para update_corte
    assert db_manager.update_corte_status(corte_id, status="completed", output_path="x.mp4") is True
    assert db_manager.get_corte(corte_id)["status"] == "completed"

    # list_cortes respeita filtro min_virality_score e desserializa layout_params
    high = db_manager.list_cortes(video_id=vid_id, min_virality_score=80.0)
    assert len(high) == 1
    assert isinstance(high[0]["layout_params"], dict)
    assert db_manager.list_cortes(video_id=vid_id, min_virality_score=95.0) == []

    # Exclusão individual
    assert db_manager.delete_corte(corte_id, delete_file=False) is True
    assert db_manager.get_corte(corte_id) is None
    assert db_manager.delete_corte(corte_id, delete_file=False) is False
    # Vídeo pai continua existindo
    assert db_manager.get_video(vid_id) is not None


def test_corte_domain_validation_and_sqlite_check_constraints(sample_video_and_cut, isolated_env):
    vid_id = sample_video_and_cut["video_id"]
    corte_id = sample_video_and_cut["corte_id"]

    # Validações de domínio em add_corte
    with pytest.raises(ValueError):
        db_manager.add_corte(video_id=None, start_time=0, end_time=10)
    with pytest.raises(ValueError):
        db_manager.add_corte(video_id=True, start_time=0, end_time=10)
    with pytest.raises(ValueError):
        db_manager.add_corte(video_id=0, start_time=0, end_time=10)
    with pytest.raises(ValueError):
        db_manager.add_corte(video_id=vid_id, start_time=-1.0, end_time=10.0)
    with pytest.raises(ValueError):
        db_manager.add_corte(video_id=vid_id, start_time=20.0, end_time=20.0)
    with pytest.raises(ValueError):
        db_manager.add_corte(video_id=vid_id, start_time=10.0, end_time=20.0, virality_score=101.0)

    # Validação de limites combinados em update_corte (PATCH só de start_time > end_time atual=45.0)
    with pytest.raises(ValueError):
        db_manager.update_corte(corte_id, start_time=50.0)
    with pytest.raises(ValueError):
        db_manager.update_corte(corte_id, layout_mode="modo_inexistente")
    with pytest.raises(ValueError):
        db_manager.update_corte(corte_id, tipo="invalido")
    assert db_manager.update_corte(999999, title="Inexistente") is False

    # Constraints CHECK diretamente no SQLite
    with db_manager.get_connection(isolated_env["db_path"]) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cortes (video_id, start_time, end_time, virality_score) VALUES (?, ?, ?, ?)",
                (vid_id, 30.0, 10.0, 50.0),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cortes (video_id, start_time, end_time, virality_score) VALUES (?, ?, ?, ?)",
                (vid_id, 0.0, 10.0, 150.0),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO videos (url, duration) VALUES (?, ?)",
                ("https://youtu.be/dQw4w9WgXcQ", -5.0),
            )


def test_save_analysis_recovers_missing_or_invalid_parent_video(isolated_env):
    cuts_payload = [
        {
            "title": "Corte 1",
            "start_time": 5.0,
            "end_time": 35.0,
            "virality_score": 91.0,
            "justification": "Gancho forte",
        },
        {
            "title": "Corte Longo",
            "start_time": 100.0,
            "end_time": 600.0,
            "virality_score": 120.0,  # será limitado a 100.0
            "tipo": "longo",
        },
    ]

    # Caso 1: video_id inexistente (9999) + URL -> recria registro pai sem FOREIGN KEY constraint failed
    ids_created = db_manager.save_analysis(
        video_id=9999,
        transcript={"text": "olá"},
        analysis_data={"cortes": cuts_payload},
        url="https://www.youtube.com/watch?v=recovered01",
        title="Vídeo Recuperado",
        duration=700.0,
    )
    assert len(ids_created) == 2

    videos = db_manager.list_videos(search="recovered01")
    assert len(videos) == 1
    recovered_vid_id = videos[0]["id"]
    assert videos[0]["status"] == "analyzed"

    saved_cuts = db_manager.list_cortes(video_id=recovered_vid_id)
    assert len(saved_cuts) == 2
    by_title = {c["title"]: c for c in saved_cuts}
    assert by_title["Corte Longo"]["virality_score"] == pytest.approx(100.0)
    assert by_title["Corte Longo"]["layout_mode"] == "horizontal_16_9"
    assert by_title["Corte 1"]["layout_mode"] == "vertical_blur"

    # Caso 2: video_id=None com mesma URL -> reaproveita o registro pai já existente
    ids_reused = db_manager.save_analysis(
        video_id=None,
        transcript={},
        analysis_data=[{"title": "Corte Extra", "start_time": 1.0, "end_time": 15.0, "virality_score": 80.0}],
        url="https://www.youtube.com/watch?v=recovered01",
    )
    assert len(ids_reused) == 1
    assert len(db_manager.list_videos(search="recovered01")) == 1
    assert len(db_manager.list_cortes(video_id=recovered_vid_id)) == 3


def test_delete_video_and_delete_corte_confine_physical_files_to_data_dir(isolated_env, tmp_path: Path):
    # Arquivo externo fora de DATA_DIR nunca deve ser apagado
    outside_secret = tmp_path / "external_important.txt"
    outside_secret.write_text("do not delete", encoding="utf-8")

    # Arquivos legítimos dentro de DATA_DIR devem ser removidos
    inside_video = isolated_env["downloads_dir"] / "raw.mp4"
    inside_video.write_bytes(b"video-bytes")
    inside_cut = isolated_env["cuts_dir"] / "cut_1.mp4"
    inside_cut.write_bytes(b"cut-bytes")

    vid_id = db_manager.add_video(
        url="https://youtu.be/dQw4w9WgXcQ",
        title="Teste Confinamento",
        file_path=str(inside_video),
        audio_path=str(outside_secret),
    )
    cache_transcript = isolated_env["cache_dir"] / f"transcript_{vid_id}.json"
    cache_transcript.write_text("{}", encoding="utf-8")

    corte_inside_id = db_manager.add_corte(
        video_id=vid_id,
        start_time=0.0,
        end_time=10.0,
        title="Corte Interno",
        output_path=str(inside_cut),
    )
    corte_outside_id = db_manager.add_corte(
        video_id=vid_id,
        start_time=10.0,
        end_time=20.0,
        title="Corte Externo Malicioso",
        output_path=str(outside_secret),
    )
    corte_db_target_id = db_manager.add_corte(
        video_id=vid_id,
        start_time=20.0,
        end_time=30.0,
        title="Tentativa Apagar DB",
        output_path=str(isolated_env["db_path"]),
    )

    # delete_corte apaga apenas arquivo dentro de DATA_DIR (e preserva arquivo externo e o próprio DB)
    assert db_manager.delete_corte(corte_inside_id, delete_file=True) is True
    assert not inside_cut.exists()

    assert db_manager.delete_corte(corte_outside_id, delete_file=True) is True
    assert outside_secret.exists()

    assert db_manager.delete_corte(corte_db_target_id, delete_file=True) is True
    assert isolated_env["db_path"].exists()

    # delete_video apaga arquivo em downloads e cache de transcrição, mas preserva arquivo externo
    assert db_manager.delete_video(vid_id, delete_files=True) is True
    assert not inside_video.exists()
    assert not cache_transcript.exists()
    assert outside_secret.exists()
    assert isolated_env["db_path"].exists()


def test_list_videos_with_cortes_eliminates_n_plus_1_and_paginates(isolated_env, monkeypatch):
    for i in range(5):
        vid_id = db_manager.add_video(
            url=f"https://youtu.be/vid0000000{i}",
            title=f"Podcast Ep {i}",
            duration=120.0 + i,
            status="completed",
        )
        db_manager.add_corte(video_id=vid_id, start_time=5.0, end_time=25.0, title=f"Corte A-{i}", virality_score=85.0)
        db_manager.add_corte(video_id=vid_id, start_time=30.0, end_time=55.0, title=f"Corte B-{i}", virality_score=90.0)

    # Conta aberturas de conexão para provar execução em lote sem N+1 (1 única conexão)
    orig_get_conn = db_manager.get_connection
    open_calls = []

    def _counting_conn(db_path=None):
        open_calls.append(1)
        return orig_get_conn(db_path)

    monkeypatch.setattr(db_manager, "get_connection", _counting_conn)

    page1, total = db_manager.list_videos_with_cortes(limit=2, offset=0)
    assert total == 5
    assert len(page1) == 2
    assert len(open_calls) == 1
    assert all(len(v["cortes"]) == 2 for v in page1)

    page2, total2 = db_manager.list_videos_with_cortes(limit=2, offset=2)
    assert total2 == 5
    assert len(page2) == 2
    assert {v["id"] for v in page1}.isdisjoint({v["id"] for v in page2})

    filtered, total_filtered = db_manager.list_videos_with_cortes(search="Ep 3", limit=10, offset=0)
    assert total_filtered == 1
    assert len(filtered) == 1
    assert filtered[0]["title"] == "Podcast Ep 3"


def test_platform_repo_brand_kits_job_queue_and_scheduled_posts(sample_video_and_cut, isolated_env):
    # 1. Brand Kits CRUD + upsert por nome
    kit1 = platform_repo.upsert_brand_kit(name="Canal Principal", subtitle_preset="neon", progress_bar_color="#00FFAA")
    assert kit1["id"] > 0
    assert kit1["subtitle_preset"] == "neon"

    kit1_upd = platform_repo.upsert_brand_kit(name="Canal Principal", subtitle_preset="boxed", hook_banner_enabled=True)
    assert kit1_upd["id"] == kit1["id"]
    assert kit1_upd["subtitle_preset"] == "boxed"
    assert kit1_upd["hook_banner_enabled"] is True
    assert len(platform_repo.list_brand_kits()) == 1
    assert platform_repo.delete_brand_kit(kit1["id"]) is True
    assert platform_repo.get_brand_kit(kit1["id"]) is None

    # 2. Job queue persistence + recuperação de órfãos
    platform_repo.save_job_snapshot({
        "job_id": "job_orphan_1",
        "type": "render",
        "status": "running",
        "step": "Renderizando...",
        "progress": 50,
        "result": None,
        "error": None,
        "created_at": 1000.0,
        "updated_at": 1005.0,
    })
    recovered = platform_repo.mark_orphaned_jobs_interrupted()
    assert recovered == 1
    persisted = platform_repo.list_persisted_jobs()
    assert persisted[0]["status"] == "interrupted"

    # 3. Scheduled posts CRUD
    corte_id = sample_video_and_cut["corte_id"]
    post = platform_repo.create_scheduled_post(
        corte_id=corte_id,
        platform="youtube_shorts",
        title="Hook Imperdível",
        caption="Legenda pronta",
        hashtags="#shorts #viral",
        scheduled_at="2026-12-01T18:00",
    )
    assert post["id"] > 0
    assert len(platform_repo.list_scheduled_posts(corte_id=corte_id)) == 1
    updated = platform_repo.update_scheduled_post_status(post["id"], status="published", external_id="yt_123")
    assert updated["status"] == "published"
    assert updated["external_id"] == "yt_123"
    assert platform_repo.delete_scheduled_post(post["id"]) is True


