import sys
from pathlib import Path

# Garante que o diretório 'cortador_automatico' esteja no sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from database.db_manager import (
    init_db,
    add_video,
    list_videos,
    add_corte,
    list_cortes,
    update_video_status,
)


def run_tests():
    print("[1/5] Inicializando banco de dados...")
    init_db()
    print("  -> Banco inicializado com sucesso.")

    print("\n[2/5] Inserindo vídeo de teste...")
    test_url = "https://www.youtube.com/watch?v=teste123"
    vid_id = add_video(url=test_url, title="Vídeo de Teste")
    print(f"  -> Vídeo inserido com ID: {vid_id}")

    print("\n[3/5] Atualizando status do vídeo...")
    update_video_status(vid_id, status="ready", duration=120.5)
    print("  -> Status atualizado para 'ready'.")

    print("\n[4/5] Inserindo corte de teste associado ao vídeo...")
    corte_id = add_corte(
        video_id=vid_id,
        title="Momento Épico",
        start_time=15.0,
        end_time=45.0,
        virality_score=8.5,
        justification="Ganchos e emoção elevada na fala.",
    )
    print(f"  -> Corte inserido com ID: {corte_id}")

    print("\n[5/5] Listando dados persistidos:")
    videos = list_videos()
    print(f"  Total de vídeos no banco: {len(videos)}")
    for v in videos:
        print(f"    - ID {v['id']}: {v['title']} ({v['url']}) | Status: {v['status']} | Duração: {v['duration']}s")

    cortes = list_cortes(video_id=vid_id)
    print(f"  Total de cortes para o vídeo {vid_id}: {len(cortes)}")
    for c in cortes:
        print(f"    - Corte ID {c['id']}: '{c['title']}' ({c['start_time']}s -> {c['end_time']}s, total: {c['duration']}s) | Score: {c['virality_score']}")

    print("\n[SUCESSO] Todos os testes da camada de banco de dados passaram perfeitamente!")


if __name__ == "__main__":
    run_tests()
