"""
Módulo de Configuração e Gerenciamento de Variáveis de Ambiente (EasyKut)
Carrega e valida chaves de API e caminhos de diretórios do projeto.
"""

import os
import shutil
from pathlib import Path
from typing import Dict, Optional, Tuple
from dotenv import load_dotenv, find_dotenv

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
PROMPTS_DIR = BASE_DIR / "prompts"

# Cria diretórios essenciais caso não existam
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
CUTS_DIR.mkdir(parents=True, exist_ok=True)
PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

# Procura e carrega arquivo .env de forma determinística
env_file = BASE_DIR / ".env"
if env_file.exists():
    load_dotenv(dotenv_path=env_file)
else:
    load_dotenv(find_dotenv())

# Chaves de API
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
YOUTUBE_COOKIES_ENV = os.getenv("YOUTUBE_COOKIES_PATH", "").strip()


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
