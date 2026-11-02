# Teste REAL do fluxo "Reabrir como admin" sem usar mouse (ok com jogo em tela cheia).
# Exige UM clique do usuario em "Sim" no UAC. Mostra a linha do tempo do diagnostico.log.
# A copia administradora FICA ABERTA no final (um processo comum nao consegue encerrar um elevado): feche pelo X.
param([string]$Programa = (Join-Path $PSScriptRoot "..\atualizador_apps.py"), [int]$EsperaUacS = 120)
$Programa = (Resolve-Path $Programa).Path
$driver = Join-Path $PSScriptRoot "driver_admin_real.py"
$pyw = Join-Path (Split-Path (Get-Command python).Source) "pythonw.exe"
$log = Join-Path $env:APPDATA "AtualizadorApps\diagnostico.log"
$antes = @(Get-Process -ErrorAction SilentlyContinue | ForEach-Object Id)
$ja = @(Get-Process AtualizadorApps -ErrorAction SilentlyContinue)
if ($ja.Count) { "[teste] ABORTADO: ja existe o app aberto (pid $($ja.Id -join ',')). Feche-o antes."; exit 4 }
Remove-Item "$log*" -ErrorAction SilentlyContinue
$t0 = Get-Date
Start-Process $pyw -ArgumentList "`"$driver`" `"$Programa`"" | Out-Null
"[teste] driver iniciado. Em ~2 s o app chama _reabrir_admin e o UAC aparece: clique em SIM (espero ate ${EsperaUacS}s)"

function Linha($padrao) { if (Test-Path $log) { Select-String -Path $log -Pattern $padrao | Select-Object -Last 1 } }
# 1) ShellExecute retornou?
$ret = $null
for ($i = 0; $i -lt $EsperaUacS * 2 -and -not $ret; $i++) { Start-Sleep -Milliseconds 500; $ret = Linha "ShellExecute retornou" }
if (-not $ret) { "[teste] o ShellExecute nao retornou em ${EsperaUacS}s (UAC sem resposta)" }
else { "[teste] " + $ret.Line.Substring(0, [Math]::Min(150, $ret.Line.Length)) }

# 2) a copia administradora subiu?
$admin = $null
if ($ret -and $ret.Line -match "abriu") {
  for ($i = 0; $i -lt 120 -and -not $admin; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Path $log) { $admin = Select-String -Path $log -Pattern "interface pronta" | Where-Object { $_.Line -match "ADMIN" } | Select-Object -Last 1 }
  }
  "[teste] copia ADMIN pronta: " + [bool]$admin
  Start-Sleep -Seconds 4
}
"[teste] processos do app agora:"
Get-Process -ErrorAction SilentlyContinue | Where-Object { $antes -notcontains $_.Id -and $_.ProcessName -match "AtualizadorApps|^pythonw" } |
  Select-Object Id, ProcessName, StartTime, MainWindowTitle | Format-Table -AutoSize
"[teste] ===== diagnostico.log ====="
if (Test-Path $log) { Get-Content $log -Encoding utf8 }
