"""
Script de Validação e Teste da Fase 3 - Motor de Vídeo Avançado (EasyKut)
Testa a geração de legendas SRT ajustadas a 00:00:00,000,
o corte vertical 9:16 com Face Tracking suave (OpenCV) e
o corte horizontal 16:9 com queima de legendas via FFmpeg.
"""

import argparse
import sys
from pathlib import Path

# Garante que 'cortador_automatico' esteja no sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.video_engine import (
    generate_srt_for_segment,
    cut_video,
    get_video_metadata,
)
from core.config import DATA_DIR, CUTS_DIR


def run_test(mode: str, video_path: str, start: float, end: float, burn_subs: bool = True):
    print("=" * 60)
    print(f"🎬 TESTE DA FASE 3: CORTE ({mode.upper()})")
    print("=" * 60)

    src = Path(video_path)
    if not src.exists():
        print(f"❌ Vídeo não encontrado: {video_path}")
        print("💡 Dica: Você pode usar qualquer vídeo MP4 baixado na pasta data/downloads/")
        sys.exit(1)

    meta = get_video_metadata(str(src))
    print(f"📊 Metadados do vídeo original: {meta['width']}x{meta['height']} ({meta['aspect_ratio']:.2f}) | {meta['duration']:.1f}s")

    # Segmentos de exemplo simulando saída do Whisper
    mock_segments = [
        {"start": start + 0.5, "end": start + 3.0, "text": "Este é um teste de corte automatizado."},
        {"start": start + 3.2, "end": start + 6.0, "text": "Legenda sincronizada gerada pelo EasyKut!"},
        {"start": start + 6.2, "end": end - 0.5, "text": "Face tracking suave e proporção perfeita."},
    ]

    print(f"\n[1/3] Gerando arquivo .srt temporário ajustado a 00:00:00,000...")
    srt_path = generate_srt_for_segment(mock_segments, start_time=start, end_time=end)
    if srt_path:
        print(f"  -> Arquivo .srt gerado em: {srt_path}")
        with open(srt_path, "r", encoding="utf-8") as f:
            print("\n--- CONTEÚDO DO SRT ---")
            print(f.read().strip())
            print("-----------------------\n")

    dest_filename = f"teste_fase3_{mode}_{int(start)}_{int(end)}.mp4"
    dest_path = CUTS_DIR / dest_filename

    print(f"[2/3] Renderizando corte no modo '{mode}' via FFmpeg (queimar legendas: {burn_subs})...")
    output = cut_video(
        input_path=str(src),
        start_time=start,
        end_time=end,
        output_path=str(dest_path),
        mode=mode,
        segments=mock_segments if burn_subs else None,
        burn_subtitles=burn_subs
    )

    print(f"[3/3] Validando vídeo renderizado...")
    out_meta = get_video_metadata(output)
    print(f"  -> Arquivo final: {output}")
    print(f"  -> Dimensões: {out_meta['width']}x{out_meta['height']} (Aspect Ratio: {out_meta['aspect_ratio']:.4f})")
    print(f"  -> Duração: {out_meta['duration']:.2f}s")
    print("\n✅ [SUCESSO] Corte renderizado com sucesso na Fase 3!")


def main():
    parser = argparse.ArgumentParser(description="Teste da Fase 3 - Motor de Vídeo EasyKut")
    parser.add_argument("--mode", choices=["face_tracking_9_16", "horizontal_16_9", "blur_background", "crop_center"],
                        default="face_tracking_9_16", help="Modo de corte a ser testado")
    parser.add_argument("--video", type=str, required=True, help="Caminho do arquivo de vídeo MP4 de entrada")
    parser.add_argument("--start", type=float, default=0.0, help="Tempo de início em segundos")
    parser.add_argument("--end", type=float, default=8.0, help="Tempo de fim em segundos")
    parser.add_argument("--no-subs", action="store_true", help="Desativa queima de legendas")

    args = parser.parse_args()
    run_test(mode=args.mode, video_path=args.video, start=args.start, end=args.end, burn_subs=not args.no_subs)


if __name__ == "__main__":
    main()
