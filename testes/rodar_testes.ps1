# Executor de testes do Atualizador de Apps (usado pelo agente Tester).
# Roda: compilação, lógica, interface e sonda de clique. Grava um registro por execução em testes\logs\execucoes.jsonl.
#
# !! TRAVA DE JOGO: os testes de interface criam janelas do Tk. Mesmo FORA DA TELA, uma janela nova toma o foco e
# !! MINIMIZA um jogo em tela cheia (aconteceu com o usuário, repetidas vezes). Por isso, se houver QUALQUER janela
# !! cobrindo um monitor inteiro (jogo, vídeo em tela cheia), este script roda só compilação + lógica (sem janelas),
# !! avisa "PARCIAL" e sai com código 2. Só -Forcar ignora a trava (use apenas se o usuário liberou).
#   .\rodar_testes.ps1                      -> testa ..\atualizador_apps.py
#   .\rodar_testes.ps1 -Programa C:\x.py     -> testa outro arquivo
#   .\rodar_testes.ps1 -Visivel             -> mostra janelas/pop-ups e tira prints (evite: faz a tela piscar)
#   .\rodar_testes.ps1 -Forcar              -> roda interface/sonda mesmo com tela cheia (só com autorização do usuário)
param(
  [string]$Programa = (Join-Path $PSScriptRoot "..\atualizador_apps.py"),
  [string]$Motivo = "manual",
  [switch]$Visivel,
  [switch]$Forcar
)
$ErrorActionPreference = "Continue"
$Programa = (Resolve-Path $Programa).Path
$pasta = $PSScriptRoot
$logs = Join-Path $pasta "logs"; New-Item -ItemType Directory -Force $logs | Out-Null
$env:PYTHONIOENCODING = "utf-8"
if ($Visivel) { $env:TESTE_VISIVEL = "1" } else { Remove-Item Env:\TESTE_VISIVEL -ErrorAction SilentlyContinue }
$png = Join-Path $env:TEMP "toast_teste.png"

function Contar($linhas) {
  $ok = @($linhas | Where-Object { $_ -match "^OK\s" }).Count
  $falhas = @($linhas | Where-Object { $_ -match "^FALHA" })
  $res = @($linhas | Where-Object { $_ -match "^RESULTADO" } | Select-Object -Last 1)
  [pscustomobject]@{ ok = $ok; falhas = $falhas; resultado = $(if ($res) { $res[0] } else { "SEM RESULTADO (o teste nao terminou)" }) }
}

# Detecta janela(s) cobrindo um monitor inteiro (jogo/vídeo em tela cheia), sem criar janela nenhuma.
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System; using System.Collections.Generic; using System.Runtime.InteropServices;
public class TC {
  delegate bool Cb(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(Cb cb, IntPtr l);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [StructLayout(LayoutKind.Sequential)] struct RECT { public int L, T, R, B; }
  public static List<uint> CobremMonitor(int[] telas) {
    var pids = new List<uint>();
    EnumWindows((h, l) => {
      if (!IsWindowVisible(h)) return true;
      RECT w; GetWindowRect(h, out w); uint pid; GetWindowThreadProcessId(h, out pid);
      for (int i = 0; i < telas.Length; i += 4)
        if (w.L <= telas[i] + 1 && w.T <= telas[i + 1] + 1 && w.R >= telas[i + 2] - 1 && w.B >= telas[i + 3] - 1) { pids.Add(pid); break; }
      return true; }, IntPtr.Zero);
    return pids;
  }
}
"@
function Tela-Cheia-Em-Uso {
  $telas = @(); foreach ($s in [System.Windows.Forms.Screen]::AllScreens) { $telas += $s.Bounds.Left, $s.Bounds.Top, $s.Bounds.Right, $s.Bounds.Bottom }
  $ignorar = "explorer|SearchHost|ShellExperienceHost|StartMenuExperienceHost|TextInputHost|ApplicationFrameHost|dwm|LockApp|wallpaper\d*|SystemSettings|python|pythonw|AtualizadorApps|pwsh|powershell|WindowsTerminal|Code|claude"
  $nomes = foreach ($p in [TC]::CobremMonitor($telas) | Select-Object -Unique) {
    $n = (Get-Process -Id $p -ErrorAction SilentlyContinue).ProcessName
    if ($n -and $n -notmatch "^($ignorar)$") { $n }
  }
  @($nomes | Select-Object -Unique)
}

$t0 = Get-Date
$versao = (Get-FileHash $Programa -Algorithm SHA1).Hash.Substring(0, 10).ToLower()
$linhasArq = (Get-Content $Programa -Encoding utf8 | Measure-Object -Line).Lines
"== Tester: $Programa (versao $versao, $linhasArq linhas) =="
$telaCheia = Tela-Cheia-Em-Uso
$pularGui = ($telaCheia.Count -gt 0) -and -not $Forcar
if ($pularGui) { "!! TELA CHEIA em uso por: $($telaCheia -join ', '). Para NAO minimizar o jogo, nao vou abrir janela nenhuma: so compilacao + logica." }

# 1) compilacao
python -I -m py_compile $Programa 2>&1 | Out-Null
$compila = ($LASTEXITCODE -eq 0)
"1) compilacao: " + $(if ($compila) { "OK" } else { "ERRO" })
Remove-Item (Join-Path (Split-Path $Programa) "__pycache__") -Recurse -Force -ErrorAction SilentlyContinue

# 2) logica (funcoes puras, regras, historico, instancia unica)
$saidaLogica = @(python -u -I (Join-Path $pasta "teste_logica.py") $Programa 2>&1 | ForEach-Object { "$_" })
$lg = Contar $saidaLogica
"2) logica:    $($lg.ok) ok, $($lg.falhas.Count) falha(s) -> $($lg.resultado)"

$saidaGui = @(); $saidaSonda = @()
$gu = [pscustomobject]@{ ok = 0; falhas = @(); resultado = "NAO RODOU" }
$travou = @(); $sondaOk = $false; $sondaCob = ""
if ($pularGui) {
  "3) interface: PULADO (tela cheia em uso: $($telaCheia -join ', '))"
  "4) cliques:   PULADO (tela cheia em uso)"
} else {
  # 3) interface (janela, tabela, pop-ups, bandeja, admin simulado, vigia de travamento)
  $saidaGui = @(python -u -X faulthandler -I (Join-Path $pasta "teste_gui.py") $Programa $png 2>&1 | ForEach-Object { "$_" })
  $gu = Contar $saidaGui
  $travou = @($saidaGui | Where-Object { $_ -match "Fatal Python error|Traceback" })
  "3) interface: $($gu.ok) ok, $($gu.falhas.Count) falha(s) -> $($gu.resultado)"
  if ($travou.Count) { "   !! o teste de interface quebrou: " + ($travou -join " | ") }

  # 4) sonda de clique (SEM mouse): nenhum ponto da janela pode deixar o clique atravessar
  $saidaSonda = @(python -u -I (Join-Path $pasta "sonda_cliques.py") $Programa 2>&1 | ForEach-Object { "$_" })
  $sondaOk = [bool]($saidaSonda | Where-Object { $_ -match "RESULTADO SONDA: TUDO CLICAVEL" })
  $sondaCob = ($saidaSonda | Where-Object { $_ -match "^COBERTURA" } | Select-Object -First 1)
  "4) cliques:   " + $(if ($sondaOk) { "OK" } else { "FALHOU" }) + " -> " + $sondaCob
  if (-not $sondaOk) { $saidaSonda | Where-Object { $_ -match "FURADO|RESULTADO SONDA|buraco" } | ForEach-Object { "   $_" } }
}

$seg = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
$basicoOk = $compila -and $lg.resultado -match "TUDO OK"
$passou = $basicoOk -and -not $pularGui -and $gu.resultado -match "TUDO OK" -and -not $travou.Count -and $sondaOk
$reg = [ordered]@{
  quando = (Get-Date).ToString("s"); programa = (Split-Path $Programa -Leaf); versao = $versao; linhas = $linhasArq
  motivo = $Motivo; compila = $compila
  logica_ok = $lg.ok; logica_falhas = @($lg.falhas)
  interface_pulada = [bool]$pularGui; tela_cheia = @($telaCheia)
  gui_ok = $gu.ok; gui_falhas = @($gu.falhas); gui_quebrou = @($travou)
  sonda_cliques_ok = $sondaOk; sonda_cobertura = "$sondaCob"
  segundos = $seg; passou = $passou
}
($reg | ConvertTo-Json -Compress -Depth 4) | Add-Content (Join-Path $logs "execucoes.jsonl") -Encoding utf8
# saida completa da ultima rodada (para o analista olhar o detalhe)
($saidaLogica + "" + $saidaGui + "" + $saidaSonda) | Set-Content (Join-Path $logs "ultima_saida.txt") -Encoding utf8
foreach ($f in $lg.falhas + $gu.falhas) { "   $f" }
if ($passou) { "== RESULTADO: TUDO OK em ${seg}s (registro em testes\logs\execucoes.jsonl) ==" }
elseif ($basicoOk -and $pularGui) { "== RESULTADO: PARCIAL em ${seg}s: compilacao + logica OK; interface e cliques NAO testados (jogo em tela cheia). Rode de novo quando o jogo fechar. ==" }
else { "== RESULTADO: HA FALHAS em ${seg}s (registro em testes\logs\execucoes.jsonl) ==" }
exit $(if ($passou) { 0 } elseif ($basicoOk -and $pularGui) { 2 } else { 1 })
