<#
.SYNOPSIS
  Setup do ReplayFlow no Windows: venv, dependências Python e (opcional) FFmpeg/Tesseract via winget.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -InstallTools -Model small
#>
param(
    [switch]$InstallTools,          # instala FFmpeg e Tesseract com winget
    [string]$Model = ""             # baixa um modelo whisper (tiny|base|small|medium|large-v3)
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Test-Cmd($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

# 1. Python 3.11+
$py = if (Test-Cmd "py") { "py -3" } elseif (Test-Cmd "python") { "python" } else { throw "Python 3.11+ não encontrado. Instale: winget install Python.Python.3.12" }
$ver = Invoke-Expression "$py -c `"import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')`""
if ([version]$ver -lt [version]"3.11") { throw "Python $ver encontrado; é preciso 3.11 ou superior." }
Write-Host "Python $ver OK"

# 2. venv + pacote
if (-not (Test-Path .venv)) { Invoke-Expression "$py -m venv .venv" }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[all,dev]"

# 3. Ferramentas externas
if ($InstallTools) {
    if (-not (Test-Cmd "ffmpeg")) { winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements }
    if (-not (Test-Path "C:\Program Files\Tesseract-OCR\tesseract.exe")) {
        winget install --id UB-Mannheim.TesseractOCR -e --accept-source-agreements --accept-package-agreements
    }
}
$tessdata = "C:\Program Files\Tesseract-OCR\tessdata"
if ((Test-Path $tessdata) -and -not (Test-Path "$tessdata\por.traineddata")) {
    Write-Host "Baixando idioma português do Tesseract (requer terminal como administrador)..."
    Invoke-WebRequest "https://github.com/tesseract-ocr/tessdata_fast/raw/main/por.traineddata" -OutFile "$tessdata\por.traineddata"
}

# 4. Modelo whisper (única ação com rede do app; depois tudo roda offline)
if ($Model) { & .\.venv\Scripts\replayflow.exe download-model $Model }

# 5. Diagnóstico
Write-Host ""
Write-Host ("FFmpeg:    " + $(if (Test-Cmd "ffmpeg") { "OK" } else { "AUSENTE (reabra o terminal após instalar ou use -InstallTools)" }))
Write-Host ("Tesseract: " + $(if ((Test-Cmd "tesseract") -or (Test-Path "C:\Program Files\Tesseract-OCR\tesseract.exe")) { "OK" } else { "AUSENTE" }))
Write-Host ""
Write-Host "Pronto. Rode: .\.venv\Scripts\replayflow.exe"
