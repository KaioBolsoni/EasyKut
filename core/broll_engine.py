"""
Módulo de B-Roll Automático & Efeitos de Retenção (Onda 4).
Extrai palavras-chave visuais da transcrição, consulta biblioteca local (data/broll/)
e integra opcionalmente com Pexels / Pixabay API sob allowlist HTTPS estrita.
"""

from collections import Counter
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import urllib.parse
import urllib.request

from core import config

logger = logging.getLogger(__name__)

STOPWORDS_PT_EN = {
    "para", "como", "isso", "aqui", "voce", "você", "sobre", "quando", "muito", "mais",
    "essa", "esse", "pela", "pelo", "porque", "tambem", "também", "entao", "então",
    "agora", "tudo", "pode", "fazer", "estar", "estao", "estão", "onde", "qual", "quem",
    "with", "that", "this", "from", "have", "your", "about", "what", "when", "they",
    "there", "their", "just", "into", "will", "would", "could", "should", "been",
}
ALLOWED_BROLL_HOSTS = {"api.pexels.com", "pixabay.com"}


def extract_visual_keywords(text: str, max_keywords: int = 5) -> List[str]:
    """Extrai palavras-chave visuais relevantes de um trecho de transcrição (offline)."""
    tokens = re.findall(r"[A-Za-zÀ-ÿ]{4,}", str(text or "").lower())
    filtered = [t for t in tokens if t not in STOPWORDS_PT_EN]
    if not filtered:
        return ["studio", "creator", "technology"]
    counts = Counter(filtered)
    return [word for word, _ in counts.most_common(max(1, min(8, int(max_keywords))))]


def _safe_https_json_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 10) -> Dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_BROLL_HOSTS:
        raise ValueError(f"Host B-Roll não autorizado: {parsed.hostname}")
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "EasyKutStudio/4.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _search_pexels_videos(query: str, orientation: str, max_results: int) -> List[Dict[str, Any]]:
    if not config.PEXELS_API_KEY:
        return []
    q = urllib.parse.quote_plus(query)
    url = f"https://api.pexels.com/videos/search?query={q}&orientation={orientation}&per_page={max_results}"
    data = _safe_https_json_get(
        url,
        headers={"Authorization": config.PEXELS_API_KEY, "User-Agent": "EasyKutStudio/4.0"},
    )
    out: List[Dict[str, Any]] = []
    for v in data.get("videos", [])[:max_results]:
        v_files = v.get("video_files") or []
        best = next((f for f in v_files if f.get("quality") == "hd"), v_files[0] if v_files else {})
        out.append({
            "id": str(v.get("id", "")),
            "title": f"Pexels #{v.get('id', '')} ({query})",
            "provider": "pexels",
            "duration": float(v.get("duration", 0.0)),
            "thumbnail": str(v.get("image", "")),
            "preview_url": str(best.get("link", "")),
            "source_url": str(v.get("url", "")),
            "is_local": False,
        })
    return out


def _search_pixabay_videos(query: str, max_results: int) -> List[Dict[str, Any]]:
    if not config.PIXABAY_API_KEY:
        return []
    q = urllib.parse.quote_plus(query)
    key = urllib.parse.quote_plus(config.PIXABAY_API_KEY)
    url = f"https://pixabay.com/api/videos/?key={key}&q={q}&per_page={max(3, max_results)}"
    data = _safe_https_json_get(url)
    out: List[Dict[str, Any]] = []
    for hit in data.get("hits", [])[:max_results]:
        videos = hit.get("videos") or {}
        medium = videos.get("medium") or videos.get("small") or {}
        out.append({
            "id": str(hit.get("id", "")),
            "title": str(hit.get("tags", query))[:80],
            "provider": "pixabay",
            "duration": float(hit.get("duration", 0.0)),
            "thumbnail": str(medium.get("thumbnail", "")),
            "preview_url": str(medium.get("url", "")),
            "source_url": str(hit.get("pageURL", "")),
            "is_local": False,
        })
    return out


def _list_local_broll(broll_dir: Path, max_results: int) -> List[Dict[str, Any]]:
    if not broll_dir.exists():
        return []
    items: List[Dict[str, Any]] = []
    for f in sorted(broll_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in (".mp4", ".mov", ".webm", ".jpg", ".png"):
            items.append({
                "id": f.stem,
                "title": f.stem.replace("_", " "),
                "provider": "local",
                "duration": 0.0,
                "thumbnail": "",
                "preview_url": f"/data/broll/{f.name}",
                "source_url": f"/data/broll/{f.name}",
                "is_local": True,
            })
            if len(items) >= max_results:
                break
    return items


def suggest_broll_assets(
    *,
    query: Optional[str] = None,
    transcript_excerpt: Optional[str] = None,
    orientation: str = "portrait",
    max_results: int = 6,
    broll_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Sugere palavras-chave visuais e busca clipes B-Roll em Pexels, Pixabay ou biblioteca local."""
    keywords = extract_visual_keywords(query or transcript_excerpt or "", max_keywords=5)
    search_term = (query or " ".join(keywords[:2])).strip() or "studio"

    items: List[Dict[str, Any]] = []
    provider_used = "local"

    if config.PEXELS_API_KEY:
        try:
            items = _search_pexels_videos(search_term, orientation, max_results)
            if items:
                provider_used = "pexels"
        except Exception as exc:
            logger.warning("Busca B-Roll Pexels falhou: %s", exc)

    if not items and config.PIXABAY_API_KEY:
        try:
            items = _search_pixabay_videos(search_term, max_results)
            if items:
                provider_used = "pixabay"
        except Exception as exc:
            logger.warning("Busca B-Roll Pixabay falhou: %s", exc)

    if not items:
        items = _list_local_broll(broll_dir or config.BROLL_DIR, max_results)

    return {
        "query": search_term,
        "keywords": keywords,
        "provider": provider_used,
        "configured_providers": {
            "pexels": bool(config.PEXELS_API_KEY),
            "pixabay": bool(config.PIXABAY_API_KEY),
        },
        "items": items,
    }
