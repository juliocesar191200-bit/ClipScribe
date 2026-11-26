# ClipScribe - instalador automatico (Windows 10/11)
# Instala o que falta (Python, ffmpeg, Whisper), cria o atalho na Area de Trabalho.
# Pode rodar de novo quantas vezes quiser: so completa o que estiver faltando.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$app = $PSScriptRoot
Set-Location $app
function Passo($t) { Write-Host ''; Write-Host "==> $t" -ForegroundColor Cyan }
function Falhou($t) { Write-Host ''; Write-Host "ERRO: $t" -ForegroundColor Red; exit 1 }

# 1) Python 3.10-3.12 (Whisper/torch funcionam melhor nessas versoes)
Passo 'Procurando Python...'
function Achar-Python {
    foreach ($v in '3.12','3.11','3.10') {
        try { $p = (& py -$v -c "import sys;print(sys.executable)" 2>$null); if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() } } catch {}
    }
    foreach ($v in '312','311','310') {
        $c = "$env:LOCALAPPDATA\Programs\Python\Python$v\python.exe"
        if (Test-Path $c) { return $c }
    }
    return $null
}
$py = Achar-Python
if (-not $py) {
    Passo 'Instalando Python 3.12 (pode levar uns minutos)...'
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    $py = Achar-Python
    if (-not $py) { Falhou 'Nao consegui instalar o Python. Instale o Python 3.12 em python.org (marque "Add to PATH") e rode de novo.' }
}
Write-Host "Python: $py"

# 2) ffmpeg (fica dentro da pasta do app, em .\ffmpeg)
if (-not (Test-Path "$app\ffmpeg\ffmpeg.exe")) {
    Passo 'Baixando ffmpeg...'
    $zip = "$env:TEMP\ffmpeg-clipscribe.zip"; $tmp = "$env:TEMP\ffmpeg-clipscribe"
    Invoke-WebRequest 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $zip
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive $zip $tmp -Force
    New-Item -ItemType Directory -Force "$app\ffmpeg" | Out-Null
    Get-ChildItem $tmp -Recurse -Include ffmpeg.exe,ffprobe.exe | Copy-Item -Destination "$app\ffmpeg" -Force
    Remove-Item $zip, $tmp -Recurse -Force
    if (-not (Test-Path "$app\ffmpeg\ffmpeg.exe")) { Falhou 'Nao consegui baixar o ffmpeg.' }
}
Write-Host 'ffmpeg: ok'

# 3) Ambiente Python proprio do app (.venv) + bibliotecas
if (-not (Test-Path "$app\.venv\Scripts\python.exe")) {
    Passo 'Criando ambiente do app...'
    & $py -m venv "$app\.venv"
}
$vpy = "$app\.venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip --quiet

$temNvidia = $false
try { nvidia-smi *> $null; if ($LASTEXITCODE -eq 0) { $temNvidia = $true } } catch {}
if ($temNvidia) {
    Passo 'Placa NVIDIA encontrada: instalando Whisper com GPU (download grande, ~2,5 GB)...'
    & $vpy -m pip install torch --index-url https://download.pytorch.org/whl/cu121
} else {
    Passo 'Sem placa NVIDIA: instalando Whisper no processador...'
    & $vpy -m pip install torch --index-url https://download.pytorch.org/whl/cpu
}
if ($LASTEXITCODE -ne 0) { Falhou 'Falha ao instalar o torch.' }
& $vpy -m pip install flask openai-whisper
if ($LASTEXITCODE -ne 0) { Falhou 'Falha ao instalar flask/whisper.' }

# 4) Baixa o modelo do Whisper agora (1,5 GB) pra primeira transcricao nao demorar
Passo 'Baixando o modelo de transcricao (medium, ~1,5 GB)...'
& $vpy -c "import whisper; whisper.load_model('medium', device='cpu'); print('modelo ok')"
if ($LASTEXITCODE -ne 0) { Falhou 'Falha ao baixar o modelo do Whisper.' }

# 5) Atalho na Area de Trabalho
Passo 'Criando atalho ClipScribe na Area de Trabalho...'
$desk = [Environment]::GetFolderPath('Desktop')
$s = (New-Object -ComObject WScript.Shell).CreateShortcut("$desk\ClipScribe.lnk")
$s.TargetPath = "$env:WINDIR\System32\wscript.exe"
$s.Arguments = "`"$app\launcher.vbs`""
$s.WorkingDirectory = $app
$s.IconLocation = "$app\clipscribe.ico"
$s.Save()

# 6) Claude Code (so pra traducao PT-BR da aba ADS Validados)
$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude -and -not (Test-Path "$HOME\.local\bin\claude.exe")) {
    Write-Host ''
    Write-Host 'Aviso: Claude Code nao encontrado. A transcricao funciona normal;' -ForegroundColor Yellow
    Write-Host 'so a traducao PT-BR (aba ADS Validados) precisa do Claude Code logado.' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'PRONTO! Abra o ClipScribe pelo icone na Area de Trabalho.' -ForegroundColor Green
