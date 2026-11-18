# ✂️ EasyKut — Criador Automático de Cortes Virais (v1.0)

O **EasyKut** é uma aplicação local em Python e Streamlit que automatiza a criação de cortes virais a partir de vídeos do YouTube. Ele combina download otimizado via **yt-dlp**, transcrição de alta velocidade via **Groq Whisper**, curadoria inteligente com **Google Gemini / Groq LLMs**, rastreamento facial (**OpenCV Face Tracking**) e renderização com legendas queimadas via **FFmpeg**.

---

## ✨ Funcionalidades Implementadas (v1.0)

- **Ingestão Inteligente (`yt-dlp`):** Extração de metadados, download de áudio leve (MP3 64kbps) para transcrição rápida e download do vídeo fonte em MP4 (720p ou 1080p).
- **Transcrição com Timestamps (`Groq Whisper`):** Uso do modelo `whisper-large-v3-turbo` gerando segmentos temporais precisos frase a frase.
- **Curadoria de Momentos Virais com IA:** Seleção automática dos melhores ganchos (20s a 90s), geração de títulos chamativos, nota de viralidade (`virality_score` de 0 a 10) e justificativa usando **Google Gemini** (`gemini-3.1-flash-lite`) ou **Groq LLM** (`openai/gpt-oss-120b` / `qwen/qwen3.8-27b`) com fallback automático entre provedores.
- **4 Modos de Enquadramento e Renderização (`FFmpeg` + `OpenCV`):**
  1. `face_tracking_9_16`: Corte vertical 9:16 com detecção facial OpenCV (compatível com OpenCV 4.x e 5.x), priorização do maior rosto em cena, suavização por Média Móvel (10 frames) e fallback automático para corte central.
  2. `blur_background`: Corte vertical 9:16 (1080x1920) com fundo ampliado e desfocado (`boxblur`) e vídeo original centralizado.
  3. `crop_center`: Recorte vertical 9:16 direto no centro da imagem.
  4. `horizontal_16_9`: Mantém a proporção horizontal original 16:9.
- **Legendas Sincronizadas Queimadas (`.srt`):** Geração automática de arquivos `.srt` recalculados a partir do marco `00:00:00,000` de cada corte e queimados no vídeo com tipografia adaptada para formatos verticais ou horizontais.
- **Histórico Persistente (`SQLite WAL`):** Banco de dados local (`data/cortador.db`) que armazena vídeos processados e seus respectivos cortes para visualização, download ou exclusão a qualquer momento.
- **Gestão de Armazenamento:** Limpeza automática ou manual dos arquivos temporários (`data/temp/`), preservando os cortes finais em `data/cortes/`.

---

## 🛠️ Pré-requisitos

1. **Sistema Operacional:** Windows 10/11 (recomendado para uso do inicializador `.bat`).
2. **Python:** Versão 3.10 ou superior com ambiente virtual (`venv`) criado na pasta `cortador_automatico/venv`.
3. **FFmpeg e FFprobe:** Instalados no sistema. O EasyKut detecta automaticamente binários no `PATH` do Windows ou nos diretórios:
   - `C:\ffmpeg\bin`
   - `C:\Ghost Toolbox\wget\Youtube-DLP`
   - `C:\ProgramData\chocolatey\bin`
4. **Chaves de API (Gratuitas):**
   - **Groq API Key:** Obtenha em [console.groq.com](https://console.groq.com)
   - **Gemini API Key:** Obtenha em [aistudio.google.com](https://aistudio.google.com)

### Configuração do `.env` e Dependências

Na pasta `cortador_automatico/`, crie um arquivo `.env` (baseado no `.env.example`) com suas chaves:

```env
GROQ_API_KEY=gsk_sua_chave_groq_aqui
GEMINI_API_KEY=AIzaSy_sua_chave_gemini_aqui
```

Para instalar as dependências no ambiente virtual:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

---

## 🚀 Como Iniciar com 1 Clique (`iniciar_easykut.bat`)

Para abrir o EasyKut no Windows sem precisar digitar comandos no terminal:

1. Dê um **duplo clique** no arquivo **`iniciar_easykut.bat`** (você também pode enviar um atalho dele para a Área de Trabalho).
2. O script executará automaticamente as seguintes etapas:
   - Entra no diretório raiz do projeto (`cd /d "%~dp0"`).
   - Verifica se a porta `8502` já está ocupada e encerra silenciosamente qualquer instância anterior travada.
   - Valida a presença do Python isolado em `venv\Scripts\python.exe`.
   - Inicia o servidor Streamlit diretamente pelo ambiente virtual (`venv\Scripts\python.exe -m streamlit run app.py --server.port 8502`) e abre o navegador em `http://localhost:8502`.

---

## 🖥️ Fluxo de Uso na Interface

1. **Painel Lateral (Configurações):**
   - Verifique no bloco **Diagnóstico do Sistema** se `Groq`, `Gemini` e `FFmpeg` estão com status verde (`Conectado` / `Pronto`).
   - Escolha o **Modelo de IA para Curadoria** (Google Gemini ou Groq LLM).
   - Selecione o **Formato e Enquadramento** desejado (Face Tracking 9:16, Fundo Desfocado 9:16, Recorte Central 9:16 ou Horizontal 16:9).
   - Marque ou desmarque a opção **Queimar legendas automáticas sincronizadas (.srt)**.
   - Defina a **Qualidade Máxima de Download** (`720p` ou `1080p`) e o **Limite Máximo de Cortes a Renderizar** (1 a 6).
2. **Aba `🚀 Processar Novo Vídeo`:**
   - Cole a URL de um vídeo público do YouTube e clique em **✨ Criar Cortes Automáticos**.
   - Acompanhe a barra de progresso nas 6 etapas (Metadados ➔ Áudio ➔ Transcrição Whisper ➔ Curadoria IA ➔ Vídeo HD ➔ Renderização FFmpeg).
   - Assista aos clipes gerados diretamente na galeria e clique em **⬇️ Baixar Corte (.mp4)**.
3. **Aba `📂 Histórico & Cortes Salvos`:**
   - Consulte todos os vídeos processados anteriormente, expanda para rever os cortes, baixe os arquivos `.mp4` ou exclua registros do banco de dados.

---

## 📁 Estrutura de Pastas

```text
cortador_automatico/
├── .env                          # Chaves de API locais (ignorado pelo Git)
├── .env.example                  # Exemplo de configuração de variáveis de ambiente
├── .gitignore                    # Arquivos e pastas ignorados pelo Git
├── app.py                        # Interface Web Streamlit (Dashboard principal)
├── iniciar_easykut.bat           # Inicializador Windows de 1 clique (porta 8502)
├── requirements.txt              # Lista de dependências Python (opencv-python<5, etc.)
├── README.md                     # Guia rápido do projeto
├── test_db.py                    # Script de teste do banco de dados SQLite
├── test_fase2.py                 # Script de teste CLI de transcrição e curadoria IA
├── test_fase3.py                 # Script de teste CLI do motor de vídeo e legendas
├── .streamlit/
│   └── config.toml               # Configurações do servidor Streamlit
├── core/
│   ├── __init__.py               # Exportação unificada do pacote core
│   ├── config.py                 # Descoberta do FFmpeg, diretórios e leitura do .env
│   ├── downloader.py             # Validação de URL, metadados e download via yt-dlp
│   ├── ai_apis.py                # Clientes Groq/Gemini, retries, fallback e parser JSON
│   └── video_engine.py           # Face tracking OpenCV, gerador SRT, cortes FFmpeg e limpeza
├── database/
│   ├── __init__.py               # Exportação das funções CRUD
│   └── db_manager.py             # Persistência SQLite (modo WAL) para vídeos e cortes
├── data/
│   ├── cortador.db               # Banco de dados SQLite local
│   ├── downloads/                # Áudios (.mp3) e vídeos fonte (.mp4) baixados
│   ├── cortes/                   # Cortes finais renderizados (.mp4)
│   ├── models/                   # Modelo ONNX YuNet para detecção facial no OpenCV 5
│   └── temp/                     # Arquivos temporários (.srt e mídias de teste)
├── docs/
│   ├── arquitetura.md            # Resumo arquitetural e fases concluídas
│   └── documentacao_tecnica.md   # Documentação técnica detalhada da versão 1.0
└── prompts/
    └── diretor_cortes.txt        # System prompt utilizado pelos LLMs para curadoria viral
```

---

## 🌿 Diretriz de Versionamento (Git)

- **Fluxo `main`-only:** Todo o desenvolvimento e manutenção são realizados diretamente na branch `main` (não criar branches de feature).
