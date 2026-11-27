"""
Módulo de Configuração e Gerenciamento de Variáveis de Ambiente (EasyKut)
Carrega e valida chaves de API e caminhos de diretórios do projeto.
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Dict, Optional, Tuple
from dotenv import load_dotenv, find_dotenv

logger = logging.getLogger(__name__)

# Configuração e descoberta automática do FFmpeg no sistema
FFMPEG_KNOWN_PATHS = [
    r"C:\ffmpeg\bin",
    r"C:\Ghost Toolbox\wget\Youtube-DLP",
    r"C:\ProgramData\chocolatey\bin",
]

for p in FFMPEG_KNOWN_PATHS:
    if Path(p).exists() and p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")

FFMPEG_AVAILABLE = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

# Diretórios principais
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DOWNLOADS_DIR = DATA_DIR / "downloads"
CUTS_DIR = DATA_DIR / "cortes"
CACHE_DIR = DATA_DIR / "cache"
LOGS_DIR = DATA_DIR / "logs"
BRANDS_DIR = DATA_DIR / "brands"
MUSIC_DIR = DATA_DIR / "music"
BROLL_DIR = DATA_DIR / "broll"
PROMPTS_DIR = BASE_DIR / "prompts"

# Cria diretórios essenciais caso não existam
for _dir in (DOWNLOADS_DIR, CUTS_DIR, CACHE_DIR, LOGS_DIR, BRANDS_DIR, MUSIC_DIR, BROLL_DIR, PROMPTS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# Procura e carrega arquivo .env de forma determinística
env_file = BASE_DIR / ".env"
if env_file.exists():
    load_dotenv(dotenv_path=env_file)
else:
    load_dotenv(find_dotenv())

# Alerta preventivo caso existam arquivos de credenciais duplicados fora da pasta do projeto
for stray_name in ("APIS.txt", ".env"):
    stray_path = BASE_DIR.parent / stray_name
    if stray_path.exists() and stray_path.is_file():
        logger.warning(
            "Aviso de segurança: arquivo '%s' detectado fora de cortador_automatico/. "
            "Consolide suas chaves apenas em cortador_automatico/.env e remova o arquivo redundante.",
            stray_name
        )

# Chaves de API
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
YOUTUBE_COOKIES_ENV = os.getenv("YOUTUBE_COOKIES_PATH", "").strip()

# Integrações opcionais (Onda 4: B-Roll & Publicação OAuth)
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "").strip()
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "").strip()
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN", "").strip()
TIKTOK_ACCESS_TOKEN = os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
IG_BUSINESS_ACCOUNT_ID = os.getenv("IG_BUSINESS_ACCOUNT_ID", "").strip()


def get_cookies_file() -> Optional[Path]:
    """
    Localiza dinamicamente o arquivo cookies.txt (formato Netscape) para autenticação no yt-dlp.
    Procura na variável de ambiente YOUTUBE_COOKIES_PATH, na raiz de cortador_automatico/,
    em data/cookies.txt ou na pasta pai EasyKut/.
    """
    candidates = []
    if YOUTUBE_COOKIES_ENV:
        candidates.append(Path(YOUTUBE_COOKIES_ENV))
    candidates.extend([
        BASE_DIR / "cookies.txt",
        DATA_DIR / "cookies.txt",
        BASE_DIR.parent / "cookies.txt",
    ])
    for cand in candidates:
        try:
            if cand.exists() and cand.is_file() and cand.stat().st_size > 10:
                return cand.resolve()
        except Exception:
            continue
    return None


def validate_config(provider: Optional[str] = None) -> Tuple[bool, Dict[str, bool]]:
    """
    Verifica a presença das chaves de API e binários necessários para o funcionamento.
    Exige obrigatoriamente GROQ_API_KEY e FFMPEG_AVAILABLE.
    Torna GEMINI_API_KEY obrigatória apenas se o provedor Gemini for selecionado.
    Retorna uma tupla (sistema_pronto, status_detalhado).
    """
    has_groq = bool(GROQ_API_KEY and not GROQ_API_KEY.startswith("sua_chave"))
    has_gemini = bool(GEMINI_API_KEY and not GEMINI_API_KEY.startswith("sua_chave"))
    has_ffmpeg = FFMPEG_AVAILABLE
    has_cookies = get_cookies_file() is not None

    status = {
        "GROQ_API_KEY": has_groq,
        "GEMINI_API_KEY": has_gemini,
        "FFMPEG": has_ffmpeg,
        "COOKIES_TXT": has_cookies,
    }

    is_ready = has_ffmpeg and has_groq
    if provider and provider.lower() == "gemini":
        is_ready = is_ready and has_gemini

    return is_ready, status
