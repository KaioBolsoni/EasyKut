"""
Testes automatizados completos da Plataforma V4.0 (Ondas 2, 3 e 4):
- Onda 2: Legendas Karaoke .ass (5 presets + override inline), Face Tracking dinâmico X(t),
          Jump Cuts + remapeamento de timestamps, Brand Kit (overlays + upload seguro de logo).
- Onda 3: GPU NVENC/QSV/AMF + fallback CPU, Áudio -14 LUFS + Auto-Ducking, layout 1:1,
          Cancelamento de jobs ativos + fila SQLite, Kit SEO IA, Export ZIP.
- Onda 4: B-Roll (keywords, local e SSRF guard), Highlight Reel (xfade), Publicação/Agendamento.
100% offline, determinístico e isolado em tmp_path.
"""

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import zipfile

import numpy as np
import pytest

from core import (
    audio_mastering,
    brand_overlay,
    broll_engine,
    face_tracker,
    highlight_reel,
    hw_accel,
    jump_cuts,
    layout_filters,
    publishers,
    subtitles,
)
from database import db_manager
import server
from services import platform_service
from services.job_manager import job_manager


# ==========================================
# ONDA 2 — KARAOKE .ASS, FACE DINÂMICO, JUMP CUTS, BRAND KIT
# ==========================================
def test_ass_karaoke_generation_all_presets_and_inline_overrides(isolated_env):
    segments = [{"start": 10.0, "end": 16.0, "text": "Esse segredo muda tudo agora"}]
    words = [
        {"word": "Esse", "start": 10.0, "end": 11.0},
        {"word": "segredo", "start": 11.0, "end": 12.2},
        {"word": "muda", "start": 12.2, "end": 13.5},
        {"word": "tudo", "start": 13.5, "end": 14.8},
        {"word": "agora", "start": 14.8, "end": 16.0},
    ]

    for preset_key in subtitles.SUBTITLE_PRESETS:
        ass_path = subtitles.generate_ass_karaoke_for_segment(
            segments=segments,
            words=words,
            start_time=10.0,
            end_time=16.0,
            preset=preset_key,
            temp_dir=isolated_env["temp_dir"],
        )
        assert ass_path is not None
        content = Path(ass_path).read_text(encoding="utf-8-sig")
        assert "[Script Info]" in content
        assert "[V4+ Styles]" in content
        assert "Dialogue: 0," in content
        # Valida sintaxe ASS inline correta (\1c&HBBGGRR& com & de fechamento)
        assert "\\1c&H" in content
        assert "&\\fscx114\\fscy114" in content

        flt = subtitles.format_ffmpeg_ass_filter(ass_path)
        assert flt.startswith("ass='")

    # Garante que palavras fora de ordem / sobrepostas do Whisper nunca geram eventos ASS sobrepostos
    messy_words = [
        {"word": "um", "start": 10.0, "end": 10.8},
        {"word": "dois", "start": 10.9, "end": 11.4},
        {"word": "tres", "start": 10.5, "end": 11.9},  # timestamp fora de ordem (10.5 < 10.9)
    ]
    ass_mono = subtitles.generate_ass_karaoke_for_segment(
        segments=None,
        words=messy_words,
        start_time=10.0,
        end_time=13.0,
        preset="neon",
        temp_dir=isolated_env["temp_dir"],
    )
    dialogue_lines = [ln for ln in Path(ass_mono).read_text(encoding="utf-8-sig").splitlines() if ln.startswith("Dialogue:")]
    assert len(dialogue_lines) == 3
    prev_end_ts = ""
    for dl in dialogue_lines:
        parts = dl.split(",")
        start_ts, end_ts = parts[1], parts[2]
        assert start_ts >= prev_end_ts
        assert end_ts > start_ts
        prev_end_ts = end_ts

    # Override inline de texto (prefer_segments=True ignora word-level antigo e usa texto corrigido)
    edited_segs = [{"start": 10.0, "end": 14.0, "text": "Texto Corrigido Manualmente"}]
    ass_edited = subtitles.generate_ass_karaoke_for_segment(
        segments=edited_segs,
        words=words,
        start_time=10.0,
        end_time=14.0,
        preset="minimal",
        prefer_segments=True,
        temp_dir=isolated_env["temp_dir"],
    )
    edited_content = Path(ass_edited).read_text(encoding="utf-8")
    assert "Corrigido" in edited_content
    assert "segredo" not in edited_content


def test_dynamic_face_tracking_keyframes_and_ffmpeg_expr(monkeypatch):
    # Simula vídeo com rosto migrando da esquerda (x=200) para a direita (x=1400)
    fake_frame = np.zeros((360, 640, 3), dtype=np.uint8)
    fake_cap = MagicMock()
    fake_cap.isOpened.return_value = True
    fake_cap.get.return_value = 30.0
    fake_cap.read.return_value = (True, fake_frame)
    fake_cap.grab.return_value = True

    positions = iter([
        (150, 100, 120, 120),
        (160, 100, 120, 120),
        (700, 100, 120, 120),
        (720, 100, 120, 120),
        (1200, 100, 120, 120),
        (1250, 100, 120, 120),
    ])

    fake_detector = MagicMock()
    fake_detector.is_available.return_value = True
    fake_detector.detect_largest_face.side_effect = lambda _f, scale_factor=0.5: next(positions, (1250, 100, 120, 120))

    monkeypatch.setattr(face_tracker, "_FaceDetectorWrapper", lambda *a, **kw: fake_detector)
    monkeypatch.setattr(face_tracker.cv2, "VideoCapture", lambda _p: fake_cap)

    kfs = face_tracker.detect_dynamic_face_crop_keyframes(
        video_path="fake.mp4",
        start_time=0.0,
        end_time=4.5,
        orig_w=1920,
        orig_h=1080,
        crop_w=608,
        window_sec=1.5,
        sample_rate_sec=0.75,
    )
    assert len(kfs) >= 2
    fake_cap.release.assert_called_once()

    expr = face_tracker.build_ffmpeg_dynamic_crop_x_expr(kfs, max_crop_x=1312)
    assert "between(t," in expr

    # Verifica integração no Strategy Pattern build_manual_crop_args com dynamic_face_tracking=True
    monkeypatch.setattr(layout_filters, "detect_dynamic_face_crop_keyframes", lambda **kw: [(0.0, 120), (2.0, 680)])
    args, is_copy = layout_filters.build_layout_ffmpeg_args(
        effective_mode="manual_crop",
        src_path="fake.mp4",
        start_time=0.0,
        end_time=5.0,
        orig_w=1920,
        orig_h=1080,
        target_width=1080,
        target_height=1920,
        cfg={},
        face_tracking=True,
        dynamic_face_tracking=True,
        sub_filter=None,
    )
    assert is_copy is False
    assert "x='min(1314,max(0," in args[1]


def test_jump_cuts_intervals_remapping_and_filter_graph():
    words = [
        {"word": "Oi", "start": 10.0, "end": 10.5},
        {"word": "pessoal", "start": 10.6, "end": 11.2},
        # Silêncio de 3.8s (11.2 -> 15.0)
        {"word": "voltamos", "start": 15.0, "end": 16.0},
        {"word": "agora", "start": 16.1, "end": 17.0},
    ]
    intervals = jump_cuts.compute_speech_intervals_from_transcript(
        segments=None,
        words=words,
        start_time=10.0,
        end_time=18.0,
        min_silence_sec=0.6,
        pad_sec=0.05,
    )
    assert len(intervals) == 2
    assert intervals[0][0] == pytest.approx(0.0)
    assert intervals[1][0] > intervals[0][1]

    remapped = jump_cuts.remap_timestamps_for_intervals(words, intervals, base_start=10.0)
    assert len(remapped) == 4
    # A palavra "voltamos" (antes em t_rel=5.0s) agora vem logo após o fim do 1º bloco (~1.25s)
    assert remapped[2]["start"] < 2.0

    graph, v_out, a_out = jump_cuts.build_jump_cut_filter_graph(intervals)
    assert "concat=n=2:v=1:a=1[jcv_out][jca_out]" in graph
    assert v_out == "[jcv_out]"
    assert a_out == "[jca_out]"


def test_brand_overlay_filters_and_logo_confinement(isolated_env, tmp_path: Path):
    valid_logo = isolated_env["brands_dir"] / "logo_ok.png"
    valid_logo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    outside_logo = tmp_path / "outside.png"
    outside_logo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    assert brand_overlay.resolve_confined_logo_path(str(valid_logo)) == str(valid_logo.resolve())
    assert brand_overlay.resolve_confined_logo_path(str(outside_logo)) is None

    filters, out_label, resolved_logo = brand_overlay.build_brand_overlay_filters(
        brand_kit={
            "logo_path": str(valid_logo),
            "logo_position": "top_right",
            "logo_opacity": 0.8,
            "logo_scale": 0.15,
            "progress_bar_enabled": True,
            "progress_bar_color": "#FF5500",
            "hook_banner_enabled": True,
            "hook_banner_duration": 3.0,
        },
        duration=30.0,
        target_width=1080,
        target_height=1920,
        hook_title="Gancho: 'Incrível' [2026]",
        input_video_label="[vbase]",
        logo_input_index=1,
    )
    assert resolved_logo == str(valid_logo.resolve())
    assert out_label == "[v_hook]"
    joined = ";".join(filters)
    assert "colorchannelmixer=aa=0.80" in joined
    assert "0xFF5500@0.95" in joined
    assert "drawtext=" in joined
    assert "text='Gancho - Incrível (2026)'" in joined


# ==========================================
# ONDA 3 — GPU, AUDIO PRO, 1:1, CANCELAMENTO, SEO & ZIP
# ==========================================
def test_gpu_encoder_detection_and_cpu_fallback(monkeypatch):
    hw_accel.reset_gpu_probe_cache()
    monkeypatch.setattr(
        hw_accel.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=" V..... h264_nvenc NVIDIA NVENC\n"),
    )
    assert hw_accel.detect_gpu_encoder(force_refresh=True) == "h264_nvenc"
    args_gpu, name_gpu = hw_accel.build_video_encoder_args(use_gpu=True)
    assert name_gpu == "h264_nvenc"
    assert "-cq" in args_gpu

    args_cpu, name_cpu = hw_accel.build_video_encoder_args(use_gpu=False)
    assert name_cpu == "libx264"
    assert "-crf" in args_cpu
    hw_accel.reset_gpu_probe_cache()


def test_audio_mastering_loudnorm_denoise_and_sidechain_ducking(isolated_env, tmp_path: Path):
    good_bgm = isolated_env["music_dir"] / "chill.mp3"
    good_bgm.write_bytes(b"ID3" + b"\x00" * 32)
    bad_bgm = tmp_path / "outside.mp3"
    bad_bgm.write_bytes(b"ID3" + b"\x00" * 32)

    assert audio_mastering.resolve_confined_music_path(str(good_bgm)) == str(good_bgm.resolve())
    assert audio_mastering.resolve_confined_music_path(str(bad_bgm)) is None

    filters, out_a = audio_mastering.build_audio_mastering_filters(
        normalize_audio=True,
        denoise_audio=True,
        music_input_index=2,
        music_volume=0.2,
    )
    joined = ";".join(filters)
    assert "afftdn=nf=-25" in joined
    assert "loudnorm=I=-14:TP=-1.5:LRA=11" in joined
    assert "sidechaincompress=" in joined
    assert "amix=inputs=2" in joined
    assert out_a == "[a_master]"


def test_square_1_1_layout_builder():
    mode, ft = layout_filters.normalize_effective_mode("blur", "square_1_1", False)
    assert (mode, ft) == ("square_1_1", False)
    args, is_copy = layout_filters.build_layout_ffmpeg_args(
        effective_mode="square_1_1",
        src_path="fake.mp4",
        start_time=0.0,
        end_time=10.0,
        orig_w=1920,
        orig_h=1080,
        target_width=1080,
        target_height=1080,
        cfg={},
        face_tracking=False,
        sub_filter=None,
    )
    assert is_copy is False
    assert "crop=1080:1080:420:0,scale=1080:1080" in args[1]


def test_job_cancellation_and_sqlite_persistence(client, isolated_env):
    jid = job_manager.create_job("render", "Renderizando corte...", progress=30)
    assert jid is not None

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    job_manager.register_process(jid, fake_proc)

    r_list = client.get("/api/jobs")
    assert r_list.status_code == 200
    assert any(j["job_id"] == jid for j in r_list.json()["jobs"])

    r_cancel = client.delete(f"/api/jobs/{jid}")
    assert r_cancel.status_code == 200
    assert r_cancel.json()["job"]["status"] == "cancelled"
    assert job_manager.is_cancelled(jid) is True
    fake_proc.terminate.assert_called_once()
    assert client.delete("/api/jobs/job_inexistente").status_code == 404


def test_brand_kits_music_upload_seo_kit_and_zip_export_api(client, sample_video_and_cut, isolated_env, monkeypatch):
    vid_id = sample_video_and_cut["video_id"]
    corte_id = sample_video_and_cut["corte_id"]

    # 1. Subtitle presets
    r_pres = client.get("/api/subtitle-presets")
    assert r_pres.status_code == 200
    assert any(p["id"] == "hormozi" for p in r_pres.json()["presets"])

    # 2. Upload logo (rejeita magic bytes falsos, aceita PNG real)
    assert client.post("/api/brand-kits/upload-logo", files={"file": ("bad.png", b"NOT_PNG_DATA", "image/png")}).status_code == 400
    png_bytes = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 32)
    r_logo = client.post("/api/brand-kits/upload-logo", files={"file": ("marca.png", png_bytes, "image/png")})
    assert r_logo.status_code == 201
    logo_name = r_logo.json()["logo_path"]

    # 3. Brand Kit CRUD API
    r_bk = client.post(
        "/api/brand-kits",
        json={"name": "Studio Main", "logo_path": logo_name, "subtitle_preset": "hormozi", "progress_bar_color": "#10B981"},
    )
    assert r_bk.status_code == 201
    kit_id = r_bk.json()["brand_kit"]["id"]
    assert len(client.get("/api/brand-kits").json()["brand_kits"]) == 1
    assert client.delete(f"/api/brand-kits/{kit_id}").status_code == 200

    # 4. Upload música (rejeita assinatura inválida, aceita MP3 ID3)
    assert client.post("/api/music/upload", files={"file": ("fake.mp3", b"<html>nope</html>", "audio/mpeg")}).status_code == 400
    r_mus = client.post("/api/music/upload", files={"file": ("lofi.mp3", b"ID3\x03\x00\x00\x00" + b"\x00" * 32, "audio/mpeg")})
    assert r_mus.status_code == 201
    mus_name = r_mus.json()["filename"]
    assert any(t["filename"] == mus_name for t in client.get("/api/music").json()["tracks"])
    assert client.delete(f"/api/music/{mus_name}").status_code == 200

    # 5. SEO Publish Kit IA (mocked)
    monkeypatch.setattr(
        server,
        "generate_seo_publish_kit",
        lambda **kw: {
            "titles": ["Título A", "Título B", "Título C"],
            "hook": "Gancho Forte",
            "description": "Descrição pronta",
            "hashtags": ["#shorts", "#viral"],
            "source": "ai",
        },
    )
    r_seo = client.post(f"/api/cortes/{corte_id}/seo-kit", json={"provider": "gemini"})
    assert r_seo.status_code == 200
    assert len(r_seo.json()["seo_kit"]["titles"]) == 3
    assert db_manager.get_corte(corte_id)["seo_kit"]["hook"] == "Gancho Forte"

    # 6. Export ZIP (400 sem cortes renderizados -> 200 ZIP válido com MP4 renderizado)
    assert client.get(f"/api/videos/{vid_id}/export-zip").status_code == 400
    mp4_out = isolated_env["cuts_dir"] / "clip_zip_test.mp4"
    mp4_out.write_bytes(b"\x00\x00\x00\x18ftypmp42ZIPTEST")
    db_manager.update_corte_status(corte_id, status="completed", output_path=str(mp4_out))

    r_zip = client.get(f"/api/videos/{vid_id}/export-zip")
    assert r_zip.status_code == 200
    assert r_zip.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r_zip.content), "r") as zf:
        names = zf.namelist()
        assert len(names) == 1
        assert zf.read(names[0]) == b"\x00\x00\x00\x18ftypmp42ZIPTEST"


# ==========================================
# ONDA 4 — B-ROLL, HIGHLIGHT REEL (XFADE), PUBLICAÇÃO & AGENDAMENTO
# ==========================================
def test_broll_keywords_local_and_host_allowlist(client, isolated_env):
    kws = broll_engine.extract_visual_keywords("Inteligência artificial transforma programação e edição de vídeos virais")
    assert "inteligência" in kws or "artificial" in kws or "programação" in kws

    # Allowlist HTTPS anti-SSRF
    with pytest.raises(ValueError):
        broll_engine._safe_https_json_get("https://evil.attacker.com/api")
    with pytest.raises(ValueError):
        broll_engine._safe_https_json_get("http://api.pexels.com/videos/search")

    # Biblioteca local data/broll/
    local_clip = isolated_env["broll_dir"] / "cyber_studio.mp4"
    local_clip.write_bytes(b"broll-data")

    resp = client.post("/api/broll/suggest", json={"transcript_excerpt": "Estúdio moderno de edição de vídeo"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["keywords"]) >= 1
    assert any(it["is_local"] and it["preview_url"] == "/data/broll/cyber_studio.mp4" for it in body["items"])


def test_highlight_reel_xfade_filter_and_endpoint(client, sample_video_and_cut, isolated_env, monkeypatch):
    fc = highlight_reel.build_xfade_filter_complex([10.0, 8.0, 6.0], transition="dissolve", transition_duration=0.5)
    assert "xfade=transition=dissolve:duration=0.50" in fc
    assert "acrossfade=d=0.50" in fc
    assert "[vout]" in fc and "[aout]" in fc

    vid_id = sample_video_and_cut["video_id"]
    c1 = sample_video_and_cut["corte_id"]
    c2 = db_manager.add_corte(video_id=vid_id, start_time=50.0, end_time=75.0, title="Corte 2")

    f1 = isolated_env["cuts_dir"] / "c1.mp4"
    f2 = isolated_env["cuts_dir"] / "c2.mp4"
    reel_out = isolated_env["cuts_dir"] / "reel_final.mp4"
    for f in (f1, f2, reel_out):
        f.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    db_manager.update_corte_status(c1, status="completed", output_path=str(f1))
    db_manager.update_corte_status(c2, status="completed", output_path=str(f2))

    # Endpoint 202 Accepted
    monkeypatch.setattr(server.threading, "Thread", lambda *a, **kw: type("_T", (), {"start": lambda self: None})())
    r_reel = client.post(
        f"/api/videos/{vid_id}/highlight-reel",
        json={"corte_ids": [c1, c2], "transition": "fade", "transition_duration": 0.5},
    )
    assert r_reel.status_code == 202
    assert r_reel.json()["job_id"].startswith("job_reel_")

    # Execução do serviço run_highlight_reel_job com compile_fn mockado
    jid = job_manager.create_job("reel", "Compilando...", progress=10)
    platform_service.run_highlight_reel_job(
        job_id=jid,
        video_id=vid_id,
        corte_ids=[c1, c2],
        transition="fade",
        transition_duration=0.5,
        title="Meu Reel",
        manager=job_manager,
        compile_fn=lambda clips, **kw: str(reel_out),
    )
    snap = job_manager.get_job_snapshot(jid)
    assert snap["status"] == "completed"
    assert snap["result"]["video_url"] == "/data/cortes/reel_final.mp4"
    assert snap["result"]["clip_count"] == 2


def test_publish_providers_and_schedule_api(client, sample_video_and_cut, isolated_env):
    corte_id = sample_video_and_cut["corte_id"]

    r_prov = client.get("/api/publish/providers")
    assert r_prov.status_code == 200
    ids = {p["id"] for p in r_prov.json()["providers"]}
    assert ids == {"youtube_shorts", "tiktok", "instagram_reels"}

    # Corte não renderizado -> 400
    r_unrendered = client.post(
        "/api/publish/schedule",
        json={
            "corte_id": corte_id,
            "platform": "youtube_shorts",
            "title": "Sem arquivo",
            "scheduled_at": "2026-12-01T18:00",
        },
    )
    assert r_unrendered.status_code == 400

    # Associa MP4 renderizado em cuts_dir -> 201
    clip = isolated_env["cuts_dir"] / "pub_ready.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    db_manager.update_corte_status(corte_id, status="completed", output_path=str(clip))

    r_sched = client.post(
        "/api/publish/schedule",
        json={
            "corte_id": corte_id,
            "platform": "tiktok",
            "title": "Corte Viral Pronto",
            "caption": "Assista até o final!",
            "hashtags": "#tiktok #viral",
            "scheduled_at": "2026-12-01T18:00",
            "publish_now": True,
        },
    )
    assert r_sched.status_code == 201
    post_id = r_sched.json()["post"]["id"]
    assert len(client.get("/api/publish/schedule").json()["posts"]) == 1
    assert client.delete(f"/api/publish/schedule/{post_id}").status_code == 200
    assert publishers.validate_publish_media(str(clip)) == clip.resolve()
