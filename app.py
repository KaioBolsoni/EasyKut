"""
EasyKut - Aplicação Web Streamlit
Plataforma completa para download de vídeos, análise inteligente de cortes virais
com Whisper/Gemini/Groq e renderização vertical automatizada (9:16) com FFmpeg.
"""

import os
import sys
from pathlib import Path

# Adiciona o diretório atual ao path de execução
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import streamlit as st
from core.config import validate_config, FFMPEG_AVAILABLE, GROQ_API_KEY, GEMINI_API_KEY
from core.downloader import is_valid_video_url, extract_video_info, download_audio, download_video
from core.ai_apis import transcribe_audio_groq, detect_viral_moments
from core.video_engine import cut_video, get_video_metadata, cleanup_temporary_files
from database.db_manager import (
    init_db,
    add_video,
    update_video_status,
    list_videos,
    add_corte,
    list_cortes,
    update_corte_status,
    delete_video,
)

# Inicializa o banco de dados SQLite
init_db()

# Configuração da página do Streamlit
st.set_page_config(
    page_title="EasyKut - Criador de Cortes Virais",
    page_icon="✂️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilização customizada em CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.3rem;
        font-weight: 800;
        background: linear-gradient(90deg, #FF4B4B 0%, #FF8533 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.05rem;
        color: #888888;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background-color: #1a1c24;
        border: 1px solid #2d313e;
        border-radius: 8px;
        padding: 12px;
        margin-bottom: 10px;
    }
    .corte-card {
        background: #1e212b;
        border: 1px solid #343a46;
        border-radius: 10px;
        padding: 16px;
        margin-bottom: 16px;
    }
</style>
""", unsafe_allow_html=True)


# ==========================================
# BARRA LATERAL (SIDEBAR) - CONFIGURAÇÕES
# ==========================================
with st.sidebar:
    st.markdown("## ⚙️ Painel de Controle")
    st.markdown("---")

    # Diagnóstico do Sistema
    st.markdown("### 🔍 Diagnóstico do Sistema")
    is_ready, config_status = validate_config()

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        if config_status.get("GROQ_API_KEY"):
            st.success("Groq: Conectado", icon="✅")
        else:
            st.error("Groq: Ausente", icon="❌")

        if config_status.get("GEMINI_API_KEY"):
            st.success("Gemini: Conectado", icon="✅")
        else:
            st.error("Gemini: Ausente", icon="❌")

    with col_s2:
        if config_status.get("FFMPEG"):
            st.success("FFmpeg: Pronto", icon="🎬")
        else:
            st.error("FFmpeg: Ausente", icon="⚠️")

    st.markdown("---")
    st.markdown("### 🎯 Parâmetros de Edição")

    ai_provider = st.selectbox(
        "Modelo de IA para Curadoria:",
        options=["gemini", "groq"],
        format_func=lambda x: "Google Gemini (gemini-3.1-flash-lite)" if x == "gemini" else "Groq LLM (gpt-oss-120b)",
        help="Provedor de inteligência artificial que analisará os ganchos e momentos de maior retenção."
    )

    crop_mode = st.selectbox(
        "Formato e Enquadramento:",
        options=["face_tracking_9_16", "blur_background", "crop_center", "horizontal_16_9"],
        format_func=lambda x: {
            "face_tracking_9_16": "Vertical 9:16 com Face Tracking Suave (Shorts/TikTok)",
            "blur_background": "Vertical 9:16 com Fundo Desfocado (Blur)",
            "crop_center": "Vertical 9:16 com Recorte Central",
            "horizontal_16_9": "Horizontal 16:9 (Vídeos Longos 7+ min)"
        }[x],
        help="Padrão de aspecto e enquadramento do vídeo final renderizado."
    )

    burn_subs = st.checkbox(
        "🔥 Queimar legendas automáticas sincronizadas (.srt)",
        value=True,
        help="Gera e queima legendas com tipografia profissional ajustadas para começarem em 00:00:00,000."
    )

    max_resolution = st.select_slider(
        "Qualidade Máxima de Download:",
        options=[720, 1080],
        value=1080,
        format_func=lambda x: f"{x}p"
    )

    max_cuts_limit = st.slider(
        "Limite Máximo de Cortes a Renderizar:",
        min_value=1,
        max_value=6,
        value=3,
        help="Quantidade de cortes que o motor FFmpeg irá renderizar após a seleção da IA."
    )

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
    st.caption("EasyKut v1.0 • Desenvolvido com Streamlit, yt-dlp, FFmpeg e IA Generativa")


# ==========================================
# ÁREA PRINCIPAL - ABAS
# ==========================================
st.markdown('<div class="main-header">✂️ EasyKut — Criador Automático de Cortes Virais</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Transforme vídeos longos em clipes verticais de alta retenção prontos para Reels, TikTok e YouTube Shorts.</div>', unsafe_allow_html=True)

tab_novo, tab_historico = st.tabs(["🚀 Processar Novo Vídeo", "📂 Histórico & Cortes Salvos"])

# ------------------------------------------
# ABA 1: PROCESSAR NOVO VÍDEO
# ------------------------------------------
with tab_novo:
    with st.container():
        input_url = st.text_input(
            "Cole a URL do Vídeo do YouTube:",
            placeholder="https://www.youtube.com/watch?v=...",
            help="Insira o link de qualquer vídeo ou transmissão pública."
        )

        col_btn, col_info = st.columns([1, 3])
        with col_btn:
            btn_processar = st.button("✨ Criar Cortes Automáticos", type="primary", use_container_width=True)

    if btn_processar:
        if not input_url or not is_valid_video_url(input_url):
            st.error("Por favor, insira uma URL de vídeo válida (YouTube).")
        elif not is_ready:
            st.error("As configurações de ambiente não estão completas. Verifique suas chaves no .env e a presença do FFmpeg.")
        else:
            progress_bar = st.progress(0, text="Iniciando esteira de produção...")
            status_placeholder = st.empty()

            try:
                # 1. Obtenção de Metadados
                status_placeholder.info("📥 [1/6] Extraindo metadados do vídeo...")
                progress_bar.progress(10, text="[1/6] Extraindo metadados...")
                info = extract_video_info(input_url)
                video_title = info.get("title", "Vídeo Sem Título")
                video_duration = info.get("duration", 0.0)

                # Salva o vídeo no banco de dados SQLite
                video_id = add_video(url=input_url, title=video_title, duration=video_duration, status="processing")

                col_meta1, col_meta2 = st.columns([1, 4])
                with col_meta1:
                    if info.get("thumbnail"):
                        st.image(info["thumbnail"], use_container_width=True)
                with col_meta2:
                    st.markdown(f"**Título:** {video_title}")
                    st.markdown(f"**Duração:** {int(video_duration // 60)}m {int(video_duration % 60)}s | **Canal:** {info.get('uploader')}")

                # 2. Download do Áudio Otimizado
                status_placeholder.info("🎵 [2/6] Baixando faixa de áudio otimizada...")
                progress_bar.progress(25, text="[2/6] Baixando áudio...")
                audio_file = download_audio(input_url)
                update_video_status(video_id, status="transcribing", audio_path=audio_file)

                # 3. Transcrição com Groq Whisper
                status_placeholder.info("🎙️ [3/6] Transcrevendo áudio com Groq Whisper-large-v3...")
                progress_bar.progress(45, text="[3/6] Transcrevendo áudio com timestamps...")
                transcript_data = transcribe_audio_groq(audio_file)

                # 4. Detecção de Momentos Virais via IA
                status_placeholder.info(f"🧠 [4/6] Analisando momentos virais com {ai_provider.upper()}...")
                progress_bar.progress(60, text="[4/6] IA selecionando os melhores trechos...")
                cortes_detectados = detect_viral_moments(transcript_data, provider=ai_provider)

                if not cortes_detectados:
                    st.warning("A IA não detectou segmentos com critérios de viralização suficientes para corte.")
                    update_video_status(video_id, status="completed_no_cuts")
                else:
                    st.success(f"🎯 IA identificou {len(cortes_detectados)} potenciais cortes virais!")

                    # 5. Download do Vídeo em Alta Definição
                    status_placeholder.info(f"🎥 [5/6] Baixando vídeo fonte em {max_resolution}p...")
                    progress_bar.progress(75, text="[5/6] Baixando vídeo para renderização...")
                    video_file = download_video(input_url, max_resolution=max_resolution)
                    update_video_status(video_id, status="cutting", file_path=video_file)

                    # 6. Renderização dos Cortes com FFmpeg
                    status_placeholder.info(f"⚡ [6/6] Renderizando {min(len(cortes_detectados), max_cuts_limit)} cortes em formato 9:16 vertical...")
                    cortes_renderizados = []

                    for idx, c in enumerate(cortes_detectados[:max_cuts_limit]):
                        current_step = 75 + int(((idx + 1) / max_cuts_limit) * 23)
                        progress_bar.progress(current_step, text=f"[6/6] Renderizando corte {idx+1}/{max_cuts_limit}: '{c['title']}'...")

                        # Salva registro no banco
                        corte_db_id = add_corte(
                            video_id=video_id,
                            start_time=c["start_time"],
                            end_time=c["end_time"],
                            title=c["title"],
                            virality_score=c["virality_score"],
                            justification=c["justification"],
                            status="rendering"
                        )

                        # Renderiza no FFmpeg com suporte a legendas e enquadramento dinâmico
                        output_clip = cut_video(
                            input_path=video_file,
                            start_time=c["start_time"],
                            end_time=c["end_time"],
                            mode=crop_mode,
                            segments=transcript_data.get("segments", []),
                            burn_subtitles=burn_subs
                        )

                        update_corte_status(corte_db_id, status="completed", output_path=output_clip)
                        c["output_path"] = output_clip
                        c["corte_db_id"] = corte_db_id
                        cortes_renderizados.append(c)

                    update_video_status(video_id, status="completed")
                    if auto_clean:
                        cleanup_temporary_files(remove_raw_downloads=False)

                    progress_bar.progress(100, text="✨ Todos os cortes foram renderizados com sucesso!")
                    status_placeholder.success("🎉 Processo concluído com êxito! Confira os cortes gerados abaixo:")

                    # Exibição dos Cortes Gerados
                    st.markdown("---")
                    st.markdown("### 🎬 Galeria de Cortes Gerados (9:16)")

                    cols = st.columns(min(3, max(1, len(cortes_renderizados))))
                    for i, corte in enumerate(cortes_renderizados):
                        with cols[i % len(cols)]:
                            st.markdown(f"#### ✂️ {corte['title']}")
                            st.markdown(f"⭐ **Viral Score:** `{corte['virality_score']}/10`")
                            st.caption(f"⏱️ **Tempo:** {corte['start_time']}s até {corte['end_time']}s ({corte['end_time'] - corte['start_time']:.1f}s)")
                            st.info(f"💡 *{corte['justification']}*")

                            # Player de vídeo
                            if os.path.exists(corte["output_path"]):
                                st.video(corte["output_path"])
                                with open(corte["output_path"], "rb") as vf:
                                    st.download_button(
                                        label=f"⬇️ Baixar Corte {i+1} (.mp4)",
                                        data=vf.read(),
                                        file_name=Path(corte["output_path"]).name,
                                        mime="video/mp4",
                                        key=f"dl_corte_{corte['corte_db_id']}_{i}",
                                        use_container_width=True
                                    )

            except Exception as e:
                status_placeholder.empty()
                progress_bar.empty()
                st.error(f"Erro durante o processamento: {str(e)}")
                if 'video_id' in locals():
                    update_video_status(video_id, status="failed")

# ------------------------------------------
# ABA 2: HISTÓRICO & CORTES SALVOS
# ------------------------------------------
with tab_historico:
    st.markdown("### 🗄️ Histórico de Vídeos e Cortes Processados")
    todos_videos = list_videos()

    if not todos_videos:
        st.info("Nenhum vídeo processado ainda no histórico.")
    else:
        for vid in todos_videos:
            with st.expander(f"📹 {vid['title'] or vid['url']} (ID: {vid['id']} | Status: {vid['status']})"):
                col_h1, col_h2 = st.columns([3, 1])
                with col_h1:
                    st.markdown(f"**URL:** [{vid['url']}]({vid['url']})")
                    st.markdown(f"**Duração:** {vid['duration']}s | **Criado em:** {vid['created_at']}")
                with col_h2:
                    if st.button("🗑️ Excluir Registro", key=f"del_vid_{vid['id']}", type="secondary"):
                        delete_video(vid['id'])
                        st.rerun()

                # Cortes associados a este vídeo
                cortes_do_video = list_cortes(video_id=vid["id"])
                if not cortes_do_video:
                    st.caption("Nenhum corte registrado para este vídeo.")
                else:
                    st.markdown("#### Cortes Gerados:")
                    cols_hist = st.columns(min(3, max(1, len(cortes_do_video))))
                    for idx, c in enumerate(cortes_do_video):
                        with cols_hist[idx % len(cols_hist)]:
                            st.markdown(f"**{c['title']}**")
                            st.caption(f"Score: `{c['virality_score']}/10` | {c['start_time']}s -> {c['end_time']}s")
                            if c["justification"]:
                                st.caption(f"_{c['justification']}_")

                            if c["output_path"] and os.path.exists(c["output_path"]):
                                st.video(c["output_path"])
                                with open(c["output_path"], "rb") as f_c:
                                    st.download_button(
                                        label="⬇️ Baixar Clipe (.mp4)",
                                        data=f_c.read(),
                                        file_name=Path(c["output_path"]).name,
                                        mime="video/mp4",
                                        key=f"hist_dl_{c['id']}",
                                        use_container_width=True
                                    )
                            else:
                                st.warning("Arquivo de vídeo não encontrado no disco.")
