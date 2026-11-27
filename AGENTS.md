# Contexto de Desenvolvimento & Padrões de Engenharia — EasyKut Studio (V3.2)

Este documento define o contexto técnico oficial, a arquitetura, os invariantes de segurança, as convenções de código, o contrato de testes e o fluxo Git do **EasyKut Studio V3.2**. Todas as evoluções, correções e refatorações futuras devem seguir rigorosamente estas práticas.

---

## 1. Stack Oficial & Escopo

- **Runtime:** Python `3.11+` em ambiente virtual isolado (`cortador_automatico/venv`), Windows 10/11 (porta local `127.0.0.1:8502`).
- **Backend:** FastAPI + Uvicorn + Pydantic v2 (`server.py` + `schemas/` + `services/` + `core/` + `database/`).
- **Frontend:** SPA Vanilla sem bundler utilizando **10 ES Modules** (`web/index.html`, `web/styles.css`, `web/app.js`, `web/js/*.js`) e HTML5 Canvas 2D a 60fps, com **zero** handlers inline (`on*=`) e **zero** exportações em `window.*`.
- **Persistência:** SQLite 3 nativo em modo WAL (`data/cortador.db`, `PRAGMA user_version = 3`).
- **Mídia & IA:** `yt-dlp` (+ `Deno` em `%USERPROFILE%\.deno\bin` para EJS), FFmpeg/FFprobe via `subprocess`, OpenCV (Haar / YuNet ONNX), Groq Whisper (`whisper-large-v3-turbo`), Google Gemini e Groq LLMs.
- **Legado Aposentado:** `app.py`, `.streamlit/`, `streamlit` e `ffmpeg-python` foram removidos e **não devem ser reintroduzidos**.

---

## 2. Arquitetura em Camadas (DAG Estrito — Zero Dependências Circulares)

```text
web/ (10 ES Modules + Canvas 60fps + Timeline Interativa + Atalhos)
  ├── app.js (Bootstrap e event wiring via addEventListener — sem window.*)
  └── js/
        ├── utils.js (escapeHtml, formatação de tempo, getSafeCutDownloadUrl, toast)
        ├── api.js (16 wrappers HTTP REST + FormData upload + X-Total-Count)
        ├── state.js (Estado global, step tracker, pollJob, debounce PATCH)
        ├── canvas_engine.js (Render 60fps + blur offscreen + 5 layouts incl. Face Tracking)
        ├── canvas_interactions.js (Drag/resize proporcional de caixas no Canvas)
        ├── clip_player.js (Player HTML5 <video> ao vivo, seek, mute e loop rAF)
        ├── studio_view.js (Ingestão URL/upload, cortes manuais/duplicação, Trim Timeline, atalhos)
        ├── library_view.js (Histórico paginado, filtros, reabertura no Estúdio, exclusão)
        └── settings_modal.js (Status de chaves, /api/health disco/DB/fila e /api/cleanup)

server.py (Camada HTTP fina: rotas REST, middlewares, upload seguro, StaticFiles)
  ├── schemas/api_schemas.py (Contratos Pydantic v2 e validadores OpenAPI)
  ├── services/job_manager.py (Estado thread-safe, concorrência, TTL/LRU, semáforo)
  ├── services/media_service.py (Pipelines URL/local, workspace, renderização, fonte confiável)
  ├── database/db_manager.py (SQLite WAL, migrations, CRUD, consulta em lote sem N+1)
  └── core/
        ├── config.py (Paths, .env, FFmpeg, cookies)
        ├── logging_config.py (RotatingFileHandler + ContextVar X-Request-ID)
        ├── media_utils.py (Validação FFmpeg anti-SSRF/LFI, normalize_virality_score)
        ├── downloader.py (Allowlist pública, yt-dlp, timeouts, lock)
        ├── ai_apis.py (Groq/Gemini, retries, fallback, parser JSON)
        ├── subtitles.py (Geração .srt e escape libavfilter)
        ├── face_tracker.py (OpenCV + SHA-256 YuNet ONNX)
        ├── layout_filters.py (Strategy Pattern de builders FFmpeg por layout_mode)
        ├── media_renderer.py (ffprobe, prévias, orquestração cut_video, GC)
        └── video_engine.py (Fachada retrocompatível — sem lógica duplicada)
```

### Regras de Fronteira
1. **`server.py` é uma camada HTTP fina:** contratos Pydantic vivem em `schemas/api_schemas.py`; regras de domínio, pipelines assíncronos e estado em memória vivem em `services/`.
2. **Strategy Pattern em layouts FFmpeg:** novos modos de enquadramento devem ser registrados em `core/layout_filters.py` (`LAYOUT_FILTER_BUILDERS`), mantendo `core/media_renderer.py::cut_video` enxuto.
3. **Proibido importar para cima ou criar ciclos:** `core/downloader.py` nunca importa `video_engine`, `media_renderer` ou `layout_filters`; utilitários compartilhados ficam em `core/media_utils.py`.
4. **DRY na normalização de domínio:** toda conversão de `virality_score` (0–10 legado → 0–100) deve chamar `core.media_utils.normalize_virality_score`.

---

## 3. Padrões de API REST, Concorrência & Observabilidade

- **Operações Assíncronas:** endpoints que disparam background jobs (`POST /api/analyze`, `POST /api/upload`, `POST /api/render`) retornam `202 Accepted` com `response_model=JobAcceptedResponse`.
- **Workspace & Cortes Manuais:**
  - `GET /api/videos/{video_id}/workspace` carrega vídeo, cortes salvos e transcrição em cache (`data/cache/transcript_{id}.json`) reaquecendo o `JobManager` para reabertura instantânea no Estúdio.
  - `POST /api/cortes` retorna `201 Created` (`CorteMutationResponse`) para criação ou duplicação manual de cortes.
- **Contratos Pydantic (`schemas/api_schemas.py`):** usar `Literal`, `Field(ge/le/gt)`, `@model_validator` (ex.: `end_time > start_time`) e `response_model` explícito para documentação OpenAPI fidedigna.
- **Estado Thread-Safe (`JobManager`):**
  - Nunca retornar referências mutáveis de `jobs` — usar `job_manager.get_job_snapshot()` (`copy.deepcopy` sob lock).
  - Respeitar limites de concorrência (`MAX_CONCURRENT_ANALYZE_JOBS`, `MAX_CONCURRENT_RENDER_JOBS`, `preview_ffmpeg_semaphore`) retornando `HTTP 429` quando saturado (removendo eventuais uploads temporários em caso de rejeição).
  - Em falhas de background jobs, sempre gravar `status="error"` e `error=str(exc)` via `job_manager.mark_job_error(...)`.
  - Manter política TTL/LRU (`job_manager.prune()`) e GC de temporários (`max_age_hours=24.0` no startup + `POST /api/cleanup`).
- **Listagens Eficientes:** toda consulta pai-filho (`GET /api/history`) usa busca em lote (`list_videos_with_cortes`), paginação (`limit`/`offset` + cabeçalho `X-Total-Count`) e zero syscalls redundantes por item.
- **Observabilidade:**
  - `GET /api/health` valida SQLite (`SELECT 1`), FFmpeg, disco livre e jobs ativos (`200` ou `503`), exibido diretamente no Modal de Configurações.
  - Logs passam por `core/logging_config.py` (`data/logs/easykut.log`, 5 MB × 5 backups) com correlação `X-Request-ID` (HTTP) e `job_id` (workers) via `request_id_var`.

---

## 4. Padrões de Banco de Dados (SQLite WAL)

- **Conexão:** usar exclusivamente `get_connection()` e `_resolve_db_path()` (`PRAGMA foreign_keys = ON`, `WAL`, `timeout=15.0`, fechamento no `finally`).
- **SQL Seguro:** 100% parametrizado com placeholders `?`. Para cláusulas `IN (...)`, gerar placeholders dinamicamente a partir do tamanho da lista e anotar `# nosec B608`.
- **Migrations Versionadas:**
  - Incrementar `SCHEMA_VERSION` e adicionar bloco idempotente `if current_version < N:` em `_run_migrations(conn)`.
  - Atenção ao SQLite: `ALTER TABLE ... ADD COLUMN` não aceita default não-constante (como `CURRENT_TIMESTAMP`); adicionar a coluna sem default e fazer `UPDATE` backfill em seguida.
- **Integridade & Exclusão Segura:**
  - Preservar constraints `CHECK` (`duration >= 0`, `start_time >= 0`, `end_time > start_time`, `virality_score BETWEEN 0 AND 100`) e validações de domínio antes de persistir.
  - Em `delete_video` / `delete_corte`, executar e commitar o `DELETE` SQL **antes** de remover arquivos físicos; validar estritamente `resolved.is_relative_to(DATA_DIR)` e impedir remoção do arquivo `.db`.

---

## 5. Invariantes de Segurança (OWASP & Supply Chain)

1. **Segredos:** jamais ler, exibir em logs/respostas ou versionar `.env`, `APIS.txt`, `cookies.txt` ou `data/`. Consolidar credenciais apenas em `cortador_automatico/.env`.
2. **SSRF, LFI & Upload Local Seguro:**
   - URLs externas passam obrigatoriamente por `core.downloader.is_valid_video_url` (allowlist pública `youtube.com`, `youtu.be`, `vimeo.com`, `twitch.tv` + checagem de DNS/IP contra loopback/privados/metadata).
   - `POST /api/upload` exige extensão na allowlist (`.mp4`, `.mov`, `.mkv`, `.webm`, `.m4v`), leitura em streaming por chunks de 1 MB com teto de 1 GB (`413`), verificação de magic bytes (`_is_valid_video_magic_bytes`), nomeação via UUID em `data/downloads/` (imune a path traversal) e limpeza automática do arquivo em qualquer erro (`400`/`413`/`429`/`500`).
   - Alvos passados ao FFmpeg passam por `validate_ffmpeg_input_target` + `-protocol_whitelist file,http,https,tcp,tls`.
   - Rotas de prévia (`/api/frame`, `/api/preview-clip`) resolvem fontes exclusivamente via `resolve_trusted_video_source`: vídeos locais (`local://`) só são aceitos via `video_id` cadastrado **e** confinados dentro de `DATA_DIR_RESOLVED`; caminhos locais crus no campo `source` permanecem estritamente bloqueados (`400`) e caminhos absolutos do SO nunca são expostos na resposta.
3. **Hardening HTTP Local:** manter `ALLOWED_ORIGINS` (`127.0.0.1:8502` e `localhost:8502`, `allow_credentials=False`), `ALLOWED_HOSTS` (anti-DNS rebinding), checagem de `Origin` em rotas mutáveis `/api/*` (`403`) e headers `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`.
4. **Segregação de Cache:** arquivos de transcrição (`transcript_*.json`) ficam em `data/cache/` (nunca sob `StaticFiles` em `data/temp/`).
5. **Anti-XSS & Higiene no Frontend:** proibido interpolar dados de vídeo/IA/API (`title`, `justification`, `url`, `text`, `error`, `step`) em `innerHTML`, proibido usar atributos inline `on*=` no HTML e proibido exportar funções para `window.*`. Construir elementos com `document.createElement`, `textContent` e `addEventListener`, validando links de download com `getSafeCutDownloadUrl` (prefixo estrito `/data/cortes/`).
6. **Supply Chain:**
   - Manter versões pinadas (`==`) em `requirements.txt` e `requirements-dev.txt`.
   - Não executar `pip install -U` implícito em scripts de inicialização (atualizações apenas via flag opt-in explícita `--update-ytdlp`).
   - Qualquer modelo/binário baixado automaticamente (como `face_detection_yunet_2023mar.onnx`) exige verificação de checksum SHA-256 antes da gravação em disco.

---

## 6. Padrões de Mídia, IA, Frontend & Scripts Windows

- **Subprocessos no Windows:** sempre passar `text=True, encoding="utf-8", errors="replace"` e `timeout` explícito em `subprocess.run`.
- **Timeouts Externos:** Groq (`timeout=60.0`), Gemini (`types.HttpOptions(timeout=60000)`), `yt-dlp` (`socket_timeout=30`).
- **Recursos & Concorrência:** liberar `cv2.VideoCapture` em `try/finally`; proteger `download_video` com `_download_video_lock`; manter escape `libavfilter` de apóstrofo como `\'` (nunca `'\''`).
- **Frontend (`web/`):**
  - Organizar responsabilidades nos módulos dedicados em `web/js/` carregados por `<script type="module" src="/app.js">`.
  - Preservar o buffer offscreen `270×480` (`blurCanvas`) para manter `vertical_blur` suave a 60fps.
  - Manter guarda `(!canvasState.imgLoaded && !canvasState.isVideoReady)` no Canvas e encerramento garantido de `pollJob` para `completed`/`error`/`failed`/`404` e falhas consecutivas.
  - Manter atalhos de teclado (`Space`, `←/→`, `[`, `]`) ignorados automaticamente quando o foco estiver em `input`, `textarea` ou `select`.
- **Scripts `.bat`:** sempre portáveis usando `%~dp0` (proibido caminho absoluto de usuário).

---

## 7. Testes Isolados & Gate de Qualidade Obrigatório

- **Regra de Ouro de Testes:** toda suíte em `tests/` deve rodar 100% offline, determinística e isolada em `tmp_path` via `tests/conftest.py` (`isolated_env`). **Nunca** ler ou alterar `data/cortador.db` de produção em testes automatizados.
- **Novas features ou correções** devem incluir testes correspondentes em `tests/test_database.py`, `tests/test_security.py`, `tests/test_core_media_ai.py` ou `tests/test_api.py`.
- **Gate Local Obrigatório antes de qualquer commit** (espelha `.github/workflows/ci.yml`):

```powershell
cd cortador_automatico
.\venv\Scripts\python.exe -m ruff check core database schemas services server.py tests
.\venv\Scripts\python.exe -m bandit -c pyproject.toml -r core database schemas services server.py -ll
.\venv\Scripts\python.exe -m pytest
Get-ChildItem web\app.js, web\js\*.js | ForEach-Object { node --check $_.FullName }
```

---

## 8. Fluxo Git & Commits Modulares

- **Branch única (`main`):** trabalhar diretamente em `main` (não criar branches de feature).
- **Commits Modulares:** separar alterações por camada/responsabilidade em ordem de dependência usando Conventional Commits (`feat`, `fix`, `perf`, `refactor`, `test`, `chore`, `docs`) com escopo claro (`(database)`, `(core)`, `(schemas)`, `(services)`, `(api)`, `(web)`, `(ci)`).
- **Higiene pré-push:** confirmar `git status` limpo e validar que nenhum arquivo sensível (`.env`, `APIS.txt`, `cookies.txt`, `data/`, `venv/`) está incluído no diff.
