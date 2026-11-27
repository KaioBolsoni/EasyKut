"""
Testes de segurança (OWASP / DevSecOps) do EasyKut Studio.
Cobre SSRF, DNS rebinding, LFI, Path Traversal, argument/protocol injection no FFmpeg,
CORS, CSRF cross-origin, headers defensivos, segregação de cache e proteção anti-XSS no frontend.
"""

from pathlib import Path
import re
import shutil
import socket
import subprocess

import cv2
import numpy as np
import pytest

from core import config, downloader, face_tracker, media_utils, video_engine
import server


def _mock_public_dns(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.190.78", 0))]


def _mock_loopback_dns(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]


@pytest.mark.parametrize(
    "valid_url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "youtube.com/watch?v=dQw4w9WgXcQ",
        "https://vimeo.com/123456789",
        "https://www.twitch.tv/videos/123456789",
    ],
)
def test_is_valid_video_url_allows_supported_public_platforms(valid_url: str, monkeypatch):
    monkeypatch.setattr(downloader.socket, "getaddrinfo", _mock_public_dns)
    assert downloader.is_valid_video_url(valid_url) is True


@pytest.mark.parametrize(
    "malicious_url",
    [
        "",
        "   ",
        "-o /tmp/pwn",
        "http://127.0.0.1:8502/api/config",
        "http://localhost:8502/",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.10/video.mp4",
        "http://10.0.0.5/internal",
        "http://[::1]:8502/",
        "file:///C:/Windows/win.ini",
        "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://user:pass@www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://evil.example/watch?v=dQw4w9WgXcQ",
        "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
        "C:\\Windows\\win.ini",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ\r\nInjected: 1",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ\x00",
        "https://www.youtube.com/channel/UC12345",
    ],
)
def test_is_valid_video_url_blocks_ssrf_and_malicious_inputs(malicious_url: str, monkeypatch):
    monkeypatch.setattr(downloader.socket, "getaddrinfo", _mock_public_dns)
    assert downloader.is_valid_video_url(malicious_url) is False


def test_is_valid_video_url_blocks_dns_rebinding_to_private_ip(monkeypatch):
    monkeypatch.setattr(downloader.socket, "getaddrinfo", _mock_loopback_dns)
    assert downloader.is_valid_video_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is False


def test_validate_ffmpeg_input_target_blocks_lfi_ssrf_and_protocols(isolated_env, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(media_utils.socket, "getaddrinfo", _mock_public_dns)

    # Argument injection e caracteres de controle
    for bad in ("", "-i evil.mp4", "video.mp4\x00.jpg", "https://a.com/x\r\nHeader: 1"):
        with pytest.raises(ValueError):
            video_engine._validate_ffmpeg_input_target(bad)

    # Protocolos perigosos
    for proto in ("concat:a.mp4|b.mp4", "subfile:test.mp4", "file:///etc/passwd", "ftp://example.com/a.mp4"):
        with pytest.raises(ValueError):
            video_engine._validate_ffmpeg_input_target(proto)

    # SSRF para localhost e IPs privados/metadata
    for internal in (
        "http://localhost/video.mp4",
        "http://127.0.0.1:8502/a",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.0.1/stream",
        "http://10.0.0.1/stream",
        "http://[::1]/stream",
    ):
        with pytest.raises(ValueError):
            video_engine._validate_ffmpeg_input_target(internal)

    # Arquivo local fora de DATA_DIR -> PermissionError (LFI bloqueado)
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("top-secret", encoding="utf-8")
    with pytest.raises(PermissionError):
        video_engine._validate_ffmpeg_input_target(str(outside_file), require_within_data_dir=True)

    # Arquivo ausente dentro de DATA_DIR -> FileNotFoundError
    with pytest.raises(FileNotFoundError):
        video_engine._validate_ffmpeg_input_target(str(isolated_env["downloads_dir"] / "nao_existe.mp4"))

    # Arquivo legítimo dentro de DATA_DIR -> permitido
    valid_media = isolated_env["downloads_dir"] / "ok.mp4"
    valid_media.write_bytes(b"mp4-content")
    assert video_engine._validate_ffmpeg_input_target(str(valid_media)) == str(valid_media.resolve())


def test_draw_framing_preview_confines_input_and_output_paths(isolated_env, tmp_path: Path):
    # Cria uma imagem sintética válida dentro e fora de DATA_DIR
    img = np.zeros((180, 320, 3), dtype=np.uint8)
    outside_img = tmp_path / "outside.jpg"
    inside_img = isolated_env["temp_dir"] / "frame_ok.jpg"
    cv2.imwrite(str(outside_img), img)
    cv2.imwrite(str(inside_img), img)

    # Leitura fora de DATA_DIR bloqueada
    with pytest.raises(PermissionError):
        video_engine.draw_framing_preview(str(outside_img), mode="vertical_blur")

    # Escrita fora de DATA_DIR bloqueada
    with pytest.raises(PermissionError):
        video_engine.draw_framing_preview(
            str(inside_img),
            mode="vertical_blur",
            output_path=str(tmp_path / "pwned.jpg"),
        )

    # Tentativa de path traversal no parâmetro mode é sanitizada para dentro de TEMP_DIR
    out_path = Path(video_engine.draw_framing_preview(str(inside_img), mode="../../evil_mode")).resolve()
    assert out_path.exists()
    assert out_path.is_relative_to(isolated_env["temp_dir"].resolve())


def test_local_security_middleware_blocks_dns_rebinding_host(client):
    resp = client.get("/api/config", headers={"Host": "evil.attacker.com:8502"})
    assert resp.status_code == 403
    assert "Host" in resp.json()["detail"]


def test_local_security_middleware_blocks_cross_origin_csrf(client, sample_video_and_cut):
    corte_id = sample_video_and_cut["corte_id"]

    for method, url, payload in (
        ("POST", "/api/analyze", {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}),
        ("PATCH", f"/api/cortes/{corte_id}", {"title": "CSRF"}),
        ("DELETE", f"/api/cortes/{corte_id}", None),
    ):
        resp = client.request(
            method,
            url,
            json=payload,
            headers={"Origin": "https://evil.attacker.com"},
        )
        assert resp.status_code == 403
        assert "Origem" in resp.json()["detail"]

    # Origem local autorizada passa normalmente
    ok_resp = client.patch(
        f"/api/cortes/{corte_id}",
        json={"title": "Origem Permitida"},
        headers={"Origin": "http://localhost:8502"},
    )
    assert ok_resp.status_code == 200
    assert ok_resp.json()["corte"]["title"] == "Origem Permitida"


def test_security_headers_and_cors_policy(client):
    resp = client.get("/api/config", headers={"Origin": "http://127.0.0.1:8502"})
    assert resp.status_code == 200
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Referrer-Policy") == "same-origin"
    assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:8502"
    assert resp.headers.get("access-control-allow-credentials") != "true"

    # Origem externa não deve receber cabeçalho ACAO
    evil = client.get("/api/config", headers={"Origin": "https://evil.example"})
    assert evil.status_code == 200
    assert "access-control-allow-origin" not in evil.headers


def test_removed_orphan_frame_preview_and_private_transcript_cache(client):
    # Endpoint órfão SEC-01 não deve mais existir
    resp = client.post("/api/frame/preview", json={"frame_path": "C:/Windows/win.ini", "mode": "manual_crop"})
    assert resp.status_code in (404, 405)

    # Diretório de cache de transcrições fica isolado fora de data/temp/
    assert config.CACHE_DIR.resolve().name == "cache"
    assert config.CACHE_DIR.resolve() != (config.DATA_DIR / "temp").resolve()

    # Nenhum transcript_*.json deve estar acessível em /data/temp/
    resp_temp = client.get("/data/temp/transcript_1.json")
    assert resp_temp.status_code == 404


def test_frontend_es_modules_xss_regression_guard_and_blur_optimization():
    web_dir = Path(__file__).resolve().parent.parent / "web"
    js_files = [web_dir / "app.js", *sorted((web_dir / "js").glob("*.js"))]
    assert len(js_files) >= 11, f"Esperado ao menos 11 módulos ES no frontend V4, encontrado {len(js_files)}"

    forbidden_interpolations = ("title", "justification", "video_url", "v.url", "s.text", "cut.", "error", "step")
    for js_file in js_files:
        assert js_file.exists(), f"Módulo JS ausente: {js_file}"
        src = js_file.read_text(encoding="utf-8")
        for block in re.findall(r"\.innerHTML\s*\+?=\s*`([\s\S]*?)`", src):
            for expr in re.findall(r"\$\{([^}]+)\}", block):
                for bad_token in forbidden_interpolations:
                    assert bad_token not in expr, f"Interpolação insegura em {js_file.name}: ${{{expr}}}"

    # Verifica ausência total de handlers inline (on*=) no index.html e ausência de window.* em app.js
    html_src = (web_dir / "index.html").read_text(encoding="utf-8")
    inline_handlers = re.findall(r"\bon[a-z]+\s*=", html_src, flags=re.IGNORECASE)
    assert inline_handlers == [], f"Handlers inline proibidos no index.html: {inline_handlers}"

    app_src = (web_dir / "app.js").read_text(encoding="utf-8")
    assert "Object.assign(window" not in app_src, "app.js não deve poluir o escopo global window.*"

    # Verifica presença da otimização de blur 60fps via canvas offscreen e modo Face Tracking
    canvas_src = (web_dir / "js" / "canvas_engine.js").read_text(encoding="utf-8")
    assert "drawOptimizedBlurredBackground" in canvas_src
    assert "initBlurBuffer" in canvas_src
    assert "face_tracking_9_16" in canvas_src

    utils_src = (web_dir / "js" / "utils.js").read_text(encoding="utf-8")
    assert "export function escapeHtml(" in utils_src
    assert "export function getSafeCutDownloadUrl(" in utils_src

    node_bin = shutil.which("node")
    if node_bin:
        for js_file in js_files:
            syntax = subprocess.run(
                [node_bin, "--check", str(js_file)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            assert syntax.returncode == 0, f"{js_file.name}: {syntax.stderr}"

        utils_url = (web_dir / "js" / "utils.js").as_uri()
        js_probe = f"""
        import assert from 'node:assert';
        import {{ escapeHtml, getSafeCutDownloadUrl }} from '{utils_url}';
        assert.strictEqual(escapeHtml('<img src=x onerror="alert(1)">'), '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;');
        assert.strictEqual(getSafeCutDownloadUrl('/data/cortes/clip_01.mp4'), '/data/cortes/clip_01.mp4');
        assert.strictEqual(getSafeCutDownloadUrl('javascript:alert(1)'), null);
        assert.strictEqual(getSafeCutDownloadUrl('/data/cortes/../temp/secret.json'), null);
        assert.strictEqual(getSafeCutDownloadUrl('https://evil.com/clip.mp4'), null);
        """
        res = subprocess.run(
            [node_bin, "--input-type=module", "-e", js_probe],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        assert res.returncode == 0, res.stderr


def test_upload_video_magic_bytes_validation():
    assert server._is_valid_video_magic_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00", ".mp4") is True
    assert server._is_valid_video_magic_bytes(b"\x00\x00\x00\x14moov\x00\x00\x00\x00", ".mov") is True
    assert server._is_valid_video_magic_bytes(b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81", ".mkv") is True
    assert server._is_valid_video_magic_bytes(b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81", ".webm") is True

    assert server._is_valid_video_magic_bytes(b"MZ\x90\x00\x03\x00\x00\x00", ".mp4") is False
    assert server._is_valid_video_magic_bytes(b"<script>alert(1)</script>", ".webm") is False
    assert server._is_valid_video_magic_bytes(b"short", ".mp4") is False
    assert server._is_valid_video_magic_bytes(b"\x00\x00\x00\x18ftypmp42", ".exe") is False


def test_yunet_model_sha256_verification_rejects_tampered_binary(tmp_path: Path, monkeypatch):
    fake_model_path = tmp_path / "models" / face_tracker.YUNET_MODEL_NAME
    monkeypatch.setattr(face_tracker, "YUNET_MODEL_PATH", fake_model_path)
    monkeypatch.setattr(face_tracker, "MODELS_DIR", fake_model_path.parent)

    tampered_bytes = b"A" * 10000

    class _FakeResp:
        def __init__(self, payload: bytes):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self._payload

    # Binário adulterado deve ser rejeitado e nunca gravado em disco
    monkeypatch.setattr(face_tracker.urllib.request, "urlopen", lambda req, timeout=10: _FakeResp(tampered_bytes))
    assert face_tracker._ensure_yunet_model() is None
    assert not fake_model_path.exists()

    # Binário cujo SHA-256 bate com o esperado deve ser aceito e salvo
    import hashlib

    valid_bytes = b"B" * 10000
    expected_hash = hashlib.sha256(valid_bytes).hexdigest()
    monkeypatch.setattr(face_tracker, "YUNET_MODEL_SHA256", expected_hash)
    monkeypatch.setattr(face_tracker.urllib.request, "urlopen", lambda req, timeout=10: _FakeResp(valid_bytes))

    resolved = face_tracker._ensure_yunet_model()
    assert resolved == fake_model_path
    assert fake_model_path.exists()


def test_x_request_id_correlation_header_generated_and_propagated(client):
    r1 = client.get("/api/config")
    assert r1.status_code == 200
    assert r1.headers.get("X-Request-ID")
    assert len(r1.headers["X-Request-ID"]) >= 8

    r2 = client.get("/api/config", headers={"X-Request-ID": "trace-abc-123"})
    assert r2.status_code == 200
    assert r2.headers.get("X-Request-ID") == "trace-abc-123"

    # Header malformado/injetado é descartado e substituído por ID seguro
    r3 = client.get("/api/config", headers={"X-Request-ID": "bad id; <script>"})
    assert r3.status_code == 200
    assert r3.headers.get("X-Request-ID") != "bad id; <script>"

