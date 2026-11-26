"""
Utilitários compartilhados de mídia, validação de segurança para FFmpeg e normalização de domínio.
Mantém baixo acoplamento e elimina dependências circulares entre downloader e motor de vídeo.
"""

import ipaddress
from pathlib import Path
import re
import shutil
import socket
from typing import Any, Optional
from urllib.parse import urlparse

from core.config import CUTS_DIR, DATA_DIR, DOWNLOADS_DIR

TEMP_DIR = (DATA_DIR / "temp").resolve()
TEMP_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR_RESOLVED = DATA_DIR.resolve()
FFMPEG_ALLOWED_PROTOCOLS = "file,http,https,tcp,tls"


def normalize_virality_score(raw_score: Any, default: float = 0.0) -> float:
    """
    Normaliza scores de viralidade para a escala 0..100.
    Converte valores legados na escala (0, 10] multiplicando por 10 e limita a [0, 100].
    """
    try:
        val = float(raw_score)
    except (TypeError, ValueError):
        val = float(default)
    if 0.0 < val <= 10.0:
        val = val * 10.0
    return max(0.0, min(100.0, val))


def is_path_within(path: Path, base_dir: Path) -> bool:
    """Verifica estritamente se um caminho resolvido está confinado dentro de base_dir."""
    try:
        return path.resolve().is_relative_to(base_dir.resolve())
    except Exception:
        return False


def _is_forbidden_ip(ip_obj: ipaddress._BaseAddress) -> bool:
    return bool(
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


def validate_ffmpeg_input_target(
    target: str,
    require_within_data_dir: bool = True,
    allowed_data_dir: Optional[Path] = None,
) -> str:
    """
    Valida alvos de entrada passados ao FFmpeg contra argument injection (prefixo '-'),
    protocolos perigosos, SSRF para IPs internos e leitura de arquivos locais fora de DATA_DIR.
    """
    if not target or not isinstance(target, str):
        raise ValueError("Alvo de entrada FFmpeg vazio ou inválido.")
    clean = target.strip()
    if not clean or clean.startswith("-") or any(ch in clean for ch in ("\x00", "\r", "\n")):
        raise ValueError("Alvo de entrada FFmpeg contém caracteres ou prefixo proibidos.")

    if clean.startswith(("http://", "https://")):
        parsed = urlparse(clean)
        host = (parsed.hostname or "").strip().lower().rstrip(".")
        if not host or host in ("localhost", "localhost.localdomain"):
            raise ValueError("URL remota com host local não permitida no FFmpeg.")
        try:
            ip_obj = ipaddress.ip_address(host)
            if _is_forbidden_ip(ip_obj):
                raise ValueError("URL remota apontando para IP interno não permitida no FFmpeg.")
        except ValueError as ip_exc:
            if "não permitida" in str(ip_exc):
                raise
            try:
                for info in socket.getaddrinfo(host, None):
                    sockaddr = info[4]
                    if sockaddr:
                        ip_obj = ipaddress.ip_address(sockaddr[0].split("%")[0])
                        if _is_forbidden_ip(ip_obj):
                            raise ValueError("Host remoto resolve para endereço interno proibido.")
            except OSError:
                pass
        return clean

    # Bloqueia esquemas/protocolos arbitrários (ex: concat:, subfile:, file:, crypto:, ftp:)
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]{1,}:", clean) and not re.match(r"^[a-zA-Z]:[\\/]", clean):
        raise ValueError("Protocolo não permitido no FFmpeg.")

    resolved = Path(clean).resolve()
    base_dir = allowed_data_dir.resolve() if allowed_data_dir is not None else DATA_DIR_RESOLVED
    if require_within_data_dir and not is_path_within(resolved, base_dir):
        raise PermissionError("Acesso negado: arquivo local fora do diretório de dados permitido.")
    if not resolved.exists() or not resolved.is_file():
        raise FileNotFoundError(f"Arquivo de entrada não encontrado: {resolved.name}")
    return str(resolved)


def check_ffmpeg() -> None:
    """Verifica se o FFmpeg e o FFprobe estão disponíveis no PATH do sistema."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError(
            "FFmpeg ou FFprobe não foram encontrados no PATH. "
            "Certifique-se de que o executável está instalado (ex: C:\\ffmpeg\\bin)."
        )


__all__ = [
    "CUTS_DIR",
    "DATA_DIR",
    "DATA_DIR_RESOLVED",
    "DOWNLOADS_DIR",
    "FFMPEG_ALLOWED_PROTOCOLS",
    "TEMP_DIR",
    "check_ffmpeg",
    "is_path_within",
    "normalize_virality_score",
    "validate_ffmpeg_input_target",
]
