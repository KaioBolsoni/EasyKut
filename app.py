"""
EasyKut - Aplicação Web Streamlit (v2.0)
Plataforma profissional para curadoria inteligente de cortes virais com Groq Whisper,
Google Gemini / Groq LLMs, Estúdio de Enquadramento em Tempo Real (4 Modos de Layout)
e Renderização Precisa com FFmpeg e OpenCV.
"""

import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

import cv2

# Adiciona o diretório atual ao path de execução
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import streamlit as st

from core.config import (
    CUTS_DIR,
    DATA_DIR,
    DOWNLOADS_DIR,
    FFMPEG_AVAILABLE,
    GEMINI_API_KEY,
    GROQ_API_KEY,
    validate_config,
)
from core.downloader import (
    download_audio,
    download_video,
    extract_video_info,
    get_direct_stream_url,
    is_valid_video_url,
)
from core.ai_apis import detect_viral_moments, transcribe_audio_groq
from core.video_engine import (
    cleanup_temporary_files,
    cut_video,
    draw_framing_preview,
    extract_preview_frame,
    get_video_metadata,
)
from database.db_manager import (
    add_corte,
    add_video,
    delete_video,
    init_db,
    list_cortes,
    list_videos,
    save_analysis,
    update_corte_status,
    update_video_status,
)

logger = logging.getLogger(__name__)

# Inicializa o banco de dados SQLite
init_db()

# Configuração da página do Streamlit
st.set_page_config(
    page_title="EasyKut - Estúdio de Cortes Virais",
    page_icon="✂️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Estilização visual customizada em CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(90deg, #FF4B4B 0%, #FF8533 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.05rem;
        color: #888888;
        margin-bottom: 1.4rem;
    }
    .corte-container {
        background: #181b24;
        border: 1px solid #2d3342;
        border-radius: 10px;
        padding: 16px;
        margin-bottom: 18px;
    }
    .score-badge {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 6px;
        font-weight: 700;
        font-size: 0.9rem;
    }
    .score-high {
        background-color: #0f5132;
        color: #75b798;
        border: 1px solid #198754;
    }
    .score-med {
        background-color: #664d03;
        color: #ffda6a;
        border: 1px solid #ffc107;
    }
</style>
""", unsafe_allow_html=True)


# ==========================================
# FUNÇÕES AUXILIARES DE FORMATAÇÃO E CÁLCULO
# ==========================================
def format_score_badge(score_raw: Any) -> str:
    """Normaliza o score de viralidade para a escala 0 a 100."""
    try:
        val = float(score_raw or 0.0)
        norm = val * 10.0 if (0.0 < val <= 10.0) else val
        return f"{min(100.0, max(0.0, norm)):.0f}"
    except Exception:
        return "80"


def get_image_dimensions(image_path: str) -> tuple[int, int]:
    """Retorna (largura, altura) da imagem informada ou fallback 1920x1080."""
    try:
        p = Path(image_path)
        if p.exists():
            img = cv2.imread(str(p.resolve()))
            if img is not None:
                h, w = img.shape[:2]
                return w, h
    except Exception:
        pass
    return 1920, 1080


# ==========================================
# BARRA LATERAL (SIDEBAR) - CONFIGURAÇÕES
# ==========================================
with st.sidebar:
    st.markdown("## ⚙️ Painel de Controle")
    st.markdown("---")

    st.markdown("### 🎯 Parâmetros Globais")

    ai_provider = st.selectbox(
        "Modelo de IA para Curadoria:",
        options=["gemini", "groq"],
        format_func=lambda x: "Google Gemini (gemini-3.8-flash)" if x == "gemini" else "Groq LLM (gpt-oss-120b)",
        help="Provedor de inteligência artificial que analisará a retenção e identificará os momentos virais."
    )

    default_layout_mode = st.selectbox(
        "Enquadramento Padrão Inicial:",
        options=["vertical_blur", "horizontal_16_9", "manual_crop", "split_screen", "face_tracking_9_16"],
        format_func=lambda x: {
            "vertical_blur": "Vertical Sem Zoom (Blur 9:16) — Shorts/Reels/TikTok",
            "horizontal_16_9": "Horizontal Natural (16:9) — Vídeos Longos (Sem Crop)",
            "manual_crop": "Seleção Manual (Caixa 9:16 Customizada)",
            "split_screen": "Split-Screen (2 Caixas — Podcast/Entrevistas)",
            "face_tracking_9_16": "Vertical 9:16 com Face Tracking Automático",
        }[x],
        help="Modo inicial atribuído aos cortes. Cada corte pode ser personalizado no Estúdio de Enquadramento."
    )

    burn_subs_default = st.checkbox(
        "🔥 Queimar legendas automáticas sincronizadas (.srt)",
        value=True,
        help="Ajusta o referencial temporal das legendas para começar rigorosamente em 00:00:00,000."
    )

    max_resolution = st.select_slider(
        "Resolução Máxima do Vídeo Fonte:",
        options=[720, 1080],
        value=1080,
        format_func=lambda x: f"{x}p"
    )

    max_cuts_limit = st.slider(
        "Cortes Pré-marcados por Padrão:",
        min_value=1,
        max_value=8,
        value=3,
        help="Número de cortes que virão marcados inicialmente para renderização."
    )

    st.markdown("---")
    # Diagnóstico do Sistema com validação dinâmica conforme o provedor
    st.markdown("### 🔍 Diagnóstico do Sistema")
    is_ready, config_status = validate_config(provider=ai_provider)

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        if config_status.get("GROQ_API_KEY"):
            st.success("Groq: OK", icon="✅")
        else:
            st.error("Groq: Off", icon="❌")

        if config_status.get("GEMINI_API_KEY"):
            st.success("Gemini: OK", icon="✅")
        else:
            if ai_provider == "gemini":
                st.error("Gemini: Off", icon="❌")
            else:
                st.caption("Gemini: Opcional")

    with col_s2:
        if config_status.get("FFMPEG"):
            st.success("FFmpeg: OK", icon="🎬")
        else:
            st.error("FFmpeg: Off", icon="⚠️")

    st.markdown("---")
    st.markdown("### 🧹 Gestão de Armazenamento")
    auto_clean = st.checkbox(
        "Limpeza automática pós-processamento",
        value=True,
        help="Remove arquivos temporários (.srt, áudio intermediário) em data/temp/ preservando os clipes finais."
    )

    if st.button("🗑️ Limpar Arquivos Temporários", use_container_width=True):
        clean_res = cleanup_temporary_files(remove_raw_downloads=False)
        st.success(f"Limpeza concluída! {clean_res['deleted_count']} arquivo(s) removido(s) ({clean_res['freed_mb']} MB liberados).")

    st.markdown("---")
    st.caption("EasyKut v2.0 • Estúdio de Enquadramento, FFmpeg & IA Multimodal")


# ==========================================
# ÁREA PRINCIPAL - CABEÇALHO E ABAS
# ==========================================
st.markdown('<div class="main-header">✂️ EasyKut — Estúdio Inteligente de Cortes Virais</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Transforme vídeos longos em clipes verticais de alta retenção com revisão humana, prévia visual em tempo real e renderização FFmpeg.</div>', unsafe_allow_html=True)

tab_novo, tab_historico = st.tabs(["🚀 Processar Novo Vídeo & Estúdio", "📂 Histórico & Cortes Salvos"])


# ------------------------------------------
# ABA 1: NOVO VÍDEO & ESTÚDIO DE ENQUADRAMENTO
# ------------------------------------------
with tab_novo:
    st.markdown("### 1️⃣ Passo 1: Ingestão e Curadoria de IA")
    input_url = st.text_input(
        "Cole a URL do Vídeo do YouTube:",
        placeholder="https://www.youtube.com/watch?v=...",
        help="Insira o link de qualquer vídeo ou transmissão pública suportada pelo yt-dlp."
    )

    col_btn_analisar, col_btn_limpar = st.columns([2, 1])
    with col_btn_analisar:
        btn_analisar = st.button("🔍 Analisar Vídeo & Extrair Prévias", type="primary", use_container_width=True)
    with col_btn_limpar:
        if st.session_state.get("analise_atual"):
            if st.button("🔄 Limpar Análise Atual", use_container_width=True):
                st.session_state.pop("analise_atual", None)
                st.rerun()

    # Execução do Passo 1: Download de Áudio + Whisper + LLM + Extração de Frames de Prévia
    if btn_analisar:
        if not input_url or not is_valid_video_url(input_url):
            st.error("Por favor, insira uma URL válida do YouTube.")
        elif not is_ready:
            msg_erro = "Configurações incompletas no ambiente: "
            if not config_status.get("FFMPEG"):
                msg_erro += "FFmpeg ausente. "
            if not config_status.get("GROQ_API_KEY"):
                msg_erro += "GROQ_API_KEY não encontrada. "
            if ai_provider == "gemini" and not config_status.get("GEMINI_API_KEY"):
                msg_erro += "GEMINI_API_KEY necessária para o provedor Gemini. "
            st.error(msg_erro)
        else:
            with st.status("🚀 Iniciando esteira inteligente de análise e curadoria...", expanded=True) as status_box:
                t_inicio_total = time.time()
                try:
                    # 1. Metadados do vídeo
                    t0 = time.time()
                    status_box.write("📥 [1/5] Extraindo metadados do vídeo via yt-dlp...")
                    info = extract_video_info(input_url)
                    video_title = info.get("title", "Vídeo Sem Título")
                    video_duration = info.get("duration", 0.0)
                    video_id = add_video(url=input_url, title=video_title, duration=video_duration, status="processing")
                    status_box.write(f"✅ [1/5] Metadados extraídos em {time.time() - t0:.1f}s: **{video_title}** ({int(video_duration // 60)}m {int(video_duration % 60)}s)")

                    # 2. Download do áudio leve (mono 16kHz)
                    t0 = time.time()
                    status_box.write("🎵 [2/5] Baixando faixa de áudio leve (MP3 mono 16kHz)...")
                    audio_file = download_audio(input_url)
                    update_video_status(video_id, status="transcribing", audio_path=audio_file)
                    status_box.write(f"✅ [2/5] Áudio baixado em {time.time() - t0:.1f}s.")

                    # 3. Transcrição com Groq Whisper
                    t0 = time.time()
                    status_box.write("🎙️ [3/5] Transcrevendo áudio com Groq Whisper-large-v3...")
                    transcript_data = transcribe_audio_groq(audio_file)
                    total_segs = len(transcript_data.get("segments", []))
                    status_box.write(f"✅ [3/5] Transcrição concluída em {time.time() - t0:.1f}s ({total_segs} segmentos temporais).")

                    # 4. Análise de Momentos Virais via IA
                    t0 = time.time()
                    status_box.write(f"🧠 [4/5] Identificando ganchos de retenção com {ai_provider.upper()}...")
                    cortes_detectados = detect_viral_moments(transcript_data, provider=ai_provider)

                    if not cortes_detectados:
                        status_box.update(label="⚠️ Nenhum corte viral detectado pela IA.", state="error")
                        st.warning("A IA não detectou segmentos com critérios suficientes de retenção.")
                        update_video_status(video_id, status="completed_no_cuts")
                    else:
                        # Ordena explicitamente por maior score de viralidade (decrescente)
                        cortes_detectados.sort(
                            key=lambda x: float(format_score_badge(x.get("virality_score", 0.0))),
                            reverse=True
                        )

                        # 5. Extração de frames reais de prévia para cada corte sugerido
                        t0 = time.time()
                        status_box.write(f"📸 [5/5] Extraindo frames reais de prévia para {len(cortes_detectados)} cortes...")

                        cortes_formatados = []
                        for idx, c in enumerate(cortes_detectados):
                            c_start = float(c.get("start_time", 0.0))
                            c_end = float(c.get("end_time", 0.0))
                            c_dur = max(1.0, c_end - c_start)
                            c_tipo = str(c.get("tipo", "short")).lower().strip()

                            # Modo padrão: horizontal se longo (7+ min ou tipo "longo"), senão o padrão selecionado
                            if c_tipo == "longo" or c_dur >= 180.0:
                                modo_inicial = "horizontal_16_9"
                            else:
                                modo_inicial = default_layout_mode

                            # Captura do frame de prévia no tempo inicial + 1.0s
                            frame_path = ""
                            ts_preview = max(0.0, c_start + min(1.5, c_dur / 2.0))
                            try:
                                frame_path = extract_preview_frame(source=input_url, timestamp_sec=ts_preview)
                            except Exception as exc_frame:
                                logger.warning("Não foi possível extrair preview frame do corte %d: %s", idx, exc_frame)

                            cortes_formatados.append({
                                "idx": idx,
                                "selected": (idx < max_cuts_limit),
                                "title": str(c.get("title", f"Corte #{idx+1}")),
                                "start_time": c_start,
                                "end_time": c_end,
                                "virality_score": float(format_score_badge(c.get("virality_score", 80.0))),
                                "tipo": c_tipo,
                                "justification": str(c.get("justification", "")),
                                "layout_mode": modo_inicial,
                                "burn_subtitles": burn_subs_default,
                                "preview_frame_path": frame_path,
                                "zoom_factor": 1.0,
                                "manual_crop": {"x_pct": 50, "y_pct": 50, "zoom_pct": 100},
                                "split_screen": {
                                    "top_x_pct": 25, "top_y_pct": 50, "top_zoom_pct": 100,
                                    "bot_x_pct": 75, "bot_y_pct": 50, "bot_zoom_pct": 100
                                },
                                "layout_params": {},
                            })

                        status_box.write(f"✅ [5/5] Frames de prévia capturados em {time.time() - t0:.1f}s.")

                        # Salva análise no SQLite
                        save_analysis(video_id=video_id, transcript=transcript_data, analysis_data=cortes_detectados)

                        # Armazena estado persistente na sessão do Streamlit
                        st.session_state["analise_atual"] = {
                            "video_id": video_id,
                            "url": input_url,
                            "title": video_title,
                            "duration": video_duration,
                            "thumbnail": info.get("thumbnail"),
                            "uploader": info.get("uploader"),
                            "audio_path": audio_file,
                            "video_file": None,
                            "transcript_data": transcript_data,
                            "cortes": cortes_formatados,
                        }

                        tempo_total = time.time() - t_inicio_total
                        status_box.update(
                            label=f"🎉 Análise concluída em {tempo_total:.1f}s! {len(cortes_formatados)} cortes prontos para revisão no Estúdio abaixo.",
                            state="complete"
                        )
                except Exception as exc:
                    status_box.update(label=f"❌ Erro durante a esteira de análise: {exc}", state="error")
                    st.error(f"Erro no processamento: {exc}")
                    if "video_id" in locals():
                        update_video_status(video_id, status="failed")

    # ----------------------------------------------------
    # PASSO 1.5: ESTÚDIO DE ENQUADRAMENTO (HUMAN-IN-THE-LOOP)
    # ----------------------------------------------------
    if st.session_state.get("analise_atual"):
        analise = st.session_state["analise_atual"]
        video_dur = float(analise.get("duration", 0.0))

        st.markdown("---")
        # Banner com metadados do vídeo analisado
        col_meta_img, col_meta_txt = st.columns([1, 4])
        with col_meta_img:
            if analise.get("thumbnail"):
                st.image(analise["thumbnail"], use_container_width=True)
        with col_meta_txt:
            st.markdown(f"#### 📹 {analise.get('title')}")
            st.markdown(f"⏱️ **Duração Total:** {int(video_dur // 60)}m {int(video_dur % 60)}s | 👤 **Canal:** {analise.get('uploader') or 'Desconhecido'}")
            st.caption(f"🔗 [Abrir vídeo original no YouTube]({analise.get('url')})")

        st.markdown("### 🎨 Passo 1.5: Estúdio de Enquadramento & Revisão")
        st.markdown("Personalize o enquadramento ao vivo, ajuste os tempos com precisão e escolha o formato ideal antes da renderização final:")

        # Iteração sobre cada corte sugerido
        for i, c in enumerate(analise["cortes"]):
            dur_corte = max(0.0, c["end_time"] - c["start_time"])
            score_num = float(c.get("virality_score", 80.0))
            score_badge_cls = "score-high" if score_num >= 75 else "score-med"

            exp_label = (
                f"{'✅' if c['selected'] else '⬜'} Corte #{i+1}: {c['title']} "
                f"({dur_corte:.1f}s) — ⭐ Score: {score_num:.0f}/100"
            )

            with st.expander(exp_label, expanded=c["selected"]):
                col_c1, col_c2, col_c3 = st.columns([3, 1, 1])
                with col_c1:
                    c["selected"] = st.checkbox(
                        f"**Renderizar Corte #{i+1} na exportação final**",
                        value=c["selected"],
                        key=f"cb_sel_{c['idx']}"
                    )
                with col_c2:
                    st.markdown(
                        f'<div class="score-badge {score_badge_cls}">⭐ Score: {score_num:.0f}/100</div>',
                        unsafe_allow_html=True
                    )
                with col_c3:
                    tipo_tag = c.get("tipo", "short").upper()
                    st.markdown(f"🏷️ **Tipo:** `{tipo_tag}`")

                c["title"] = st.text_input(
                    f"Título do Corte #{i+1}:",
                    value=c["title"],
                    key=f"title_{c['idx']}"
                )

                # Ajuste de tempos e botão para re-extrair frame no novo tempo
                col_t1, col_t2, col_t3 = st.columns([2, 2, 1.2])
                with col_t1:
                    c["start_time"] = st.number_input(
                        f"Início (s) - Corte #{i+1}:",
                        min_value=0.0,
                        max_value=max(1.0, video_dur),
                        value=min(video_dur, float(c["start_time"])),
                        step=0.5,
                        key=f"start_{c['idx']}"
                    )
                with col_t2:
                    c["end_time"] = st.number_input(
                        f"Fim (s) - Corte #{i+1}:",
                        min_value=0.0,
                        max_value=max(1.0, video_dur),
                        value=min(video_dur, float(c["end_time"])),
                        step=0.5,
                        key=f"end_{c['idx']}"
                    )
                with col_t3:
                    st.write("")
                    st.write("")
                    if st.button("🔄 Atualizar Frame", key=f"btn_upd_frame_{c['idx']}", help="Captura um novo frame exatamente no segundo de início ajustado"):
                        try:
                            novo_frame = extract_preview_frame(
                                source=analise.get("video_file") or analise.get("url"),
                                timestamp_sec=max(0.0, float(c["start_time"]) + 0.5)
                            )
                            c["preview_frame_path"] = novo_frame
                            st.toast(f"Frame do corte #{i+1} atualizado!")
                            st.rerun()
                        except Exception as err_frame:
                            st.error(f"Erro ao extrair frame: {err_frame}")

                if c.get("justification"):
                    st.caption(f"💡 *Justificativa da IA:* {c['justification']}")

                st.markdown("---")

                # Seletor de Modo de Saída e Opção de Legendas
                col_mode_sel, col_mode_subs = st.columns([3, 2])
                with col_mode_sel:
                    mode_options = [
                        "vertical_blur",
                        "horizontal_16_9",
                        "manual_crop",
                        "split_screen",
                        "face_tracking_9_16",
                    ]
                    mode_names = {
                        "vertical_blur": "Vertical Sem Zoom (Blur 9:16) — Padrão Shorts/TikTok",
                        "horizontal_16_9": "Horizontal Natural (16:9) — Widescreen Sem Crop",
                        "manual_crop": "Seleção Manual (Caixa 9:16 Customizada)",
                        "split_screen": "Split-Screen (2 Caixas — Podcast/Entrevistas)",
                        "face_tracking_9_16": "Vertical 9:16 com Face Tracking Automático",
                    }
                    current_mode_idx = mode_options.index(c["layout_mode"]) if c["layout_mode"] in mode_options else 0
                    chosen_mode = st.selectbox(
                        f"Formato de Saída (Corte #{i+1}):",
                        options=mode_options,
                        index=current_mode_idx,
                        format_func=lambda x: mode_names[x],
                        key=f"mode_sel_{c['idx']}"
                    )
                    c["layout_mode"] = chosen_mode

                with col_mode_subs:
                    st.write("")
                    c["burn_subtitles"] = st.checkbox(
                        "🔥 Queimar legendas sincronizadas (.srt)",
                        value=c.get("burn_subtitles", True),
                        key=f"subs_cb_{c['idx']}"
                    )

                # Dimensões da imagem de prévia
                frame_path = c.get("preview_frame_path", "")
                fw, fh = get_image_dimensions(frame_path)

                # Divisão Lado a Lado: Controles de Enquadramento vs. Prévia Visual ao Vivo
                col_ctrls, col_prev = st.columns([1, 1])

                layout_params: Dict[str, Any] = {}

                with col_ctrls:
                    st.markdown("##### 🎛️ Parâmetros de Enquadramento")

                    if chosen_mode == "vertical_blur":
                        st.caption("O vídeo original 16:9 é mantido centralizado sem distorção, sobre um fundo ampliado desfocado e suavemente escurecido.")
                        zoom_val = st.slider(
                            "Aproximação Suave do Vídeo (Zoom):",
                            min_value=1.0,
                            max_value=1.3,
                            value=float(c.get("zoom_factor", 1.0)),
                            step=0.05,
                            key=f"zoom_slider_{c['idx']}",
                            help="1.0x mantém tamanho 100% original; valores até 1.3x aproximam suavemente sem perder nitidez."
                        )
                        c["zoom_factor"] = zoom_val
                        layout_params = {"zoom_factor": zoom_val}

                    elif chosen_mode == "manual_crop":
                        st.caption("Ajuste a posição horizontal e vertical da caixa de recorte vertical 9:16 sobre o frame real.")
                        mc = c.get("manual_crop", {"x_pct": 50, "y_pct": 50, "zoom_pct": 100})

                        x_pct = st.slider("Posição Horizontal (X %):", 0, 100, int(mc.get("x_pct", 50)), 1, key=f"mc_x_{c['idx']}")
                        y_pct = st.slider("Posição Vertical (Y %):", 0, 100, int(mc.get("y_pct", 50)), 1, key=f"mc_y_{c['idx']}")
                        z_pct = st.slider("Tamanho da Caixa (%):", 50, 100, int(mc.get("zoom_pct", 100)), 1, key=f"mc_z_{c['idx']}")
                        c["manual_crop"] = {"x_pct": x_pct, "y_pct": y_pct, "zoom_pct": z_pct}

                        # Cálculo das coordenadas sobre a resolução real
                        crop_h = int(fh * (z_pct / 100.0))
                        crop_w = int(crop_h * (9.0 / 16.0))
                        crop_w -= (crop_w % 2)
                        crop_h -= (crop_h % 2)
                        max_x = max(0, fw - crop_w)
                        max_y = max(0, fh - crop_h)
                        crop_x = int(max_x * (x_pct / 100.0))
                        crop_y = int(max_y * (y_pct / 100.0))
                        crop_x -= (crop_x % 2)
                        crop_y -= (crop_y % 2)

                        layout_params = {"crop_x": crop_x, "crop_y": crop_y, "crop_w": crop_w, "crop_h": crop_h}
                        st.caption(f"📐 Recorte calculado: `{crop_w}x{crop_h}` em `(X={crop_x}, Y={crop_y})`")

                    elif chosen_mode == "split_screen":
                        st.caption("Dois recortes verticais empilhados (1080x960 cada) para gerar o formato final 1080x1920.")
                        sc = c.get("split_screen", {})

                        tab_top, tab_bot = st.tabs(["👤 Caixa Topo (Falante 1)", "👥 Caixa Base (Falante 2)"])
                        with tab_top:
                            tx_pct = st.slider("Topo X (%):", 0, 100, int(sc.get("top_x_pct", 25)), 1, key=f"sc_tx_{c['idx']}")
                            ty_pct = st.slider("Topo Y (%):", 0, 100, int(sc.get("top_y_pct", 50)), 1, key=f"sc_ty_{c['idx']}")
                            tz_pct = st.slider("Tamanho Topo (%):", 50, 100, int(sc.get("top_zoom_pct", 100)), 1, key=f"sc_tz_{c['idx']}")
                        with tab_bot:
                            bx_pct = st.slider("Base X (%):", 0, 100, int(sc.get("bot_x_pct", 75)), 1, key=f"sc_bx_{c['idx']}")
                            by_pct = st.slider("Base Y (%):", 0, 100, int(sc.get("bot_y_pct", 50)), 1, key=f"sc_by_{c['idx']}")
                            bz_pct = st.slider("Tamanho Base (%):", 50, 100, int(sc.get("bot_zoom_pct", 100)), 1, key=f"sc_bz_{c['idx']}")

                        c["split_screen"] = {
                            "top_x_pct": tx_pct, "top_y_pct": ty_pct, "top_zoom_pct": tz_pct,
                            "bot_x_pct": bx_pct, "bot_y_pct": by_pct, "bot_zoom_pct": bz_pct,
                        }

                        target_ar = 1080.0 / 960.0
                        max_box_h = min(fh, int(fw / target_ar))

                        # Topo
                        th = int(max_box_h * (tz_pct / 100.0))
                        tw = int(th * target_ar)
                        tw -= (tw % 2)
                        th -= (th % 2)
                        tx = int(max(0, fw - tw) * (tx_pct / 100.0))
                        ty = int(max(0, fh - th) * (ty_pct / 100.0))
                        tx -= (tx % 2)
                        ty -= (ty % 2)

                        # Base
                        bh = int(max_box_h * (bz_pct / 100.0))
                        bw = int(bh * target_ar)
                        bw -= (bw % 2)
                        bh -= (bh % 2)
                        bx = int(max(0, fw - bw) * (bx_pct / 100.0))
                        by = int(max(0, fh - bh) * (by_pct / 100.0))
                        bx -= (bx % 2)
                        by -= (by % 2)

                        layout_params = {
                            "box_top": {"x": tx, "y": ty, "w": tw, "h": th},
                            "box_bottom": {"x": bx, "y": by, "w": bw, "h": bh}
                        }
                        st.caption(f"📐 Topo (Ciano): `{tw}x{th}` | Base (Laranja): `{bw}x{bh}`")

                    elif chosen_mode == "horizontal_16_9":
                        st.caption("Exportação limpa e ultrarrápida mantendo 100% da resolução 16:9 widescreen nativa. Ideal para clipes longos (7+ minutos).")
                        layout_params = {}

                    elif chosen_mode == "face_tracking_9_16":
                        st.caption("O rastreador facial OpenCV (YuNet/Haar) detecta o rosto principal e ajusta suavemente o recorte vertical para manter o falante centralizado.")
                        layout_params = {}

                    c["layout_params"] = layout_params

                with col_prev:
                    st.markdown("##### 👁️ Prévia Visual do Enquadramento")
                    if frame_path and os.path.exists(frame_path):
                        try:
                            preview_jpg = draw_framing_preview(
                                frame_path=frame_path,
                                mode=chosen_mode,
                                params=layout_params
                            )
                            if os.path.exists(preview_jpg):
                                st.image(preview_jpg, use_container_width=True)
                            else:
                                st.image(frame_path, use_container_width=True)
                        except Exception as exc_preview:
                            st.warning(f"Prévia indisponível: {exc_preview}")
                            st.image(frame_path, use_container_width=True)
                    else:
                        st.info("💡 Frame de prévia não capturado. Clique em 'Atualizar Frame' acima para gerar a imagem de referência.")

        cortes_selecionados = [c for c in analise["cortes"] if c["selected"]]

        st.markdown("---")
        col_act1, col_act2 = st.columns([2, 1])
        with col_act1:
            btn_renderizar = st.button(
                f"🎬 Renderizar {len(cortes_selecionados)} Corte(s) Selecionado(s)",
                type="primary",
                disabled=(len(cortes_selecionados) == 0),
                use_container_width=True
            )
        with col_act2:
            st.info(f"📊 **{len(cortes_selecionados)}** de **{len(analise['cortes'])}** corte(s) selecionado(s)")

        # ----------------------------------------------------
        # PASSO 2: DOWNLOAD DO VÍDEO HD & RENDERIZAÇÃO FFMPEG
        # ----------------------------------------------------
        if btn_renderizar:
            if not cortes_selecionados:
                st.warning("Selecione pelo menos um corte acima para renderizar.")
            else:
                with st.status(f"⚡ Renderizando {len(cortes_selecionados)} corte(s) com FFmpeg...", expanded=True) as status_render:
                    t_rend_inicio = time.time()
                    try:
                        # 1. Download do vídeo em alta definição (apenas agora, sob demanda)
                        video_file = analise.get("video_file")
                        if not video_file or not os.path.exists(video_file):
                            t0 = time.time()
                            status_render.write(f"🎥 [1/2] Baixando vídeo fonte em {max_resolution}p via yt-dlp...")
                            video_file = download_video(analise["url"], max_resolution=max_resolution)
                            analise["video_file"] = video_file
                            update_video_status(analise["video_id"], status="cutting", file_path=video_file)
                            status_render.write(f"✅ [1/2] Vídeo baixado em {time.time() - t0:.1f}s: `{Path(video_file).name}`")
                        else:
                            status_render.write(f"✅ [1/2] Vídeo fonte já disponível em disco: `{Path(video_file).name}`")

                        # 2. Renderização dos cortes selecionados
                        cortes_renderizados = []
                        total_rend = len(cortes_selecionados)

                        for idx, c in enumerate(cortes_selecionados):
                            t_cut_inicio = time.time()
                            status_render.write(f"⏳ Processando corte {idx+1}/{total_rend}: '{c['title']}' ({c['start_time']}s -> {c['end_time']}s) no modo '{c['layout_mode']}'...")

                            # Registra o corte com os tempos e título revisados
                            corte_db_id = add_corte(
                                video_id=analise["video_id"],
                                start_time=c["start_time"],
                                end_time=c["end_time"],
                                title=c["title"],
                                virality_score=c["virality_score"],
                                justification=c["justification"],
                                status="rendering"
                            )

                            # Renderiza o vídeo com o modo e parâmetros de enquadramento exatos
                            output_clip = cut_video(
                                input_path=video_file,
                                start_time=c["start_time"],
                                end_time=c["end_time"],
                                layout_mode=c["layout_mode"],
                                layout_params=c["layout_params"],
                                burn_subtitles=c.get("burn_subtitles", True),
                                segments=analise["transcript_data"].get("segments", [])
                            )

                            update_corte_status(corte_db_id, status="completed", output_path=output_clip)
                            c_item = dict(c)
                            c_item["output_path"] = output_clip
                            c_item["corte_db_id"] = corte_db_id
                            cortes_renderizados.append(c_item)

                            status_render.write(f"✅ Corte {idx+1}/{total_rend} finalizado com sucesso em {time.time() - t_cut_inicio:.1f}s!")

                        update_video_status(analise["video_id"], status="completed")

                        if auto_clean:
                            cleanup_temporary_files(remove_raw_downloads=False)

                        # Armazena os clipes renderizados no session_state para galeria persistente
                        st.session_state["ultimos_cortes"] = cortes_renderizados
                        st.session_state["ultimo_video_title"] = analise["title"]

                        tempo_rend_total = time.time() - t_rend_inicio
                        status_render.update(
                            label=f"✨ {len(cortes_renderizados)} corte(s) renderizado(s) com sucesso em {tempo_rend_total:.1f}s!",
                            state="complete"
                        )
                    except Exception as exc_rend:
                        status_render.update(label=f"❌ Erro durante a renderização: {exc_rend}", state="error")
                        st.error(f"Erro na renderização: {exc_rend}")

    # ----------------------------------------------------
    # GALERIA DE CORTES PRONTOS (PERSISTENTE FORA DE BOTÕES)
    # ----------------------------------------------------
    if st.session_state.get("ultimos_cortes"):
        st.markdown("---")
        st.markdown(f"### 🎬 Galeria de Cortes Prontos — {st.session_state.get('ultimo_video_title', 'Vídeo Recente')}")

        cortes_exibir = st.session_state["ultimos_cortes"]
        cols_gal = st.columns(min(3, max(1, len(cortes_exibir))))
        for i, corte in enumerate(cortes_exibir):
            with cols_gal[i % len(cols_gal)]:
                st.markdown(f"#### ✂️ {corte['title']}")
                st.markdown(f"⭐ **Viral Score:** `{corte.get('virality_score', 80.0):.0f}/100`")
                st.caption(f"⏱️ **Tempo:** {corte['start_time']}s até {corte['end_time']}s ({corte['end_time'] - corte['start_time']:.1f}s)")
                st.caption(f"📐 **Layout:** `{corte.get('layout_mode', 'vertical_blur')}`")

                if corte.get("justification"):
                    st.info(f"💡 *{corte['justification']}*")

                out_p = corte.get("output_path", "")
                if out_p and os.path.exists(out_p):
                    st.video(out_p)
                    with open(out_p, "rb") as f_dl:
                        st.download_button(
                            label=f"⬇️ Baixar Corte #{i+1} (.mp4)",
                            data=f_dl,
                            file_name=Path(out_p).name,
                            mime="video/mp4",
                            key=f"dl_corte_recent_{corte.get('corte_db_id', i)}_{i}",
                            use_container_width=True
                        )
                else:
                    st.warning("Arquivo de vídeo não encontrado no disco.")


# ------------------------------------------
# ABA 2: HISTÓRICO & CORTES SALVOS
# ------------------------------------------
with tab_historico:
    col_hist_h, col_hist_btn = st.columns([3, 1])
    with col_hist_h:
        st.markdown("### 🗄️ Histórico de Vídeos e Cortes Processados")
    with col_hist_btn:
        if st.button("📂 Abrir Pasta de Cortes", use_container_width=True, help="Abre a pasta data/cortes/ no Explorador de Arquivos do Windows"):
            try:
                if hasattr(os, "startfile"):
                    os.startfile(str(CUTS_DIR.resolve()))
                    st.toast("Pasta data/cortes/ aberta no Explorador!")
                else:
                    subprocess.Popen(["explorer", str(CUTS_DIR.resolve())])
                    st.toast("Pasta data/cortes/ aberta!")
            except Exception as exc_open:
                st.error(f"Erro ao abrir pasta de cortes: {exc_open}")

    # Filtros de Busca e Nota Mínima
    col_f1, col_f2 = st.columns([3, 1])
    with col_f1:
        filtro_busca = st.text_input("🔍 Buscar por título:", placeholder="Filtrar por título do vídeo ou URL...", key="hist_search_input")
    with col_f2:
        filtro_min_score = st.slider("⭐ Nota Mínima (0–100):", min_value=0, max_value=100, value=0, step=5, key="hist_score_slider")

    todos_videos = list_videos(search=filtro_busca)

    if not todos_videos:
        if filtro_busca:
            st.info(f"Nenhum vídeo encontrado com o termo '{filtro_busca}'.")
        else:
            st.info("Nenhum vídeo processado ainda no histórico.")
    else:
        for vid in todos_videos:
            todos_cortes_vid = list_cortes(video_id=vid["id"])

            # Filtra por score normalizado (compatível com scores 0-10 legados e 0-100 atuais)
            if filtro_min_score > 0:
                cortes_do_video = [
                    c for c in todos_cortes_vid
                    if float(format_score_badge(c.get("virality_score", 0.0))) >= filtro_min_score
                ]
            else:
                cortes_do_video = todos_cortes_vid

            with st.expander(f"📹 {vid['title'] or vid['url']} (ID: {vid['id']} | Status: {vid['status']} | {len(cortes_do_video)} corte(s))"):
                col_h1, col_h2, col_h3 = st.columns([3, 2, 2])
                with col_h1:
                    st.markdown(f"**URL:** [{vid['url']}]({vid['url']})")
                    st.markdown(f"**Duração:** {vid['duration']}s | **Criado em:** {vid['created_at']}")
                with col_h2:
                    del_files_opt = st.checkbox(
                        "Apagar também os arquivos .mp4 do disco",
                        value=True,
                        key=f"cb_del_files_{vid['id']}",
                        help="Remove com segurança os arquivos físicos .mp4 e .mp3 em data/cortes/ e data/downloads/"
                    )
                with col_h3:
                    if st.button("🗑️ Excluir Registro", key=f"del_vid_btn_{vid['id']}", type="secondary", use_container_width=True):
                        delete_video(vid['id'], delete_files=del_files_opt)
                        st.toast(f"Vídeo {vid['id']} e registros excluídos com sucesso!")
                        st.rerun()

                # Listagem dos cortes associados a este vídeo
                if not cortes_do_video:
                    if filtro_min_score > 0:
                        st.caption(f"Nenhum corte deste vídeo atingiu a nota mínima de {filtro_min_score}/100.")
                    else:
                        st.caption("Nenhum corte registrado para este vídeo.")
                else:
                    st.markdown("#### Cortes Gerados:")
                    cols_hist = st.columns(min(3, max(1, len(cortes_do_video))))
                    for idx, c in enumerate(cortes_do_video):
                        with cols_hist[idx % len(cols_hist)]:
                            st.markdown(f"**{c['title']}**")
                            score_fmt = format_score_badge(c.get("virality_score", 80.0))
                            st.caption(f"⭐ Score: `{score_fmt}/100` | ⏱️ `{c['start_time']}s -> {c['end_time']}s`")
                            if c.get("justification"):
                                st.caption(f"_{c['justification']}_")

                            out_clip = c.get("output_path", "")
                            if out_clip and os.path.exists(out_clip):
                                st.video(out_clip)
                                # Transmite o descritor de arquivo aberto sob demanda sem carregar bytes inteiros na RAM
                                with open(out_clip, "rb") as f_hist_dl:
                                    st.download_button(
                                        label="⬇️ Baixar Clipe (.mp4)",
                                        data=f_hist_dl,
                                        file_name=Path(out_clip).name,
                                        mime="video/mp4",
                                        key=f"hist_dl_{c['id']}",
                                        use_container_width=True
                                    )
                            else:
                                st.warning("Arquivo de vídeo não encontrado no disco.")
