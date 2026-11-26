"""
Testes de integração da API REST FastAPI (server.py) via TestClient.
Cobre CRUD de cortes e histórico, validação Pydantic, fontes confiáveis de prévia,
limites de concorrência (429), deepcopy de jobs e pipelines de análise/renderização com mocks.
"""

import socket
import time

import pytest

from core import downloader
from database import db_manager
import server
from services.job_manager import JobManager, job_manager


def _mock_public_dns(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.190.78", 0))]


def test_get_config_returns_structured_status(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "ready" in body
    assert "keys" in body
    assert set(body["keys"].keys()) == {"GROQ_API_KEY", "GEMINI_API_KEY", "FFMPEG_AVAILABLE", "COOKIES_TXT"}
    assert body["default_provider"] in ("gemini", "groq")


def test_cortes_rest_crud_and_validation(client, sample_video_and_cut, isolated_env):
    corte_id = sample_video_and_cut["corte_id"]

    # GET corte existente
    r_get = client.get(f"/api/cortes/{corte_id}")
    assert r_get.status_code == 200
    data = r_get.json()
    assert data["id"] == corte_id
    assert data["title"] == "Gancho Inicial"
    assert data["layout_params"] == {"zoom_factor": 1.15}
    assert data["burn_subtitles"] is True
    assert data["video_url"] is None

    # GET inexistente -> 404
    assert client.get("/api/cortes/999999").status_code == 404

    # PATCH parcial válido
    r_patch = client.patch(
        f"/api/cortes/{corte_id}",
        json={
            "title": "Corte Editado no Studio",
            "start_time": 12.0,
            "end_time": 48.0,
            "layout_mode": "split_screen",
            "layout_params": {"top_x": 0, "top_y": 0, "top_w": 960, "top_h": 540},
            "burn_subtitles": False,
        },
    )
    assert r_patch.status_code == 200
    updated = r_patch.json()["corte"]
    assert updated["title"] == "Corte Editado no Studio"
    assert updated["start_time"] == pytest.approx(12.0)
    assert updated["end_time"] == pytest.approx(48.0)
    assert updated["layout_mode"] == "split_screen"
    assert updated["layout_params"]["top_w"] == 960
    assert updated["burn_subtitles"] is False

    # PATCH com erro de validação Pydantic (422)
    assert client.patch(f"/api/cortes/{corte_id}", json={"layout_mode": "modo_invalido"}).status_code == 422
    assert client.patch(f"/api/cortes/{corte_id}", json={"start_time": 40.0, "end_time": 20.0}).status_code == 422
    assert client.patch(f"/api/cortes/{corte_id}", json={"start_time": -5.0}).status_code == 422

    # PATCH parcial violando limite combinado com registro atual (start_time=60 > end_time=48) -> 400
    assert client.patch(f"/api/cortes/{corte_id}", json={"start_time": 60.0}).status_code == 400

    # PATCH inexistente -> 404
    assert client.patch("/api/cortes/999999", json={"title": "Nada"}).status_code == 404

    # Associa arquivo físico dentro de DATA_DIR e testa DELETE ?delete_file=true
    mp4_file = isolated_env["cuts_dir"] / "clip_renderizado.mp4"
    mp4_file.write_bytes(b"mp4-fake")
    db_manager.update_corte_status(corte_id, status="completed", output_path=str(mp4_file))
    assert client.get(f"/api/cortes/{corte_id}").json()["video_url"] == "/data/cortes/clip_renderizado.mp4"

    r_del = client.delete(f"/api/cortes/{corte_id}?delete_file=true")
    assert r_del.status_code == 200
    assert r_del.json()["success"] is True
    assert not mp4_file.exists()
    assert client.get(f"/api/cortes/{corte_id}").status_code == 404
    assert client.delete(f"/api/cortes/{corte_id}").status_code == 404


def test_history_list_filter_and_delete(client, sample_video_and_cut, isolated_env):
    vid_id = sample_video_and_cut["video_id"]
    server.analyses_cache[vid_id] = {"url": sample_video_and_cut["url"]}

    # GET /api/history com busca e filtro de nota
    r_all = client.get("/api/history?search=Isolado&min_score=80")
    assert r_all.status_code == 200
    items = r_all.json()
    assert len(items) == 1
    assert items[0]["id"] == vid_id
    assert len(items[0]["cortes"]) == 1

    # min_score acima da nota filtra os cortes
    r_high = client.get("/api/history?min_score=95")
    assert r_high.status_code == 200
    assert r_high.json()[0]["cortes"] == []

    # DELETE /api/history/{video_id} limpa DB e analyses_cache
    r_del = client.delete(f"/api/history/{vid_id}?delete_files=true")
    assert r_del.status_code == 200
    assert vid_id not in server.analyses_cache
    assert client.delete(f"/api/history/{vid_id}").status_code == 404


def test_frame_and_preview_clip_require_trusted_source_and_hide_local_paths(
    client, sample_video_and_cut, isolated_env, monkeypatch
):
    monkeypatch.setattr(downloader.socket, "getaddrinfo", _mock_public_dns)
    vid_id = sample_video_and_cut["video_id"]

    # Rejeita caminho local arbitrário e URL não cadastrada
    assert client.post("/api/frame", json={"source": "C:\\Windows\\win.ini", "timestamp_sec": 1.0}).status_code == 400
    assert client.post(
        "/api/frame",
        json={"source": "https://www.youtube.com/watch?v=unregistered1", "timestamp_sec": 1.0},
    ).status_code == 400
    assert client.post(
        "/api/preview-clip",
        json={"video_id": vid_id, "start_time": 20.0, "end_time": 10.0},
    ).status_code == 400

    # Mock de extração bem-sucedida usando video_id confiável
    fake_frame = isolated_env["temp_dir"] / "frame_ok.jpg"
    fake_frame.write_bytes(b"\xff\xd8\xff")
    fake_clip = isolated_env["temp_dir"] / "clip_ok.mp4"
    fake_clip.write_bytes(b"mp4-preview")

    monkeypatch.setattr(server, "extract_preview_frame", lambda **kwargs: str(fake_frame))
    monkeypatch.setattr(server, "extract_preview_clip", lambda **kwargs: str(fake_clip))

    r_frame = client.post("/api/frame", json={"video_id": vid_id, "timestamp_sec": 12.0})
    assert r_frame.status_code == 200
    frame_body = r_frame.json()
    assert frame_body["frame_url"] == "/data/temp/frame_ok.jpg"
    assert "frame_path" not in frame_body

    r_clip = client.post("/api/preview-clip", json={"video_id": vid_id, "start_time": 10.0, "end_time": 25.0})
    assert r_clip.status_code == 200
    clip_body = r_clip.json()
    assert clip_body["clip_url"] == "/data/temp/clip_ok.mp4"
    assert "clip_path" not in clip_body


def test_analyze_and_render_validation_and_concurrency_429(client, sample_video_and_cut, monkeypatch):
    monkeypatch.setattr(downloader.socket, "getaddrinfo", _mock_public_dns)
    vid_id = sample_video_and_cut["video_id"]

    # Validação AnalyzeRequest
    assert client.post("/api/analyze", json={"url": "https://youtu.be/dQw4w9WgXcQ", "provider": "openai"}).status_code == 422
    assert client.post("/api/analyze", json={"url": "https://youtu.be/dQw4w9WgXcQ", "max_cuts": 0}).status_code == 422
    assert client.post("/api/analyze", json={"url": "http://127.0.0.1:8502/"}).status_code == 400

    # Validação RenderRequest
    assert client.post("/api/render", json={"video_id": vid_id, "cuts": []}).status_code == 400
    assert client.post(
        "/api/render",
        json={
            "video_id": vid_id,
            "max_resolution": 480,
            "cuts": [{"start_time": 0.0, "end_time": 10.0}],
        },
    ).status_code == 422
    assert client.post(
        "/api/render",
        json={
            "video_id": vid_id,
            "cuts": [{"start_time": 20.0, "end_time": 10.0}],
        },
    ).status_code == 422
    assert client.post(
        "/api/render",
        json={
            "video_id": 999999,
            "cuts": [{"start_time": 0.0, "end_time": 10.0}],
        },
    ).status_code == 404

    # Limite de concorrência analyze -> 429
    with server.jobs_lock:
        for i in range(server.MAX_CONCURRENT_ANALYZE_JOBS):
            server.jobs[f"a_{i}"] = {"job_id": f"a_{i}", "type": "analyze", "status": "running"}
        for i in range(server.MAX_CONCURRENT_RENDER_JOBS):
            server.jobs[f"r_{i}"] = {"job_id": f"r_{i}", "type": "render", "status": "running"}

    assert client.post("/api/analyze", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}).status_code == 429
    assert client.post(
        "/api/render",
        json={"video_id": vid_id, "cuts": [{"start_time": 0.0, "end_time": 10.0}]},
    ).status_code == 429

    # Semáforo FFmpeg esgotado -> 429 imediato
    class _BusySemaphore:
        def acquire(self, timeout=None):
            return False

        def release(self):
            pass

    monkeypatch.setattr(server, "preview_ffmpeg_semaphore", _BusySemaphore())
    assert client.post("/api/frame", json={"video_id": vid_id, "timestamp_sec": 1.0}).status_code == 429
    assert client.post("/api/preview-clip", json={"video_id": vid_id, "start_time": 0.0, "end_time": 5.0}).status_code == 429


def test_get_job_status_returns_deep_copy_and_404_when_missing(client):
    assert client.get("/api/jobs/job_inexistente").status_code == 404

    with server.jobs_lock:
        server.jobs["job_copy_test"] = {
            "job_id": "job_copy_test",
            "type": "analyze",
            "status": "running",
            "step": "Etapa 1",
            "progress": 25,
            "result": {"nested": [1, 2, 3]},
            "error": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    # Chamada direta para provar isolamento de referência (deepcopy)
    snapshot = server.get_job_status("job_copy_test")
    snapshot["result"]["nested"].append(999)
    assert server.jobs["job_copy_test"]["result"]["nested"] == [1, 2, 3]

    # Chamada HTTP via TestClient
    resp = client.get("/api/jobs/job_copy_test")
    assert resp.status_code == 200
    assert resp.json()["result"]["nested"] == [1, 2, 3]


def test_run_analyze_job_success_and_error_paths_with_mocks(isolated_env, monkeypatch):
    audio_file = isolated_env["temp_dir"] / "audio_mock.mp3"
    audio_file.write_bytes(b"mp3-bytes")
    frame_file = isolated_env["temp_dir"] / "frame_mock.jpg"
    frame_file.write_bytes(b"jpg-bytes")

    monkeypatch.setattr(
        server,
        "download_audio_with_metadata",
        lambda url: {
            "info": {"title": "Vídeo Mockado", "duration": 180.0, "thumbnail": "https://img/t.jpg", "uploader": "Canal"},
            "audio_path": str(audio_file),
            "direct_stream_url": "https://cdn.example/stream.mp4",
        },
    )
    monkeypatch.setattr(
        server,
        "transcribe_audio_groq",
        lambda _audio: {
            "text": "Fala de teste viral.",
            "segments": [{"start": 10.0, "end": 40.0, "text": "Fala de teste viral."}],
        },
    )
    monkeypatch.setattr(
        server,
        "detect_viral_moments",
        lambda _tr, provider="gemini": [
            {
                "title": "Momento Mock",
                "start_time": 10.0,
                "end_time": 40.0,
                "virality_score": 92.0,
                "tipo": "short",
                "justification": "Ótimo gancho",
            }
        ],
    )
    monkeypatch.setattr(server, "extract_preview_frame", lambda **kwargs: str(frame_file))

    job_id = "job_analyze_mock_ok"
    with server.jobs_lock:
        server.jobs[job_id] = {"job_id": job_id, "type": "analyze", "status": "running", "progress": 0}

    server._run_analyze_job(job_id, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "gemini", 5)

    job = server.get_job_status(job_id)
    assert job["status"] == "completed"
    assert job["progress"] == 100
    vid_id = job["result"]["video_id"]
    assert len(job["result"]["cortes"]) == 1
    assert job["result"]["cortes"][0]["id"] is not None

    # Cache de transcrição salvo em data/cache/ e NÃO em data/temp/
    assert (isolated_env["cache_dir"] / f"transcript_{vid_id}.json").exists()
    assert not (isolated_env["temp_dir"] / f"transcript_{vid_id}.json").exists()

    # Caminho de erro deve sempre gravar status="error" e erro descritivo
    def _boom(_url):
        raise RuntimeError("Falha simulada yt-dlp")

    monkeypatch.setattr(server, "download_audio_with_metadata", _boom)
    err_job_id = "job_analyze_mock_err"
    with server.jobs_lock:
        server.jobs[err_job_id] = {"job_id": err_job_id, "type": "analyze", "status": "running", "progress": 0}

    server._run_analyze_job(err_job_id, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "gemini", 5)
    err_job = server.get_job_status(err_job_id)
    assert err_job["status"] == "error"
    assert "Falha simulada yt-dlp" in err_job["error"]


def test_run_render_job_persists_cut_edits_and_handles_failure(sample_video_and_cut, isolated_env, monkeypatch):
    vid_id = sample_video_and_cut["video_id"]
    corte_id = sample_video_and_cut["corte_id"]

    raw_video = isolated_env["downloads_dir"] / "raw_mock.mp4"
    raw_video.write_bytes(b"raw-video")
    rendered_clip = isolated_env["cuts_dir"] / "rendered_mock.mp4"
    rendered_clip.write_bytes(b"rendered-clip")

    monkeypatch.setattr(server, "download_video", lambda url, max_resolution=1080: str(raw_video))
    monkeypatch.setattr(server, "cut_video", lambda **kwargs: str(rendered_clip))

    edited_cut = server.RenderCutItem(
        id=corte_id,
        title="Título Final Editado",
        start_time=15.0,
        end_time=42.0,
        layout_mode="manual_crop",
        layout_params={"crop_x": 120, "crop_y": 0, "crop_w": 608, "crop_h": 1080},
        burn_subtitles=False,
    )

    job_id = "job_render_mock_ok"
    with server.jobs_lock:
        server.jobs[job_id] = {"job_id": job_id, "type": "render", "status": "running", "progress": 0}

    server._run_render_job(job_id, vid_id, [edited_cut], 1080)

    job = server.get_job_status(job_id)
    assert job["status"] == "completed"
    assert len(job["result"]["rendered_cuts"]) == 1

    # Confirma que todas as edições feitas no Estúdio foram persistidas no SQLite
    persisted = db_manager.get_corte(corte_id)
    assert persisted["title"] == "Título Final Editado"
    assert persisted["start_time"] == pytest.approx(15.0)
    assert persisted["end_time"] == pytest.approx(42.0)
    assert persisted["layout_mode"] == "manual_crop"
    assert persisted["layout_params"]["crop_x"] == 120
    assert persisted["burn_subtitles"] is False
    assert persisted["status"] == "completed"
    assert persisted["output_path"] == str(rendered_clip)

    # Caminho de falha em renderização deve gravar status="error" sob jobs_lock
    monkeypatch.setattr(server, "cut_video", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("FFmpeg crash")))
    err_job_id = "job_render_mock_err"
    with server.jobs_lock:
        server.jobs[err_job_id] = {"job_id": err_job_id, "type": "render", "status": "running", "progress": 0}

    server._run_render_job(err_job_id, vid_id, [edited_cut], 1080)
    err_job = server.get_job_status(err_job_id)
    assert err_job["status"] == "error"
    assert "FFmpeg crash" in err_job["error"]
    assert db_manager.get_video(vid_id)["status"] == "failed"


def test_health_endpoint_ok_and_degraded_503(client, monkeypatch):
    r_ok = client.get("/api/health")
    assert r_ok.status_code == 200
    body = r_ok.json()
    assert body["status"] == "ok"
    assert body["db_ok"] is True
    assert body["disk_free_mb"] >= 0
    assert "analyze_running" in body["active_jobs"]

    def _broken_conn():
        raise RuntimeError("DB offline")

    monkeypatch.setattr(server, "get_connection", _broken_conn)
    r_deg = client.get("/api/health")
    assert r_deg.status_code == 503
    assert r_deg.json()["status"] == "degraded"
    assert r_deg.json()["db_ok"] is False


def test_history_pagination_and_total_count_header(client, isolated_env):
    for i in range(3):
        vid_id = db_manager.add_video(url=f"https://youtu.be/pag0000000{i}", title=f"Video {i}", duration=60.0)
        db_manager.add_corte(video_id=vid_id, start_time=0.0, end_time=15.0, title=f"Clip {i}", virality_score=85.0)

    r1 = client.get("/api/history?limit=2&offset=0")
    assert r1.status_code == 200
    assert r1.headers.get("X-Total-Count") == "3"
    assert len(r1.json()) == 2

    r2 = client.get("/api/history?limit=2&offset=2")
    assert r2.status_code == 200
    assert r2.headers.get("X-Total-Count") == "3"
    assert len(r2.json()) == 1


def test_post_cleanup_endpoint_disk_and_memory_pruning(client, isolated_env):
    temp_junk = isolated_env["temp_dir"] / "temp_preview.jpg"
    temp_junk.write_bytes(b"0" * 4096)
    cut_keep = isolated_env["cuts_dir"] / "final.mp4"
    cut_keep.write_bytes(b"1" * 4096)

    # Insere um job finalizado já expirado (há 2h)
    old_ts = time.time() - 7200
    with job_manager.lock:
        job_manager.jobs["expired_job"] = {
            "job_id": "expired_job",
            "type": "analyze",
            "status": "completed",
            "created_at": old_ts,
            "updated_at": old_ts,
        }

    resp = client.post("/api/cleanup", json={"remove_raw_downloads": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["deleted_count"] == 1
    assert body["pruned_jobs"] == 1
    assert not temp_junk.exists()
    assert cut_keep.exists()


def test_analyze_and_render_return_202_accepted(client, sample_video_and_cut, monkeypatch):
    monkeypatch.setattr(downloader.socket, "getaddrinfo", _mock_public_dns)
    monkeypatch.setattr(server.threading, "Thread", lambda *a, **kw: type("_T", (), {"start": lambda self: None})())

    r_an = client.post("/api/analyze", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    assert r_an.status_code == 202
    assert r_an.json()["job_id"].startswith("job_analyze_")

    r_rn = client.post(
        "/api/render",
        json={
            "video_id": sample_video_and_cut["video_id"],
            "cuts": [{"start_time": 0.0, "end_time": 10.0}],
        },
    )
    assert r_rn.status_code == 202
    assert r_rn.json()["job_id"].startswith("job_render_")


def test_job_manager_ttl_and_lru_eviction():
    jm = JobManager(job_ttl_seconds=10.0, max_jobs=3, cache_ttl_seconds=10.0, max_cache_entries=2)
    now = 1000.0

    with jm.lock:
        jm.jobs["running_old"] = {"job_id": "running_old", "type": "analyze", "status": "running", "updated_at": 100.0}
        jm.jobs["done_expired"] = {"job_id": "done_expired", "type": "analyze", "status": "completed", "updated_at": 900.0}
        jm.jobs["done_fresh"] = {"job_id": "done_fresh", "type": "analyze", "status": "completed", "updated_at": 995.0}

    pruned = jm.prune(now=now)
    assert pruned["pruned_jobs"] == 1
    assert "done_expired" not in jm.jobs
    assert "running_old" in jm.jobs
    assert "done_fresh" in jm.jobs

    # Testa evicção LRU do analyses_cache
    jm.set_analysis_cache(1, {"url": "u1"})
    jm.set_analysis_cache(2, {"url": "u2"})
    # Acessa chave 1 para torná-la mais recente; inserir chave 3 deve evictar chave 2
    assert jm.get_analysis_cache(1)["url"] == "u1"
    jm.set_analysis_cache(3, {"url": "u3"})
    assert jm.get_analysis_cache(2) is None
    assert jm.get_analysis_cache(1) is not None
    assert jm.get_analysis_cache(3) is not None

