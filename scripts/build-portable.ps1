<#
.SYNOPSIS
  Monta o ReplayFlow portátil numa pasta autossuficiente (nada é instalado no Windows).

.DESCRIPTION
  Resultado:
    <Target>\ReplayFlow.bat      lançador (duplo clique)
    <Target>\app\                código do ReplayFlow
    <Target>\python\             Python 3.12 embeddable + dependências
    <Target>\tools\ffmpeg\       ffmpeg.exe / ffprobe.exe
    <Target>\tools\tesseract\    tesseract.exe + tessdata\por.traineddata
    <Target>\models\             cache dos modelos Whisper (HF_HOME)
    <Target>\Sessions\           sessões gravadas
    <Target>\settings.ini        preferências da interface

  Rodar de novo atualiza o código e as dependências, sem baixar de novo o que já existe.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\build-portable.ps1 -Target F:\Pipeline-Capture -Model small
#>
param(
    [string]$Target = "F:\Pipeline-Capture",
    [string]$Model = "small",
    [string]$PythonVersion = "3.12.10",
    [string]$TesseractUrl = "https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest fica muito mais rápido
$Repo = Split-Path $PSScriptRoot -Parent

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Get-File($url, $out) { if (-not (Test-Path $out)) { Invoke-WebRequest $url -OutFile $out } }

$dirs = "app", "python", "tools\ffmpeg", "tools\tesseract", "models", "Sessions", "_downloads"
foreach ($d in $dirs) { New-Item -ItemType Directory -Force (Join-Path $Target $d) | Out-Null }
$dl = Join-Path $Target "_downloads"

Step "Copiando o código para $Target\app"
robocopy $Repo (Join-Path $Target "app") /MIR /XD .git .venv __pycache__ .pytest_cache /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy falhou (código $LASTEXITCODE)" }

$py = Join-Path $Target "python\python.exe"
if (-not (Test-Path $py)) {
    Step "Baixando Python $PythonVersion embeddable"
    $zip = Join-Path $dl "python-embed.zip"
    Get-File "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" $zip
    Expand-Archive $zip (Join-Path $Target "python") -Force
}
# ._pth: habilita site-packages e põe ..\app no sys.path (caminho relativo = portátil entre letras de unidade)
$pth = Get-ChildItem (Join-Path $Target "python") -Filter "python3*._pth" | Select-Object -First 1
$zipName = (Get-ChildItem (Join-Path $Target "python") -Filter "python3*.zip" | Select-Object -First 1).Name
Set-Content $pth.FullName -Encoding ascii -Value @($zipName, ".", "Lib\site-packages", "..\app", "import site")

if (-not (Test-Path (Join-Path $Target "python\Scripts\pip.exe"))) {
    Step "Instalando pip"
    Get-File "https://bootstrap.pypa.io/get-pip.py" (Join-Path $dl "get-pip.py")
    & $py (Join-Path $dl "get-pip.py") --no-warn-script-location
    if ($LASTEXITCODE) { throw "get-pip falhou" }
}

Step "Instalando dependências Python"
$deps = & $py -c @"
import tomllib
p = tomllib.load(open(r'$Repo\pyproject.toml', 'rb'))['project']
extras = p['optional-dependencies']
print('\n'.join(p['dependencies'] + [d for k in ('media', 'input', 'ui', 'obs') for d in extras[k]]))
"@
& $py -m pip install --no-warn-script-location --upgrade @($deps | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($LASTEXITCODE) { throw "pip install falhou" }

$ffmpegDir = Join-Path $Target "tools\ffmpeg"
if (-not (Test-Path (Join-Path $ffmpegDir "ffmpeg.exe"))) {
    Step "Baixando FFmpeg"
    $zip = Join-Path $dl "ffmpeg.zip"
    Get-File "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" $zip
    $tmp = Join-Path $dl "ffmpeg"
    Expand-Archive $zip $tmp -Force
    Get-ChildItem $tmp -Recurse -Include ffmpeg.exe, ffprobe.exe | Copy-Item -Destination $ffmpegDir
}

$tessDir = Join-Path $Target "tools\tesseract"
if (-not (Test-Path (Join-Path $tessDir "tesseract.exe"))) {
    $system = "C:\Program Files\Tesseract-OCR"
    if (Test-Path "$system\tesseract.exe") {
        Step "Copiando Tesseract já instalado"
        robocopy $system $tessDir /E /NFL /NDL /NJH /NJS /NP | Out-Null
    } else {
        Step "Instalando Tesseract dentro da pasta (o Windows pode pedir confirmação de administrador)"
        $exe = Join-Path $dl "tesseract-setup.exe"
        Get-File $TesseractUrl $exe
        # NSIS: /S silencioso, /D=destino (deve ser o último argumento, sem aspas)
        Start-Process $exe -ArgumentList "/S", "/D=$tessDir" -Wait -Verb RunAs
    }
    if (-not (Test-Path (Join-Path $tessDir "tesseract.exe"))) { throw "Tesseract não foi instalado em $tessDir" }
}
$por = Join-Path $tessDir "tessdata\por.traineddata"
if (-not (Test-Path $por)) {
    Step "Baixando idioma português do Tesseract"
    Invoke-WebRequest "https://github.com/tesseract-ocr/tessdata_fast/raw/main/por.traineddata" -OutFile $por
}

Step "Gerando ReplayFlow.bat"
Set-Content (Join-Path $Target "ReplayFlow.bat") -Encoding ascii -Value @'
@echo off
rem Lançador portátil: tudo relativo a esta pasta.
set "RF=%~dp0"
set "REPLAYFLOW_HOME=%RF:~0,-1%"
set "PATH=%RF%tools\ffmpeg;%RF%tools\tesseract;%PATH%"
set "TESSDATA_PREFIX=%RF%tools\tesseract\tessdata"
set "HF_HOME=%RF%models"
if "%~1"=="" (
  start "" "%RF%python\pythonw.exe" -m replayflow.app.main
) else (
  "%RF%python\python.exe" -m replayflow.app.main %*
)
'@

if ($Model) {
    Step "Baixando modelo Whisper '$Model' para $Target\models"
    & (Join-Path $Target "ReplayFlow.bat") download-model $Model
    if ($LASTEXITCODE) { throw "download do modelo falhou" }
}

Step "Verificando"
& (Join-Path $Target "ReplayFlow.bat") --help | Select-Object -First 1
& (Join-Path $Target "tools\ffmpeg\ffmpeg.exe") -version | Select-Object -First 1
& (Join-Path $Target "tools\tesseract\tesseract.exe") --list-langs 2>&1 | Select-String "^por$"
Remove-Item $dl -Recurse -Force
Write-Host ""
Write-Host "Pronto. Abra: $Target\ReplayFlow.bat" -ForegroundColor Green
