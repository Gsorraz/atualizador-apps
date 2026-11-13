# Gera AtualizadorApps.exe (PyInstaller --onefile --windowed --uac-admin) com o número de APP_VERSAO nas propriedades.
# Uso:  powershell -File gerar_exe.ps1            -> gera em %TEMP% e troca o .exe da pasta (apaga o antigo)
#       powershell -File gerar_exe.ps1 -SemTrocar -> só gera em %TEMP%\build_atualizador\dist
# Antes: rodar testes\rodar_testes.ps1 (TUDO OK) e fechar o app se estiver aberto. Não encerra nenhum processo.
param([switch]$SemTrocar)
$ErrorActionPreference = 'Stop'
$pasta = Split-Path -Parent $MyInvocation.MyCommand.Path
$fonte = Join-Path $pasta 'atualizador_apps.py'
$m = Select-String -Path $fonte -Pattern '^APP_VERSAO\s*=\s*"(\d+)\.(\d+)\.(\d+)"' | Select-Object -First 1
if (-not $m) { throw 'APP_VERSAO (formato "1.0.1") nao encontrada em atualizador_apps.py' }
$v = $m.Matches[0].Groups; $maj = [int]$v[1].Value; $min = [int]$v[2].Value; $pat = [int]$v[3].Value
$versao = "$maj.$min.$pat"

# nao gerar dois .exe com o mesmo numero: compara com a ultima versao registrada
$ultima = Join-Path $pasta 'testes\logs\ultima_versao_exe.txt'
if (Test-Path $ultima) {
    $anterior = (Get-Content $ultima -Raw).Trim()
    if ($anterior -eq $versao) { throw "A versao $versao ja foi gerada. Aumente APP_VERSAO em atualizador_apps.py antes de gerar o .exe." }
}

$w = Join-Path $env:TEMP 'build_atualizador'
if (Test-Path $w) { Remove-Item $w -Recurse -Force }
New-Item -ItemType Directory $w | Out-Null
$vi = Join-Path $w 'versao.txt'
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($maj, $min, $pat, 0), prodvers=($maj, $min, $pat, 0), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041604B0', [
      StringStruct('CompanyName', 'Gian'),
      StringStruct('FileDescription', 'Atualizador de Apps'),
      StringStruct('FileVersion', '$versao'),
      StringStruct('InternalName', 'AtualizadorApps'),
      StringStruct('OriginalFilename', 'AtualizadorApps.exe'),
      StringStruct('ProductName', 'Atualizador de Apps'),
      StringStruct('ProductVersion', '$versao')])]),
    VarFileInfo([VarStruct('Translation', [0x0416, 1200])])
  ]
)
"@ | Set-Content -Path $vi -Encoding UTF8

$ico = Join-Path $pasta 'app.ico'
& python -m PyInstaller --noconfirm --onefile --windowed --uac-admin --icon $ico --add-data "$ico;." `
    --version-file $vi --name AtualizadorApps --distpath (Join-Path $w 'dist') --workpath (Join-Path $w 'work') `
    --specpath $w $fonte
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou ($LASTEXITCODE)" }
$novo = Join-Path $w 'dist\AtualizadorApps.exe'
if (-not (Test-Path $novo)) { throw 'o .exe nao foi gerado' }
$bytes = [System.IO.File]::ReadAllBytes($novo)
if (-not [System.Text.Encoding]::Latin1.GetString($bytes).Contains('requireAdministrator')) { throw 'manifesto de administrador ausente' }
$pv = (Get-Item $novo).VersionInfo.ProductVersion
if ($pv -ne $versao) { throw "versao nas propriedades ($pv) difere de APP_VERSAO ($versao)" }

if ($SemTrocar) { Write-Host "Gerado (sem trocar): $novo  v$versao"; exit 0 }
$destino = Join-Path $pasta 'AtualizadorApps.exe'
Remove-Item $destino, (Join-Path $pasta 'AtualizadorApps_novo.exe') -Force -ErrorAction SilentlyContinue
if (Test-Path $destino) { throw "nao consegui apagar $destino (o app esta aberto?)" }
Copy-Item $novo $destino
Set-Content -Path $ultima -Value $versao -Encoding ASCII
Write-Host "OK: AtualizadorApps.exe v$versao em $pasta"
