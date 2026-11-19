# ✂️ EasyKut — Criador Automático de Cortes Virais (v2.0)

O **EasyKut** é uma aplicação local em Python e Streamlit que automatiza a criação de cortes virais a partir de vídeos do YouTube. Ele combina download otimizado via **yt-dlp**, transcrição de alta velocidade via **Groq Whisper**, curadoria inteligente com **Google Gemini / Groq LLMs**, **Estúdio de Enquadramento em Tempo Real** (com prévias dinâmicas OpenCV) e renderização profissional com legendas queimadas via **FFmpeg**.

---

## ✨ Funcionalidades Implementadas (v2.0)

- **Ingestão Inteligente (`yt-dlp`):** Extração de metadados, download de áudio leve (MP3 48kbps mono) para transcrição ultrarrápida e download do vídeo fonte em MP4 (720p ou 1080p) apenas sob demanda.
- **Transcrição com Timestamps (`Groq Whisper`):** Uso do modelo `whisper-large-v3-turbo` gerando segmentos temporais precisos frase a frase.
- **Curadoria Viral com IA:** Seleção automática dos melhores ganchos (duração inteligente de 20s a 90s para shorts e 7+ min para vídeos longos), geração de títulos magnéticos, nota de viralidade (`virality_score` de 0 a 100) e justificativa usando **Google Gemini** ou **Groq LLM** com chaveamento e fallback automático.
- **Estúdio de Enquadramento em Tempo Real (4 Modos de Layout):**
  1. `vertical_blur`: Vertical 9:16 (1080x1920) com vídeo original centralizado sem distorção sobre fundo ampliado + `boxblur=20:5` + leve escurecimento (`eq=brightness=-0.15`), com aproximação suave ajustável (zoom de 1.0x a 1.3x).
  2. `horizontal_16_9`: Mantém a proporção horizontal 16:9 original widescreen sem cortes laterais (Stream Copy instantâneo quando sem legendas).
  3. `manual_crop`: Seleção milimétrica da caixa de corte 9:16 com sliders de Posição Horizontal (X %), Posição Vertical (Y %) e Tamanho (%) com prévia ao vivo sobre o frame real.
  4. `split_screen`: Dois recortes verticais empilhados (1080x960 cada) para podcasts e entrevistas, com controles independentes para Caixa Topo (Ciano) e Caixa Base (Laranja/Amarelo).
  5. `face_tracking_9_16`: Preset opcional com rastreamento facial dinâmico OpenCV (compatível com OpenCV 4.x e 5.x) e amortecimento por Média Móvel.
- **Prévia de Frame Real Instantânea:** Extração automática de frame por corte direto via streaming (`get_direct_stream_url`) com atualização instantânea ao alterar os tempos de início/fim.
- **Legendas Sincronizadas Queimadas (`.srt`):** Recálculo automático dos tempos iniciando em `00:00:00,000` com escape estrito de caracteres especiais (`:, ', [, ], ,`).
- **Histórico Persistente (`SQLite WAL`):** Abertura de pasta no Windows Explorer com 1 clique (`os.startfile`), busca por título, filtro por nota mínima (0–100), exclusão física opcional e download otimizado via file streams.

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

1. **Painel Lateral (Configurações Globais):**
   - Verifique no bloco **Diagnóstico do Sistema** se `Groq`, `Gemini` e `FFmpeg` estão com status verde (`OK` / `Pronto`).
   - Escolha o **Modelo de IA para Curadoria** (Google Gemini ou Groq LLM).
   - Defina o **Enquadramento Padrão Inicial**, a ativação de **Legendas Queimadas**, a **Qualidade Máxima** (`720p` ou `1080p`) e o número de cortes pré-marcados.
2. **Aba `🚀 Processar Novo Vídeo & Estúdio`:**
   - **Passo 1 (Analisar Vídeo & Extrair Prévias):** Cole a URL do YouTube e inicie a esteira. O app baixa o áudio leve, transcreve com Whisper, detecta momentos virais com IA e extrai automaticamente frames reais de prévia de cada corte com feedback em tempo real via `st.status`.
   - **Passo 1.5 (Estúdio de Enquadramento em Tempo Real):** Abra o card expansível de cada corte para ajustar títulos, tempos de início/fim, atualizar frame no instante exato e selecionar entre os 4 modos de layout (`Vertical Blur`, `Horizontal 16:9`, `Manual 9:16`, `Split-Screen` ou `Face Tracking`). Visualize a prévia gerada em tempo real com sliders interativos de posição, zoom e caixas de enquadramento.
   - **Passo 2 (Renderizar Cortes Selecionados):** Clique em **🎬 Renderizar Cortes Selecionados**. O app baixa o vídeo HD sob demanda e processa no FFmpeg com as dimensões e parâmetros exatos configurados no Estúdio.
   - Os clipes finalizados são exibidos na galeria persistente com reprodução e download seguro (sem apagar a tela no rerun).
3. **Aba `📂 Histórico & Cortes Salvos`:**
   - Botão **📂 Abrir Pasta de Cortes** para abrir `data/cortes/` diretamente no Explorador de Arquivos do Windows via `os.startfile`.
   - Filtros dinâmicos de **busca por título/URL** e **nota mínima de viralidade (0 a 100)**.
   - Reprodução dos clipes, download em streaming leve (sem sobrecarregar a memória RAM) e exclusão de vídeos com opção de apagar também os arquivos físicos do disco.

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
