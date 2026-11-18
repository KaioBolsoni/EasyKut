# Arquitetura Real do Repositório EasyKut

Este documento descreve a estrutura real de código, tecnologias, banco de dados e modelos implementados no projeto **EasyKut**.

---

## 1. Estrutura de Pastas e Módulos

```text
EasyKut/
├── .env                              # Variáveis de ambiente
├── APIS.txt                          # Aviso de segurança e referência
└── cortador_automatico/              # Raiz da aplicação e repositório Git
    ├── .env                          # Variáveis de ambiente locais (protegido no .gitignore)
    ├── .env.example                  # Modelo de variáveis de ambiente sem chaves reais
    ├── .gitignore                    # Regras de exclusão do Git
    ├── app.py                        # Interface Web Streamlit completa
    ├── requirements.txt              # Dependências Python do projeto
    ├── test_db.py                    # Teste automatizado do banco SQLite
    ├── test_fase2.py                 # Teste CLI da esteira da Fase 2 (IA e curadoria)
    ├── .streamlit/
    │   └── config.toml               # Configurações do servidor Streamlit
    ├── core/
    │   ├── __init__.py               # Fachada pública do pacote core
    │   ├── config.py                 # Configurações de caminhos, ambiente e FFmpeg
    │   ├── downloader.py             # Extração de metadados e downloads via yt-dlp
    │   ├── ai_apis.py                # Integração com Groq Whisper, Gemini e Groq LLMs
    │   └── video_engine.py           # Renderização vertical 9:16 e filtros FFmpeg
    ├── database/
    │   ├── __init__.py               # Exportação das funções CRUD
    │   └── db_manager.py             # Camada de persistência SQLite com modo WAL
    ├── data/
    │   ├── cortador.db               # Banco de dados SQLite da aplicação
    │   ├── downloads/                # Mídias brutas baixadas (áudio e vídeo)
    │   ├── cortes/                   # Clipes finais renderizados em 9:16
    │   └── temp/                     # Arquivos temporários de teste e esteira
    ├── docs/
    │   └── arquitetura.md            # Este documento de referência arquitetural
    └── prompts/
        └── diretor_cortes.txt        # Prompt especialista para curadoria de cortes virais
```

---

## 2. Banco de Dados e Modelagem de Dados

- **Arquivo do Banco:** `data/cortador.db`
- **Engine:** SQLite 3 nativo do Python, configurado com:
  - `PRAGMA journal_mode = WAL;` (Write-Ahead Logging para concorrência segura com Streamlit)
  - `PRAGMA foreign_keys = ON;` (Integridade referencial estrita)
  - `check_same_thread = False` e timeout de 15 segundos.
- **Tabelas Implementadas:**
  1. `videos`:
     - `id`: INTEGER PRIMARY KEY AUTOINCREMENT
     - `url`: TEXT NOT NULL
     - `title`: TEXT DEFAULT ''
     - `duration`: REAL DEFAULT 0.0
     - `file_path`: TEXT DEFAULT ''
     - `audio_path`: TEXT DEFAULT ''
     - `status`: TEXT DEFAULT 'pending'
     - `created_at`, `updated_at`: TIMESTAMP
  2. `cortes`:
     - `id`: INTEGER PRIMARY KEY AUTOINCREMENT
     - `video_id`: INTEGER NOT NULL (FK referenciando `videos(id)` com `ON DELETE CASCADE`)
     - `title`: TEXT DEFAULT ''
     - `start_time`: REAL NOT NULL
     - `end_time`: REAL NOT NULL
     - `duration`: REAL VIRTUAL (`GENERATED ALWAYS AS (end_time - start_time)`)
     - `virality_score`: REAL DEFAULT 0.0
     - `justification`: TEXT DEFAULT ''
     - `output_path`: TEXT DEFAULT ''
     - `status`: TEXT DEFAULT 'pending'
     - `created_at`: TIMESTAMP

- **Funções CRUD em `database/db_manager.py`:**
  - `init_db()`
  - `add_video(url, title, ...)`
  - `get_video(video_id)`
  - `update_video_status(video_id, status, ...)`
  - `list_videos()`
  - `delete_video(video_id)`
  - `add_corte(video_id, start_time, end_time, ...)`
  - `list_cortes(video_id)`
  - `update_corte_status(corte_id, status, ...)`
  - `save_analysis(video_id, transcript, analysis_data)`: Persiste os cortes gerados pela IA e atualiza o status do vídeo.

---

## 3. Inteligência Artificial e Modelos (`core/ai_apis.py`)

- **Transcrição de Áudio:**
  - **Provedor:** Groq Cloud
  - **Modelo:** `whisper-large-v3-turbo` (temperatura 0.0, saída estruturada com `response_format="verbose_json"` gerando timestamps frase a frase).
- **Curadoria de Cortes Virais:**
  - **Modelo Primário:** Google Gemini (`gemini-3.1-flash-lite` via SDK oficial `google-genai`), com saída forçada em `application/json`.
  - **Modelo Fallback / Alternativo:** Groq LLM (`openai/gpt-oss-120b` ou `qwen/qwen3.8-27b`), com `response_format={"type": "json_object"}`.
  - **Resiliência:** Se o provedor primário falhar ou retornar erro, a função `detect_viral_moments` ativa automaticamente o fallback sem quebrar a execução.

---

## 4. Motor Audiovisual Avançado — Fase 3 (`core/video_engine.py`)

- **Binários:** Auto-detecção de `ffmpeg.exe` e `ffprobe.exe` (ex: `C:\ffmpeg\bin` injetado no PATH do processo).
- **Gerador de Legendas Sincronizadas (`generate_srt_for_segment`):**
  - Converte segmentos do Whisper para `.srt` temporário em `data/temp/`.
  - Recalcula os tempos de cada legenda para iniciarem em `00:00:00,000` relativo ao início do clipe.
  - Queima automática com tipografia e posicionamento adaptados para mobile ou widescreen via filtro FFmpeg `subtitles`.
- **Modos de Renderização:**
  - `face_tracking_9_16` / `vertical_face`: Detecção facial com OpenCV Haar Cascade, estabilização temporal da coordenada X com Média Móvel (Moving Average nos últimos 10 frames) para eliminar solavancos, e fallback automático para corte central caso nenhum rosto seja detectado.
  - `horizontal_16_9` / `long_16_9`: Mantém a proporção horizontal 16:9 original para vídeos longos e highlights (7+ min).
  - `blur_background`: Fundo 9:16 (1080x1920) ampliado com desfoque gaussiano (`boxblur=25:10`) e o vídeo original centralizado nítido.
  - `crop_center`: Recorte central direto em 9:16 (`crop=ih*(9/16):ih,scale=1080:1920`).
- **Preview:** Extração estática de posters em JPEG (`extract_frame_thumbnail`).

---

## 5. Interface Web (`app.py`)

- Desenvolvida em **Streamlit**.
- Contém duas abas principais:
  1. **Processar Novo Vídeo:** Formulário de URL com feedback em barra de progresso (metadados ➔ download áudio ➔ transcrição ➔ IA ➔ download vídeo ➔ renderização 9:16 ➔ galeria com player e botões de download).
  2. **Histórico & Cortes Salvos:** Consulta do banco SQLite com listagem e reprodução de vídeos processados anteriormente.
- **Gestão de Armazenamento:** Opção de limpeza automática pós-corte e botão manual para exclusão de arquivos temporários pesados.

---

## 6. Status do Projeto — 5 Fases Concluídas

| Fase | Título | Status | Principais Entregas |
| :---: | :--- | :---: | :--- |
| **Fase 1** | **Arquitetura & Persistência** | ✅ Concluída | SQLite com WAL mode, tabelas `videos` e `cortes`, CRUD completo e `test_db.py`. |
| **Fase 2** | **Inteligência Artificial & Prompts** | ✅ Concluída | Groq Whisper (`whisper-large-v3-turbo`), Gemini (`gemini-3.1-flash-lite`), prompt `diretor_cortes.txt` e `test_fase2.py`. |
| **Fase 3** | **Motor de Vídeo Avançado** | ✅ Concluída | Geração de legendas `.srt` sincronizadas (00:00:00,000), cortes horizontais 16:9, Face Tracking OpenCV com Média Móvel 9:16 e `test_fase3.py`. |
| **Fase 4** | **Interface Web Interativa** | ✅ Concluída | Dashboard Streamlit completo, esteira visual com barra de progresso, galeria de cortes e histórico persistido. |
| **Fase 5** | **Resiliência & Limpeza** | ✅ Concluída | Retentativas automáticas com backoff exponencial (3x) contra 429/timeouts, sanitização avançada de JSON, priorização de maior rosto em múltiplos interlocutores e rotina de limpeza de temporários. |

