# ✂️ EasyKut Studio — Editor Profissional de Cortes Virais (v3.1)

O **EasyKut Studio** é uma aplicação local em **FastAPI + HTML5 Canvas 60fps (ES Modules)** que automatiza a criação de cortes virais a partir de vídeos públicos (YouTube, Vimeo e Twitch). Combina ingestão otimizada via **yt-dlp**, transcrição ultrarrápida via **Groq Whisper**, curadoria inteligente com **Google Gemini / Groq LLMs**, **Estúdio de Enquadramento Interativo em Tempo Real** (com prévia de vídeo + áudio no Canvas e mockup 9:16 ao vivo) e renderização profissional com legendas queimadas via **FFmpeg**.

---

## ✨ Funcionalidades Principais (v3.1)

- **Ingestão Segura e Otimizada (`yt-dlp`):** Validação estrita por allowlist de plataformas públicas (proteção SSRF/DNS rebinding), extração unificada de metadados + áudio leve (MP3 48kbps mono) + URL de stream direto em 1 chamada, e download do vídeo HD (`720p`/`1080p`) apenas sob demanda.
- **Transcrição com Timestamps (`Groq Whisper`):** Modelo `whisper-large-v3-turbo` gerando segmentos frase a frase com recompressão automática abaixo de 24 MB e cache privado em `data/cache/`.
- **Curadoria Viral com IA:** Cadeia de fallback entre **Google Gemini** (`gemini-3.8-flash` → `gemini-3.5-flash-lite` → `gemini-3.1-flash-lite`) e **Groq LLM** (`openai/gpt-oss-120b` → `openai/gpt-oss-20b` → `qwen/qwen3.8-27b`), com retry exponencial, diagnóstico por modelo e recuperação estruturada de JSON.
- **Estúdio Interativo em HTML5 Canvas 60fps (4 Modos de Layout + Prévia de Vídeo):**
  1. `vertical_blur`: Canvas 9:16 (1080×1920) com vídeo 16:9 nítido centralizado sobre fundo desfocado (blur offscreen otimizado 270×480 no mockup e `boxblur=20:5` + `eq=brightness=-0.15` no FFmpeg), com zoom ajustável (`1.0x` a `1.3x`).
  2. `horizontal_16_9`: Proporção widescreen 16:9 original sem corte (Stream Copy instantâneo quando sem legendas).
  3. `manual_crop`: Caixa 9:16 arrastável e redimensionável direto no mouse sobre o frame ou vídeo ao vivo.
  4. `split_screen`: Duas caixas independentes (Topo Índigo + Base Âmbar, proporção 9:8 / 1080×960 cada) empilhadas verticalmente.
  5. `face_tracking_9_16`: Preset opcional com detecção facial OpenCV (Haar Cascade / YuNet ONNX com verificação SHA-256) e amortecimento por Média Móvel.
- **Prévia de Vídeo & Áudio no Estúdio:** Reprodução ao vivo do trecho recortado (`POST /api/preview-clip`) sincronizada com legendas e painel de transcrição clicável antes da renderização final.
- **Auto-Save & CRUD Completo (`SQLite WAL v3`):** Edições de título, tempos, layout, zoom e legendas são salvas automaticamente via `PATCH /api/cortes/{id}` com debounce e persistidas no render. Biblioteca com consulta em lote sem N+1, paginação (`X-Total-Count`), exclusão individual de corte (`DELETE /api/cortes/{id}`) ou vídeo completo (`DELETE /api/history/{id}`) com confinamento seguro em `data/`.
- **Observabilidade, GC e CI/CD:** `GET /api/health`, `POST /api/cleanup`, limpeza automática de temporários (>24h) no startup, logs rotativos em `data/logs/easykut.log` com `X-Request-ID`, e pipeline CI (`ruff`, `bandit`, `pip-audit`, `node --check`, `pytest`).

---

## 🛠️ Pré-requisitos

1. **Sistema Operacional:** Windows 10/11 (recomendado para os scripts `.bat` e abertura de pasta no Explorer).
2. **Python:** 3.11+ com ambiente virtual em `cortador_automatico/venv`.
3. **FFmpeg e FFprobe:** No `PATH` do sistema ou em `C:\ffmpeg\bin`, `C:\Ghost Toolbox\wget\Youtube-DLP` ou `C:\ProgramData\chocolatey\bin`.
4. **Deno (recomendado para `yt-dlp` EJS no YouTube):** Instalado em `%USERPROFILE%\.deno\bin\deno.exe` (injetado no `PATH` pelo `iniciar_easykut.bat`).
5. **Chaves de API:**
   - **Groq API Key:** [console.groq.com/keys](https://console.groq.com/keys)
   - **Gemini API Key:** [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
6. **YouTube Cookies (opcional):** Exporte `cookies.txt` (formato Netscape) para `cortador_automatico/cookies.txt` ou defina `YOUTUBE_COOKIES_PATH` no `.env`.

### Instalação e Configuração

```powershell
cd cortador_automatico
Copy-Item .env.example .env
# Edite .env inserindo GROQ_API_KEY e GEMINI_API_KEY

.\venv\Scripts\python.exe -m pip install -r requirements.txt
# Opcional (desenvolvimento, lint, SAST e testes):
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

---

## 🚀 Como Iniciar

- **Com 1 clique (Windows):** Dê duplo clique em `iniciar_easykut.bat` (na raiz ou em `cortador_automatico/`).
  - Libera processos travados na porta `8502` (filtrando PID > 0).
  - Inicia o servidor local em `http://127.0.0.1:8502` via `uvicorn server:app`.
  - Para atualizar manualmente o `yt-dlp` de forma explícita (opt-in), execute:
    ```bat
    iniciar_easykut.bat --update-ytdlp
    ```
- **Via terminal:**
  ```powershell
  cd cortador_automatico
  .\venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8502
  ```

---

## 🧪 Testes e Qualidade Local

A suíte automatizada roda 100% isolada em diretórios temporários (nunca altera `data/cortador.db`):

```powershell
cd cortador_automatico
.\venv\Scripts\python.exe -m pytest
.\venv\Scripts\python.exe -m ruff check core database services server.py tests
.\venv\Scripts\python.exe -m bandit -c pyproject.toml -r core database services server.py -ll
.\venv\Scripts\python.exe -m pip_audit -r requirements.txt --no-deps --disable-pip
```

Ou utilize o atalho `RODAR_TESTES.bat` na pasta raiz do projeto.

---

## 📁 Estrutura de Pastas

```text
cortador_automatico/
├── .env                          # Chaves locais (ignorado pelo Git)
├── .env.example                  # Modelo de variáveis de ambiente
├── .gitignore                    # Regras de exclusão Git
├── .github/workflows/ci.yml      # Pipeline GitHub Actions (ruff, bandit, pip-audit, node, pytest)
├── pyproject.toml                # Configuração do Ruff e Bandit
├── pytest.ini                    # Configuração da suíte Pytest (testpaths = tests)
├── requirements.txt              # Dependências de produção (versões pinadas)
├── requirements-dev.txt          # Dependências de desenvolvimento e testes (pinadas)
├── iniciar_easykut.bat           # Launcher Windows (porta 8502, Deno no PATH)
├── server.py                     # API REST FastAPI, middlewares de segurança e StaticFiles
├── services/
│   ├── job_manager.py            # Fila thread-safe de jobs, semáforo FFmpeg e cache LRU/TTL
│   └── media_service.py          # Orquestração de análise (frames paralelos) e renderização
├── core/
│   ├── config.py                 # Diretórios, FFmpeg, cookies e leitura do .env
│   ├── logging_config.py         # RotatingFileHandler (data/logs/) e Correlation ID
│   ├── media_utils.py            # Validação FFmpeg anti-SSRF/LFI e normalização de score
│   ├── downloader.py             # Allowlist pública, yt-dlp, timeouts e lock de download
│   ├── ai_apis.py                # Groq Whisper, Gemini/Groq LLMs, fallback e parser JSON
│   ├── subtitles.py              # Geração .srt sincronizada e escape libavfilter
│   ├── face_tracker.py           # OpenCV Haar/YuNet ONNX (SHA-256 verificado) + média móvel
│   ├── media_renderer.py         # ffprobe, prévias, renderização 4 modos e GC de temporários
│   └── video_engine.py           # Fachada retrocompatível agregando o motor de mídia
├── database/
│   ├── __init__.py               # Exportação das operações CRUD
│   └── db_manager.py             # SQLite WAL, migrations v3, CHECK constraints e índices
├── web/
│   ├── index.html                # SPA Dark Studio (Estúdio Master-Detail + Biblioteca)
│   ├── styles.css                # Tema escuro profissional e phone mockup 9:16
│   ├── app.js                    # Orquestrador SPA (ES Module)
│   └── js/
│       ├── utils.js              # Sanitização XSS, URLs seguras, debounce e formatadores
│       ├── api.js                # Cliente REST tipado
│       └── canvas_engine.js      # Canvas 60fps + blur offscreen otimizado (270×480)
├── prompts/
│   └── diretor_cortes.txt        # Diretrizes editoriais e regras de curadoria viral
├── tests/
│   ├── conftest.py               # Fixtures temporárias 100% isoladas
│   ├── test_database.py          # Migrations v3, índices, CHECK, FK, CRUD e fim do N+1
│   ├── test_security.py          # SSRF, LFI, XSS, CORS, CSRF, DNS rebinding e SHA-256
│   ├── test_core_media_ai.py     # Parser JSON, fallback IA, SRT, VideoCapture e GC
│   └── test_api.py               # Integração FastAPI TestClient, 202, 429, /health e /cleanup
├── docs/
│   ├── arquitetura.md            # Visão arquitetural atualizada (V3.1)
│   └── documentacao_tecnica.md   # Especificação técnica e contratos REST (V3.1)
└── data/                         # Dados locais (ignorado pelo Git)
    ├── cortador.db               # Banco SQLite (WAL, schema v3)
    ├── cache/                    # Transcrições privadas (fora do StaticFiles)
    ├── cortes/                   # Clipes finais renderizados (.mp4)
    ├── downloads/                # Mídias brutas baixadas
    ├── logs/                     # Logs rotativos (easykut.log)
    ├── models/                   # Modelo ONNX YuNet verificado por SHA-256
    └── temp/                     # Frames e clipes de prévia temporários (GC >24h)
```

---

## 🌿 Diretriz de Versionamento (Git)

- **Fluxo `main`-only:** Todo o desenvolvimento e manutenção são realizados diretamente na branch `main`.
