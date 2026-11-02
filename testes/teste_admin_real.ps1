# Teste REAL do botão "Reabrir como admin" com o .exe da pasta do projeto.
# Abre o app, clica no botão (mouse sintético) e acompanha o diagnostico.log. Exige UM clique seu em "Sim" no UAC.
param([string]$Exe = "F:\Backups\Pythonprojeto\Atualização\AtualizadorApps.exe", [int]$EsperaUacS = 120)
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, UIntPtr e);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  public delegate bool Cb(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr p, Cb cb, IntPtr l);
  public static System.Collections.Generic.List<IntPtr> Filhos(IntPtr pai) { var l = new System.Collections.Generic.List<IntPtr>(); EnumChildWindows(pai, (h, x) => { l.Add(h); return true; }, IntPtr.Zero); return l; }
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}
"@
$log = Join-Path $env:APPDATA "AtualizadorApps\diagnostico.log"
$antes = @(Get-Process AtualizadorApps -ErrorAction SilentlyContinue | ForEach-Object Id)
"[teste] instancias pre-existentes: $($antes.Count)"

$t0 = Get-Date
Start-Process $Exe | Out-Null
$j = $null
for ($i = 0; $i -lt 60 -and -not $j; $i++) {
  Start-Sleep -Milliseconds 500
  $j = Get-Process AtualizadorApps -ErrorAction SilentlyContinue | Where-Object { $antes -notcontains $_.Id -and $_.MainWindowTitle -eq "Atualizador de Apps" } | Select-Object -First 1
}
if (-not $j) { "[teste] FALHA: janela nao abriu"; exit 2 }
"[teste] janela A abriu em {0:N1}s (pid {1})" -f ((Get-Date) - $t0).TotalSeconds, $j.Id
Start-Sleep -Seconds 3   # deixa a primeira verificacao terminar de montar a tela

$r = New-Object W+RECT
[W]::GetWindowRect($j.MainWindowHandle, [ref]$r) | Out-Null
"[teste] janela em x=$($r.L) y=$($r.T) largura=$($r.R - $r.L) altura=$($r.B - $r.T)"
# acha o botao pela propria janela: o controle de ~156x32 px mais acima e mais a direita do app (o "Reabrir como admin")
$melhor = $null
foreach ($h in [W]::Filhos($j.MainWindowHandle)) {
  $rr = New-Object W+RECT; [W]::GetWindowRect($h, [ref]$rr) | Out-Null
  $ww = $rr.R - $rr.L; $hh = $rr.B - $rr.T
  if ($ww -gt 100 -and $ww -lt 220 -and $hh -ge 28 -and $hh -le 36) {
    if (-not $melhor -or $rr.T -lt $melhor.T -or ($rr.T -eq $melhor.T -and $rr.L -gt $melhor.L)) { $melhor = $rr }
  }
}
if (-not $melhor) { "[teste] FALHA: nao achei o botao"; Get-Process AtualizadorApps -ErrorAction SilentlyContinue | Where-Object { $antes -notcontains $_.Id } | Stop-Process -Force; exit 5 }
$x = [int](($melhor.L + $melhor.R) / 2); $y = [int](($melhor.T + $melhor.B) / 2)
[W]::SetForegroundWindow($j.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 400
[W]::SetCursorPos($x - 15, $y) | Out-Null
Start-Sleep -Milliseconds 150
[W]::SetCursorPos($x, $y) | Out-Null
[W]::mouse_event(0x0001, 1, 0, 0, [UIntPtr]::Zero)     # movimento real de 1 px: faz o Windows entregar o "mouse entrou"
Start-Sleep -Milliseconds 300
$tClique = Get-Date
"[teste] CLIQUE em ($x,$y) as $($tClique.ToString('HH:mm:ss.fff'))"
[W]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 80; [W]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)

# 1o confirma que o CLIQUE chegou no app (rapido); so entao espera o UAC
$clicou = $false
for ($i = 0; $i -lt 16; $i++) {
  Start-Sleep -Milliseconds 500
  if ((Test-Path $log) -and (Select-String -Path $log -Pattern "CLIQUE em 'Reabrir como admin'" -Quiet)) { $clicou = $true; break }
}
if (-not $clicou) {
  "[teste] FALHA: o clique NAO chegou ao botao em 8s (nao vou esperar o UAC)"
  Get-Process AtualizadorApps -ErrorAction SilentlyContinue | Where-Object { $antes -notcontains $_.Id } | Stop-Process -Force -ErrorAction SilentlyContinue
  if (Test-Path $log) { Get-Content $log -Encoding utf8 }
  exit 3
}
"[teste] clique recebido pelo app. Agora aparece o UAC: clique em SIM (espero ate ${EsperaUacS}s)"

# acompanha o log ate o ShellExecute retornar
$achou = $false
for ($i = 0; $i -lt $EsperaUacS * 2; $i++) {
  Start-Sleep -Milliseconds 500
  if ((Test-Path $log) -and (Select-String -Path $log -Pattern "ShellExecute retornou" -Quiet)) { $achou = $true; break }
}
if (-not $achou) { "[teste] o botao nao chegou a chamar o ShellExecute em ${EsperaUacS}s (clique nao pegou ou UAC sem resposta)" }

# se abriu, espera a copia administradora subir
if ($achou) {
  $subiu = $false
  for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Milliseconds 500
    if (Select-String -Path $log -Pattern "interface pronta" | Where-Object { $_.Line -match "ADMIN" }) { $subiu = $true; break }
  }
  "[teste] copia ADMIN pronta: $subiu"
  Start-Sleep -Seconds 4
}
"[teste] processos meus agora:"
Get-Process AtualizadorApps -ErrorAction SilentlyContinue | Where-Object { $antes -notcontains $_.Id } | Select-Object Id, StartTime, MainWindowTitle | Format-Table -AutoSize
"[teste] ===== diagnostico.log ====="
if (Test-Path $log) { Get-Content $log -Encoding utf8 }
