"""
Testes unitários dos módulos de núcleo (core/ai_apis.py, core/downloader.py e core/video_engine.py).
Todos executam 100% offline com mocks determinísticos, sem depender de YouTube, APIs pagas ou FFmpeg real.
"""

import logging
import os
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core import ai_apis, downloader, face_tracker, layout_filters, logging_config, video_engine


def test_clean_and_parse_json_handles_plain_fenced_regex_and_score_scaling():
    # 1. JSON encapsulado em bloco markdown + escala legada 0-10 + filtro < 5s
    raw_fenced = """
    Aqui está o resultado:
    ```json
    {
      "cortes": [
        {"title": "Gancho Forte", "start_time": 10.0, "end_time": 42.5, "virality_score": 9.2, "justification": "Excelente hook"},
        {"title": "Muito Curto", "start_time": 50.0, "end_time": 52.0, "virality_score": 95},
        {"title": "Debate Longo", "start_time": 100.0, "end_time": 600.0, "virality_score": 88}
      ]
    }
    ```
    """
    parsed = ai_apis._clean_and_parse_json(raw_fenced)
    assert len(parsed) == 2
    assert parsed[0]["title"] == "Gancho Forte"
    assert parsed[0]["virality_score"] == pytest.approx(92.0)
    assert parsed[0]["tipo"] == "short"
    assert parsed[1]["title"] == "Debate Longo"
    assert parsed[1]["tipo"] == "longo"

    # 2. Recuperação estruturada via regex quando o JSON vem truncado
    malformed = '{"cortes": [{"title": "Recuperado", "start_time": 5.0, "end_time": 25.0, "virality_score": 84, "justification": "OK"}'
    recovered = ai_apis._clean_and_parse_json(malformed)
    assert len(recovered) == 1
    assert recovered[0]["title"] == "Recuperado"
    assert recovered[0]["virality_score"] == pytest.approx(84.0)

    # 3. Resposta vazia retorna lista vazia
    assert ai_apis._clean_and_parse_json("") == []


def test_detect_viral_moments_provider_fallback_and_aggregated_errors(monkeypatch):
    transcript = {
        "text": "Olá mundo",
        "segments": [{"start": 0.0, "end": 30.0, "text": "Uma frase impactante de teste."}],
    }

    # Gemini falha -> cai automaticamente para Groq
    monkeypatch.setattr(ai_apis, "analyze_viral_cuts_gemini", MagicMock(side_effect=RuntimeError("Gemini 404")))
    monkeypatch.setattr(
        ai_apis,
        "analyze_viral_cuts_groq",
        MagicMock(return_value=[{"title": "Via Groq", "start_time": 0.0, "end": 30.0, "virality_score": 85.0}]),
    )
    cuts = ai_apis.detect_viral_moments(transcript, provider="gemini")
    assert len(cuts) == 1
    assert cuts[0]["title"] == "Via Groq"

    # Ambos falham -> RuntimeError agrega diagnóstico dos dois provedores
    monkeypatch.setattr(ai_apis, "analyze_viral_cuts_groq", MagicMock(side_effect=RuntimeError("Groq 429")))
    with pytest.raises(RuntimeError) as exc_info:
        ai_apis.detect_viral_moments(transcript, provider="gemini")
    assert "Gemini 404" in str(exc_info.value)
    assert "Groq 429" in str(exc_info.value)


def test_ai_clients_configure_explicit_timeouts(monkeypatch):
    groq_ctor = MagicMock(return_value="groq_instance")
    gemini_ctor = MagicMock(return_value="gemini_instance")

    monkeypatch.setattr(ai_apis, "GROQ_API_KEY", "test-groq-key")
    monkeypatch.setattr(ai_apis, "GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setattr(ai_apis, "Groq", groq_ctor)
    monkeypatch.setattr(ai_apis.genai, "Client", gemini_ctor)

    assert ai_apis.get_groq_client() == "groq_instance"
    groq_ctor.assert_called_once_with(api_key="test-groq-key", timeout=60.0)

    assert ai_apis.get_gemini_client() == "gemini_instance"
    _, kwargs = gemini_ctor.call_args
    assert kwargs["api_key"] == "test-gemini-key"
    assert kwargs["http_options"].timeout == 60000


def test_transcribe_audio_groq_retries_and_normalizes_segments(tmp_path: Path, monkeypatch):
    audio_file = tmp_path / "sample.mp3"
    audio_file.write_bytes(b"ID3-fake-audio")

    fake_response = SimpleNamespace(
        text="Texto completo",
        segments=[
            {"start": 0.0, "end": 4.2, "text": "  Primeira frase  "},
            SimpleNamespace(start=4.5, end=9.0, text="Segunda frase"),
        ],
    )

    mock_create = MagicMock(side_effect=[RuntimeError("429 temporary"), fake_response])
    fake_client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=mock_create)))
    monkeypatch.setattr(ai_apis, "get_groq_client", lambda: fake_client)

    result = ai_apis.transcribe_audio_groq(str(audio_file), max_retries=2, base_delay=0.0)
    assert mock_create.call_count == 2
    assert result["text"] == "Texto completo"
    assert result["segments"] == [
        {"start": 0.0, "end": 4.2, "text": "Primeira frase"},
        {"start": 4.5, "end": 9.0, "text": "Segunda frase"},
    ]


def test_downloader_common_opts_socket_timeout_and_sanitize_filename():
    opts = downloader._apply_common_ydl_opts({})
    assert opts["socket_timeout"] == 30
    assert opts["retries"] == 3
    assert opts["fragment_retries"] == 3
    assert opts["extractor_retries"] == 3

    # Respeita valor customizado já definido via setdefault
    custom = downloader._apply_common_ydl_opts({"socket_timeout": 15})
    assert custom["socket_timeout"] == 15

    # sanitize_filename remove caracteres inválidos no Windows
    cleaned = downloader.sanitize_filename('Corte: "Épico" <1/2>? *|')
    for forbidden in '<>:"/\\|?*':
        assert forbidden not in cleaned
    assert len(cleaned) > 0


def test_generate_srt_for_segment_and_ffmpeg_filter_escaping(isolated_env):
    assert video_engine.format_srt_time(0.0) == "00:00:00,000"
    assert video_engine.format_srt_time(3661.25) == "01:01:01,250"

    segments = [
        {"start": 0.0, "end": 5.0, "text": "Fora antes"},
        {"start": 8.0, "end": 14.0, "text": "Cruza início"},
        {"start": 15.0, "end": 22.0, "text": "No meio do corte"},
        {"start": 35.0, "end": 40.0, "text": "Fora depois"},
    ]
    srt_path = video_engine.generate_srt_for_segment(segments, start_time=10.0, end_time=30.0)
    assert srt_path is not None
    content = Path(srt_path).read_text(encoding="utf-8")
    assert "Fora antes" not in content
    assert "Fora depois" not in content
    assert "00:00:00,000 --> 00:00:04,000\nCruza início" in content
    assert "00:00:05,000 --> 00:00:12,000\nNo meio do corte" in content

    # Nenhum segmento no intervalo retorna None
    assert video_engine.generate_srt_for_segment(segments, start_time=100.0, end_time=120.0) is None

    # Escape de caracteres especiais para libavfilter (sem padrão POSIX quebrado '\'')
    tricky = r"C:\Users\O'Connor\[test],;sub.srt"
    flt = video_engine._format_ffmpeg_subtitles_filter(tricky, is_vertical=True)
    assert "C\\:/" in flt or "\\:/" in flt
    assert "'\\''" not in flt
    assert "\\'" in flt
    assert "\\[" in flt and "\\]" in flt and "\\," in flt and "\\;" in flt


def test_detect_smooth_face_crop_x_releases_capture_on_exception(monkeypatch):
    fake_cap = MagicMock()
    fake_cap.isOpened.return_value = True
    fake_cap.get.return_value = 30.0
    fake_cap.read.side_effect = RuntimeError("corrupted frame")

    fake_detector = MagicMock()
    fake_detector.is_available.return_value = True
    monkeypatch.setattr(face_tracker, "_FaceDetectorWrapper", lambda *args, **kwargs: fake_detector)
    monkeypatch.setattr(face_tracker.cv2, "VideoCapture", lambda _path: fake_cap)

    result = video_engine.detect_smooth_face_crop_x(
        video_path="fake.mp4",
        start_time=0.0,
        end_time=5.0,
        orig_w=1920,
        orig_h=1080,
        crop_w=608,
    )
    assert result is None
    fake_cap.release.assert_called_once()


def test_cleanup_temporary_files_preserves_cuts_and_gitkeep(isolated_env):
    temp_keep = isolated_env["temp_dir"] / ".gitkeep"
    temp_keep.write_text("", encoding="utf-8")
    temp_junk = isolated_env["temp_dir"] / "frame_old.jpg"
    temp_junk.write_bytes(b"x" * 2048)

    dl_file = isolated_env["downloads_dir"] / "raw_video.mp4"
    dl_file.write_bytes(b"y" * 4096)

    cut_file = isolated_env["cuts_dir"] / "final_cut.mp4"
    cut_file.write_bytes(b"z" * 4096)

    res1 = video_engine.cleanup_temporary_files(
        temp_dir=isolated_env["temp_dir"],
        downloads_dir=isolated_env["downloads_dir"],
        remove_raw_downloads=False,
    )
    assert res1["deleted_count"] == 1
    assert temp_keep.exists()
    assert not temp_junk.exists()
    assert dl_file.exists()
    assert cut_file.exists()

    res2 = video_engine.cleanup_temporary_files(
        temp_dir=isolated_env["temp_dir"],
        downloads_dir=isolated_env["downloads_dir"],
        remove_raw_downloads=True,
    )
    assert res2["deleted_count"] == 1
    assert not dl_file.exists()
    assert cut_file.exists()


def test_cleanup_temporary_files_respects_max_age_hours(isolated_env):
    old_file = isolated_env["temp_dir"] / "stale_frame.jpg"
    old_file.write_bytes(b"old-data")
    old_ts = time.time() - (48 * 3600)
    os.utime(old_file, (old_ts, old_ts))

    fresh_file = isolated_env["temp_dir"] / "fresh_frame.jpg"
    fresh_file.write_bytes(b"fresh-data")

    res = video_engine.cleanup_temporary_files(
        temp_dir=isolated_env["temp_dir"],
        max_age_hours=24.0,
    )
    assert res["deleted_count"] == 1
    assert not old_file.exists()
    assert fresh_file.exists()


def test_no_circular_import_between_downloader_and_video_engine():
    downloader_src = (Path(__file__).resolve().parent.parent / "core" / "downloader.py").read_text(encoding="utf-8")
    assert "video_engine" not in downloader_src
    assert "media_renderer" not in downloader_src


def test_rotating_logging_and_request_id_filter(tmp_path: Path):
    logs_dir = tmp_path / "logs"
    log_path = logging_config.configure_logging(logs_dir=logs_dir, log_filename="test_easykut.log")
    test_logger = logging.getLogger("test_corr_logger")
    token = logging_config.request_id_var.set("req-test-999")
    try:
        test_logger.info("Evento rastreado com sucesso")
    finally:
        logging_config.request_id_var.reset(token)

    for h in list(logging.getLogger().handlers):
        if getattr(h, "baseFilename", None) == str(log_path):
            h.flush()
            h.close()
            logging.getLogger().removeHandler(h)

    assert log_path.exists()
    assert "[req=req-test-999]" in log_path.read_text(encoding="utf-8")


def test_layout_filters_strategy_builders(monkeypatch):
    # 1. Normalização de aliases legados e Face Tracking
    assert layout_filters.normalize_effective_mode("blur_background", None, False) == ("vertical_blur", False)
    assert layout_filters.normalize_effective_mode("crop_center", None, False) == ("manual_crop", False)
    assert layout_filters.normalize_effective_mode("original", None, False) == ("horizontal_16_9", False)
    assert layout_filters.normalize_effective_mode("blur", "face_tracking_9_16", False) == ("manual_crop", True)

    base_kwargs = {
        "src_path": "fake.mp4",
        "start_time": 0.0,
        "end_time": 10.0,
        "orig_w": 1920,
        "orig_h": 1080,
        "target_width": 1080,
        "target_height": 1920,
        "cfg": {},
        "face_tracking": False,
        "sub_filter": None,
    }

    # 2. horizontal_16_9: stream copy sem legendas vs re-encode com legendas
    args_copy, is_copy = layout_filters.build_layout_ffmpeg_args(effective_mode="horizontal_16_9", **base_kwargs)
    assert is_copy is True
    assert args_copy == ["-c", "copy", "-movflags", "+faststart"]

    args_sub, is_copy_sub = layout_filters.build_layout_ffmpeg_args(
        effective_mode="horizontal_16_9",
        **{**base_kwargs, "sub_filter": "subtitles='a.srt'"},
    )
    assert is_copy_sub is False
    assert args_sub[0] == "-vf" and "subtitles='a.srt'" in args_sub[1]

    # 3. vertical_blur: zoom 1.0 vs zoom 1.25 + legendas
    args_blur_1, _ = layout_filters.build_layout_ffmpeg_args(
        effective_mode="vertical_blur",
        **{**base_kwargs, "cfg": {"zoom_factor": 1.0}},
    )
    assert "boxblur=20:5" in args_blur_1[1]
    assert "[merged]" in args_blur_1

    args_blur_z, _ = layout_filters.build_layout_ffmpeg_args(
        effective_mode="vertical_blur",
        **{**base_kwargs, "cfg": {"zoom_factor": 1.25}, "sub_filter": "subtitles='b.srt'"},
    )
    assert "crop=min(1080,iw):min(1920,ih)[fg]" in args_blur_z[1]
    assert "[outv]" in args_blur_z

    # 4. manual_crop com ref_w / ref_h rescaling e dimensões pares
    args_crop, _ = layout_filters.build_layout_ffmpeg_args(
        effective_mode="manual_crop",
        **{
            **base_kwargs,
            "cfg": {"ref_w": 640, "ref_h": 360, "crop_x": 100, "crop_y": 0, "crop_w": 203, "crop_h": 360},
        },
    )
    assert args_crop[0] == "-vf"
    assert "crop=608:1080:300:0,scale=1080:1920" in args_crop[1]

    # 5. face_tracking_9_16 com detecção mockada e fallback central em caso de erro
    monkeypatch.setattr(layout_filters, "detect_smooth_face_crop_x", lambda **kw: 420)
    args_ft, _ = layout_filters.build_layout_ffmpeg_args(
        effective_mode="manual_crop",
        **{**base_kwargs, "face_tracking": True},
    )
    assert "crop=606:1076:420:0,scale=1080:1920" in args_ft[1]

    monkeypatch.setattr(
        layout_filters,
        "detect_smooth_face_crop_x",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("detector falhou")),
    )
    args_ft_fb, _ = layout_filters.build_layout_ffmpeg_args(
        effective_mode="manual_crop",
        **{**base_kwargs, "face_tracking": True},
    )
    assert "scale=1080:1920" in args_ft_fb[1]

    # 6. split_screen com vstack=inputs=2 e legendas
    args_split, _ = layout_filters.build_layout_ffmpeg_args(
        effective_mode="split_screen",
        **{
            **base_kwargs,
            "cfg": {
                "ref_w": 640,
                "ref_h": 360,
                "box_top": {"x": 20, "y": 10, "w": 280, "h": 250},
                "box_bottom": {"x": 340, "y": 10, "w": 280, "h": 250},
            },
            "sub_filter": "subtitles='c.srt'",
        },
    )
    assert "vstack=inputs=2[stacked]" in args_split[1]
    assert "[outv]" in args_split


