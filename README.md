# Atualizador de Apps

App para Windows que confere se há atualização para **todos os programas instalados** (via `winget`) e,
à parte, para **Python, Node.js e Java**, comparando com as versões publicadas em endoflife.date. Ele também
oferece as **novas versões principais** (ex.: Python 3.13 → 3.14), que o `winget upgrade` não oferece sozinho.

## O que ele faz
- Lista o que tem atualização e atualiza um a um ou tudo de uma vez.
- Confere sozinho em horários configuráveis (padrão 09:00, 13:00 e 17:00) e mostra um aviso discreto no canto da tela.
- Pode iniciar com o Windows, minimizado na bandeja (`--minimizado`).
- Registra um log de diagnóstico em `%APPDATA%\AtualizadorApps\diagnostico.log`, com vigia de travamento.

## Requisitos
Windows 10/11 com o **winget** (App Installer). Só usa a biblioteca padrão do Python.

## Rodar
```powershell
python atualizador_apps.py              # abre a janela
python atualizador_apps.py --minimizado # só a bandeja
```

## Gerar o .exe
```powershell
pyinstaller --onefile --windowed --name AtualizadorApps --icon app.ico --add-data "app.ico;." atualizador_apps.py
```

## Testes
`testes\rodar_testes.ps1` roda compilação, testes de lógica e de interface. Com um jogo aberto em tela cheia,
ele pula os testes que abrem janela (resultado `PARCIAL`).

## Versão atual
**1.0.1**. O número fica em `APP_VERSAO` no código e aparece no título da janela e no log. Para gerar o `.exe`, rode `gerar_exe.ps1`: ele grava a versão nas propriedades do arquivo e recusa repetir um número já usado.
