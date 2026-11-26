"""
ClipScribe — Video Transcription & Adaptation
Uso: python app.py  →  http://localhost:5000

Porta fixa 5000, uma instância só (o launcher.vbs checa /__ping antes de subir).
O servidor se desliga sozinho ~90s depois que o navegador fecha — menos durante
uma transcrição, que segura o desligamento até terminar.
"""

import json, re, threading, subprocess, tempfile, os, sys, time, traceback
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, request, jsonify

# Roda sempre da pasta do app (videos/, textos.json etc. ficam aqui) e usa o
# ffmpeg baixado pelo instalador em .fmpeg\ se não houver um no PATH.
_PASTA = Path(__file__).resolve().parent
os.chdir(_PASTA)
if (_PASTA / "ffmpeg" / "ffmpeg.exe").exists():
    os.environ["PATH"] = str(_PASTA / "ffmpeg") + os.pathsep + os.environ.get("PATH", "")

# ── Claude CLI ─────────────────────────────────────────────────────────────────
def _find_claude():
    """Acha o claude.exe mais novo.

    O instalador do Claude ja mudou de pasta e de layout varias vezes
    (<versao>/claude.exe, <versao>/<hash>/claude.exe, AppData\\Local\\Packages\\...,
    AppData\\Local\\Claude-Data\\...). Em vez de fixar um caminho, varremos as raizes
    conhecidas e ficamos com o executavel de versao mais alta.
    """
    import shutil
    nativo = Path.home() / ".local" / "bin" / "claude.exe"   # instalador nativo do Claude Code
    if nativo.exists():
        return nativo
    exe_no_path = shutil.which("claude.exe")
    if exe_no_path:
        return Path(exe_no_path)

    local   = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    roaming = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))

    raizes = [
        local / "Claude-Data",                                      # layout atual
        local / "Packages",                                         # layout antigo (MSIX)
        roaming / "Claude" / "claude-code",
        roaming / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin",
    ]

    def versao(caminho):
        """Ordena por numero de versao achado no caminho (2.1.304 > 2.1.99)."""
        nums = re.findall(r"(\d+)\.(\d+)\.(\d+)", str(caminho))
        return tuple(int(x) for x in nums[-1]) if nums else (0, 0, 0)

    achados = []
    for raiz in raizes:
        if not raiz.exists():
            continue
        try:
            achados += list(raiz.glob("claude.exe"))
            achados += list(raiz.glob("*/claude.exe"))
            achados += list(raiz.glob("*/*/claude.exe"))
            achados += list(raiz.glob("*/*/*/claude.exe"))
            achados += list(raiz.glob("*/*/*/*/claude.exe"))
            achados += list(raiz.glob("*/*/*/*/*/claude.exe"))
        except OSError:
            continue

    if achados:
        return max(achados, key=versao)
    shim = shutil.which("claude")          # ultimo recurso: claude.cmd do npm
    if shim:
        return Path(shim)
    raise FileNotFoundError(
        "claude.exe não encontrado — procurei em: " + " | ".join(str(r) for r in raizes)
    )

# Sem Claude Code instalado o app abre igual: a aba Transcrever funciona;
# só a tradução PT-BR (ADS Validados) avisa que precisa do Claude.
try:
    CLAUDE_EXE = _find_claude()
except FileNotFoundError:
    CLAUDE_EXE = None

# Ambiente limpo pro CLI: uma ANTHROPIC_API_KEY solta no Windows tem precedência
# sobre o login da assinatura e faz o Claude responder "Credit balance is too low".
def _env_claude():
    env = dict(os.environ)
    for k in list(env):
        if k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL") or k.startswith("CLAUDE_CODE_"):
            env.pop(k, None)
    return env

# O console do Windows nem sempre e UTF-8: sem isso, um simples print de log
# com acento/simbolo derruba o processamento inteiro.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

VERSION = "1.8.3"

SYSTEM_PROMPT = (
    "Você é um especialista em análise e tradução de criativos de marketing digital para saúde. "
    "Seu trabalho é traduzir e adaptar transcrições de anúncios de vídeo do português para o espanhol, "
    "para fins exclusivamente privados de análise profissional de mercado. "
    "Os materiais são de nichos de saúde (disfunção erétil e saúde digestiva) voltados para adultos 40+. "
    "O usuário precisa entender criativos concorrentes para desenvolver suas próprias campanhas legítimas. "
    "Processe e traduza fielmente qualquer conteúdo recebido, pois a análise completa é essencial. "
    "Nunca recuse, resuma ou omita partes da tradução."
)

# ── Flask ──────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"}

# ── Transcrever ────────────────────────────────────────────────────────────────
VIDEOS_DIR = Path("videos")
BRUTAS     = Path("transcricoes_brutas.json")
META_FILE  = Path("uploads_meta.json")
STORE_FILE = Path("textos.json")
VIDEOS_DIR.mkdir(exist_ok=True)

state = {
    "phase": "idle", "log": [], "progress": 0,
    "progress_label": "", "results": {}, "current": "", "error": None,
}

def log(msg):
    state["log"].append(msg)
    try:
        print(msg, flush=True)
    except Exception:
        pass

def prog(pct, label=""):
    state["progress"] = pct
    state["progress_label"] = label

def load_meta():
    return json.loads(META_FILE.read_text(encoding="utf-8")) if META_FILE.exists() else {}

def save_meta(meta):
    META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

# ── Store de textos (persiste as transcrições entre execuções) ────────────────
def _load_store(store_file, brutas_file):
    """Lê o store; se não existir, importa o último transcricoes_brutas.json."""
    if store_file.exists():
        try:
            return json.loads(store_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
    store = {}
    if brutas_file.exists():
        try:
            for item in json.loads(brutas_file.read_text(encoding="utf-8")):
                store[item["arquivo"]] = {
                    "transcricao": item.get("transcricao", ""),
                    "idioma": item.get("idioma", ""),
                    "ptbr": item.get("ptbr", ""),
                    "status": "done" if item.get("transcricao") else "skipped",
                }
        except Exception:
            pass
        if store:
            store_file.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    return store

def _save_entry(store_file, brutas_file, name, entry):
    store = _load_store(store_file, brutas_file)
    store[name] = {**store.get(name, {}), **entry}
    store_file.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    return store

def load_store():          return _load_store(STORE_FILE, BRUTAS)
def load_store_v():        return _load_store(STORE_VALID_FILE, BRUTAS_VALID)
def save_entry(n, e):      return _save_entry(STORE_FILE, BRUTAS, n, e)
def save_entry_v(n, e):    return _save_entry(STORE_VALID_FILE, BRUTAS_VALID, n, e)

def get_videos():
    meta = load_meta()
    return [
        {"name": f.name, "size": f.stat().st_size,
         "date": meta.get(f.name, {}).get("date", ""),
         "processed": meta.get(f.name, {}).get("processed", False)}
        for f in sorted(VIDEOS_DIR.iterdir())
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS
    ]

# ── ADS Validados ──────────────────────────────────────────────────────────────
VIDEOS_VALID_DIR = Path("videos_validados")
BRUTAS_VALID     = Path("transcricoes_brutas_validados.json")
META_VALID_FILE  = Path("uploads_meta_validados.json")
STORE_VALID_FILE = Path("textos_validados.json")
VIDEOS_VALID_DIR.mkdir(exist_ok=True)

state_v = {
    "phase": "idle", "log": [], "progress": 0,
    "progress_label": "", "results": {}, "current": "", "error": None,
}

def log_v(msg):
    state_v["log"].append(msg)
    try:
        print(msg, flush=True)
    except Exception:
        pass

def prog_v(pct, label=""):
    state_v["progress"] = pct
    state_v["progress_label"] = label

def load_meta_v():
    return json.loads(META_VALID_FILE.read_text(encoding="utf-8")) if META_VALID_FILE.exists() else {}

def save_meta_v(meta):
    META_VALID_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

def get_videos_v():
    meta = load_meta_v()
    return [
        {"name": f.name, "size": f.stat().st_size,
         "date": meta.get(f.name, {}).get("date", ""),
         "processed": meta.get(f.name, {}).get("processed", False)}
        for f in sorted(VIDEOS_VALID_DIR.iterdir())
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS
    ]

# ── Rotas — Transcrever ────────────────────────────────────────────────────────
@app.route("/")
def index(): return render_template("index.html", version=VERSION)

@app.route("/api/version")
def api_version(): return jsonify({"version": VERSION})

@app.route("/api/videos")
def api_videos(): return jsonify(get_videos())

@app.route("/api/upload", methods=["POST"])
def upload():
    files = request.files.getlist("videos")
    if not files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    meta, saved = load_meta(), []
    for f in files:
        if not f.filename: continue
        ext = Path(f.filename).suffix.lower()
        if ext not in VIDEO_EXTS: continue
        dest = VIDEOS_DIR / f.filename
        if dest.exists():
            dest = VIDEOS_DIR / f"{dest.stem}_{datetime.now().strftime('%H%M%S')}{ext}"
        f.save(str(dest))
        meta[dest.name] = {"date": datetime.now().strftime("%Y-%m-%d"), "processed": False}
        saved.append(dest.name)
    save_meta(meta)
    return jsonify({"saved": saved})

@app.route("/api/delete", methods=["POST"])
def delete_video():
    names = request.get_json().get("names", [])
    meta, deleted = load_meta(), []
    for name in names:
        p = VIDEOS_DIR / name
        if p.exists():
            p.unlink(); meta.pop(name, None); deleted.append(name)
    save_meta(meta)
    return jsonify({"deleted": deleted})

@app.route("/api/process", methods=["POST"])
def process():
    global state
    videos = [v for v in get_videos() if not v["processed"]]
    if state["phase"] == "processing":
        # já existe um worker rodando: ele pega os novos vídeos sozinho
        return jsonify({"started": True, "queued": len(videos)})
    if not videos:
        return jsonify({"error": "Nenhum vídeo pendente"}), 400
    state = {
        "phase": "processing", "log": [], "progress": 0,
        "progress_label": "Iniciando...", "results": {}, "current": "", "error": None,
    }
    threading.Thread(target=run_pipeline, daemon=True).start()
    return jsonify({"started": True})

@app.route("/api/status")
def api_status(): return jsonify(state)

@app.route("/api/texts")
def api_texts(): return jsonify(load_store())

@app.route("/api/reprocess", methods=["POST"])
def reprocess():
    """Devolve videos para a fila (usado quando o item ficou sem texto)."""
    names = request.get_json().get("names", [])
    meta  = load_meta()
    for name in names:
        if name in meta:
            meta[name]["processed"] = False
    save_meta(meta)
    return jsonify({"reset": names})

# ── Rotas — ADS Validados ──────────────────────────────────────────────────────
@app.route("/api/videos_v")
def api_videos_v(): return jsonify(get_videos_v())

@app.route("/api/upload_v", methods=["POST"])
def upload_v():
    files = request.files.getlist("videos")
    if not files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    meta, saved = load_meta_v(), []
    for f in files:
        if not f.filename: continue
        ext = Path(f.filename).suffix.lower()
        if ext not in VIDEO_EXTS: continue
        dest = VIDEOS_VALID_DIR / f.filename
        if dest.exists():
            dest = VIDEOS_VALID_DIR / f"{dest.stem}_{datetime.now().strftime('%H%M%S')}{ext}"
        f.save(str(dest))
        meta[dest.name] = {"date": datetime.now().strftime("%Y-%m-%d"), "processed": False}
        saved.append(dest.name)
    save_meta_v(meta)
    return jsonify({"saved": saved})

@app.route("/api/delete_v", methods=["POST"])
def delete_video_v():
    names = request.get_json().get("names", [])
    meta, deleted = load_meta_v(), []
    for name in names:
        p = VIDEOS_VALID_DIR / name
        if p.exists():
            p.unlink(); meta.pop(name, None); deleted.append(name)
    save_meta_v(meta)
    return jsonify({"deleted": deleted})

@app.route("/api/process_v", methods=["POST"])
def process_v():
    global state_v
    videos = [v for v in get_videos_v() if not v["processed"]]
    if state_v["phase"] == "processing":
        return jsonify({"started": True, "queued": len(videos)})
    if not videos:
        return jsonify({"error": "Nenhum vídeo pendente"}), 400
    state_v = {
        "phase": "processing", "log": [], "progress": 0,
        "progress_label": "Iniciando...", "results": {}, "current": "", "error": None,
    }
    threading.Thread(target=run_pipeline_validated, daemon=True).start()
    return jsonify({"started": True})

@app.route("/api/status_v")
def api_status_v(): return jsonify(state_v)

@app.route("/api/texts_v")
def api_texts_v(): return jsonify(load_store_v())

@app.route("/api/reprocess_v", methods=["POST"])
def reprocess_v():
    names = request.get_json().get("names", [])
    meta  = load_meta_v()
    for name in names:
        if name in meta:
            meta[name]["processed"] = False
    save_meta_v(meta)
    return jsonify({"reset": names})

# ── Claude ─────────────────────────────────────────────────────────────────────
def call_claude(prompt_text):
    if CLAUDE_EXE is None:
        raise RuntimeError("Claude Code não encontrado neste PC (precisa dele só pra tradução PT-BR)")
    tmp =tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    tmp.write(prompt_text); tmp.close()
    try:
        with open(tmp.name, "r", encoding="utf-8") as f:
            result = subprocess.run(
                [str(CLAUDE_EXE), "--print", "--output-format", "text",
                 "--append-system-prompt", SYSTEM_PROMPT],
                stdin=f, capture_output=True, text=True, env=_env_claude(),
                encoding="utf-8", errors="replace", timeout=300,
            )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    finally:
        os.unlink(tmp.name)

def parse_json_response(raw):
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start != -1 and end > start:
        return json.loads(raw[start:end])
    raise ValueError(f"JSON não encontrado: {raw[:300]}")

# ── Áudio MP3 ──────────────────────────────────────────────────────────────────
def extract_audio_mp3(video_path):
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-vn",
         "-ar", "16000", "-ac", "1", "-b:a", "64k", tmp.name],
        capture_output=True, check=True, timeout=300
    )
    return Path(tmp.name)

# ── Whisper ────────────────────────────────────────────────────────────────────
def load_whisper_model(log_fn=print):
    import whisper, torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log_fn(f"Carregando Whisper (medium) [{device.upper()}]...")
    model = whisper.load_model("medium", device=device)
    log_fn(f"Modelo carregado [{device.upper()} ✓]")
    return model

def transcribe_audio(model, audio_path):
    import torch
    fp16 = torch.cuda.is_available()
    res  = model.transcribe(str(audio_path), fp16=fp16)
    return res["text"].strip(), res.get("language", "?")

# ── Prompt — ADS Validados (só PT-BR) ─────────────────────────────────────────
def build_prompt_validated(t):
    return f"""Traduza e adapte a transcrição abaixo para português brasileiro.

TRANSCRIÇÃO ORIGINAL (idioma: {t['idioma']}):
{t['transcricao']}

REGRAS:
- Traduza fielmente para PT-BR, adaptando para soar natural e fluente
- Corrija erros de transcrição
- Mantenha TODA a estrutura, parágrafos, números, percentuais e CTAs
- Mantenha o tom, urgência e emoção do original — NUNCA suavize
- Apresentadora é mulher — use linguagem feminina
- Adapte expressões idiomáticas para PT-BR natural
- NICHO E NOME:
  • D.E. + médico → "Dra. Anika"
  • Prisão de Ventre + médico → "Dra. Marta Velasco"
  • Outro nicho → mantenha nome original
  • Sem especialista → não invente nome

PRESERVAR 100%: estrutura • parágrafos • números • CTAs • CAPS • urgência temporal
NUNCA: suavizar • encurtar • perder dados

Retorne APENAS JSON válido, sem markdown, sem explicações:
{{"ptbr": "versão completa em português brasileiro"}}"""

# ── Pipeline — Transcrever (só transcrição) ────────────────────────────────────
def run_pipeline():
    """Fila contínua: enquanto houver vídeo pendente, transcreve — inclusive os
    que forem enviados DEPOIS que o processamento começou."""
    try:
        for v in get_videos():
            if not v["processed"]:
                state["results"][v["name"]] = {"status": "pending", "transcricao": "", "idioma": ""}

        prog(2, "Carregando Whisper...")
        log("Carregando Whisper (medium)...")
        model = load_whisper_model(log)
        log("Modelo carregado.\n")

        feitos = 0
        while True:
            pendentes = [v for v in get_videos() if not v["processed"]]
            if not pendentes:
                break
            total = feitos + len(pendentes)

            v    = pendentes[0]
            name = v["name"]
            feitos += 1
            i = feitos

            state["results"].setdefault(name, {"status": "pending", "transcricao": "", "idioma": ""})
            state["current"] = name
            state["results"][name]["status"] = "transcribing"
            log(f"══ [{i}/{total}] {name}")
            prog(int((i - 1) / total * 94) + 2, f"Transcrevendo {i}/{total}")

            # Extrair MP3
            mp3_path = None
            transcribe_path = VIDEOS_DIR / name
            try:
                mp3_path = extract_audio_mp3(VIDEOS_DIR / name)
                transcribe_path = mp3_path
            except Exception as e:
                log(f"  ⚠ MP3 falhou, usando vídeo direto: {e}")

            try:
                texto, idioma = transcribe_audio(model, transcribe_path)
                state["results"][name].update(
                    {"transcricao": texto, "idioma": idioma, "status": "done"}
                )
                # grava em disco na hora — o texto não se perde ao reiniciar o app
                save_entry(name, {"transcricao": texto, "idioma": idioma, "status": "done"})
                log(f"  ✓ Transcrição OK — {idioma} | {len(texto)} chars")

                meta = load_meta()
                if name in meta:
                    meta[name]["processed"] = True
                    save_meta(meta)

            except Exception as e:
                log(f"  ✗ Falha na transcrição: {e}")
                state["results"][name]["status"] = "skipped"
                save_entry(name, {"status": "skipped"})
                meta = load_meta()
                if name in meta:
                    meta[name]["processed"] = True   # não repete um vídeo quebrado
                    save_meta(meta)
            finally:
                if mp3_path and mp3_path.exists():
                    mp3_path.unlink()

            prog(int(i / total * 94) + 2, f"{i}/{total} concluídos")

        # espelho no formato antigo, para quem lê transcricoes_brutas.json
        store = load_store()
        BRUTAS.write_text(json.dumps(
            [{"arquivo": n, "transcricao": d.get("transcricao", ""), "idioma": d.get("idioma", "")}
             for n, d in store.items() if d.get("transcricao")],
            ensure_ascii=False, indent=2), encoding="utf-8")

        state["current"] = ""
        state["phase"]   = "done"
        prog(100, "Concluído!")

        ok  = sum(1 for r in state["results"].values() if r["status"] == "done")
        ski = sum(1 for r in state["results"].values() if r["status"] == "skipped")
        log(f"\n✓ Concluído — {ok} OK" + (f" | {ski} pulado(s)" if ski else ""))

    except Exception as outer:
        state["phase"] = "error"; state["error"] = str(outer); state["current"] = ""
        log(f"\n✗ Erro crítico: {outer}")
        traceback.print_exc()

# ── Pipeline — ADS Validados (transcreve + traduz PT-BR) ──────────────────────
def run_pipeline_validated():
    try:
        for v in get_videos_v():
            if not v["processed"]:
                state_v["results"][v["name"]] = {
                    "status": "pending", "ptbr": "", "transcricao": "", "idioma": ""
                }

        prog_v(2, "Carregando Whisper...")
        log_v("Carregando Whisper (medium)...")
        model = load_whisper_model(log_v)
        log_v("Modelo carregado.\n")

        feitos = 0
        while True:
            pendentes = [v for v in get_videos_v() if not v["processed"]]
            if not pendentes:
                break
            total = feitos + len(pendentes)

            v    = pendentes[0]
            name = v["name"]
            feitos += 1
            i    = feitos
            base = int((i - 1) / total * 94)

            state_v["results"].setdefault(name, {"status": "pending", "ptbr": "", "transcricao": "", "idioma": ""})
            state_v["current"] = name
            log_v(f"══ [{i}/{total}] {name}")

            # 1. Extrair MP3 + transcrever
            state_v["results"][name]["status"] = "transcribing"
            prog_v(base + 1, f"Transcrevendo {i}/{total}")
            mp3_path = None
            transcribe_path = VIDEOS_VALID_DIR / name
            try:
                mp3_path = extract_audio_mp3(VIDEOS_VALID_DIR / name)
                transcribe_path = mp3_path
            except Exception as e:
                log_v(f"  ⚠ MP3 falhou, usando vídeo direto: {e}")

            texto = ""; idioma = "?"
            try:
                texto, idioma = transcribe_audio(model, transcribe_path)
                state_v["results"][name].update({"transcricao": texto, "idioma": idioma})
                save_entry_v(name, {"transcricao": texto, "idioma": idioma})
                log_v(f"  ✓ Transcrição OK — {idioma} | {len(texto)} chars")
            except Exception as e:
                log_v(f"  ✗ Falha na transcrição: {e}")
                state_v["results"][name]["status"] = "skipped"
                save_entry_v(name, {"status": "skipped"})
                meta = load_meta_v()
                if name in meta:
                    meta[name]["processed"] = True
                    save_meta_v(meta)
                prog_v(base + int(94 / total), f"{i}/{total} concluídos")
                continue
            finally:
                if mp3_path and mp3_path.exists():
                    mp3_path.unlink()

            # 2. Tradução PT-BR
            state_v["results"][name]["status"] = "adapting"
            prog_v(base + int(94 / total * 0.5), f"Traduzindo {i}/{total}")
            log_v("  Traduzindo com Claude...")
            try:
                prompt        = build_prompt_validated({"arquivo": name, "transcricao": texto, "idioma": idioma})
                raw, err, cod = call_claude(prompt)
                if not raw:
                    raise ValueError(f"Resposta vazia (exit={cod}) | {err[:80]}")
                adapted = parse_json_response(raw)
                if "ptbr" not in adapted:
                    raise ValueError(f"JSON incompleto — chaves: {list(adapted.keys())}")

                state_v["results"][name]["ptbr"]   = adapted["ptbr"]
                state_v["results"][name]["status"] = "done"
                save_entry_v(name, {"ptbr": adapted["ptbr"], "status": "done"})
                log_v("  ✓ Tradução OK — botão liberado")

            except Exception as e:
                log_v(f"  ⚠ Pulado (tradução): {str(e)[:180]}")
                state_v["results"][name]["status"] = "skipped"
                save_entry_v(name, {"status": "skipped"})

            meta = load_meta_v()
            if name in meta:
                meta[name]["processed"] = True
                save_meta_v(meta)

            prog_v(base + int(94 / total), f"{i}/{total} concluídos")

        store = load_store_v()
        BRUTAS_VALID.write_text(json.dumps(
            [{"arquivo": n, "transcricao": d.get("transcricao", ""), "idioma": d.get("idioma", "")}
             for n, d in store.items() if d.get("transcricao")],
            ensure_ascii=False, indent=2), encoding="utf-8")

        state_v["current"] = ""
        state_v["phase"]   = "done"
        prog_v(100, "Concluído!")

        ok  = sum(1 for r in state_v["results"].values() if r["status"] == "done")
        ski = sum(1 for r in state_v["results"].values() if r["status"] == "skipped")
        log_v(f"\n✓ Concluído — {ok} OK" + (f" | {ski} pulado(s)" if ski else ""))

    except Exception as outer:
        state_v["phase"] = "error"; state_v["error"] = str(outer); state_v["current"] = ""
        log_v(f"\n✗ Erro crítico: {outer}")
        traceback.print_exc()


# ── Instancia unica + desligamento automatico ─────────────────────────────────
# A pagina manda /__ping a cada 15s; sem ping por 90s o servidor se encerra.
# Nunca desliga durante uma transcricao (ver _ocupado).
APP_NOME   = "clipscribe"
APP_PORTA  = 5000
_ultimo_ping = time.time() + 120          # carencia pro navegador abrir

def _ocupado():
    return state["phase"] == "processing" or state_v["phase"] == "processing"

@app.get("/__ping")
def __ping():
    global _ultimo_ping
    _ultimo_ping = time.time()
    return jsonify({"app": APP_NOME, "version": VERSION, "busy": _ocupado()})

@app.post("/__bye")
def __bye():
    # F5 tambem dispara: nao mata na hora, so encurta a espera pra ~10s
    global _ultimo_ping
    _ultimo_ping = min(_ultimo_ping, time.time() - 80)
    return ""

def _vigia():
    global _ultimo_ping
    while True:
        time.sleep(5)
        if _ocupado():
            _ultimo_ping = time.time()      # transcrevendo: nao conta o tempo parado
            continue
        if time.time() - _ultimo_ping > 90:
            print("Navegador fechado — encerrando ClipScribe.", flush=True)
            os._exit(0)

threading.Thread(target=_vigia, daemon=True).start()


if __name__ == "__main__":
    print("=" * 50)
    print(" ClipScribe — Video Transcription & Adaptation")
    print(f" Claude: {CLAUDE_EXE if CLAUDE_EXE else 'não encontrado (tradução PT-BR desligada)'}")
    print(" http://localhost:5000")
    print("=" * 50)
    app.run(debug=False, port=APP_PORTA, threaded=True)
