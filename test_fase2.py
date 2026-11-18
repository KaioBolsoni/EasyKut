"""
Script de Teste e Validação da Fase 2 (EasyKut)
Executa a esteira de:
1. Extração de metadados e download do áudio em data/temp/
2. Transcrição com Groq Whisper (verbose_json com timestamps)
3. Análise dos momentos mais virais via IA com o prompt diretor_cortes.txt
4. Persistência dos resultados no SQLite via save_analysis
5. Exibição do JSON final estruturado no terminal
"""

import argparse
import json
import sys
from pathlib import Path

# Garante que o diretório 'cortador_automatico' esteja no sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.config import DATA_DIR, validate_config
from core.downloader import extract_video_info, download_audio, is_valid_video_url
from core.ai_apis import transcribe_audio_groq, detect_viral_moments
from database.db_manager import init_db, add_video, save_analysis, list_cortes, get_video

TEMP_DIR = DATA_DIR / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)


def run_fase2(url: str, provider: str = "gemini") -> None:
    print("=" * 60)
    print("🚀 INICIANDO TESTE DA FASE 2 - EASYKUT (IA & CURADORIA)")
    print("=" * 60)

    # 1. Validação de Ambiente e URL
    is_ready, cfg_status = validate_config()
    if not cfg_status.get("GROQ_API_KEY"):
        print("❌ [ERRO] Chave GROQ_API_KEY ausente ou não configurada no .env")
        sys.exit(1)

    if not is_valid_video_url(url):
        print(f"❌ [ERRO] URL fornecida é inválida: {url}")
        sys.exit(1)

    print(f"📌 URL Alvo: {url}")
    print(f"🤖 Provedor de Análise IA: {provider.upper()}")

    # Inicializa banco
    init_db()

    # 2. Metadados do Vídeo
    print("\n[1/5] 📥 Extraindo metadados do vídeo...")
    info = extract_video_info(url)
    title = info.get("title", "Sem Título")
    duration = info.get("duration", 0.0)
    print(f"  -> Título: {title}")
    print(f"  -> Duração: {int(duration // 60)}m {int(duration % 60)}s ({duration}s)")

    # Registra vídeo no banco
    video_id = add_video(url=url, title=title, duration=duration, status="downloading_audio")
    print(f"  -> Vídeo registrado no SQLite com ID: {video_id}")

    # 3. Download do Áudio em data/temp/
    print(f"\n[2/5] 🎵 Baixando áudio otimizado em '{TEMP_DIR}'...")
    audio_path = download_audio(url, output_dir=TEMP_DIR)
    print(f"  -> Áudio salvo em: {audio_path}")

    # 4. Transcrição com Groq Whisper
    print("\n[3/5] 🎙️ Transcrevendo áudio com Groq Whisper-large-v3-turbo...")
    transcript_data = transcribe_audio_groq(audio_path)
    segments = transcript_data.get("segments", [])
    print(f"  -> Transcrição concluída! Total de segmentos temporais: {len(segments)}")

    # 5. Análise de Momentos Virais com IA
    print(f"\n[4/5] 🧠 Analisando trechos virais com {provider.upper()} e prompt diretor_cortes.txt...")
    cortes = detect_viral_moments(transcript_data, provider=provider)
    print(f"  -> Cortes selecionados pela IA: {len(cortes)}")

    # 6. Persistência via save_analysis
    print("\n[5/5] 💾 Salvando análise e cortes no banco via save_analysis()...")
    inserted_ids = save_analysis(video_id=video_id, transcript=transcript_data, analysis_data=cortes)
    print(f"  -> Cortes inseridos com IDs: {inserted_ids}")

    # 7. Exibição do JSON Final Estruturado
    print("\n" + "=" * 60)
    print("📊 RESULTADO FINAL ESTRUTURADO (JSON):")
    print("=" * 60)

    resultado_final = {
        "video_id": video_id,
        "url": url,
        "title": title,
        "duration": duration,
        "audio_path": audio_path,
        "cortes_detectados": cortes,
        "cortes_salvos_db": list_cortes(video_id=video_id)
    }

    print(json.dumps(resultado_final, indent=2, ensure_ascii=False))
    print("\n✅ [SUCESSO] Teste da Fase 2 concluído com êxito!")


def main():
    parser = argparse.ArgumentParser(
        description="EasyKut - Validação e Teste da Fase 2 (IA e Curadoria)"
    )
    parser.add_argument(
        "url",
        nargs="?",
        default="https://www.youtube.com/watch?v=jNQXAC9IVRw",
        help="URL do vídeo do YouTube para processar (Padrão: primeiro vídeo do YouTube)"
    )
    parser.add_argument(
        "--provider",
        choices=["gemini", "groq"],
        default="gemini",
        help="Provedor de LLM para análise viral (Padrão: gemini com fallback automático)"
    )

    args = parser.parse_args()
    run_fase2(url=args.url, provider=args.provider)


if __name__ == "__main__":
    main()
