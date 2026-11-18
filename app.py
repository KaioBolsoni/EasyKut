"""
EasyKut - Aplicação Web Streamlit (v2.0)
Plataforma completa para download de vídeos, análise inteligente de cortes virais
com Whisper/Gemini/Groq, revisão interativa (Human-in-the-Loop) e renderização vertical (9:16) com FFmpeg.
"""

import os
import sys
import time
from pathlib import Path

# Adiciona o diretório atual ao path de execução
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import streamlit as st
from core.config import (
    validate_config,
    FFMPEG_AVAILABLE,
    GROQ_API_KEY,
    GEMINI_API_KEY,
    CUTS_DIR,
)
from core.downloader import (
    is_valid_video_url,
    extract_video_info,
    download_audio,
    download_video,
)
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
    save_analysis,
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
        "Sugestões Pré-selecionadas:",
        min_value=1,
        max_value=6,
        value=3,
        help="Quantidade de cortes pré-marcados por padrão para renderização."
    )

    st.markdown("---")
    # Diagnóstico do Sistema com validação flexível conforme o provedor
    st.markdown("### 🔍 Diagnóstico do Sistema")
    is_ready, config_status = validate_config(provider=ai_provider)

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        if config_status.get("GROQ_API_KEY"):
            st.success("Groq: Conectado", icon="✅")
        else:
            st.error("Groq: Ausente", icon="❌")

        if config_status.get("GEMINI_API_KEY"):
            st.success("Gemini: Conectado", icon="✅")
        else:
            if ai_provider == "gemini":
                st.error("Gemini: Ausente", icon="❌")
            else:
                st.caption("Gemini: Opcional")

    with col_s2:
        if config_status.get("FFMPEG"):
            st.success("FFmpeg: Pronto", icon="🎬")
        else:
            st.error("FFmpeg: Ausente", icon="⚠️")

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
    st.caption("EasyKut v2.0 • Desenvolvido com Streamlit, yt-dlp, FFmpeg e IA Generativa")


# ==========================================
# ÁREA PRINCIPAL - ABAS
# ==========================================
st.markdown('<div class="main-header">✂️ EasyKut — Criador Automático de Cortes Virais</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Transforme vídeos longos em clipes verticais de alta retenção com curadoria de IA e revisão humana interativa.</div>', unsafe_allow_html=True)

tab_novo, tab_historico = st.tabs(["🚀 Processar Novo Vídeo", "📂 Histórico & Cortes Salvos"])

# ------------------------------------------
# ABA 1: PROCESSAR NOVO VÍDEO (FLUXO EM 2 PASSOS)
# ------------------------------------------
with tab_novo:
    st.markdown("### 1️⃣ Passo 1: Ingestão e Curadoria de IA")
    input_url = st.text_input(
        "Cole a URL do Vídeo do YouTube:",
        placeholder="https://www.youtube.com/watch?v=...",
        help="Insira o link de qualquer vídeo ou transmissão pública."
    )

    col_btn_analisar, col_btn_limpar = st.columns([2, 1])
    with col_btn_analisar:
        btn_analisar = st.button("🔍 Analisar Vídeo & Detectar Cortes", type="primary", use_container_width=True)
    with col_btn_limpar:
        if st.session_state.get("analise_atual"):
            if st.button("🔄 Limpar Análise / Novo Vídeo", use_container_width=True):
                st.session_state.pop("analise_atual", None)
                st.rerun()

    # Execução do Passo 1: Download de Áudio + Whisper + LLM
    if btn_analisar:
        if not input_url or not is_valid_video_url(input_url):
            st.error("Por favor, insira uma URL de vídeo válida do YouTube.")
        elif not is_ready:
            msg_erro = "Configurações incompletas. "
            if not config_status.get("FFMPEG"):
                msg_erro += "FFmpeg não encontrado. "
            if not config_status.get("GROQ_API_KEY"):
                msg_erro += "GROQ_API_KEY não configurada. "
            if ai_provider == "gemini" and not config_status.get("GEMINI_API_KEY"):
                msg_erro += "GEMINI_API_KEY necessária para o provedor Gemini. "
            st.error(msg_erro)
        else:
            with st.status("🚀 Iniciando esteira de análise com Inteligência Artificial...", expanded=True) as status_box:
                t_inicio_total = time.time()
                try:
                    # 1. Metadados do vídeo
                    t0 = time.time()
                    status_box.write("📥 [1/4] Extraindo metadados do vídeo via yt-dlp...")
                    info = extract_video_info(input_url)
                    video_title = info.get("title", "Vídeo Sem Título")
                    video_duration = info.get("duration", 0.0)
                    video_id = add_video(url=input_url, title=video_title, duration=video_duration, status="processing")
                    status_box.write(f"✅ [1/4] Metadados extraídos em {time.time() - t0:.1f}s: **{video_title}** ({int(video_duration // 60)}m {int(video_duration % 60)}s)")

                    # 2. Download do áudio leve (mono 16kHz)
                    t0 = time.time()
                    status_box.write("🎵 [2/4] Baixando faixa de áudio leve (MP3 48k mono 16kHz)...")
                    audio_file = download_audio(input_url)
                    update_video_status(video_id, status="transcribing", audio_path=audio_file)
                    status_box.write(f"✅ [2/4] Áudio baixado em {time.time() - t0:.1f}s.")

                    # 3. Transcrição com Groq Whisper
                    t0 = time.time()
                    status_box.write("🎙️ [3/4] Transcrevendo áudio com Groq Whisper-large-v3...")
                    transcript_data = transcribe_audio_groq(audio_file)
                    total_segs = len(transcript_data.get("segments", []))
                    status_box.write(f"✅ [3/4] Transcrição concluída em {time.time() - t0:.1f}s ({total_segs} segmentos temporais).")

                    # 4. Análise de Momentos Virais via IA
                    t0 = time.time()
                    status_box.write(f"🧠 [4/4] Analisando momentos de retenção com {ai_provider.upper()}...")
                    cortes_detectados = detect_viral_moments(transcript_data, provider=ai_provider)

                    if not cortes_detectados:
                        status_box.update(label="⚠️ Nenhum corte viral detectado pela IA.", state="error")
                        st.warning("A IA não detectou segmentos com critérios de retenção suficientes para corte.")
                        update_video_status(video_id, status="completed_no_cuts")
                    else:
                        # Ordena explicitamente por maior score de viralidade (decrescente)
                        cortes_detectados.sort(key=lambda x: float(x.get("virality_score", 0.0)), reverse=True)

                        # Inicializa estrutura editável
                        cortes_formatados = []
                        for idx, c in enumerate(cortes_detectados):
                            cortes_formatados.append({
                                "idx": idx,
                                "selected": (idx < max_cuts_limit),
                                "title": str(c.get("title", f"Corte #{idx+1}")),
                                "start_time": float(c.get("start_time", 0.0)),
                                "end_time": float(c.get("end_time", 0.0)),
                                "virality_score": float(c.get("virality_score", 8.0)),
                                "justification": str(c.get("justification", "")),
                            })

                        # Salva análise inicial no banco
                        save_analysis(video_id=video_id, transcript=transcript_data, analysis_data=cortes_detectados)

                        # Persiste dados no session_state para a tela de revisão
                        st.session_state["analise_atual"] = {
                            "video_id": video_id,
                            "url": input_url,
                            "title": video_title,
                            "duration": video_duration,
                            "thumbnail": info.get("thumbnail"),
                            "uploader": info.get("uploader"),
                            "audio_path": audio_file,
                            "transcript_data": transcript_data,
                            "cortes": cortes_formatados,
                        }

                        tempo_total = time.time() - t_inicio_total
                        status_box.update(
                            label=f"🎉 Análise concluída em {tempo_total:.1f}s! {len(cortes_formatados)} cortes sugeridos pela IA. Revise abaixo para renderizar.",
                            state="complete"
                        )
                except Exception as exc:
                    status_box.update(label=f"❌ Erro durante a esteira de análise: {exc}", state="error")
                    st.error(f"Erro no processamento: {exc}")
                    if "video_id" in locals():
                        update_video_status(video_id, status="failed")

    # Tela de Revisão (Human-in-the-Loop) - Passo 2
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
            st.markdown(f"⏱️ **Duração Total:** {int(video_dur // 60)}m {int(video_dur % 60)}s | 👤 **Canal:** {analise.get('uploader')}")
            st.caption(f"🔗 [Abrir vídeo original no YouTube]({analise.get('url')})")

        st.markdown("### ✍️ Passo 2: Revisão dos Cortes (Human-in-the-Loop)")
        st.markdown("Marque os cortes que deseja renderizar, ajuste os títulos magnéticos e refine os segundos de início/fim:")

        # Formulário interativo de revisão dos cortes
        for i, c in enumerate(analise["cortes"]):
            with st.container():
                col_c1, col_c2, col_c3 = st.columns([3, 1, 1])
                with col_c1:
                    c["selected"] = st.checkbox(
                        f"**Incluir Corte #{i+1} na renderização**",
                        value=c["selected"],
                        key=f"rev_sel_{c['idx']}"
                    )
                with col_c2:
                    st.markdown(f"⭐ **Score:** `{c['virality_score']:.1f}/10`")
                with col_c3:
                    dur_atual = max(0.0, c["end_time"] - c["start_time"])
                    st.caption(f"⏱️ Duração: `{dur_atual:.1f}s`")

                c["title"] = st.text_input(
                    f"Título do Corte #{i+1}:",
                    value=c["title"],
                    key=f"rev_title_{c['idx']}"
                )

                col_t1, col_t2 = st.columns(2)
                with col_t1:
                    c["start_time"] = st.number_input(
                        f"Início (s) - Corte #{i+1}:",
                        min_value=0.0,
                        max_value=max(1.0, video_dur),
                        value=min(video_dur, float(c["start_time"])),
                        step=0.5,
                        key=f"rev_start_{c['idx']}"
                    )
                with col_t2:
                    c["end_time"] = st.number_input(
                        f"Fim (s) - Corte #{i+1}:",
                        min_value=0.0,
                        max_value=max(1.0, video_dur),
                        value=min(video_dur, float(c["end_time"])),
                        step=0.5,
                        key=f"rev_end_{c['idx']}"
                    )

                if c.get("justification"):
                    st.caption(f"💡 *Justificativa da IA:* {c['justification']}")
                st.markdown("---")

        cortes_selecionados = [c for c in analise["cortes"] if c["selected"]]

        col_act1, col_act2 = st.columns([2, 1])
        with col_act1:
            btn_renderizar = st.button(
                f"🎬 Renderizar {len(cortes_selecionados)} Corte(s) Selecionado(s) em 9:16",
                type="primary",
                disabled=(len(cortes_selecionados) == 0),
                use_container_width=True
            )
        with col_act2:
            st.info(f"📊 **{len(cortes_selecionados)}** de **{len(analise['cortes'])}** selecionado(s)")

        # Execução do Passo 2: Download do Vídeo HD + Renderização FFmpeg/OpenCV
        if btn_renderizar:
            if not cortes_selecionados:
                st.warning("Selecione pelo menos um corte acima para renderizar.")
            else:
                with st.status(f"⚡ Renderizando {len(cortes_selecionados)} corte(s) com FFmpeg & OpenCV...", expanded=True) as status_render:
                    t_rend_inicio = time.time()
                    try:
                        # 1. Download do vídeo em alta definição (executado somente agora)
                        t0 = time.time()
                        status_render.write(f"🎥 [1/2] Baixando vídeo fonte em {max_resolution}p...")
                        video_file = download_video(analise["url"], max_resolution=max_resolution)
                        update_video_status(analise["video_id"], status="cutting", file_path=video_file)
                        status_render.write(f"✅ [1/2] Vídeo baixado em {time.time() - t0:.1f}s.")

                        # 2. Renderização dos cortes selecionados
                        status_render.write(f"✂️ [2/2] Renderizando cortes selecionados no modo '{crop_mode}'...")
                        cortes_renderizados = []

                        for idx, c in enumerate(cortes_selecionados):
                            t_cut_inicio = time.time()
                            status_render.write(f"⏳ Processando corte {idx+1}/{len(cortes_selecionados)}: '{c['title']}' ({c['start_time']}s -> {c['end_time']}s)...")

                            # Registra o corte com os tempos e título revisados pelo usuário
                            corte_db_id = add_corte(
                                video_id=analise["video_id"],
                                start_time=c["start_time"],
                                end_time=c["end_time"],
                                title=c["title"],
                                virality_score=c["virality_score"],
                                justification=c["justification"],
                                status="rendering"
                            )

                            # Executa corte com FFmpeg e Face Tracking suave
                            output_clip = cut_video(
                                input_path=video_file,
                                start_time=c["start_time"],
                                end_time=c["end_time"],
                                mode=crop_mode,
                                segments=analise["transcript_data"].get("segments", []),
                                burn_subtitles=burn_subs
                            )

                            update_corte_status(corte_db_id, status="completed", output_path=output_clip)
                            c_item = dict(c)
                            c_item["output_path"] = output_clip
                            c_item["corte_db_id"] = corte_db_id
                            cortes_renderizados.append(c_item)
                            status_render.write(f"✅ Corte {idx+1}/{len(cortes_selecionados)} finalizado em {time.time() - t_cut_inicio:.1f}s!")

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
                    except Exception as exc:
                        status_render.update(label=f"❌ Erro durante a renderização: {exc}", state="error")
                        st.error(f"Erro na renderização: {exc}")

    # 3️⃣ Galeria de Cortes Prontos (Renderizada persistentemente fora de blocos if de botões)
    if st.session_state.get("ultimos_cortes"):
        st.markdown("---")
        st.markdown(f"### 🎬 Galeria de Cortes Prontos — {st.session_state.get('ultimo_video_title', 'Vídeo Recente')}")

        cortes_exibir = st.session_state["ultimos_cortes"]
        cols = st.columns(min(3, max(1, len(cortes_exibir))))
        for i, corte in enumerate(cortes_exibir):
            with cols[i % len(cols)]:
                st.markdown(f"#### ✂️ {corte['title']}")
                st.markdown(f"⭐ **Viral Score:** `{corte.get('virality_score', 8.0):.1f}/10`")
                st.caption(f"⏱️ **Tempo:** {corte['start_time']}s até {corte['end_time']}s ({corte['end_time'] - corte['start_time']:.1f}s)")
                if corte.get("justification"):
                    st.info(f"💡 *{corte['justification']}*")

                out_p = corte.get("output_path", "")
                if out_p and os.path.exists(out_p):
                    st.video(out_p)
                    with open(out_p, "rb") as vf:
                        st.download_button(
                            label=f"⬇️ Baixar Corte #{i+1} (.mp4)",
                            data=vf.read(),
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
        if st.button("📂 Abrir Pasta de Cortes", use_container_width=True, help="Abre a pasta data/cortes/ no Explorador do Windows"):
            try:
                if hasattr(os, "startfile"):
                    os.startfile(str(CUTS_DIR.resolve()))
                    st.toast("Pasta data/cortes/ aberta no Explorador de Arquivos!")
                else:
                    import subprocess
                    subprocess.Popen(["explorer", str(CUTS_DIR.resolve())])
            except Exception as exc:
                st.error(f"Erro ao abrir pasta de cortes: {exc}")

    # Filtros de Busca e Nota Mínima
    col_f1, col_f2 = st.columns([3, 1])
    with col_f1:
        filtro_busca = st.text_input("🔍 Buscar por título:", placeholder="Filtrar por título do vídeo ou URL...", key="hist_search_input")
    with col_f2:
        filtro_min_score = st.slider("⭐ Nota Mínima:", min_value=0.0, max_value=10.0, value=0.0, step=0.5, key="hist_score_slider")

    todos_videos = list_videos(search=filtro_busca)

    if not todos_videos:
        if filtro_busca:
            st.info(f"Nenhum vídeo encontrado com o termo '{filtro_busca}'.")
        else:
            st.info("Nenhum vídeo processado ainda no histórico.")
    else:
        for vid in todos_videos:
            cortes_do_video = list_cortes(video_id=vid["id"], min_virality_score=filtro_min_score)

            with st.expander(f"📹 {vid['title'] or vid['url']} (ID: {vid['id']} | Status: {vid['status']} | {len(cortes_do_video)} corte(s))"):
                col_h1, col_h2, col_h3 = st.columns([3, 2, 2])
                with col_h1:
                    st.markdown(f"**URL:** [{vid['url']}]({vid['url']})")
                    st.markdown(f"**Duração:** {vid['duration']}s | **Criado em:** {vid['created_at']}")
                with col_h2:
                    del_files_opt = st.checkbox(
                        "Excluir arquivos do disco",
                        value=True,
                        key=f"cb_del_files_{vid['id']}",
                        help="Remove também os arquivos .mp4 e .mp3 físicos em data/cortes/ e data/downloads/"
                    )
                with col_h3:
                    if st.button("🗑️ Excluir Registro", key=f"del_vid_btn_{vid['id']}", type="secondary", use_container_width=True):
                        delete_video(vid['id'], delete_files=del_files_opt)
                        st.toast(f"Vídeo {vid['id']} removido com sucesso!")
                        st.rerun()

                # Listagem dos cortes associados a este vídeo
                if not cortes_do_video:
                    if filtro_min_score > 0:
                        st.caption(f"Nenhum corte deste vídeo atingiu a nota mínima de {filtro_min_score}/10.")
                    else:
                        st.caption("Nenhum corte registrado para este vídeo.")
                else:
                    st.markdown("#### Cortes Gerados:")
                    cols_hist = st.columns(min(3, max(1, len(cortes_do_video))))
                    for idx, c in enumerate(cortes_do_video):
                        with cols_hist[idx % len(cols_hist)]:
                            st.markdown(f"**{c['title']}**")
                            st.caption(f"Score: `{c['virality_score']:.1f}/10` | {c['start_time']}s -> {c['end_time']}s")
                            if c.get("justification"):
                                st.caption(f"_{c['justification']}_")

                            out_clip = c.get("output_path", "")
                            if out_clip and os.path.exists(out_clip):
                                st.video(out_clip)
                                with open(out_clip, "rb") as f_c:
                                    st.download_button(
                                        label="⬇️ Baixar Clipe (.mp4)",
                                        data=f_c.read(),
                                        file_name=Path(out_clip).name,
                                        mime="video/mp4",
                                        key=f"hist_dl_{c['id']}",
                                        use_container_width=True
                                    )
                            else:
                                st.warning("Arquivo de vídeo não encontrado no disco.")
