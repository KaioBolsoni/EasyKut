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
from core.media_utils import normalize_virality_score

logger = logging.getLogger(__name__)

PROMPT_FILE = PROMPTS_DIR / "diretor_cortes.txt"

# Modelos ativos recomendados para produção e fallback (prioriza baixa latência e alta disponibilidade)
GEMINI_MODELS = [
    "gemini-3.5-flash-lite",  # 1M tokens, mais rápido e estável para extração/curadoria JSON
    "gemini-4-hydrogen",      # 1M tokens, alta qualidade e equilíbrio
    "gemini-3.1-flash-lite",  # Fallback econômico ultrarrápido
    "gemini-3.8-flash",       # Fallback secundário
]

GROQ_MODELS = [
    "openai/gpt-oss-120b",    # Modelo principal de alta capacidade
    "openai/gpt-oss-20b",     # Alternativa rápida e eficiente
    "qwen/qwen3.8-27b",       # Fallback de alta retenção
]


def get_groq_client() -> Groq:
    """Retorna instância autenticada do cliente Groq com timeout explícito."""
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY não configurada no arquivo .env")
    return Groq(api_key=GROQ_API_KEY, timeout=60.0)


def get_gemini_client() -> genai.Client:
    """Retorna instância autenticada do cliente Google GenAI com timeout explícito (60s)."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY não configurada no arquivo .env")
    return genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=60000),
    )


def load_director_prompt() -> str:
    """Carrega o prompt do sistema para análise de cortes virais."""
    if PROMPT_FILE.exists():
        return PROMPT_FILE.read_text(encoding="utf-8")
    return (
        "Você é um diretor de vídeos virais. Analise a transcrição com timestamps e "
        "retorne um JSON com a chave 'cortes' contendo title, start_time, end_time, virality_score (0 a 100), tipo ('short' ou 'longo') e justification."
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
            import subprocess  # nosec B404
            cmd = [
                "ffmpeg", "-nostdin", "-y", "-i", str(path),
                "-vn", "-ac", "1", "-ar", "16000", "-b:a", "24k",
                str(compressed)
            ]
            subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True, timeout=120)  # nosec B603
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
                audio_bytes = audio_file.read()
                try:
                    transcription = client.audio.transcriptions.create(
                        file=(target_audio_path.name, audio_bytes),
                        model=model,
                        response_format="verbose_json",
                        timestamp_granularities=["word", "segment"],
                        language=language,
                        temperature=0.0,
                    )
                except TypeError:
                    transcription = client.audio.transcriptions.create(
                        file=(target_audio_path.name, audio_bytes),
                        model=model,
                        response_format="verbose_json",
                        language=language,
                        temperature=0.0,
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

            # Processa timestamps palavra-por-palavra (para legendas Karaoke .ass e Jump Cuts)
            words = []
            raw_words = getattr(transcription, "words", []) or []
            for w_item in raw_words:
                if isinstance(w_item, dict):
                    w_txt = str(w_item.get("word", w_item.get("text", ""))).strip()
                    w_start = float(w_item.get("start", 0.0))
                    w_end = float(w_item.get("end", w_start))
                else:
                    w_txt = str(getattr(w_item, "word", getattr(w_item, "text", ""))).strip()
                    w_start = float(getattr(w_item, "start", 0.0))
                    w_end = float(getattr(w_item, "end", w_start))
                if w_txt:
                    words.append({"word": w_txt, "start": w_start, "end": max(w_start, w_end)})

            full_text = getattr(transcription, "text", "") or ""
            logger.info(
                "Transcrição concluída com sucesso. Segmentos: %d | Palavras: %d",
                len(segments), len(words),
            )

            return {
                "text": full_text,
                "segments": segments,
                "words": words,
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
    Formata os segmentos da transcrição de forma compacta com timestamps exatos em segundos.
    Exemplo: [15.0-32.4] Texto falado pelo autor...
    """
    segments = transcript_data.get("segments", [])
    if not segments:
        return transcript_data.get("text", "")

    lines = []
    for seg in segments:
        start_sec = float(seg.get("start", 0.0))
        end_sec = float(seg.get("end", start_sec))
        text = str(seg.get("text", "")).strip()
        if text:
            lines.append(f"[{start_sec:.1f}-{end_sec:.1f}] {text}")

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

    # Tenta decodificar JSON direto ou extraindo o bloco delimitador externo
    data = None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            try:
                data = json.loads(cleaned[first_brace:last_brace + 1])
            except json.JSONDecodeError:
                data = None

    if data is None:
        logger.warning("JSON padrão inválido. Tentando recuperação estruturada via regex...")

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
                    "virality_score": float(m[3]) if m[3] else 80.0,
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
            dur = end - start
            if end > start and dur >= 5.0:
                raw_score = normalize_virality_score(c.get("virality_score", c.get("score", 85.0)), default=85.0)

                tipo_val = str(c.get("tipo", "short" if dur <= 180.0 else "longo")).strip().lower()
                if tipo_val not in ("short", "longo"):
                    tipo_val = "short" if dur <= 180.0 else "longo"

                sanitized.append({
                    "title": str(c.get("title", "Corte Viral")),
                    "start_time": round(start, 2),
                    "end_time": round(end, 2),
                    "virality_score": round(raw_score, 1),
                    "tipo": tipo_val,
                    "justification": str(c.get("justification", ""))
                })
        except (ValueError, TypeError):
            continue

    return sanitized


def _should_fast_fail_model(err_str: str) -> bool:
    """Retorna True para erros que justificam trocar imediatamente para o próximo modelo (sem repetir espera longa)."""
    low = (err_str or "").lower()
    fast_fail_tokens = (
        "404",
        "not_found",
        "not found",
        "504",
        "deadline_exceeded",
        "deadline expired",
        "503",
        "unavailable",
        "high demand",
        "429",
        "resource_exhausted",
        "timed out",
        "timeout",
    )
    return any(tok in low for tok in fast_fail_tokens)


def analyze_viral_cuts_gemini(
    transcript_text: str,
    model_name: str = "gemini-3.5-flash-lite",
    max_retries: int = 2,
    base_delay: float = 2.0
) -> List[Dict[str, Any]]:
    """
    Analisa a transcrição com Google Gemini utilizando cadeia de fallback de modelos ativos,
    system_instruction dedicado e fast-fail imediato em 404/429/503/504 para evitar travamentos longos.
    """
    client = get_gemini_client()
    system_instruction = load_director_prompt()

    user_prompt = (
        "--- TRANSCRIÇÃO COM TIMESTAMPS EM SEGUNDOS [inicio-fim] ---\n"
        f"{transcript_text}\n\n"
        "Analise a transcrição acima e retorne o JSON com os melhores cortes virais."
    )

    model_candidates = []
    preferred = [model_name] if model_name else []
    for m in preferred + GEMINI_MODELS:
        if m and m not in model_candidates:
            model_candidates.append(m)

    errors_by_model: Dict[str, str] = {}

    for current_model in model_candidates:
        for attempt in range(max_retries):
            try:
                logger.info("Enviando transcrição para o Gemini (%s) [Tentativa %d/%d]...",
                            current_model, attempt + 1, max_retries)

                response = client.models.generate_content(
                    model=current_model,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                        temperature=0.3,
                    )
                )

                return _clean_and_parse_json(response.text)

            except Exception as exc:
                err_str = str(exc)
                errors_by_model[current_model] = err_str
                logger.warning("Falha no modelo Gemini %s [Tentativa %d/%d]: %s",
                               current_model, attempt + 1, max_retries, exc)

                if _should_fast_fail_model(err_str):
                    logger.warning(
                        "Modelo Gemini %s sobrecarregado/indisponível (%s). Avançando imediatamente para o próximo...",
                        current_model, err_str[:80],
                    )
                    break
                wait_time = base_delay * (2 ** attempt)
                if attempt < max_retries - 1:
                    logger.warning("Aguardando %.1fs para retry no modelo Gemini %s...", wait_time, current_model)
                    time.sleep(wait_time)

    errors_summary = " | ".join([f"[{m}: {err}]" for m, err in errors_by_model.items()])
    raise RuntimeError(f"Gemini indisponível após tentar modelos {model_candidates}. Detalhes: {errors_summary}")


def analyze_viral_cuts_groq(
    transcript_text: str,
    model_name: str = "openai/gpt-oss-120b",
    max_retries: int = 2,
    base_delay: float = 2.0
) -> List[Dict[str, Any]]:
    """
    Analisa a transcrição utilizando modelos LLM oficiais da Groq com fallback e retry automático.
    Registra e reporta erros individuais por modelo para diagnóstico transparente.
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

    model_candidates = []
    preferred = [model_name] if model_name else []
    for m in preferred + GROQ_MODELS:
        if m and m not in model_candidates:
            model_candidates.append(m)

    errors_by_model: Dict[str, str] = {}

    for current_model in model_candidates:
        for attempt in range(max_retries):
            try:
                logger.info("Enviando transcrição para a Groq (%s) [Tentativa %d/%d]...",
                            current_model, attempt + 1, max_retries)

                response = client.chat.completions.create(
                    model=current_model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0.3
                )

                content = response.choices[0].message.content or "{}"
                return _clean_and_parse_json(content)

            except Exception as exc:
                err_str = str(exc)
                errors_by_model[current_model] = err_str
                logger.warning("Falha no modelo Groq %s [Tentativa %d/%d]: %s",
                               current_model, attempt + 1, max_retries, exc)

                if "404" in err_str or "model_not_found" in err_str or "decommissioned" in err_str.lower() or "does not exist" in err_str.lower():
                    logger.warning("Modelo Groq %s indisponível (404). Testando próximo da lista...", current_model)
                    break
                wait_time = base_delay * (2 ** attempt)
                if attempt < max_retries - 1:
                    logger.warning("Aguardando %.1fs para retry no modelo Groq %s...", wait_time, current_model)
                    time.sleep(wait_time)

    errors_summary = " | ".join([f"[{m}: {err}]" for m, err in errors_by_model.items()])
    raise RuntimeError(f"Groq indisponível após tentar modelos {model_candidates}. Detalhes: {errors_summary}")


def detect_viral_moments(
    transcript_data: Dict[str, Any],
    provider: str = "gemini"
) -> List[Dict[str, Any]]:
    """
    Função unificada e resiliente de curadoria de cortes.
    Recebe os dados da transcrição e direciona para Gemini ou Groq com retry automático.
    Se o provedor primário falhar, chaveia para o secundário. Se ambos falharem, propaga o erro real.
    """
    formatted_text = format_transcript_with_timestamps(transcript_data)
    if not formatted_text.strip():
        logger.warning("Transcrição vazia. Nenhum corte pode ser detectado.")
        return []

    if provider.lower() == "gemini":
        try:
            return analyze_viral_cuts_gemini(formatted_text)
        except Exception as exc:
            logger.error("Provedor Gemini falhou: %s. Chaveando para Groq como fallback...", exc)
            try:
                return analyze_viral_cuts_groq(formatted_text)
            except Exception as fallback_exc:
                logger.error("Provedor Groq também falhou no fallback: %s", fallback_exc)
                raise RuntimeError(
                    f"Falha na análise de IA — Gemini: {exc} | Groq fallback: {fallback_exc}"
                ) from fallback_exc
    else:
        try:
            return analyze_viral_cuts_groq(formatted_text)
        except Exception as exc:
            logger.error("Provedor Groq falhou: %s. Chaveando para Gemini como fallback...", exc)
            try:
                return analyze_viral_cuts_gemini(formatted_text)
            except Exception as fallback_exc:
                logger.error("Provedor Gemini também falhou no fallback: %s", fallback_exc)
                raise RuntimeError(
                    f"Falha na análise de IA — Groq: {exc} | Gemini fallback: {fallback_exc}"
                ) from fallback_exc


def _parse_seo_kit_json(raw_text: str, fallback_title: str, fallback_excerpt: str) -> Dict[str, Any]:
    """Sanitiza resposta JSON do kit de publicação SEO garantindo 3 títulos A/B, descrição e hashtags."""
    cleaned = (raw_text or "").strip()
    if "```" in cleaned:
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
        if m:
            cleaned = m.group(1).strip()

    data: Dict[str, Any] = {}
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            data = parsed
    except Exception:
        first_b = cleaned.find("{")
        last_b = cleaned.rfind("}")
        if first_b != -1 and last_b > first_b:
            try:
                parsed = json.loads(cleaned[first_b:last_b + 1])
                if isinstance(parsed, dict):
                    data = parsed
            except Exception:
                data = {}

    raw_titles = data.get("titles") or data.get("titulos") or []
    titles = [str(t).strip()[:110] for t in raw_titles if str(t).strip()][:3]
    if not titles:
        base = (fallback_title or "Corte Viral").strip()[:80]
        titles = [
            base,
            f"Você precisa ouvir isso: {base[:75]}",
            f"O momento exato que mudou tudo — {base[:65]}",
        ]
    while len(titles) < 3:
        titles.append(f"{titles[0]} (Parte {len(titles) + 1})")

    desc = str(data.get("description") or data.get("descricao") or "").strip()
    if not desc:
        snippet = (fallback_excerpt or fallback_title or "").strip()[:220]
        desc = f"{snippet}\n\nSalve para rever depois e comente sua opinião!"

    raw_tags = data.get("hashtags") or []
    if isinstance(raw_tags, str):
        raw_tags = [tok.strip() for tok in raw_tags.replace(",", " ").split() if tok.strip()]
    hashtags: List[str] = []
    for tag in raw_tags:
        clean_tag = re.sub(r"[^a-zA-Z0-9_À-ÿ]", "", str(tag).lstrip("#"))
        if clean_tag and f"#{clean_tag}" not in hashtags:
            hashtags.append(f"#{clean_tag}")
    if not hashtags:
        hashtags = ["#shorts", "#reels", "#tiktok", "#cortes", "#viral"]

    return {
        "titles": titles[:3],
        "description": desc[:2000],
        "hashtags": hashtags[:12],
        "hook": str(data.get("hook") or titles[0])[:120],
    }


def generate_seo_publish_kit(
    cut_title: str,
    transcript_excerpt: str,
    justification: str = "",
    provider: str = "gemini",
) -> Dict[str, Any]:
    """
    Gera Kit de Publicação Viral via IA (3 títulos A/B, descrição otimizada, hook e hashtags)
    com fallback automático Gemini ↔ Groq e fallback determinístico offline se chaves não existirem.
    """
    prompt = (
        "Você é um estrategista de crescimento para YouTube Shorts, Instagram Reels e TikTok.\n"
        "Com base no título do corte e no trecho transcrito abaixo, retorne EXCLUSIVAMENTE um JSON válido com as chaves:\n"
        '{"titles": ["Título A", "Título B", "Título C"], "hook": "Frase curta de gancho (até 60 chars)", '
        '"description": "Legenda pronta para publicação com CTA", "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"]}\n\n'
        f"Título atual: {cut_title}\n"
        f"Justificativa editorial: {justification}\n"
        f"Transcrição do corte:\n{transcript_excerpt[:2500]}"
    )

    def _call_gemini() -> str:
        client = get_gemini_client()
        for model_name in GEMINI_MODELS:
            try:
                resp = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.5),
                )
                return resp.text or ""
            except Exception as exc:
                logger.warning("SEO Kit Gemini %s falhou: %s", model_name, exc)
        raise RuntimeError("Nenhum modelo Gemini respondeu para SEO Kit.")

    def _call_groq() -> str:
        client = get_groq_client()
        for model_name in GROQ_MODELS:
            try:
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.5,
                )
                return resp.choices[0].message.content or ""
            except Exception as exc:
                logger.warning("SEO Kit Groq %s falhou: %s", model_name, exc)
        raise RuntimeError("Nenhum modelo Groq respondeu para SEO Kit.")

    order = [_call_gemini, _call_groq] if provider.lower() == "gemini" else [_call_groq, _call_gemini]
    for fn in order:
        try:
            raw = fn()
            if raw.strip():
                kit = _parse_seo_kit_json(raw, cut_title, transcript_excerpt)
                kit["source"] = "ai"
                return kit
        except Exception as exc:
            logger.warning("Fallback SEO Kit acionado: %s", exc)

    kit = _parse_seo_kit_json("", cut_title, transcript_excerpt)
    kit["source"] = "template_fallback"
    return kit
