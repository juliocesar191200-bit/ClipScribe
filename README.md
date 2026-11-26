# ClipScribe 🎙

Transcreve vídeos (criativos, anúncios) com o **Whisper**, direto no PC, em qualquer idioma. Na aba **ADS Validados** ele também traduz a transcrição para português usando o **Claude Code**.

## Instalar (jeito fácil)

1. Clique no botão verde **Code → Download ZIP** aqui em cima e extraia numa pasta fixa, ex.: `C:\ClipScribe`. (Não deixe dentro de Downloads se for apagar depois.)
2. Dentro da pasta, dê dois cliques em **`INSTALAR.bat`**.
   - Ele instala sozinho o que faltar: Python, ffmpeg, Whisper e o modelo de transcrição.
   - Demora: são uns **3 a 5 GB** de download na primeira vez (menos se não tiver placa NVIDIA). Deixe terminar até aparecer **PRONTO!** em verde.
3. Vai aparecer o ícone **ClipScribe** na Área de Trabalho. Abriu, ele abre sozinho no navegador (`http://localhost:5000`).

Se o Windows avisar "O Windows protegeu o computador", clique em **Mais informações → Executar assim mesmo**.

## Como usar

**Aba Transcrever**
- Arraste os vídeos (MP4, MOV, AVI, MKV, M4V, WEBM) e clique em **Processamento** para transcrever a fila.
- Pode jogar mais vídeos enquanto ele transcreve: ele pega os novos sozinho.
- **📋 Copiar Transcrição** em cada vídeo, ou **Copiar Todas Transcrições** de uma vez.
- **↻ Transcrever de novo** se algum ficar sem texto. **🗑 Apagar selecionados** limpa a fila.

**Aba ADS Validados**
- Mesma coisa, mas além de transcrever ele **traduz para PT-BR** (adaptado, sem suavizar a copy).
- Botões **🇧🇷 Copiar PT-BR** e **Copiar Todos PT-BR**.
- Essa parte precisa do **Claude Code instalado e logado** no PC. Sem ele, a transcrição funciona normal e só a tradução fica pulada.

**Detalhes**
- Com placa NVIDIA a transcrição usa a GPU (rápido). Sem placa, usa o processador (funciona, mais devagar).
- As transcrições ficam salvas: fechar o app não perde nada.
- Fechou o navegador, o programa se desliga sozinho em ~1,5 min (nunca no meio de uma transcrição).

---

## Instruções para o Claude (instalação automática)

Se você é um assistente (Claude Code) instalando isto no Windows do usuário:

```powershell
git clone https://github.com/juliocesar191200-bit/ClipScribe.git C:\ClipScribe
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ClipScribe\instalar.ps1
```

Sem git: baixe `https://github.com/juliocesar191200-bit/ClipScribe/archive/refs/heads/main.zip`, extraia em `C:\ClipScribe` (o conteúdo de `ClipScribe-main` direto na pasta) e rode o mesmo `instalar.ps1`.

- O script é idempotente: pode rodar de novo, só completa o que faltar. Rode-o em segundo plano (demora; downloads grandes de torch + modelo Whisper medium).
- Ele usa Python 3.10–3.12 (instala o 3.12 via winget se não houver), cria `.venv` na pasta, baixa o ffmpeg para `.\ffmpeg`, instala torch (CUDA 12.1 se `nvidia-smi` existir, senão CPU), `flask` e `openai-whisper`, pré-baixa o modelo `medium` e cria o atalho **ClipScribe** na Área de Trabalho (aponta para `launcher.vbs`).
- O app sobe em `http://localhost:5000`. Conferir: `Invoke-RestMethod http://127.0.0.1:5000/__ping` → `{"app":"clipscribe",...}`.
- A tradução PT-BR chama o CLI `claude --print` (procura `claude` no PATH e em `~\.local\bin\claude.exe`). O app remove `ANTHROPIC_API_KEY` do ambiente do subprocesso para usar o login da assinatura.
- Use `openai-whisper`, não `faster-whisper`: o ctranslate2 crasha em algumas máquinas.
