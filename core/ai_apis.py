"""
Módulo de Integração com APIs de Inteligência Artificial (EasyKut - Fase 5)
Gerencia a transcrição de áudio via Groq Whisper e a curadoria inteligente
de cortes virais utilizando Google Gemini e Groq LLMs.
Implementa retentativas automáticas com backoff exponencial, resiliência contra 429/timeouts
e sanitização avançada de JSON sem quebrar a execução.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from groq import Groq
from google import genai
from google.genai import types

from core.config import GROQ_API_KEY, GEMINI_API_KEY, PROMPTS_DIR

logger = logging.getLogger(__name__)

PROMPT_FILE = PROMPTS_DIR / "diretor_cortes.txt"


def get_groq_client() -> Groq:
    """Retorna instância autenticada do cliente Groq."""
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY não configurada no arquivo .env")
    return Groq(api_key=GROQ_API_KEY)


def get_gemini_client() -> genai.Client:
    """Retorna instância autenticada do cliente Google GenAI."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY não configurada no arquivo .env")
    return genai.Client(api_key=GEMINI_API_KEY)


def load_director_prompt() -> str:
    """Carrega o prompt do sistema para análise de cortes virais."""
    if PROMPT_FILE.exists():
        return PROMPT_FILE.read_text(encoding="utf-8")
    return (
        "Você é um diretor de vídeos virais. Analise a transcrição com timestamps e "
        "retorne um JSON com a chave 'cortes' contendo title, start_time, end_time, virality_score e justification."
    )


def transcribe_audio_groq(
    audio_path: str,
    model: str = "whisper-large-v3-turbo",
    language: Optional[str] = "pt",
    max_retries: int = 3,
    base_delay: float = 2.0
) -> Dict[str, Any]:
    """
    Transcreve um arquivo de áudio utilizando a API da Groq (Whisper).
    Aplica retry automático com backoff exponencial para tratar timeouts e rate limits (429).
    """
    client = get_groq_client()
    path = Path(audio_path)

    if not path.exists():
        raise FileNotFoundError(f"Arquivo de áudio não encontrado: {audio_path}")

    # Validação e tratamento do limite de 25 MB da API Groq Whisper
    target_audio_path = path
    if path.stat().st_size > 24 * 1024 * 1024:
        logger.warning(
            "Arquivo de áudio (%.2f MB) excede a margem de segurança de 24 MB da API Groq. "
            "Recomprimindo com FFmpeg para caber no limite...",
            path.stat().st_size / (1024 * 1024)
        )
        compressed = path.parent / f"{path.stem}_groq_opt.mp3"
        try:
            import subprocess
            cmd = [
                "ffmpeg", "-y", "-i", str(path),
                "-vn", "-ac", "1", "-ar", "16000", "-b:a", "24k",
                str(compressed)
            ]
            subprocess.run(cmd, capture_output=True, check=True)
            if compressed.exists() and compressed.stat().st_size > 0:
                target_audio_path = compressed
                logger.info("Áudio recomprimido com sucesso para %.2f MB.", compressed.stat().st_size / (1024 * 1024))
        except Exception as comp_err:
            logger.warning("Falha ao recomprimir áudio: %s. Tentando envio do arquivo original.", comp_err)

    last_error: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            logger.info("Iniciando transcrição com Groq Whisper (%s) [Tentativa %d/%d]: %s",
                        model, attempt + 1, max_retries, target_audio_path.name)

            with open(target_audio_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    file=(target_audio_path.name, audio_file.read()),
                    model=model,
                    response_format="verbose_json",
                    language=language,
                    temperature=0.0
                )

            # Processa os segmentos de tempo
            segments = []
            raw_segments = getattr(transcription, "segments", []) or []
            for seg in raw_segments:
                if isinstance(seg, dict):
                    segments.append({
                        "start": float(seg.get("start", 0.0)),
                        "end": float(seg.get("end", 0.0)),
                        "text": seg.get("text", "").strip()
                    })
                else:
                    segments.append({
                        "start": float(getattr(seg, "start", 0.0)),
                        "end": float(getattr(seg, "end", 0.0)),
                        "text": getattr(seg, "text", "").strip()
                    })

            full_text = getattr(transcription, "text", "") or ""
            logger.info("Transcrição concluída com sucesso. Total de segmentos: %d", len(segments))

            return {
                "text": full_text,
                "segments": segments
            }

        except Exception as exc:
            last_error = exc
            wait_time = base_delay * (2 ** attempt)
            logger.warning("Falha na chamada de transcrição Groq (%s). Tentativa %d/%d. Aguardando %.1fs...",
                           exc, attempt + 1, max_retries, wait_time)
            if attempt < max_retries - 1:
                time.sleep(wait_time)

    raise RuntimeError(f"Falha na transcrição após {max_retries} tentativas: {last_error}")


def format_transcript_with_timestamps(transcript_data: Dict[str, Any]) -> str:
    """
    Formata os segmentos da transcrição em blocos de texto legíveis com marcas temporais.
    Exemplo: [00:15 - 00:32] Texto falado pelo autor...
    """
    segments = transcript_data.get("segments", [])
    if not segments:
        return transcript_data.get("text", "")

    lines = []
    for seg in segments:
        start_sec = seg["start"]
        end_sec = seg["end"]
        start_min, start_s = divmod(int(start_sec), 60)
        end_min, end_s = divmod(int(end_sec), 60)
        timestamp_str = f"[{start_min:02d}:{start_s:02d} - {end_min:02d}:{end_s:02d} ({start_sec:.1f}s -> {end_sec:.1f}s)]"
        lines.append(f"{timestamp_str} {seg['text']}")

    return "\n".join(lines)


def _clean_and_parse_json(raw_text: str) -> List[Dict[str, Any]]:
    """
    Sanitiza e faz o parse robusto do JSON retornado pelos modelos LLM.
    Captura erros de parse sem travar a esteira e possui algoritmo de recuperação por regex.
    """
    if not raw_text or not raw_text.strip():
        logger.warning("Resposta vazia da IA para parse de JSON.")
        return []

    cleaned = raw_text.strip()
    if "```json" in cleaned:
        match = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()
    elif "```" in cleaned:
        match = re.search(r"```\s*(.*?)\s*```", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()

    # Tenta decodificar JSON direto
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as err:
        logger.warning("JSON padrão inválido (%s). Tentando recuperação estruturada via regex...", err)
        data = None

        # Tentativa de extração via regex de cada bloco de corte
        item_matches = re.findall(
            r'\{\s*"title"\s*:\s*"([^"]+)"\s*,\s*"start_time"\s*:\s*([0-9.]+)\s*,\s*"end_time"\s*:\s*([0-9.]+)(?:.*?"virality_score"\s*:\s*([0-9.]+))?(?:.*?"justification"\s*:\s*"([^"]*)")?',
            cleaned,
            re.DOTALL
        )
        if item_matches:
            cortes_regex = []
            for m in item_matches:
                cortes_regex.append({
                    "title": m[0],
                    "start_time": float(m[1]),
                    "end_time": float(m[2]),
                    "virality_score": float(m[3]) if m[3] else 8.0,
                    "justification": m[4] if m[4] else ""
                })
            data = {"cortes": cortes_regex}
        else:
            logger.error("Recuperação de JSON falhou completamente. Resposta bruta: %s", cleaned[:200])
            return []

    cortes = []
    if isinstance(data, dict):
        cortes = data.get("cortes", []) or data.get("cuts", [])
    elif isinstance(data, list):
        cortes = data

    sanitized = []
    for c in cortes:
        if not isinstance(c, dict):
            continue
        try:
            start = float(c.get("start_time", 0.0))
            end = float(c.get("end_time", 0.0))
            if end > start and (end - start) >= 5.0:
                sanitized.append({
                    "title": str(c.get("title", "Corte Viral")),
                    "start_time": round(start, 2),
                    "end_time": round(end, 2),
                    "virality_score": float(c.get("virality_score", 5.0)),
                    "justification": str(c.get("justification", ""))
                })
        except (ValueError, TypeError):
            continue

    return sanitized


def analyze_viral_cuts_gemini(
    transcript_text: str,
    model_name: str = "gemini-3.1-flash-lite",
    max_retries: int = 3,
    base_delay: float = 2.0
) -> List[Dict[str, Any]]:
    """
    Analisa a transcrição com Google Gemini aplicando retry automático e backoff exponencial.
    """
    client = get_gemini_client()
    system_instruction = load_director_prompt()

    prompt = (
        f"{system_instruction}\n\n"
        "--- TRANSCRIÇÃO COM TIMESTAMPS DO VÍDEO ---\n"
        f"{transcript_text}\n\n"
        "Analise cuidadosamente a transcrição acima e selecione os melhores cortes virais."
    )

    last_error: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            logger.info("Enviando transcrição para o Gemini (%s) [Tentativa %d/%d]...",
                        model_name, attempt + 1, max_retries)

            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.3
                )
            )

            return _clean_and_parse_json(response.text)

        except Exception as exc:
            last_error = exc
            wait_time = base_delay * (2 ** attempt)
            logger.warning("Erro na chamada Gemini (%s). Aguardando %.1fs para retry...", exc, wait_time)
            if attempt < max_retries - 1:
                time.sleep(wait_time)

    raise RuntimeError(f"Gemini indisponível após {max_retries} tentativas: {last_error}")


def analyze_viral_cuts_groq(
    transcript_text: str,
    model_name: str = "openai/gpt-oss-120b",
    max_retries: int = 3,
    base_delay: float = 2.0
) -> List[Dict[str, Any]]:
    """
    Analisa a transcrição utilizando modelos LLM da Groq com retry automático e backoff exponencial.
    """
    client = get_groq_client()
    system_instruction = load_director_prompt()

    messages = [
        {"role": "system", "content": system_instruction},
        {
            "role": "user",
            "content": f"Transcrição com timestamps:\n{transcript_text}\n\nSelecione os melhores cortes virais."
        }
    ]

    last_error: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            logger.info("Enviando transcrição para a Groq (%s) [Tentativa %d/%d]...",
                        model_name, attempt + 1, max_retries)

            try:
                response = client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0.3
                )
            except Exception as exc:
                logger.warning("Falha com %s (%s). Tentando alternativa qwen/qwen3.8-27b...", model_name, exc)
                response = client.chat.completions.create(
                    model="qwen/qwen3.8-27b",
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0.3
                )

            content = response.choices[0].message.content or "{}"
            return _clean_and_parse_json(content)

        except Exception as exc:
            last_error = exc
            wait_time = base_delay * (2 ** attempt)
            logger.warning("Erro na chamada Groq (%s). Aguardando %.1fs para retry...", exc, wait_time)
            if attempt < max_retries - 1:
                time.sleep(wait_time)

    raise RuntimeError(f"Groq indisponível após {max_retries} tentativas: {last_error}")


def detect_viral_moments(
    transcript_data: Dict[str, Any],
    provider: str = "gemini"
) -> List[Dict[str, Any]]:
    """
    Função unificada e resiliente de curadoria de cortes.
    Recebe os dados da transcrição e direciona para Gemini ou Groq com retry automático.
    Se o provedor primário falhar após todas as retentativas, chaveia automaticamente
    para o provedor secundário garantindo 100% de disponibilidade.
    """
    formatted_text = format_transcript_with_timestamps(transcript_data)
    if not formatted_text.strip():
        logger.warning("Transcrição vazia. Nenhum corte pode ser detectado.")
        return []

    if provider.lower() == "gemini":
        try:
            return analyze_viral_cuts_gemini(formatted_text)
        except Exception as exc:
            logger.error("Provedor Gemini esgotou retentativas: %s. Chaveando para Groq como fallback...", exc)
            try:
                return analyze_viral_cuts_groq(formatted_text)
            except Exception as fallback_exc:
                logger.error("Provedor Groq também falhou no fallback: %s", fallback_exc)
                return []
    else:
        try:
            return analyze_viral_cuts_groq(formatted_text)
        except Exception as exc:
            logger.error("Provedor Groq esgotou retentativas: %s. Chaveando para Gemini como fallback...", exc)
            try:
                return analyze_viral_cuts_gemini(formatted_text)
            except Exception as fallback_exc:
                logger.error("Provedor Gemini também falhou no fallback: %s", fallback_exc)
                return []
