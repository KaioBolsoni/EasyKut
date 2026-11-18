"""
Módulo de Configuração e Gerenciamento de Variáveis de Ambiente (EasyKut)
Carrega e valida chaves de API e caminhos de diretórios do projeto.
"""

import os
import shutil
from pathlib import Path
from typing import Dict, Tuple
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


def validate_config() -> Tuple[bool, Dict[str, bool]]:
    """
    Verifica a presença das chaves de API necessárias para o funcionamento do sistema.
    Retorna uma tupla (todas_validas, status_detalhado).
    """
    status = {
        "GROQ_API_KEY": bool(GROQ_API_KEY and not GROQ_API_KEY.startswith("sua_chave")),
        "GEMINI_API_KEY": bool(GEMINI_API_KEY and not GEMINI_API_KEY.startswith("sua_chave")),
        "FFMPEG": FFMPEG_AVAILABLE,
    }
    all_valid = all(status.values())
    return all_valid, status
