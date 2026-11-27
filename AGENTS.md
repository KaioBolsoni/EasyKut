# Contexto de Desenvolvimento & Padrões de Engenharia — EasyKut Studio (V4.0)

Este documento define o contexto técnico oficial, a arquitetura, os invariantes de segurança, as convenções de código, o contrato de testes e o fluxo Git do **EasyKut Studio V4.0** (Ondas 1–4 concluídas). Todas as evoluções, correções e refatorações futuras devem seguir rigorosamente estas práticas.

---

## 1. Stack Oficial & Escopo

- **Runtime:** Python `3.11+` em ambiente virtual isolado (`cortador_automatico/venv`), Windows 10/11 (porta local `127.0.0.1:8502`).
- **Backend:** FastAPI + Uvicorn + Pydantic v2 (`server.py` + `schemas/` + `services/` + `core/` + `database/` — 24 módulos Python, DAG estrito).
- **Frontend:** SPA Vanilla sem bundler utilizando **11 ES Modules** (`web/index.html`, `web/styles.css`, `web/app.js`, `web/js/*.js`) e HTML5 Canvas 2D a 60fps, com **zero** handlers inline (`on*=`) e **zero** exportações em `window.*`.
- **Persistência:** SQLite 3 nativo em modo WAL (`data/cortador.db`, `PRAGMA user_version = 4`, tabelas `videos`, `cortes`, `brand_kits`, `job_queue`, `scheduled_posts`).
- **Mídia & IA:** `yt-dlp` (+ `Deno` para EJS), FFmpeg/FFprobe (`libx264` + GPU `h264_nvenc`/`h264_qsv`/`h264_amf` com fallback CPU), OpenCV (Haar / YuNet ONNX SHA-256), Groq Whisper (`word` + `segment` timestamps), Google Gemini e Groq LLMs.
- **Legado Aposentado:** `app.py`, `.streamlit/`, `streamlit` e `ffmpeg-python` foram removidos e **não devem ser reintroduzidos**.

---

## 2. Arquitetura em Camadas (DAG Estrito — Zero Dependências Circulares)

```text
web/ (11 ES Modules + Canvas 60fps + Timeline Interativa + Hub Pro)
  ├── app.js (Bootstrap e event wiring via addEventListener — sem window.*)
  └── js/
        ├── utils.js (escapeHtml, formatação de tempo, getSafeCutDownloadUrl, toast)
        ├── api.js (Wrappers HTTP REST completos V4.0 + uploads + X-Total-Count)
        ├── state.js (Estado global + opções Pro, pollJob com cancelamento, debounce PATCH)
        ├── canvas_engine.js (Render 60fps + blur offscreen + 6 layouts incl. Face Tracking & 1:1)
        ├── canvas_interactions.js (Drag/resize proporcional de caixas no Canvas)
        ├── clip_player.js (Player HTML5 <video> ao vivo, seek, mute e loop rAF)
        ├── studio_view.js (URL/upload, cortes manuais, editor inline de legenda, Timeline, atalhos)
        ├── library_view.js (Histórico paginado, Reabrir, ZIP, Highlight Reel, SEO IA, Publicar)
        ├── pro_panels.js (Modais Brand Kit, Trilhas Auto-Ducking, B-Roll, SEO, Reel e Publicação)
        └── settings_modal.js (Chaves, /api/health disco/DB/GPU/fila e /api/cleanup)

server.py (Camada HTTP fina: rotas REST V4.0, middlewares, uploads seguros, StaticFiles)
  ├── schemas/api_schemas.py (Contratos Pydantic v2 e validadores OpenAPI)
  ├── services/
  │     ├── job_manager.py (Estado thread-safe, concorrência, TTL/LRU, persistência SQLite, cancelamento)
  │     ├── media_service.py (Pipelines URL/local, workspace, render V4 + multi-export, fonte confiável)
  │     └── platform_service.py (SEO Kit IA, exportação ZIP, Highlight Reel job, agendamento)
  ├── database/
  │     ├── db_manager.py (SQLite WAL v4, migrations v1→v4, CRUD videos/cortes, consulta sem N+1)
  │     └── platform_repo.py (Repositório dedicado para brand_kits, job_queue e scheduled_posts)
  └── core/
        ├── config.py (Paths, .env, FFmpeg, cookies, chaves opcionais B-Roll/OAuth)
        ├── logging_config.py (RotatingFileHandler + ContextVar X-Request-ID)
        ├── media_utils.py (Validação FFmpeg anti-SSRF/LFI, normalize_virality_score)
        ├── downloader.py (Allowlist pública, yt-dlp, timeouts, lock)
        ├── ai_apis.py (Groq Whisper word+segment, curadoria Gemini/Groq, Kit SEO IA)
        ├── subtitles.py (Legendas .srt e Karaoke .ass palavra-por-palavra com 5 presets)
        ├── face_tracker.py (OpenCV + SHA-256 YuNet ONNX + keyframes dinâmicos X(t))
        ├── jump_cuts.py (Detecção de silêncios, concat sem pausas e remapeamento de legendas)
        ├── brand_overlay.py (Watermark PNG confinada, barra de progresso e Hook Banner)
        ├── hw_accel.py (Detecção GPU NVENC/QSV/AMF com fallback automático libx264)
        ├── audio_mastering.py (afftdn, loudnorm -14 LUFS e auto-ducking sidechaincompress)
        ├── broll_engine.py (Extração de keywords, biblioteca local e Pexels/Pixabay HTTPS allowlist)
        ├── highlight_reel.py (Compilação xfade + acrossfade confinada em DATA_DIR)
        ├── publishers.py (Conectores OAuth YouTube Shorts / TikTok / Instagram Reels)
        ├── layout_filters.py (Strategy Pattern FFmpeg: 16:9, blur, crop, face, split, 1:1)
        ├── media_renderer.py (ffprobe, prévias, FFmpeg cancelável Popen, composição cut_video, GC)
        └── video_engine.py (Fachada retrocompatível — sem lógica duplicada)
```

### Regras de Fronteira
1. **`server.py` é uma camada HTTP fina:** contratos vivem em `schemas/api_schemas.py`, orquestração em `services/`, persistência em `database/` e processamento em `core/`.
2. **Strategy Pattern em layouts FFmpeg:** novos modos de enquadramento entram em `core/layout_filters.py` (`LAYOUT_FILTER_BUILDERS`).
3. **Proibido criar ciclos de import:** `core/downloader.py` nunca importa `video_engine` ou `media_renderer`; utilitários comuns vivem em `core/media_utils.py`.
4. **DRY na normalização:** toda conversão de `virality_score` usa `core.media_utils.normalize_virality_score`.

---

## 3. Padrões de API REST, Concorrência & Observabilidade

- **Operações Assíncronas (`202 Accepted`):** `POST /api/analyze`, `POST /api/upload`, `POST /api/render`, `POST /api/videos/{id}/highlight-reel`.
- **Cancelamento & Persistência de Jobs:**
  - `GET /api/jobs` lista jobs ativos + histórico SQLite (`job_queue`).
  - `DELETE /api/jobs/{job_id}` marca cancelamento cooperativo e encerra processos `subprocess.Popen` ativos do FFmpeg.
  - No startup, `job_manager.recover_orphaned_jobs()` converte jobs `running` órfãos em `interrupted`.
- **Estado Thread-Safe (`JobManager`):** snapshots via `copy.deepcopy`, limites `429`, limpeza de temporários em caso de rejeição e política TTL/LRU (`POST /api/cleanup`).
- **Observabilidade:** `GET /api/health` valida SQLite (`SELECT 1`), FFmpeg, GPU encoder detectado (`gpu_encoder`), espaço livre em disco e fila de jobs; logs rotativos com `X-Request-ID`/`job_id`.

---

## 4. Padrões de Banco de Dados (SQLite WAL v4)

- **Conexão:** usar exclusivamente `db_manager.get_connection()` (`PRAGMA foreign_keys = ON`, `WAL`, `timeout=15.0`).
- **Repositórios:** operações de `videos`/`cortes` em `database/db_manager.py`; operações de `brand_kits`, `job_queue` e `scheduled_posts` em `database/platform_repo.py`.
- **SQL Seguro & Migrations Idempotentes:** 100% parametrizado com `?`; novas evoluções incrementam `SCHEMA_VERSION` com bloco `if current_version < N:` em `_run_migrations`.
- **Exclusão Segura:** `DELETE` SQL commitado antes da exclusão física; confinamento estrito em `DATA_DIR` e proteção do `.db`.

---

## 5. Invariantes de Segurança (OWASP & Supply Chain)

1. **Segredos:** jamais ler, exibir em logs/respostas ou versionar `.env`, `APIS.txt`, `cookies.txt` ou `data/`. O status OAuth/B-Roll expõe apenas booleanos (`connected`), nunca tokens.
2. **SSRF, LFI & Uploads Seguros:**
   - URLs externas passam por `is_valid_video_url` (allowlist + checagem DNS/IP). Chamadas B-Roll restringem hosts estritamente a `https://api.pexels.com` e `https://pixabay.com`.
   - Uploads (`/api/upload`, `/api/brand-kits/upload-logo`, `/api/music/upload`) validam extensão na allowlist, tamanho máximo, *magic bytes* binários, nomeação UUID/sanitizada e limpeza em erro.
   - Arquivos locais (vídeos, logos, músicas, clipes de Highlight Reel, ZIP, mídia de publicação) são confinados estritamente em `DATA_DIR_RESOLVED`.
3. **Hardening HTTP Local:** `ALLOWED_ORIGINS`, `ALLOWED_HOSTS`, verificação `Origin` em rotas mutáveis (`403`) e headers `nosniff`, `DENY`, `same-origin`.
4. **Segregação de Cache:** `transcript_*.json` fica exclusivamente em `data/cache/` (nunca sob `StaticFiles`).
5. **Anti-XSS & Higiene no Frontend:** 0 interpolações inseguras em `innerHTML`, 0 atributos `on*=` no HTML, 0 `window.*` em `app.js`.
6. **Supply Chain:** dependências pinadas (`==`), zero `pip install -U` implícito e SHA-256 obrigatório no modelo YuNet ONNX.

---

## 6. Testes Isolados, E2E & Gate de Qualidade Obrigatório

- **Regra de Ouro:** todos os testes em `tests/` (exceto `e2e`) rodam 100% offline e isolados em `tmp_path` via `isolated_env` (`tests/conftest.py`), sem tocar `data/cortador.db`.
- **E2E Playwright:** especificação em `tests/e2e/test_studio_e2e.py` marcada com `@pytest.mark.e2e` (executar com `pytest -m e2e` quando Playwright estiver instalado).
- **Gate Local Obrigatório antes de qualquer commit** (espelha `.github/workflows/ci.yml`):

```powershell
cd cortador_automatico
.\venv\Scripts\python.exe -m ruff check core database schemas services server.py tests
.\venv\Scripts\python.exe -m bandit -c pyproject.toml -r core database schemas services server.py -ll
.\venv\Scripts\python.exe -m pytest
Get-ChildItem web\app.js, web\js\*.js | ForEach-Object { node --check $_.FullName }
```

---

## 7. Fluxo Git & Commits Modulares

- **Branch única (`main`):** trabalhar diretamente em `main`.
- **Commits Modulares:** separar alterações por camada/responsabilidade em ordem de dependência usando Conventional Commits (`feat`, `fix`, `perf`, `refactor`, `test`, `chore`, `docs`, `ci`).
- **Higiene pré-commit:** confirmar `git status` limpo de arquivos sensíveis (`.env`, `APIS.txt`, `cookies.txt`, `data/`, `venv/`).
