"""
Módulo de geração, sincronização temporal e formatação FFmpeg de legendas (.srt e Karaoke .ass).
Suporta presets virais palavra-por-palavra estilo Hormozi / CapCut / Neon / Minimalista / Caixa.
"""

import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import uuid

from core import media_utils

logger = logging.getLogger(__name__)

# Presets estilizados em formato ASS (cores em BGR &H00BBGGRR)
SUBTITLE_PRESETS: Dict[str, Dict[str, Any]] = {
    "hormozi": {
        "label": "Hormozi Viral (Amarelo)",
        "font": "Arial Black",
        "font_size_v": 68,
        "font_size_h": 54,
        "primary": "&H00FFFFFF",
        "highlight": "&H0000EAFF",  # Amarelo vibrante
        "outline": "&H00000000",
        "back": "&H80000000",
        "border_style": 1,
        "outline_width": 5,
        "shadow": 2,
        "uppercase": True,
        "margin_v_vertical": 240,
        "margin_v_horizontal": 80,
    },
    "neon": {
        "label": "Neon Cyber (Ciano)",
        "font": "Arial Black",
        "font_size_v": 66,
        "font_size_h": 52,
        "primary": "&H00FFFFFF",
        "highlight": "&H00FFEF00",  # Ciano neon
        "outline": "&H00441100",
        "back": "&H90000000",
        "border_style": 1,
        "outline_width": 4,
        "shadow": 3,
        "uppercase": True,
        "margin_v_vertical": 240,
        "margin_v_horizontal": 80,
    },
    "boxed": {
        "label": "Caixa Destaque (Roxo)",
        "font": "Arial",
        "font_size_v": 60,
        "font_size_h": 48,
        "primary": "&H00FFFFFF",
        "highlight": "&H0080FF00",  # Verde-lima destaque
        "outline": "&H00000000",
        "back": "&HB0201010",
        "border_style": 3,
        "outline_width": 8,
        "shadow": 0,
        "uppercase": False,
        "margin_v_vertical": 230,
        "margin_v_horizontal": 75,
    },
    "minimal": {
        "label": "Minimalista Clean",
        "font": "Arial",
        "font_size_v": 54,
        "font_size_h": 44,
        "primary": "&H00F2F2F2",
        "highlight": "&H00FFFFFF",
        "outline": "&H00181818",
        "back": "&H00000000",
        "border_style": 1,
        "outline_width": 3,
        "shadow": 1,
        "uppercase": False,
        "margin_v_vertical": 210,
        "margin_v_horizontal": 65,
    },
    "classic": {
        "label": "Clássico (SRT/ASS)",
        "font": "Arial",
        "font_size_v": 56,
        "font_size_h": 46,
        "primary": "&H00FFFFFF",
        "highlight": "&H0055D4FF",
        "outline": "&H00000000",
        "back": "&H80000000",
        "border_style": 1,
        "outline_width": 3,
        "shadow": 1,
        "uppercase": False,
        "margin_v_vertical": 210,
        "margin_v_horizontal": 65,
    },
}


def format_srt_time(seconds: float) -> str:
    """Converte segundos em formato de timestamp padrão SRT: HH:MM:SS,mmm."""
    clamped_sec = max(0.0, seconds)
    millis = int(round((clamped_sec - int(clamped_sec)) * 1000))
    if millis >= 1000:
        millis = 999
    total_seconds = int(clamped_sec)
    secs = total_seconds % 60
    total_minutes = total_seconds // 60
    mins = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def format_ass_time(seconds: float) -> str:
    """Converte segundos em formato de timestamp ASS: H:MM:SS.cc (centésimos)."""
    clamped = max(0.0, float(seconds))
    total_cs = int(round(clamped * 100))
    cs = total_cs % 100
    total_sec = total_cs // 100
    secs = total_sec % 60
    total_min = total_sec // 60
    mins = total_min % 60
    hours = total_min // 60
    return f"{hours:d}:{mins:02d}:{secs:02d}.{cs:02d}"


def escape_libavfilter_path(file_path: str) -> str:
    """Escapa caminhos Windows/POSIX para uso seguro nos filtros subtitles/ass/movie do FFmpeg."""
    p = Path(file_path).resolve()
    clean_path = str(p).replace("\\", "/")
    if len(clean_path) > 1 and clean_path[1] == ":":
        clean_path = f"{clean_path[0]}\\:{clean_path[2:]}"
    return (
        clean_path.replace("'", "\\'")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _ass_inline_color(style_color: str) -> str:
    """Converte cor de estilo ASS (&HAABBGGRR) em cor inline válida para tag \\1c (&HBBGGRR&)."""
    raw = str(style_color or "&H00FFFFFF").strip().upper().replace("&", "")
    if raw.startswith("H"):
        raw = raw[1:]
    if len(raw) == 8:
        raw = raw[2:]  # descarta byte alpha AA para manter BBGGRR
    if len(raw) != 6 or not re.fullmatch(r"[0-9A-F]{6}", raw):
        raw = "FFFFFF"
    return f"&H{raw}&"


def _escape_ass_text(text: str) -> str:
    """Remove ou escapa tags ASS indesejadas no texto falado."""
    cleaned = str(text or "").replace("\r", " ").replace("\n", " ")
    cleaned = cleaned.replace("{", "(").replace("}", ")").replace("\\", "")
    return re.sub(r"\s+", " ", cleaned).strip()


def generate_srt_for_segment(
    segments: List[Dict[str, Any]],
    start_time: float,
    end_time: float,
    output_srt_path: Optional[str] = None,
    temp_dir: Optional[Path] = None,
) -> Optional[str]:
    """
    Recebe os segmentos com timestamps do Whisper e o intervalo do corte (start_time, end_time),
    ajusta os tempos para iniciarem em 00:00:00,000 e salva um arquivo .srt em data/temp/.
    """
    if not segments:
        return None

    srt_entries = []
    counter = 1

    for seg in segments:
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", 0.0))
        text = str(seg.get("text", "")).strip()

        if seg_end <= start_time or seg_start >= end_time or not text:
            continue

        rel_start = max(0.0, seg_start - start_time)
        rel_end = min(end_time - start_time, seg_end - start_time)

        if rel_end > rel_start:
            srt_entries.append(
                f"{counter}\n"
                f"{format_srt_time(rel_start)} --> {format_srt_time(rel_end)}\n"
                f"{text}\n"
            )
            counter += 1

    if not srt_entries:
        logger.info("Nenhuma fala encontrada no intervalo %.2fs -> %.2fs para legenda.", start_time, end_time)
        return None

    if output_srt_path:
        dest_srt = Path(output_srt_path)
    else:
        base_temp = temp_dir or media_utils.TEMP_DIR
        dest_srt = base_temp / f"sub_{int(start_time)}_{int(end_time)}_{uuid.uuid4().hex[:6]}.srt"

    dest_srt.parent.mkdir(parents=True, exist_ok=True)
    dest_srt.write_text("\n".join(srt_entries), encoding="utf-8")
    logger.info("Legenda SRT sincronizada gerada com sucesso: %s", dest_srt)
    return str(dest_srt.resolve())


def extract_words_for_segment(
    segments: Optional[List[Dict[str, Any]]],
    words: Optional[List[Dict[str, Any]]],
    start_time: float,
    end_time: float,
    prefer_segments: bool = False,
) -> List[Dict[str, Any]]:
    """
    Extrai palavras com timestamps relativos ao corte [0, end_time - start_time].
    Usa word-level timestamps reais do Groq Whisper quando disponíveis; caso ausentes
    ou quando o usuário editou legendas manualmente (prefer_segments=True), interpola
    uniformemente as palavras de cada segmento.
    """
    clip_dur = max(0.0, float(end_time) - float(start_time))
    rel_words: List[Dict[str, Any]] = []

    if words and not prefer_segments:
        raw_indexed: List[tuple[float, int, float, str]] = []
        for idx, w in enumerate(words):
            ws = float(w.get("start", 0.0))
            we = float(w.get("end", ws))
            txt = _escape_ass_text(w.get("word", w.get("text", "")))
            if not txt or we <= start_time or ws >= end_time:
                continue
            rs = max(0.0, ws - start_time)
            re_t = min(clip_dur, max(rs + 0.06, we - start_time))
            if re_t > rs:
                raw_indexed.append((rs, idx, re_t, txt))

        # Ordena e garante timeline monotônica sem sobreposições (evita empilhamento no libass)
        raw_indexed.sort(key=lambda item: (item[0], item[1]))
        cursor = 0.0
        for rs, _idx, re_t, txt in raw_indexed:
            mono_s = max(cursor, rs)
            mono_e = min(clip_dur, max(mono_s + 0.06, re_t))
            if mono_e > mono_s and mono_s < clip_dur:
                rel_words.append({"word": txt, "start": round(mono_s, 3), "end": round(mono_e, 3)})
                cursor = mono_s

    if rel_words:
        return rel_words

    for seg in segments or []:
        ss = float(seg.get("start", 0.0))
        se = float(seg.get("end", 0.0))
        text = _escape_ass_text(seg.get("text", ""))
        if not text or se <= start_time or ss >= end_time:
            continue
        rs = max(0.0, ss - start_time)
        re_t = min(clip_dur, se - start_time)
        if re_t <= rs:
            continue
        tokens = text.split()
        step = (re_t - rs) / max(1, len(tokens))
        for idx, tok in enumerate(tokens):
            w_s = rs + (idx * step)
            w_e = rs + ((idx + 1) * step)
            rel_words.append({"word": tok, "start": w_s, "end": max(w_s + 0.05, w_e)})

    return rel_words


def generate_ass_karaoke_for_segment(
    segments: Optional[List[Dict[str, Any]]],
    start_time: float,
    end_time: float,
    words: Optional[List[Dict[str, Any]]] = None,
    preset: str = "hormozi",
    is_vertical: bool = True,
    play_res_x: int = 1080,
    play_res_y: int = 1920,
    max_words_per_chunk: int = 3,
    prefer_segments: bool = False,
    output_ass_path: Optional[str] = None,
    temp_dir: Optional[Path] = None,
) -> Optional[str]:
    """
    Gera legenda dinâmica palavra-por-palavra (.ass) estilo Hormozi/CapCut com
    agrupamento em blocos de 2 a 4 palavras, destaque colorido e animação pop sutil.
    """
    rel_words = extract_words_for_segment(
        segments=segments,
        words=words,
        start_time=start_time,
        end_time=end_time,
        prefer_segments=prefer_segments,
    )
    if not rel_words:
        return None

    cfg = SUBTITLE_PRESETS.get(str(preset or "hormozi").lower(), SUBTITLE_PRESETS["hormozi"])
    font_size = cfg["font_size_v"] if is_vertical else cfg["font_size_h"]
    margin_v = cfg["margin_v_vertical"] if is_vertical else cfg["margin_v_horizontal"]
    chunk_size = max(2, min(5, int(max_words_per_chunk)))

    header = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {int(play_res_x)}",
        f"PlayResY: {int(play_res_y)}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        (
            f"Style: Default,{cfg['font']},{font_size},{cfg['primary']},{cfg['highlight']},"
            f"{cfg['outline']},{cfg['back']},-1,0,0,0,100,100,0,0,{cfg['border_style']},"
            f"{cfg['outline_width']},{cfg['shadow']},2,60,60,{margin_v},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])

    # Agrupa palavras respeitando pausas > 0.45s ou teto chunk_size
    chunks: List[List[Dict[str, Any]]] = []
    cur_chunk: List[Dict[str, Any]] = []
    for w in rel_words:
        if cur_chunk and (len(cur_chunk) >= chunk_size or (w["start"] - cur_chunk[-1]["end"]) > 0.45):
            chunks.append(cur_chunk)
            cur_chunk = []
        cur_chunk.append(w)
    if cur_chunk:
        chunks.append(cur_chunk)

    events: List[str] = []
    hl = _ass_inline_color(cfg["highlight"])
    pri = _ass_inline_color(cfg["primary"])
    upper = bool(cfg["uppercase"])
    last_ev_end = 0.0

    for c_idx, chunk in enumerate(chunks):
        next_chunk_start = chunks[c_idx + 1][0]["start"] if c_idx + 1 < len(chunks) else None
        for active_idx, active_word in enumerate(chunk):
            ev_start = max(last_ev_end, float(active_word["start"]))
            if active_idx + 1 < len(chunk):
                ev_end = float(chunk[active_idx + 1]["start"])
            else:
                ev_end = float(active_word["end"])
                if next_chunk_start is not None:
                    ev_end = min(ev_end, float(next_chunk_start))

            if ev_end <= ev_start:
                ev_end = ev_start + 0.08
                if next_chunk_start is not None and active_idx == len(chunk) - 1:
                    ev_end = max(ev_start + 0.04, min(ev_end, float(next_chunk_start)))
            last_ev_end = ev_end

            rendered_tokens: List[str] = []
            for idx, tok in enumerate(chunk):
                label = tok["word"].upper() if upper else tok["word"]
                if idx == active_idx:
                    rendered_tokens.append(
                        f"{{\\1c{hl}\\fscx114\\fscy114\\t(0,90,\\fscx100\\fscy100)}}{label}{{\\1c{pri}\\fscx100\\fscy100}}"
                    )
                else:
                    rendered_tokens.append(label)

            line_text = " ".join(rendered_tokens)
            events.append(
                f"Dialogue: 0,{format_ass_time(ev_start)},{format_ass_time(ev_end)},Default,,0,0,0,,{line_text}"
            )

    if not events:
        return None

    if output_ass_path:
        dest_ass = Path(output_ass_path)
    else:
        base_temp = temp_dir or media_utils.TEMP_DIR
        dest_ass = base_temp / f"karaoke_{preset}_{int(start_time)}_{int(end_time)}_{uuid.uuid4().hex[:6]}.ass"

    dest_ass.parent.mkdir(parents=True, exist_ok=True)
    # UTF-8 com BOM garante acentuação PT-BR perfeita no libass no Windows
    dest_ass.write_text(header + "\n" + "\n".join(events) + "\n", encoding="utf-8-sig")
    logger.info("Legenda Karaoke ASS (%s) gerada com sucesso: %s (%d eventos)", preset, dest_ass.name, len(events))
    return str(dest_ass.resolve())


def format_ffmpeg_subtitles_filter(srt_path: str, is_vertical: bool = True) -> str:
    """
    Formata e escapa o caminho do arquivo .srt para o filtro subtitles do FFmpeg (libavfilter)
    no Windows/Linux, aplicando tipografia para Shorts (9:16) ou horizontal (16:9).
    """
    clean_path = escape_libavfilter_path(srt_path)

    if is_vertical:
        style = (
            "FontName=Arial,FontSize=16,Bold=1,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,"
            "Alignment=2,MarginV=55"
        )
    else:
        style = (
            "FontName=Arial,FontSize=18,Bold=1,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,"
            "Alignment=2,MarginV=25"
        )

    return f"subtitles='{clean_path}':force_style='{style}'"


def format_ffmpeg_ass_filter(ass_path: str) -> str:
    """Formata e escapa o caminho do arquivo .ass para o filtro ass do FFmpeg (libavfilter)."""
    return f"ass='{escape_libavfilter_path(ass_path)}'"


_format_ffmpeg_subtitles_filter = format_ffmpeg_subtitles_filter
