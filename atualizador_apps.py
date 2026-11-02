# -*- coding: utf-8 -*-
"""
Atualizador de Apps
-------------------
Verifica atualizações de todos os programas do Windows via winget e,
separadamente, checa Python, Node.js e Java contra as versões mais
recentes publicadas (endoflife.date), oferecendo também as NOVAS versões
principais, que o "winget upgrade" nunca oferece sozinho.

Avisos: em horários configuráveis (padrão 09:00, 13:00 e 17:00) o app confere em segundo
plano e, se houver algo para atualizar, mostra um pop-up discreto no canto da tela.
Com "Iniciar com o Windows" ele sobe minimizado (--minimizado) e cuida disso sozinho.

Requisitos: Windows 10/11 com winget (App Installer). Só biblioteca padrão.
Rodar:  python atualizador_apps.py   (ou  python atualizador_apps.py --minimizado)
Build:  pyinstaller --onefile --windowed --name AtualizadorApps --icon app.ico --add-data "app.ico;." atualizador_apps.py
"""

import ctypes
import ctypes.wintypes
import datetime
import glob
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

APP_NOME = "Atualizador de Apps"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "AtualizadorApps")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EOL_URL = "https://endoflife.date/api/{}.json"
WINGET_ACEITE = ["--accept-source-agreements", "--accept-package-agreements", "--disable-interactivity"]

# Avisos (pop-up discreto) em horários fixos
HORARIOS_PADRAO = ["09:00", "13:00", "17:00"]
TOLERANCIA_AVISO_MIN = 20     # se o PC estava ocupado/dormindo, ainda avisa até 20 min depois do horário
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NOME = "AtualizadorApps"

# Pastas onde JDKs/JREs costumam ficar dentro de Program Files
PASTAS_JAVA = ["Eclipse Adoptium", "Java", "Microsoft", "Zulu", "Amazon Corretto",
               "BellSoft", "Semeru", "Eclipse Foundation", "AdoptOpenJDK"]

# Códigos de saída conhecidos do winget
CODIGOS_WINGET = {
    0x8A15002B: "nenhuma atualização aplicável (já está em dia)",
    0x8A150061: "pacote já instalado na versão mais recente",
    0x8A150014: "pacote não encontrado no winget",
    0x8A150101: "o programa está aberto — feche e tente de novo",
    0x8A150102: "outra instalação em andamento — tente de novo depois",
    0x8A150109: "reinicie o computador para concluir",
    0xE0000001: "não consegui baixar o instalador oficial",
    0xE0000002: "o hash SHA-256 do arquivo baixado não confere (arquivo descartado, nada foi instalado)",
    0xE0000003: "a assinatura digital do arquivo não é a esperada (arquivo descartado, nada foi instalado)",
    0xE0000004: "o instalador travou e foi encerrado por tempo esgotado",
}
ERRO_DOWNLOAD, ERRO_HASH, ERRO_ASSINATURA, ERRO_TEMPO = 0xE0000001, 0xE0000002, 0xE0000003, 0xE0000004
CODIGOS_OK = {0}
CODIGOS_REINICIAR = {0x8A150109}   # instalou, só falta reiniciar: é sucesso (não "falhou")
# O winget "terminou sem erro", mas NÃO instalou nada (não há versão mais nova nas origens dele). Isso não é
# sucesso: ex. o Python 3.8 só tem instalador até a 3.8.10, e as 3.8.11+ (até a 3.8.20) existem só como código-fonte.
CODIGOS_SEM_EFEITO = {0x8A15002B, 0x8A150061}


# ----------------------------------------------------------------------------
# Utilitários
# ----------------------------------------------------------------------------
def decodificar(dados: bytes) -> str:
    for enc in ("utf-8", "cp850", "cp1252"):
        try:
            return dados.decode(enc)
        except UnicodeDecodeError:
            pass
    return dados.decode("utf-8", errors="replace")


def rodar(args, timeout=300, env=None):
    """Executa um comando e devolve stdout+stderr como texto (None se não existir)."""
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           stdin=subprocess.DEVNULL, timeout=timeout, creationflags=NO_WINDOW, env=env)
    except (FileNotFoundError, OSError):
        return None
    except subprocess.TimeoutExpired:
        return ""
    return decodificar(p.stdout) + "\n" + decodificar(p.stderr)


def eh_ruido(linha: str) -> bool:
    """Filtra spinners e barras de progresso do winget no log."""
    s = linha.strip()
    if not s or s in "-\\|/":
        return True
    return any(ch in s for ch in "█▒━■▓░")


def executar_streaming(cmd, log, prazo_s=3600):
    """Roda o comando mostrando a saída. Tem prazo total: um instalador travado não pode deixar o app 'ocupado' para
    sempre (ao estourar, encerra o processo e seus filhos e devolve ERRO_TEMPO)."""
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except OSError as e:
        log(f"Erro ao executar: {e}")
        return -1
    estourou = []

    def encerrar():
        estourou.append(True)
        log(f"Tempo esgotado ({prazo_s // 60} min): encerrando o processo.")
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=NO_WINDOW, timeout=30)
        except Exception:
            p.kill()
    vigia = threading.Timer(prazo_s, encerrar)
    vigia.daemon = True
    vigia.start()
    try:
        ultimo = None
        if p.stdout is not None:
            for bruta in iter(p.stdout.readline, b""):
                for parte in decodificar(bruta).split("\r"):
                    t = parte.rstrip()
                    if eh_ruido(t) or t == ultimo:
                        continue
                    log("  " + t.strip())
                    ultimo = t
        code = p.wait()
    finally:
        vigia.cancel()
    return ERRO_TEMPO if estourou else code


def descrever_codigo(code: int) -> str:
    c = code & 0xFFFFFFFF
    if c in CODIGOS_WINGET:
        return CODIGOS_WINGET[c]
    if (c & 0xFFFF0000) == 0x80190000:    # HRESULT de HTTP: 0x80190000 + status (0x193 = 403, 0x194 = 404...)
        return (f"o servidor do fabricante respondeu HTTP {c & 0xFFFF} ao baixar o instalador "
                f"({'recusou o download automático' if c & 0xFFFF == 403 else 'não entregou o arquivo'}); "
                f"o winget não consegue baixar este: baixe manualmente no site do fabricante")
    return f"código 0x{c:08X}"


def falha_de_download_http(code: int) -> bool:
    return ((code & 0xFFFFFFFF) & 0xFFFF0000) == 0x80190000


def vtuple(s: str):
    """'3.13.2' -> (3,13,2); trata Java 8 ('1.8.0_402' / '8u504-b01') e sufixos '+9'."""
    s = (s or "").strip().lstrip("vV")
    m = re.match(r"^1\.8\.0_(\d+)", s) or re.match(r"^8u(\d+)", s)
    if m:
        return (8, 0, int(m.group(1)))
    s = s.split("+")[0]
    nums = [int(x) for x in re.findall(r"\d+", s)[:4]]
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    return tuple(nums)


def menor(a: str, b: str) -> bool:
    return vtuple(a) < vtuple(b)


def data(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def em_eol(ciclo) -> bool:
    e = ciclo.get("eol")
    if isinstance(e, bool):
        return e
    d = data(e)
    return bool(d and d <= datetime.date.today())


def lancado(ciclo) -> bool:
    d = data(ciclo.get("releaseDate", ""))
    return d is None or d <= datetime.date.today()


def buscar_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "AtualizadorApps/1.0",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def eh_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ----------------------------------------------------------------------------
# Diagnóstico: log em arquivo + vigia que detecta quando a interface trava
# ----------------------------------------------------------------------------
DIAG_PATH = os.path.join(CONFIG_DIR, "diagnostico.log")
DIAG_MAX = 300_000
TRAVA_LIMITE_S = 1.5          # a interface "travou" se não responder por mais que isso
_ADMIN_CACHE = []


def diag(msg):
    """Uma linha no diagnostico.log com hora, PID, se este processo é administrador e a thread. Nunca levanta erro."""
    try:
        if not _ADMIN_CACHE:
            _ADMIN_CACHE.append(eh_admin())
        os.makedirs(CONFIG_DIR, exist_ok=True)
        if os.path.exists(DIAG_PATH) and os.path.getsize(DIAG_PATH) > DIAG_MAX:
            os.replace(DIAG_PATH, DIAG_PATH + ".1")
        agora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        nivel = "ADMIN" if _ADMIN_CACHE[0] else "user "
        with open(DIAG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{agora} pid={os.getpid():<6} {nivel} {threading.current_thread().name[:10]:<10} {msg}\n")
    except Exception:
        pass


def instalar_ganchos_de_erro():
    """Qualquer exceção não tratada (thread principal, threads ou callbacks do Tk) vai para o diagnostico.log."""
    def _principal(tipo, valor, tb):
        diag("EXCEÇÃO não tratada: " + "".join(traceback.format_exception(tipo, valor, tb)).strip())
        sys.__excepthook__(tipo, valor, tb)

    def _thread(args):
        diag(f"EXCEÇÃO na thread {args.thread.name if args.thread else '?'}: "
             + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)).strip())

    def _tk(self, tipo, valor, tb):
        diag("EXCEÇÃO em callback do Tk: " + "".join(traceback.format_exception(tipo, valor, tb)).strip())

    sys.excepthook = _principal
    threading.excepthook = _thread
    setattr(tk.Tk, "report_callback_exception", _tk)


def carregar_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return {}
    return cfg if isinstance(cfg, dict) else {}


def salvar_config(cfg):
    # grava num temporário e troca, para um crash no meio não corromper o config
    tmp = CONFIG_PATH + ".tmp"
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except OSError as e:
        diag(f"ERRO ao salvar o config (as escolhas podem se perder): {e}")


# ----------------------------------------------------------------------------
# Agenda dos avisos
# ----------------------------------------------------------------------------
RE_HORARIO = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_horarios(texto: str):
    """'9:00, 13:00 17:30' -> ['09:00', '13:00', '17:30']. Levanta ValueError se algo for inválido."""
    saida = set()
    for parte in re.split(r"[,\s;]+", (texto or "").strip()):
        if not parte:
            continue
        m = RE_HORARIO.match(parte)
        if not m:
            raise ValueError(parte)
        saida.add(f"{int(m.group(1)):02d}:{m.group(2)}")
    return sorted(saida)


def horario_devido(agora: datetime.datetime, horarios, disparados, tolerancia_min=TOLERANCIA_AVISO_MIN):
    """Devolve o horário (str 'HH:MM') que deve disparar agora, ou None.
    Dispara no minuto marcado e segue valendo por `tolerancia_min` caso o app estivesse ocupado."""
    for h in sorted(horarios):
        chave = (agora.date().isoformat(), h)
        if chave in disparados:
            continue
        alvo = datetime.datetime.combine(agora.date(), datetime.time(int(h[:2]), int(h[3:])))
        if 0 <= (agora - alvo).total_seconds() <= tolerancia_min * 60:
            return h
    return None


# ----------------------------------------------------------------------------
# Regras dos avisos (funções puras, sem interface) e histórico
# ----------------------------------------------------------------------------
# Textos do pop-up num lugar só (fácil de mudar/traduzir)
TEXTOS = {
    "um": "1 atualização disponível",
    "varios": "{n} atualizações disponíveis",
    "critico": "Atualização crítica",
    "critico_sub": "Sem suporte: {nomes}",
    "abrir": "Abrir",
    "depois": "Depois",
    "pular": "Pular versão",
}
ADIAR_MIN = 120                  # "Depois": volta a avisar uma única vez após 2 h
REPETICAO_MIN = 30               # não repete o mesmo aviso (mesma lista) dentro de 30 min
INTERVALO_MIN_AVISO_S = 120      # trava geral: nunca mostra dois pop-ups com menos de 2 min de diferença
TENTATIVAS_EM_USO = 4            # com reunião/apresentação aberta, espera 5 min × 4 (= a tolerância de 20 min)
ESPERA_EM_USO_MIN = 5
REVERIFICAR_HORAS = 2            # ao voltar para a janela, confere de novo se a última checagem for mais velha que isso
MAX_TENTATIVAS_FALHA = 3         # falha na checagem agendada: tenta de novo em 15, 30 e 45 min
PROGRAMAS_PAUSA_PADRAO = ["ms-teams", "teams", "zoom", "powerpnt", "obs64", "webexmta"]
HISTORICO_PATH = os.path.join(CONFIG_DIR, "historico.jsonl")
HISTORICO_MAX = 1000


def chave_item(item):
    return item.get("id") or item["nome"]


def versao_pulada(item, puladas) -> bool:
    """'Pular' vale só para a versão que estava disponível; quando sair uma mais nova, volta a avisar."""
    pulada = puladas.get(chave_item(item))
    return bool(pulada) and pulada == item.get("disponivel")


def pendencias(lista, ignorados, puladas):
    """O que vale avisar: tem atualização E dá para o app fazê-la (item "manual", sem ação, não vira aviso fixo)."""
    return [i for i in lista
            if chave_item(i) not in ignorados and not versao_pulada(i, puladas) and i.get("acao")
            and (i["status"].startswith("Atualiza") or i["tag"] == "novo")]


def marcar_fora_do_winget(item):
    """O winget não oferece/instala essa versão: some da lista e dos avisos (sem botão de atualizar).
    Guarda a página do fabricante, que só é oferecida (menu do botão direito) porque não se achou instalador."""
    item.update(status="Fora do winget", tag="eol" if item.get("tag") == "eol" else "ok", acao=None, fora_winget=True,
                marcado=False)
    pagina = item.get("pagina") or pagina_download(item)
    if pagina:
        item["pagina"] = pagina


def eh_critico(item) -> bool:
    """Versão sem suporte (EOL) que já tem atualização: o aviso fica fixo e não pode ser pulado."""
    return item["tag"] == "eol" and item["status"].startswith("Atualiza")


def assinatura(pend) -> str:
    base = "|".join(sorted(f"{chave_item(i)}={i.get('disponivel')}" for i in pend))
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def pausado(ate_iso, agora) -> bool:
    try:
        return bool(ate_iso) and agora < datetime.datetime.fromisoformat(str(ate_iso))
    except ValueError:
        return False


def proxima_manha(agora, hora=8):
    alvo = agora.replace(hour=hora, minute=0, second=0, microsecond=0)
    return alvo if alvo > agora else alvo + datetime.timedelta(days=1)


def normalizar_programas(entrada):
    partes = re.split(r"[,;\s]+", entrada) if isinstance(entrada, str) else list(entrada)
    saida = []
    for p in partes:
        p = re.sub(r"\.exe$", "", str(p).strip().lower())
        if p and p not in saida:
            saida.append(p)
    return saida


def programas_em_uso(programas, ativos):
    return [p for p in programas if p in ativos]


def registrar_historico(acao, item="", de="", para="", detalhe=""):
    reg = {"quando": datetime.datetime.now().isoformat(timespec="seconds"), "acao": acao,
           "item": item, "de": de, "para": para, "detalhe": detalhe}
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(HISTORICO_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(reg, ensure_ascii=False) + "\n")
        if os.path.getsize(HISTORICO_PATH) > 60_000:
            aparar_historico()
    except OSError:
        pass


def aparar_historico(maximo=HISTORICO_MAX):
    """Mantém só as últimas `maximo` linhas (gravação atômica)."""
    try:
        with open(HISTORICO_PATH, encoding="utf-8") as f:
            linhas = f.readlines()
        if len(linhas) > maximo:
            tmp = HISTORICO_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(linhas[-maximo:])
            os.replace(tmp, HISTORICO_PATH)
    except OSError:
        pass


def ler_historico(n=200):
    """Últimos `n` registros, do mais novo para o mais antigo (linhas quebradas são ignoradas)."""
    try:
        with open(HISTORICO_PATH, encoding="utf-8") as f:
            linhas = f.readlines()[-n:]
    except OSError:
        return []
    regs = []
    for linha in reversed(linhas):
        try:
            r = json.loads(linha)
            if isinstance(r, dict):
                regs.append(r)
        except ValueError:
            pass
    return regs


def formatar_historico(regs) -> str:
    rotulos = {"atualizado": "atualizado", "falhou": "FALHOU", "sem_efeito": "sem efeito", "adiado": "adiado", "pulado": "pulado",
               "ignorado": "ignorado", "dispensado": "dispensado", "aberto": "aberto", "expirou": "expirou",
               "pausado": "pausado", "retomado": "retomado"}
    linhas = []
    for r in regs:
        try:
            quando = datetime.datetime.fromisoformat(r.get("quando", "")).strftime("%d/%m %H:%M")
        except ValueError:
            quando = "--/-- --:--"
        versoes = f"  {r['de']} → {r['para']}" if r.get("de") or r.get("para") else ""
        extra = f"  ({r['detalhe']})" if r.get("detalhe") else ""
        linhas.append(f"{quando}  {rotulos.get(r.get('acao'), r.get('acao', '?')):<11} {r.get('item', '')}{versoes}{extra}")
    return "\n".join(linhas)


def comando_inicializacao() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimizado'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.isfile(pyw) else sys.executable
    return f'"{exe}" "{os.path.abspath(__file__)}" --minimizado'


def iniciar_com_windows_ativo() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            winreg.QueryValueEx(k, RUN_NOME)
        return True
    except OSError:
        return False


def definir_iniciar_com_windows(ativo: bool) -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if ativo:
                winreg.SetValueEx(k, RUN_NOME, 0, winreg.REG_SZ, comando_inicializacao())
            else:
                try:
                    winreg.DeleteValue(k, RUN_NOME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# ----------------------------------------------------------------------------
# Visual: tema escuro com vidro fosco (estilo iOS)
# ----------------------------------------------------------------------------
VIDRO = "#000000"         # cor-chave: onde ela aparecer, o Windows mostra o vidro desfocado
FUNDO_SOLIDO = "#0b0b0d"  # usado no lugar do vidro se o Windows não suportar (ou "vidro": false no config)
CARTAO = "#1c1c1e"
CARTAO_2 = "#242426"
CARTAO_LOG = "#141416"
BORDA = "#2c2c2e"
TEXTO = "#f5f5f7"
TEXTO_2 = "#a1a1a6"
TEXTO_3 = "#636366"
SELECAO = "#243449"
AZUL, AZUL_HOVER, AZUL_PRESS = "#0a84ff", "#409cff", "#0066d6"
VERDE, LARANJA, VERMELHO = "#30d158", "#ff9f0a", "#ff453a"
FONTE = "Segoe UI"
FONTE_SEMI = "Segoe UI Semibold"


def escala(w) -> float:
    """Fator de DPI do monitor (1.0 = 96 dpi)."""
    try:
        return max(1.0, w.winfo_fpixels("1i") / 96.0)
    except tk.TclError:
        return 1.0


def fonte(k, px, peso="normal", familia=FONTE):
    """Fonte em pixels (negativo = pixels no Tk), já escalada pelo DPI."""
    return (familia, -max(1, int(px * k)), peso)


def retangulo_redondo(cv, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def _mistura(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def _dist_segmento(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == 0 and dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5


def desenhar_caixa(estado, tam, fundo, marca=AZUL):
    """Checkbox estilo iOS desenhada ponto a ponto (3x3 amostras por pixel = bordas suaves), sem fonte e sem PIL.
    estado: "off" (só contorno) | "on" (azul com visto) | "parcial" (azul com traço). Devolve linhas de '#rrggbb'."""
    n, raio = 3, tam * 0.27
    borda = max(1.3, tam * 0.09)
    visto = [(0.27 * tam, 0.53 * tam), (0.43 * tam, 0.69 * tam), (0.75 * tam, 0.33 * tam)]
    traco = [(0.28 * tam, 0.5 * tam), (0.72 * tam, 0.5 * tam)]
    esp = max(1.4, tam * 0.075)          # metade da espessura do visto

    def caixa(x, y, margem):
        x0 = y0 = margem
        x1 = y1 = tam - margem
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        r = max(0.5, raio - (margem - 1))
        cx, cy = min(max(x, x0 + r), x1 - r), min(max(y, y0 + r), y1 - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    def cor_ponto(x, y):
        if estado in ("on", "parcial"):
            ponto = visto if estado == "on" else traco
            if any(_dist_segmento(x, y, *ponto[i], *ponto[i + 1]) <= esp for i in range(len(ponto) - 1)):
                return "#ffffff"
            return marca if caixa(x, y, 1.0) else fundo
        if caixa(x, y, 1.0) and not caixa(x, y, 1.0 + borda):
            return "#8e8e93"
        return fundo

    linhas = []
    for j in range(tam):
        linha = []
        for i in range(tam):
            rgb = [0, 0, 0]
            for sj in range(n):
                for si in range(n):
                    c = cor_ponto(i + (si + 0.5) / n, j + (sj + 0.5) / n)
                    for k_ in range(3):
                        rgb[k_] += int(c[1 + 2 * k_:3 + 2 * k_], 16)
            linha.append("#%02x%02x%02x" % tuple(v // (n * n) for v in rgb))
        linhas.append(linha)
    return linhas


def criar_imagem_caixa(master, estado, tam, fundo):
    """PhotoImage da checkbox (mantenha uma referência, senão o Tk apaga a imagem)."""
    img = tk.PhotoImage(master=master, width=tam, height=tam)
    img.put(" ".join("{" + " ".join(linha) + "}" for linha in desenhar_caixa(estado, tam, fundo)))
    return img


class _ACCENT(ctypes.Structure):
    _fields_ = [("State", ctypes.c_int), ("Flags", ctypes.c_int), ("Gradient", ctypes.c_uint), ("AnimId", ctypes.c_int)]


class _WCA(ctypes.Structure):
    _fields_ = [("Attr", ctypes.c_int), ("Data", ctypes.c_void_p), ("Size", ctypes.c_size_t)]


class _MARGENS(ctypes.Structure):
    _fields_ = [("esq", ctypes.c_int), ("dir", ctypes.c_int), ("topo", ctypes.c_int), ("base", ctypes.c_int)]


def _hwnd(win):
    win.update_idletasks()
    return ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()


def titulo_escuro(win):
    """Barra de título escura (Windows 10 1809+ / 11)."""
    try:
        v = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(_hwnd(win), 20, ctypes.byref(v), 4)
    except Exception:
        pass


def aplicar_vidro(root, ativo=True) -> bool:
    """Liga o vidro fosco do Windows e o título escuro. True = vidro ativo; False = usar fundo sólido escuro.
    Windows 11 22H2+: acrílico do sistema + tinta escura. Windows 10 1803+: acrílico antigo."""
    titulo_escuro(root)
    if not ativo:
        return False
    try:
        hwnd = _hwnd(root)
        build = sys.getwindowsversion().build
        if build >= 22621:
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_MARGENS(-1, -1, -1, -1)))
            tipo = ctypes.c_int(3)  # DWMSBT_TRANSIENTWINDOW = acrílico
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(tipo), 4) != 0:
                return False
            tinta = 0xEE0A0A0C      # ABGR: quase opaco e bem escuro. O acrílico do Windows clareia o que está atrás (efeito
                                    # "esbranquiçado"); com alfa baixo essa névoa aparecia, então a tinta cobre quase tudo
        elif build >= 17134:
            tinta = 0xEE0A0A0C
        else:
            return False
        a = _ACCENT(4, 2, tinta, 0)  # ACCENT_ENABLE_ACRYLICBLURBEHIND
        d = _WCA(19, ctypes.cast(ctypes.pointer(a), ctypes.c_void_p), ctypes.sizeof(a))
        if not ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(d)):
            return False
        # IMPORTANTE: NÃO usar -transparentcolor aqui. Com cor-chave, todo pixel da cor-chave vira "buraco" também para o
        # mouse: o clique atravessa a janela e cai no que está atrás (ex.: um jogo), que ainda rouba o foco.
        # Sem cor-chave a janela é normal para o mouse (todo pixel recebe clique) e o vidro vem só do quadro estendido.
        root.configure(bg=VIDRO)
        return True
    except Exception:
        return False


def aplicar_estilo(root, k):
    """Estilo ttk escuro: tabela, barra de rolagem fina e barra de progresso."""
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure("Treeview", background=CARTAO, fieldbackground=CARTAO, foreground=TEXTO, borderwidth=0,
                 relief="flat", rowheight=int(34 * k), font=fonte(k, 13))
    st.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # type: ignore[arg-type]  # sem moldura
    st.map("Treeview", background=[("selected", SELECAO)], foreground=[("selected", TEXTO)])
    st.configure("Treeview.Heading", background=CARTAO_2, foreground=TEXTO_2, relief="flat", borderwidth=0,
                 bordercolor=CARTAO_2, lightcolor=CARTAO_2, darkcolor=CARTAO_2, padding=(int(8 * k), int(9 * k)),
                 font=fonte(k, 12, "bold"))
    st.map("Treeview.Heading", background=[("active", CARTAO_2)], foreground=[("active", TEXTO)])
    st.layout("Glass.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [  # type: ignore[arg-type]
        ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    st.configure("Glass.Vertical.TScrollbar", background="#48484a", troughcolor=CARTAO, bordercolor=CARTAO,
                 lightcolor="#48484a", darkcolor="#48484a", gripcount=0, width=int(9 * k))
    st.map("Glass.Vertical.TScrollbar", background=[("active", "#636366")], lightcolor=[("active", "#636366")],
           darkcolor=[("active", "#636366")])
    st.configure("Glass.Horizontal.TProgressbar", background=AZUL, troughcolor="#3a3a3c", bordercolor="#3a3a3c",
                 lightcolor=AZUL, darkcolor=AZUL, thickness=max(3, int(4 * k)))


class Botao(tk.Canvas):
    """Botão em pílula. `primario=True` é o azul de destaque; aceita configure(state=..., text=...)."""

    def __init__(self, master, text, command=None, primario=False, fundo=VIDRO):
        self.k = escala(master)
        self._texto, self._cmd, self._prim = text, command, primario
        self._estado, self._sobre, self._press = "normal", False, False
        self._fonte = tkfont.Font(family=FONTE_SEMI if primario else FONTE, size=-int(13 * self.k))
        self._alt = int(32 * self.k)
        self._larg = self._calc_larg()
        super().__init__(master, width=self._larg, height=self._alt, bg=fundo, highlightthickness=0, bd=0)
        self.bind("<Enter>", lambda e: self._mudar(sobre=True))
        self.bind("<Leave>", lambda e: self._mudar(sobre=False, press=False))
        self.bind("<ButtonPress-1>", lambda e: self._mudar(sobre=True, press=True))
        self.bind("<ButtonRelease-1>", self._soltou)
        self._desenhar()

    def _calc_larg(self):
        return self._fonte.measure(self._texto) + int(36 * self.k)

    def _mudar(self, sobre=None, press=None):
        if sobre is not None:
            self._sobre = sobre
        if press is not None and self._estado == "normal":
            self._press = press
        if press:
            diag(f"botão '{self._texto[:28]}': mouse APERTADO (estado={self._estado})")
        self._desenhar()

    def _soltou(self, e):
        # decide pelo ponto onde o botão do mouse foi solto, não pelo evento "mouse entrou": se o cursor já
        # estava parado em cima do botão (ex.: segundo clique sem mover o mouse), esse evento nunca chegou
        dentro = 0 <= e.x <= self._larg and 0 <= e.y <= self._alt
        acionar = self._estado == "normal" and self._press and dentro
        diag(f"botão '{self._texto[:28]}': mouse SOLTO em ({e.x},{e.y}) dentro={dentro} apertado={self._press} "
             f"estado={self._estado} -> {'ACIONA' if acionar else 'ignora'}")
        self._press = False
        self._desenhar()
        if acionar and self._cmd:
            self._cmd()

    def _desenhar(self):
        ativo = self._estado == "normal"
        if self._prim:
            cor = (AZUL_PRESS if self._press else AZUL_HOVER if self._sobre else AZUL) if ativo else "#1c3350"
            txt = "#ffffff" if ativo else "#6f86a3"
        else:
            cor = ("#48484a" if self._press else "#3a3a3c" if self._sobre else BORDA) if ativo else CARTAO
            txt = TEXTO if ativo else TEXTO_3
        self.delete("all")
        retangulo_redondo(self, 1, 1, self._larg - 1, self._alt - 1, (self._alt - 2) // 2, fill=cor, outline="")
        self.create_text(self._larg // 2, self._alt // 2, text=self._texto, fill=txt, font=self._fonte)
        tk.Canvas.configure(self, cursor="hand2" if ativo else "arrow")

    def configure(self, cnf=None, **kw):
        if cnf:
            kw = dict(cnf, **kw)
        estado, texto = kw.pop("state", None), kw.pop("text", None)
        if estado is not None:
            self._estado = estado
        if texto is not None:
            self._texto = texto
            self._larg = self._calc_larg()
            tk.Canvas.configure(self, width=self._larg)
        if kw:
            tk.Canvas.configure(self, **kw)
        self._desenhar()

    config = configure


class Interruptor(tk.Canvas):
    """Chave liga/desliga estilo iOS ligada a uma BooleanVar; clicar no texto também alterna."""

    def __init__(self, master, texto, variable, command=None, fundo=VIDRO):
        k = self.k = escala(master)
        self._var, self._cmd, self._texto = variable, command, texto
        self._fonte = tkfont.Font(family=FONTE, size=-int(13 * k))
        self._tw, self._th = int(40 * k), int(24 * k)
        larg = self._tw + int(10 * k) + self._fonte.measure(texto) + int(4 * k)
        super().__init__(master, width=larg, height=self._th + int(4 * k), bg=fundo, highlightthickness=0,
                         bd=0, cursor="hand2")
        self._pos = 1.0 if variable.get() else 0.0
        variable.trace_add("write", lambda *a: self._animar())
        self.bind("<Button-1>", self._alternar)
        self._desenhar()

    def _alternar(self, e=None):
        self._var.set(not self._var.get())
        if self._cmd:
            self._cmd()

    def _animar(self):
        try:
            alvo = 1.0 if self._var.get() else 0.0
            if abs(self._pos - alvo) < 0.05:
                self._pos = alvo
                self._desenhar()
                return
            self._pos += (alvo - self._pos) * 0.45
            self._desenhar()
            self.after(16, self._animar)
        except tk.TclError:
            pass

    def _desenhar(self):
        k, tw, th = self.k, self._tw, self._th
        y0 = int(2 * k)
        self.delete("all")
        retangulo_redondo(self, 0, y0, tw, y0 + th, th // 2, fill=_mistura("#3a3a3c", VERDE, self._pos), outline="")
        d = th - int(4 * k)
        x = int(2 * k) + self._pos * (tw - th)
        self.create_oval(x, y0 + int(2 * k), x + d, y0 + int(2 * k) + d, fill="#ffffff", outline="")
        self.create_text(tw + int(10 * k), y0 + th // 2, text=self._texto, anchor="w", fill=TEXTO, font=self._fonte)


class Cartao(tk.Canvas):
    """Painel de cantos arredondados. Coloque os widgets em `.dentro`."""

    def __init__(self, master, fundo=VIDRO, cor=CARTAO, borda=BORDA, raio=16, **kw):
        super().__init__(master, bg=fundo, highlightthickness=0, bd=0, **kw)
        k = escala(master)
        self._cor, self._borda, self._raio, self._m = cor, borda, int(raio * k), int(6 * k)
        self.dentro = tk.Frame(self, bg=cor)
        self._janela = self.create_window(self._m, self._m, window=self.dentro, anchor="nw")
        self.bind("<Configure>", self._ajustar)

    def _ajustar(self, ev):
        w, h = ev.width, ev.height
        self.delete("fundo")
        retangulo_redondo(self, 1, 1, w - 2, h - 2, self._raio, fill=self._cor, outline=self._borda, tags="fundo")
        self.tag_lower("fundo")
        self.itemconfigure(self._janela, width=max(1, w - 2 * self._m), height=max(1, h - 2 * self._m))


class Caixa(tk.Toplevel):
    """Diálogo modal escuro (substitui messagebox/simpledialog, que ficam brancos no tema escuro)."""

    def __init__(self, parent, titulo, texto, ok="OK", cancelar=None, entrada=None, erro=False):
        super().__init__(parent)
        self.withdraw()
        k = escala(parent)
        self.resultado = None
        self._entrada = entrada is not None
        self.title(titulo)
        self.configure(bg=CARTAO)
        self.resizable(False, False)
        try:
            self.iconbitmap(caminho_icone())
        except Exception:
            pass
        try:
            if parent.state() == "normal":
                self.transient(parent)
        except tk.TclError:
            pass
        corpo = tk.Frame(self, bg=CARTAO)
        corpo.pack(fill="both", expand=True, padx=int(26 * k), pady=(int(22 * k), int(12 * k)))
        tk.Label(corpo, text=titulo, bg=CARTAO, fg=VERMELHO if erro else TEXTO, anchor="w",
                 font=fonte(k, 18, familia=FONTE_SEMI)).pack(fill="x")
        linhas = texto.count("\n") + 1
        if linhas > 12 or len(texto) > 700:
            caixa = tk.Frame(corpo, bg=CARTAO)
            caixa.pack(fill="both", expand=True, pady=(int(10 * k), 0))
            txt = tk.Text(caixa, width=62, height=min(linhas, 13), wrap="word", bg=CARTAO_2, fg=TEXTO_2,
                          relief="flat", bd=0, highlightthickness=0, padx=int(12 * k), pady=int(8 * k),
                          font=fonte(k, 13), selectbackground=SELECAO)
            txt.insert("1.0", texto)
            txt.configure(state="disabled")
            sb = ttk.Scrollbar(caixa, orient="vertical", command=txt.yview, style="Glass.Vertical.TScrollbar")
            txt.configure(yscrollcommand=sb.set)
            txt.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
        else:
            tk.Label(corpo, text=texto, bg=CARTAO, fg=TEXTO_2, justify="left", anchor="w",
                     wraplength=int(470 * k), font=fonte(k, 13)).pack(fill="x", pady=(int(8 * k), 0))
        self._campo = None
        if self._entrada:
            self._campo = tk.Entry(corpo, bg=CARTAO_2, fg=TEXTO, insertbackground=TEXTO, relief="flat",
                                   highlightthickness=1, highlightbackground=BORDA, highlightcolor=AZUL,
                                   font=fonte(k, 14), selectbackground=SELECAO, selectforeground=TEXTO)
            self._campo.insert(0, entrada or "")
            self._campo.pack(fill="x", ipady=int(6 * k), pady=(int(14 * k), 0))
        rodape = tk.Frame(self, bg=CARTAO)
        rodape.pack(fill="x", padx=int(26 * k), pady=(int(8 * k), int(22 * k)))
        Botao(rodape, ok, self._ok, primario=True, fundo=CARTAO).pack(side="right")
        if cancelar:
            Botao(rodape, cancelar, self._cancelar, fundo=CARTAO).pack(side="right", padx=(0, int(10 * k)))
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancelar())
        self.protocol("WM_DELETE_WINDOW", self._cancelar)
        titulo_escuro(self)
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        try:
            if parent.state() == "normal":
                cx, cy = parent.winfo_rootx() + parent.winfo_width() // 2, parent.winfo_rooty() + parent.winfo_height() // 2
            else:
                raise tk.TclError
        except tk.TclError:
            cx, cy = self.winfo_screenwidth() // 2, self.winfo_screenheight() // 2
        self.geometry(f"+{max(0, cx - w // 2)}+{max(0, cy - h // 2)}")
        self.deiconify()
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        (self._campo or self).focus_force()

    def _ok(self):
        self.resultado = self._campo.get() if self._campo is not None else True
        self.destroy()

    def _cancelar(self):
        self.resultado = None if self._entrada else False
        self.destroy()


def perguntar(parent, titulo, texto, ok="Confirmar", cancelar="Cancelar") -> bool:
    c = Caixa(parent, titulo, texto, ok=ok, cancelar=cancelar)
    parent.wait_window(c)
    return bool(c.resultado)


def avisar(parent, titulo, texto, erro=False):
    parent.wait_window(Caixa(parent, titulo, texto, ok="OK", erro=erro))


def pedir_texto(parent, titulo, texto, inicial=""):
    c = Caixa(parent, titulo, texto, ok="Salvar", cancelar="Cancelar", entrada=inicial)
    parent.wait_window(c)
    return c.resultado if isinstance(c.resultado, str) else None


def caminho_icone():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "app.ico")


_ICONES = []   # mantém os ícones carregados vivos enquanto o app roda


def _carregar_icone(tamanho):
    """Carrega o app.ico no tamanho pedido (o .ico traz vários; sem o arquivo, devolve None)."""
    try:
        u32 = ctypes.windll.user32
        u32.LoadImageW.restype = ctypes.c_void_p
        u32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_uint]
        h = u32.LoadImageW(None, caminho_icone(), 1, tamanho, tamanho, 0x10)   # IMAGE_ICON, LR_LOADFROMFILE
        if h:
            _ICONES.append(h)
        return h
    except Exception:
        return None


def definir_icone_janela(win):
    """Ícone nítido na barra de tarefas/Alt+Tab (grande) e no título (pequeno), cada um no seu tamanho de DPI."""
    try:
        u32 = ctypes.windll.user32
        u32.SendMessageW.restype = ctypes.c_void_p
        u32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
        hwnd = _hwnd(win)
        grande = _carregar_icone(u32.GetSystemMetrics(11))     # SM_CXICON
        pequeno = _carregar_icone(u32.GetSystemMetrics(49))    # SM_CXSMICON
        if grande:
            u32.SendMessageW(hwnd, 0x80, 1, grande)            # WM_SETICON, ICON_BIG
        if pequeno:
            u32.SendMessageW(hwnd, 0x80, 0, pequeno)           # WM_SETICON, ICON_SMALL
    except Exception:
        pass


def modo_escuro_menus():
    """Menus nativos do Windows (clique direito, bandeja) no tema escuro. Recurso não documentado; falha em silêncio."""
    try:
        ux = ctypes.WinDLL("uxtheme")
        ux[135](2)      # type: ignore[index]  # SetPreferredAppMode(ForceDark), função por ordinal
        ux[136]()       # type: ignore[index]  # FlushMenuThemes
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Ícone na bandeja do sistema (perto do relógio): só ctypes, sem dependências
# ----------------------------------------------------------------------------
WM_APP_BANDEJA = 0x8001       # WM_APP + 1: eventos do ícone
WM_APP_ABRIR = 0x8002         # outra instância pedindo para esta abrir a janela
CLASSE_BANDEJA = "AtualizadorAppsBandeja"
_MUTEX = []


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


class _NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("hWnd", ctypes.c_void_p), ("uID", ctypes.c_uint),
                ("uFlags", ctypes.c_uint), ("uCallbackMessage", ctypes.c_uint), ("hIcon", ctypes.c_void_p),
                ("szTip", ctypes.c_wchar * 128), ("dwState", ctypes.c_ulong), ("dwStateMask", ctypes.c_ulong),
                ("szInfo", ctypes.c_wchar * 256), ("uVersion", ctypes.c_uint), ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", ctypes.c_ulong), ("guidItem", _GUID), ("hBalloonIcon", ctypes.c_void_p)]


class _NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("hWnd", ctypes.c_void_p), ("uID", ctypes.c_uint), ("guidItem", _GUID)]


_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)


class _WNDCLASS(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", _WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p)]


NOME_MUTEX = "Local\\AtualizadorApps_instancia_unica"


def outra_instancia_aberta(espera_s=0) -> bool:
    """Instância única: se já existe um app aberto, pede para ele mostrar a janela e este sai.
    Com `espera_s` > 0 (reabertura como administrador), espera a cópia antiga terminar de fechar em vez de
    pedir para ela abrir — senão a nova sairia e a antiga se fecharia, deixando o app sem nenhuma janela."""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        inicio = time.time()
        limite = inicio + espera_s
        avisou = False
        while True:
            ctypes.set_last_error(0)
            h = k32.CreateMutexW(None, False, NOME_MUTEX)
            erro = ctypes.get_last_error()
            # 183 = já existe. 5 (acesso negado, sem handle) = existe e foi criado por uma cópia ELEVADA (outra DACL).
            ja_existe = erro == 183 or (not h and erro == 5)
            if not ja_existe:
                if h:
                    _MUTEX.append(h)
                diag(f"instância única: trava obtida (erro={erro}) após {time.time() - inicio:.1f}s de espera")
                return False
            if not avisou:
                diag("instância única: outra cópia está segurando a trava"
                     + (f"; esperando até {espera_s}s" if espera_s else ""))
                avisou = True
            # Já existe. Soltar o nosso handle é obrigatório: enquanto o segurarmos, o mutex nunca "morre".
            if h:
                k32.CloseHandle(h)
            if espera_s and time.time() < limite:
                time.sleep(0.25)
                continue
            break
        if espera_s:
            diag(f"instância única: a cópia antiga NÃO saiu em {espera_s}s; abrindo assim mesmo")
            return False
        u32 = ctypes.windll.user32
        u32.FindWindowW.restype = ctypes.c_void_p
        u32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        u32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        hwnd = u32.FindWindowW(CLASSE_BANDEJA, None)
        if not hwnd:
            diag("instância única: a outra cópia não tem janela de bandeja; abrindo normalmente")
            return False     # a outra cópia não tem como receber o pedido: abre normalmente em vez de sumir
        u32.PostMessageW(hwnd, WM_APP_ABRIR, 0, 0)
        diag("instância única: pedi para a outra cópia mostrar a janela; esta vai sair")
        return True
    except Exception as e:
        diag(f"instância única: erro {e!r}")
        return False


def liberar_instancia():
    """Solta a trava de instância única (antes de passar o bastão para a cópia reaberta como administrador)."""
    try:
        k32 = ctypes.WinDLL("kernel32")
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        while _MUTEX:
            k32.CloseHandle(_MUTEX.pop())
    except Exception:
        pass


def lancar_como_admin(exe, params) -> int:
    """Pede ao Windows para abrir `exe` como administrador. Devolve o código do ShellExecute (>32 = abriu;
    5 = o usuário negou o UAC). Bloqueia enquanto o aviso do UAC estiver na tela: rode fora da thread da interface."""
    sh = ctypes.windll.shell32
    sh.ShellExecuteW.restype = ctypes.c_void_p
    sh.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                 ctypes.c_wchar_p, ctypes.c_int]
    return int(sh.ShellExecuteW(None, "runas", exe, params, None, 1) or 0)


class Bandeja:
    """Ícone ao lado do relógio enquanto o app está aberto. Clique abre a janela; botão direito abre o menu.
    `itens()` devolve [(id, texto, marcado)] (texto None = separador); `ao_item(id)` recebe o escolhido."""

    def __init__(self, root, dica, ao_abrir, itens, ao_item):
        self.root, self.ao_abrir, self._itens, self._ao_item = root, ao_abrir, itens, ao_item
        self.ok, self.dica_atual, self.hwnd, self.hicon = False, dica[:127], None, None
        self.eventos = []
        u32, self._sh, k32 = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32
        self._u32 = u32
        # protótipos explícitos: sem eles, ponteiros de 64 bits seriam truncados
        u32.DefWindowProcW.restype = ctypes.c_ssize_t
        u32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        u32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASS)]
        u32.CreateWindowExW.restype = ctypes.c_void_p
        u32.CreateWindowExW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        u32.DestroyWindow.argtypes = [ctypes.c_void_p]
        u32.CreatePopupMenu.restype = ctypes.c_void_p
        u32.AppendMenuW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_wchar_p]
        u32.TrackPopupMenu.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_void_p, ctypes.c_void_p]
        u32.DestroyMenu.argtypes = [ctypes.c_void_p]
        u32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        u32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        u32.RegisterWindowMessageW.argtypes = [ctypes.c_wchar_p]
        k32.GetModuleHandleW.restype = ctypes.c_void_p
        k32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        self._sh.Shell_NotifyIconW.argtypes = [ctypes.c_ulong, ctypes.POINTER(_NOTIFYICONDATA)]

        self._proc = _WNDPROC(self._wndproc)           # precisa ficar referenciado, senão o Python o coleta
        hinst = k32.GetModuleHandleW(None)
        wc = _WNDCLASS()
        wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self._proc, hinst, CLASSE_BANDEJA
        if not u32.RegisterClassW(ctypes.byref(wc)) and ctypes.windll.kernel32.GetLastError() != 1410:
            return                                       # 1410 = classe já registrada (ok)
        self.hwnd = u32.CreateWindowExW(0, CLASSE_BANDEJA, "Atualizador de Apps (bandeja)", 0, 0, 0, 0, 0,
                                        None, None, hinst, None)
        if not self.hwnd:
            return
        try:   # UIPI: sem isto, uma cópia comum não consegue pedir "mostre a janela" para esta, se ela estiver elevada
            u32.ChangeWindowMessageFilterEx.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
            u32.ChangeWindowMessageFilterEx(self.hwnd, WM_APP_ABRIR, 1, None)      # MSGFLT_ALLOW
        except Exception:
            pass
        self._msg_barra = u32.RegisterWindowMessageW("TaskbarCreated")   # o Explorer reiniciou: recolocar o ícone
        self.hicon = _carregar_icone(u32.GetSystemMetrics(49))
        self.ok = self._nim(0)                           # NIM_ADD

    def _nid(self, flags):
        d = _NOTIFYICONDATA()
        d.cbSize, d.hWnd, d.uID, d.uFlags = ctypes.sizeof(d), self.hwnd, 1, flags
        d.uCallbackMessage, d.hIcon, d.szTip = WM_APP_BANDEJA, self.hicon, self.dica_atual
        return d

    def _nim(self, operacao, flags=0x7, nid=None):          # NIF_MESSAGE | NIF_ICON | NIF_TIP
        return bool(self._sh.Shell_NotifyIconW(operacao, ctypes.byref(nid or self._nid(flags))))

    def dica(self, texto):
        texto = texto[:127]
        if self.ok and texto != self.dica_atual:
            self.dica_atual = texto
            self._nim(1)                                     # NIM_MODIFY

    def balao(self, titulo, texto):
        if not self.ok:
            return
        d = self._nid(0x7 | 0x10)                           # + NIF_INFO
        d.szInfo, d.szInfoTitle, d.dwInfoFlags = texto[:255], titulo[:63], 1   # NIIF_INFO
        self._nim(1, nid=d)

    def existe(self) -> bool:
        """Pergunta ao Windows se o ícone está mesmo na bandeja (Shell_NotifyIconGetRect)."""
        try:
            ident = _NOTIFYICONIDENTIFIER()
            ident.cbSize, ident.hWnd, ident.uID = ctypes.sizeof(ident), self.hwnd, 1
            ret = ctypes.wintypes.RECT()
            return self._sh.Shell_NotifyIconGetRect(ctypes.byref(ident), ctypes.byref(ret)) == 0
        except Exception:
            return False

    def remover(self):
        if self.ok:
            self._nim(2)                                     # NIM_DELETE
            self.ok = False
        if self.hwnd:
            self._u32.DestroyWindow(self.hwnd)
            self.hwnd = None

    def _wndproc(self, hwnd, msg, wparam, lparam):
        """Roda DENTRO do laço de mensagens do Tk: não pode chamar o Tk daqui (o Tkinter perde o estado da
        thread e o Python cai). Só anota o evento; `processar()` o trata no ciclo normal do app."""
        try:
            if msg == WM_APP_BANDEJA:
                evento = lparam & 0xFFFF
                if evento in (0x202, 0x203):                 # clique / duplo clique esquerdo
                    self.eventos.append("abrir")
                elif evento in (0x205, 0x7B):                # botão direito / tecla de menu
                    self.eventos.append("menu")
                return 0
            if msg == WM_APP_ABRIR:
                self.eventos.append("abrir")
                return 0
            if self.ok and msg == getattr(self, "_msg_barra", -1):
                self._nim(0)
                return 0
        except Exception:
            pass
        return self._u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def processar(self):
        """Chamado pelo app a cada 100 ms, já no contexto normal do Tk."""
        while self.eventos:
            evento = self.eventos.pop(0)
            if evento == "abrir":
                self.ao_abrir()
            elif evento == "menu" and self.hwnd:
                escolha = self._menu(self.hwnd)
                if escolha:
                    self._ao_item(escolha)

    def _menu(self, hwnd):
        u32 = self._u32
        hmenu = u32.CreatePopupMenu()
        for id_, texto, marcado in self._itens():
            if texto is None:
                u32.AppendMenuW(hmenu, 0x800, 0, None)       # MF_SEPARATOR
            else:
                u32.AppendMenuW(hmenu, 0x8 if marcado else 0, id_, texto)   # MF_CHECKED
        pt = ctypes.wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        u32.SetForegroundWindow(hwnd)                        # sem isso o menu não fecha ao clicar fora
        escolha = u32.TrackPopupMenu(hmenu, 0x100 | 0x2 | 0x20, pt.x, pt.y, 0, hwnd, None)
        u32.PostMessageW(hwnd, 0, 0, 0)                      # WM_NULL: recomendação da documentação
        u32.DestroyMenu(hmenu)
        return escolha


# ----------------------------------------------------------------------------
# Pop-up discreto (preto, estilo Apple)
# ----------------------------------------------------------------------------
class Aviso:
    """Cartão flutuante no canto da tela: não rouba o foco e pausa a contagem ao passar o mouse.
    `ao_acao(motivo)` recebe: "abrir", "depois", "pular", "dispensar" (✕) ou "expirou" (sumiu sozinho).
    `fixo=True` (atualização crítica) não some sozinho."""
    FUNDO = "#000000"
    BORDA = "#2c2c2e"
    TEXTO = "#f5f5f7"
    SUB = "#8e8e93"
    AZUL = "#0a84ff"
    AZUL_HOVER = "#409cff"
    TRANSP = "#010101"
    LARG, ALT = 372, 100
    DURACAO_MS = 14000

    def __init__(self, root, titulo, subtitulo, ao_acao, permitir_depois=True, permitir_pular=True, fixo=False):
        self.root, self.ao_acao, self.fixo = root, ao_acao, fixo
        self.fechado = False
        self.mouse_dentro = False
        self.resta = self.DURACAO_MS
        k = escala(root)
        self.k = k
        w, h = int(self.LARG * k), int(self.ALT * k)
        px = lambda v: int(v * k)
        cor_destaque = LARANJA if fixo else self.AZUL

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=self.TRANSP)
        try:
            self.win.attributes("-transparentcolor", self.TRANSP)
        except tk.TclError:
            pass
        self.cv = tk.Canvas(self.win, width=w, height=h, bg=self.TRANSP, highlightthickness=0, bd=0)
        self.cv.pack()
        self._f_btn = tkfont.Font(family=FONTE_SEMI, size=-px(12))
        self._f_link = tkfont.Font(family=FONTE, size=-px(12))

        self._rrect(1, 1, w - 2, h - 2, px(18), fill=self.FUNDO, outline=self.BORDA, width=1)
        # ícone: quadrado arredondado (azul; laranja se for crítico)
        self._rrect(px(16), px(16), px(56), px(56), px(11), fill=cor_destaque, outline="")
        self.cv.create_text(px(36), px(36), text="!" if fixo else "↓", fill="#ffffff",
                            font=("Segoe UI", -px(20), "bold"))
        self.cv.create_text(px(70), px(28), text=titulo, anchor="w", fill=self.TEXTO,
                            font=("Segoe UI Semibold", -px(15)))
        self.cv.create_text(px(70), px(48), text=subtitulo, anchor="w", fill=LARANJA if fixo else self.SUB,
                            font=("Segoe UI", -px(12)))
        # linha de ações (da direita para a esquerda): Abrir | Depois | Pular versão
        x = w - px(16)
        x = self._pilula(x, TEXTOS["abrir"], cor_destaque, "#ffffff", "abrir")
        if permitir_depois:
            x = self._pilula(x - px(8), TEXTOS["depois"], "#2c2c2e", self.TEXTO, "depois")
        if permitir_pular:
            self._link(x - px(14), TEXTOS["pular"], "pular")
        # fechar discreto
        self.x_fechar = self.cv.create_text(w - px(14), px(14), text="✕", fill="#48484a",
                                            font=("Segoe UI", -px(11)), tags="fechar")
        self.cv.tag_bind("fechar", "<Button-1>", lambda e: self._acao("dispensar"))
        self.cv.tag_bind("fechar", "<Enter>", lambda e: self.cv.itemconfigure(self.x_fechar, fill=self.TEXTO))
        self.cv.tag_bind("fechar", "<Leave>", lambda e: self.cv.itemconfigure(self.x_fechar, fill="#48484a"))
        self.cv.bind("<Enter>", lambda e: setattr(self, "mouse_dentro", True))
        self.cv.bind("<Leave>", lambda e: setattr(self, "mouse_dentro", False))

        self._posicionar(w, h)
        self.win.update_idletasks()
        self._sem_foco()
        self.win.attributes("-alpha", 0.0)
        self.win.deiconify()
        self._animar_entrada()
        if not fixo:
            self.win.after(100, self._contagem)

    def _rrect(self, x1, y1, x2, y2, r, **kw):
        return retangulo_redondo(self.cv, x1, y1, x2, y2, r, **kw)

    def _pilula(self, x_dir, texto, fundo, cor_texto, tag):
        """Botão em pílula encostado em x_dir; devolve o x da borda esquerda."""
        px = lambda v: int(v * self.k)
        larg = self._f_btn.measure(texto) + px(26)
        x1, y1, y2 = x_dir - larg, px(64), px(90)
        forma = self._rrect(x1, y1, x_dir, y2, (y2 - y1) // 2, fill=fundo, outline="", tags=tag)
        self.cv.create_text((x1 + x_dir) // 2, (y1 + y2) // 2, text=texto, fill=cor_texto, font=self._f_btn, tags=tag)
        hover = self.AZUL_HOVER if fundo == self.AZUL else _mistura(fundo, "#ffffff", 0.12)
        self.cv.tag_bind(tag, "<Button-1>", lambda e: self._acao(tag))
        self.cv.tag_bind(tag, "<Enter>", lambda e: self.cv.itemconfigure(forma, fill=hover))
        self.cv.tag_bind(tag, "<Leave>", lambda e: self.cv.itemconfigure(forma, fill=fundo))
        return x1

    def _link(self, x_dir, texto, tag):
        px = lambda v: int(v * self.k)
        item = self.cv.create_text(x_dir, px(77), text=texto, anchor="e", fill=self.SUB, font=self._f_link, tags=tag)
        self.cv.tag_bind(tag, "<Button-1>", lambda e: self._acao(tag))
        self.cv.tag_bind(tag, "<Enter>", lambda e: self.cv.itemconfigure(item, fill=self.TEXTO))
        self.cv.tag_bind(tag, "<Leave>", lambda e: self.cv.itemconfigure(item, fill=self.SUB))

    def _posicionar(self, w, h):
        """Canto inferior direito da área útil (acima da barra de tarefas)."""
        dir_, base = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        try:
            r = ctypes.wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(r), 0):  # SPI_GETWORKAREA
                dir_, base = r.right, r.bottom
        except Exception:
            pass
        margem = int(16 * self.k)
        self.x = dir_ - w - margem
        self.y_final = base - h - margem
        self.y_ini = self.y_final + int(18 * self.k)
        self.win.geometry(f"{w}x{h}+{self.x}+{self.y_ini}")

    def _sem_foco(self):
        """Janela de ferramenta que não ativa (não tira o foco do que o usuário está digitando)."""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            GWL_EXSTYLE, NOACTIVATE, TOOLWINDOW = -20, 0x08000000, 0x00000080
            estilo = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, estilo | NOACTIVATE | TOOLWINDOW)
        except Exception:
            pass

    def _animar_entrada(self, passo=0):
        if self.fechado:
            return
        n = 10
        t = min(1.0, passo / n)
        ease = 1 - (1 - t) ** 3
        try:
            self.win.attributes("-alpha", 0.97 * ease)
            self.win.geometry(f"+{self.x}+{int(self.y_ini + (self.y_final - self.y_ini) * ease)}")
        except tk.TclError:
            return
        if passo < n:
            self.win.after(16, self._animar_entrada, passo + 1)

    def _contagem(self):
        if self.fechado:
            return
        if not self.mouse_dentro:
            self.resta -= 100
        if self.resta <= 0:
            self._acao("expirou")
        else:
            self.win.after(100, self._contagem)

    def _acao(self, motivo):
        """Fecha o cartão e avisa o app do que aconteceu."""
        if self.fechado:
            return
        self.fechar()
        try:
            self.ao_acao(motivo)
        except Exception as e:
            diag(f"ERRO ao tratar a ação '{motivo}' do pop-up: {e!r}")

    def fechar(self, passo=0):
        """Fecha com fade, sem avisar o app (uso interno e para substituir um aviso por outro)."""
        if passo == 0:
            if self.fechado:
                return
            self.fechado = True
        try:
            if passo >= 8:
                self.win.destroy()
                return
            self.win.attributes("-alpha", 0.97 * (1 - passo / 8))
            self.win.after(16, self.fechar, passo + 1)
        except tk.TclError:
            pass


# ----------------------------------------------------------------------------
# Leitura da tabela do winget (funciona em pt-BR e inglês, com nomes largos)
# ----------------------------------------------------------------------------
def _largura(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _inicios_colunas(cabecalho):
    inicios, col, antes_espaco = [], 0, True
    for ch in cabecalho:
        if not ch.isspace() and antes_espaco:
            inicios.append(col)
        antes_espaco = ch.isspace()
        col += _largura(ch)
    return inicios


def _fatiar(linha, inicios):
    campos = [""] * len(inicios)
    col, idx = 0, 0
    for ch in linha:
        while idx + 1 < len(inicios) and col >= inicios[idx + 1]:
            idx += 1
        campos[idx] += ch
        col += _largura(ch)
    return [c.strip() for c in campos]


RE_ID = re.compile(r"^[\w][\w.\-+\\]*(…|\.\.\.)?$")


def parse_winget(texto):
    linhas = [b.split("\r")[-1].rstrip() for b in texto.replace("\r\n", "\n").split("\n")]
    pacotes, i = [], 0
    while i < len(linhas):
        l = linhas[i].strip()
        if i > 0 and len(l) >= 10 and set(l) == {"-"}:
            inicios = _inicios_colunas(linhas[i - 1])
            if len(inicios) >= 4:
                j = i + 1
                # 2ª tabela do winget: "...têm atualização, mas exigem direcionamento EXPLÍCITO" (pacotes com pin):
                # não dá para atualizá-los em massa, então não entram como atualizações normais
                contexto = next((linhas[k].strip() for k in range(i - 2, -1, -1) if linhas[k].strip()), "")
                explicita = bool(re.search(r"expl[ií]cit", contexto, re.I))
                while j < len(linhas) and linhas[j].strip():
                    c = _fatiar(linhas[j], inicios)
                    # fim da tabela = linha-resumo ("4 atualizações disponíveis."), que não tem ID nem versão.
                    # IDs "ARP\..." (com chaves/espaços) são válidos e não podem encerrar a leitura.
                    id_ok = RE_ID.match(c[1]) or c[1].upper().startswith(("ARP\\", "MSIX\\"))
                    if not c[1] or not c[2] or not id_ok:
                        break
                    if not explicita:
                        pacotes.append({"nome": c[0], "id": c[1], "versao": c[2], "disponivel": c[3],
                                        "origem": c[4] if len(c) > 4 else ""})
                    j += 1
                i = j
                continue
        i += 1
    return pacotes


def id_truncado(pid):
    return pid.endswith("…") or pid.endswith("...")


def resolver_ids_truncados(winget, pacotes, log):
    trunc = [p for p in pacotes if id_truncado(p["id"])]
    if not trunc:
        return
    log("Resolvendo IDs abreviados pelo winget...")
    tmp = os.path.join(os.environ.get("TEMP", "."), "atualizador_apps_export.json")
    _apagar(tmp)        # sem isso, se o export falhar, o JSON velho de outra execução seria usado
    rodar([winget, "export", "-o", tmp, "--accept-source-agreements"], timeout=300)
    try:
        with open(tmp, encoding="utf-8-sig") as f:
            dados = json.load(f)
    except Exception:
        return
    ids = [pk.get("PackageIdentifier", "") for s in dados.get("Sources", []) for pk in s.get("Packages", [])]
    for p in trunc:
        pref = p["id"].rstrip(".…")
        achados = [x for x in ids if x.startswith(pref)]
        if len(achados) == 1:
            p["id"] = achados[0]


# ----------------------------------------------------------------------------
# Detecção dos runtimes instalados
# ----------------------------------------------------------------------------
def pythons_instalados():
    """Lê o registro (PEP 514) e devolve {'3.13': '3.13.2', ...}."""
    import winreg
    exes = set()
    raizes = [(winreg.HKEY_CURRENT_USER, 0),
              (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
              (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY)]
    for raiz, flag in raizes:
        try:
            base = winreg.OpenKey(raiz, r"Software\Python\PythonCore", 0, winreg.KEY_READ | flag)
        except OSError:
            continue
        i = 0
        while True:
            try:
                tag = winreg.EnumKey(base, i)
            except OSError:
                break
            i += 1
            try:
                k = winreg.OpenKey(base, tag + r"\InstallPath", 0, winreg.KEY_READ | flag)
                try:
                    exe = winreg.QueryValueEx(k, "ExecutablePath")[0]
                except OSError:
                    exe = os.path.join(winreg.QueryValueEx(k, "")[0], "python.exe")
            except OSError:
                continue
            if exe and os.path.isfile(exe):
                exes.add(os.path.normcase(exe))
    no_path = shutil.which("python")
    if no_path and "windowsapps" not in no_path.lower():  # ignora o atalho da Microsoft Store
        exes.add(os.path.normcase(no_path))
    # instalações que o registro não descreve (ou descreve sem caminho): pastas padrão + o launcher `py`
    padroes = []
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        padroes += [os.path.join(local, "Programs", "Python", "Python*"), os.path.join(local, "Python", "pythoncore-*")]
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(var):
            padroes.append(os.path.join(os.environ[var], "Python*"))
    for padrao in padroes:
        for pasta in glob.glob(padrao):
            exe = os.path.join(pasta, "python.exe")
            if os.path.isfile(exe):
                exes.add(os.path.normcase(exe))
    py = shutil.which("py")
    if py:
        for linha in (rodar([py, "-0p"], timeout=30) or "").splitlines():
            m = re.search(r"[A-Za-z]:\\.*?python\.exe", linha)
            if m and "windowsapps" not in m.group(0).lower() and os.path.isfile(m.group(0)):
                exes.add(os.path.normcase(m.group(0)))

    resultado = {}
    for exe in exes:
        m = re.search(r"Python (\d+\.\d+\.\d+)", rodar([exe, "--version"], timeout=30) or "")
        if m:
            ver = m.group(1)
            ciclo = ".".join(ver.split(".")[:2])
            if ciclo not in resultado or menor(resultado[ciclo], ver):
                resultado[ciclo] = ver
    return resultado


def node_instalado():
    exe = shutil.which("node")
    if not exe:
        return None
    m = re.search(r"v?(\d+\.\d+\.\d+)", rodar([exe, "-v"], timeout=30) or "")
    return m.group(1) if m else None


def nvm_exe():
    exe = shutil.which("nvm")
    if exe:
        return exe
    home = os.environ.get("NVM_HOME")
    if home and os.path.isfile(os.path.join(home, "nvm.exe")):
        return os.path.join(home, "nvm.exe")
    return None


def javas_instalados():
    achados = {}

    def ler_release(home):
        try:
            with open(os.path.join(home, "release"), encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except OSError:
            return None
        v = re.search(r'^JAVA_VERSION="([^"]+)"', txt, re.M)
        imp = re.search(r'^IMPLEMENTOR="([^"]+)"', txt, re.M)
        return (v.group(1), imp.group(1) if imp else "") if v else None

    raizes = {pf for pf in (os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")) if pf}
    for pf in raizes:
        for pasta in PASTAS_JAVA:
            d = os.path.join(pf, pasta)
            if not os.path.isdir(d):
                continue
            try:
                subs = os.listdir(d)
            except OSError:
                continue
            for sub in subs:
                home = os.path.join(d, sub)
                if not os.path.isfile(os.path.join(home, "bin", "java.exe")):
                    continue
                r = ler_release(home)
                if r:
                    achados[os.path.normcase(home)] = {"versao": r[0], "vendor": r[1] or pasta,
                                                       "home": home, "path": False}

    java = shutil.which("java")
    if java:
        s = rodar([java, "-XshowSettings:properties", "-version"], timeout=30) or ""
        home = re.search(r"java\.home = (.+)", s)
        ver = re.search(r"java\.version = (\S+)", s)
        ven = re.search(r"java\.vendor = (.+)", s)
        if home and ver:
            h = home.group(1).strip()
            # JDK 8: java.home aponta para ...\jdk8\jre; a pasta de verdade (a da varredura) é a de cima
            if os.path.basename(h).lower() == "jre" and os.path.isfile(os.path.join(os.path.dirname(h), "bin", "javac.exe")):
                h = os.path.dirname(h)
            k = os.path.normcase(h)
            if k in achados:
                achados[k]["path"] = True
            else:
                achados[k] = {"versao": ver.group(1), "vendor": ven.group(1).strip() if ven else "",
                              "home": h, "path": True}
    return list(achados.values())


def atualiza_o_proprio_python(item) -> bool:
    """True se o item é o Python (mesmo ciclo) que está rodando este script — não vale para o .exe."""
    if getattr(sys, "frozen", False):
        return False
    return str(item.get("id", "")).lower() == f"python.python.{sys.version_info.major}.{sys.version_info.minor}"


def processos_em_execucao():
    """Nomes (sem .exe, minúsculos) dos processos ativos; vazio se não conseguir listar."""
    saida = rodar(["tasklist", "/fo", "csv", "/nh"], timeout=20) or ""
    nomes = set()
    for linha in saida.splitlines():
        m = re.match(r'^"([^"]+)\.exe"', linha.strip(), re.I)
        if m:
            nomes.add(m.group(1).lower())
    return nomes


def programas_abertos(itens):
    """Heurística só informativa: casa o nome/ID do pacote com processos ativos (ex.: 'Google Chrome' ~ chrome.exe)."""
    ativos = processos_em_execucao()
    if not ativos:
        return []
    ignorar = {"microsoft", "windows", "driver", "update", "runtime", "python", "node", "java", "openjs",
               "eclipse", "adoptium", "setup", "installer", "desktop", "studio", "tools", "service"}
    achados = []
    for it in itens:
        if it.get("tipo") == "Runtime":
            continue
        partes = re.findall(r"[a-z0-9]{4,}", (it["nome"] + " " + str(it.get("id", ""))).lower())
        hit = sorted({p for p in partes if p not in ignorar and any(a == p or (len(p) >= 6 and a.startswith(p)) for a in ativos)})
        if hit:
            achados.append(it["nome"])
    return achados


def java_winget_id(vendor, major, home):
    v = vendor.lower()
    jre = not os.path.isfile(os.path.join(home, "bin", "javac.exe"))   # JDK tem javac; só-JRE não (o nome da pasta engana)
    tipo = "JRE" if jre else "JDK"
    if any(x in v for x in ("adoptium", "temurin", "eclipse")):
        return f"EclipseAdoptium.Temurin.{major}.{tipo}"
    if "microsoft" in v:
        return f"Microsoft.OpenJDK.{major}"
    if "oracle" in v:
        return "Oracle.JavaRuntimeEnvironment" if (major == 8 and jre) else f"Oracle.JDK.{major}"
    if "azul" in v:
        return f"Azul.Zulu.{major}.{tipo}"
    if "amazon" in v:
        return f"Amazon.Corretto.{major}.{tipo}"
    return None


def java_nome_curto(vendor):
    v = vendor.lower()
    for chave, nome in (("adoptium", "Temurin"), ("microsoft", "Microsoft"), ("oracle", "Oracle"),
                        ("azul", "Zulu"), ("amazon", "Corretto"), ("bellsoft", "Liberica"), ("ibm", "Semeru")):
        if chave in v:
            return nome
    return vendor[:20] or "?"


# ----------------------------------------------------------------------------
# Fontes OFICIAIS (fabricante) para o que o winget não tem: achar, baixar, verificar e instalar
# ----------------------------------------------------------------------------
HOSTS_PERMITIDOS = {"www.python.org", "python.org", "github.com", "objects.githubusercontent.com",
                    "release-assets.githubusercontent.com", "api.adoptium.net"}
EXIGE_HTTPS = True
TAMANHO_MAX_DOWNLOAD = 600 * 1024 * 1024
_CACHE_URL = {}


def url_existe(url, timeout=15):
    """True = existe, False = 404/410 (não existe), None = não deu para saber (rede fora, erro do servidor)."""
    if url in _CACHE_URL:
        return _CACHE_URL[url]
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "AtualizadorApps/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        res = False if e.code in (404, 410) else None
    except Exception:
        res = None
    if res is not None:
        _CACHE_URL[url] = res
    return res


def ultimo_instalador_python(instalada, ultima, existe=url_existe):
    """Maior versão em (instalada, ultima] que TEM instalador Windows no python.org. Dict, ou None se não há
    (ex.: 3.8 e 3.10 saíram de suporte e as últimas versões só existem como código-fonte).
    Levanta OSError se não deu para consultar (não confundir 'não existe' com 'rede fora')."""
    tu = vtuple(ultima)
    # instalada=None = ciclo NOVO (ainda não instalado): procura da última até a x.y.0
    ti = vtuple(instalada) if instalada else ((tu[0], tu[1], -1) if len(tu) >= 3 else ())
    if len(ti) < 3 or len(tu) < 3 or ti[:2] != tu[:2] or tu <= ti:
        return None
    incerto = False
    for patch in range(tu[2], ti[2], -1):
        v = f"{tu[0]}.{tu[1]}.{patch}"
        url = f"https://www.python.org/ftp/python/{v}/python-{v}-amd64.exe"
        achou = existe(url)
        if achou:
            return {"versao": v, "url": url, "sha256": "", "tipo": "exe", "assinante": "Python Software Foundation",
                    "nome": f"python-{v}-amd64.exe"}
        if achou is None:
            incerto = True
    if incerto:
        raise OSError("python.org não respondeu")
    return None


def temurin_ultimo(major, jre=False, buscar=None):
    """Último Temurin que a Adoptium publicou como instalador MSI (a fonte da verdade do Java: costuma estar atrás do
    OpenJDK 'upstream' que o endoflife.date cita)."""
    tipo = "jre" if jre else "jdk"
    dados = (buscar or buscar_json)("https://api.adoptium.net/v3/assets/latest/%s/hotspot?architecture=x64"
                                   "&image_type=%s&os=windows&vendor=eclipse" % (major, tipo))
    for a in dados or []:
        inst = (a.get("binary") or {}).get("installer") or {}
        link = str(inst.get("link", ""))
        ov = str((a.get("version") or {}).get("openjdk_version", ""))
        m = re.match(r"(\d+(?:\.\d+)*)", ov)
        if link.lower().endswith(".msi") and m:
            return {"versao": m.group(1), "url": link, "sha256": str(inst.get("checksum", "")).lower(), "tipo": "msi",
                    "assinante": "Eclipse", "nome": str(inst.get("name") or os.path.basename(link))}
    return None


def winget_conhece(winget, pid):
    """O winget tem esse pacote? (só para itens 'nova versão'.) Em dúvida (sem resposta) devolve True: não esconde nada."""
    saida = rodar([winget, "show", "--id", pid, "--exact", "--accept-source-agreements"], timeout=60) or ""
    if not saida.strip():
        return True
    return not re.search(r"nenhum pacote|no package found|no packages found", saida, re.I)


def pagina_do_winget(winget, pid):
    """Página do fabricante que consta no manifesto do winget (ou ''): usada só depois de uma falha de download."""
    saida = rodar([winget, "show", "--id", pid, "--exact", "--accept-source-agreements"], timeout=60) or ""
    for linha in saida.splitlines():
        if re.match(r"\s*(Página inicial|Homepage|URL do Fornecedor|Publisher Url|URL de Suporte|Package Url)\s*:", linha, re.I):
            m = re.search(r"https://\S+", linha)
            if m:
                return m.group(0)
    return ""


def pagina_download(item):
    """Página do fabricante para a versão do item (só é oferecida quando NÃO se achou instalador)."""
    pid = str(item.get("id", ""))
    if pid.startswith("EclipseAdoptium.Temurin."):
        return "https://adoptium.net/temurin/releases/?version=%s" % pid.split(".")[2]
    m = re.match(r"Python\.Python\.(\d+\.\d+)$", pid)
    if m and re.match(r"\d+\.\d+\.\d+", str(item.get("disponivel", ""))):
        return "https://www.python.org/downloads/release/python-%s/" % str(item["disponivel"]).replace(".", "")
    return ""


def _host_ok(url):
    p = urllib.parse.urlparse(url)
    return (p.scheme == "https" or not EXIGE_HTTPS) and (p.hostname or "").lower() in HOSTS_PERMITIDOS


def baixar_arquivo(url, destino, log):
    """Baixa só de endereços permitidos (também depois dos redirecionamentos), com limite de tamanho."""
    if not _host_ok(url):
        raise ValueError(f"endereço não permitido: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "AtualizadorApps/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        if not _host_ok(r.geturl()):
            raise ValueError(f"redirecionou para um endereço não permitido: {r.geturl()}")
        total = int(r.headers.get("Content-Length") or 0)
        if total > TAMANHO_MAX_DOWNLOAD:
            raise ValueError("arquivo grande demais")
        feito, marco = 0, 0
        with open(destino, "wb") as f:
            while True:
                bloco = r.read(1024 * 1024)
                if not bloco:
                    break
                f.write(bloco)
                feito += len(bloco)
                if feito > TAMANHO_MAX_DOWNLOAD:
                    raise ValueError("arquivo grande demais")
                if total and feito * 10 // total > marco:
                    marco = feito * 10 // total
                    log(f"  baixando... {marco * 10}%")
    if total and feito != total:
        raise OSError("download incompleto")
    return feito


def sha256_arquivo(caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def assinatura_ok(caminho, esperado):
    """Assinatura digital (Authenticode) válida e emitida para quem se espera (ex.: 'Python Software Foundation')."""
    seguro = caminho.replace("'", "''")
    # Windows PowerShell 5.1 por caminho completo e SEM o PSModulePath herdado: se o app foi aberto a partir do PowerShell 7,
    # essa variável aponta para módulos do PS7 e o 5.1 não consegue carregar o módulo de assinaturas (tudo viraria "inválido")
    ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    saida = rodar([ps if os.path.isfile(ps) else "powershell", "-NoProfile", "-NonInteractive", "-Command",
                   "$s = Get-AuthenticodeSignature -LiteralPath '%s'; "
                   "@{Status=[string]$s.Status; Assunto=[string]$s.SignerCertificate.Subject} | ConvertTo-Json -Compress"
                   % seguro], timeout=90, env=env) or ""
    for linha in saida.splitlines():
        if linha.strip().startswith("{"):
            try:
                d = json.loads(linha)
            except ValueError:
                return False
            return d.get("Status") == "Valid" and esperado.lower() in str(d.get("Assunto", "")).lower()
    return False


def instalar_direto(cmd, log):
    """cmd = ["@direto", url, sha256, tipo, assinante, nome]. Baixa, confere hash e assinatura e só então instala.
    Devolve o código de saída (0 = ok)."""
    _, url, sha, tipo, assinante, nome = (list(cmd) + [""] * 6)[:6]
    pasta = os.path.join(tempfile.gettempdir(), "AtualizadorApps_download")
    destino = os.path.join(pasta, os.path.basename(nome) or "instalador.bin")
    try:
        os.makedirs(pasta, exist_ok=True)
        log(f"Baixando do site oficial: {url}")
        baixar_arquivo(url, destino, log)
    except Exception as e:
        log(f"Falhou o download: {e}")
        _apagar(destino)
        return ERRO_DOWNLOAD
    if sha:
        if sha256_arquivo(destino).lower() != sha.lower():
            log("O hash SHA-256 NÃO confere com o publicado pelo fabricante: arquivo descartado, nada foi instalado.")
            _apagar(destino)
            return ERRO_HASH
        log("Hash SHA-256 confere com o publicado pelo fabricante.")
    else:
        log("O fabricante não publica hash neste endereço: vou confiar na assinatura digital.")
    if assinante and not assinatura_ok(destino, assinante):
        log(f"A assinatura digital NÃO é válida/esperada ('{assinante}'): arquivo descartado, nada foi instalado.")
        _apagar(destino)
        return ERRO_ASSINATURA
    if assinante:
        log(f"Assinatura digital válida ({assinante}).")
    comando = ([destino, "/quiet", "InstallAllUsers=0"] if tipo == "exe"
               else ["msiexec", "/i", destino, "/passive", "/norestart"])
    log("> " + " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(comando)))
    code = executar_streaming(comando, log)
    _apagar(destino)
    return 0 if (tipo == "msi" and code == 3010) else code      # 3010 = ok, só falta reiniciar


def _apagar(caminho):
    try:
        os.remove(caminho)
    except OSError:
        pass


# ----------------------------------------------------------------------------
# Montagem das linhas de runtime
# ----------------------------------------------------------------------------
def acao_winget(winget, pid):
    if not winget or not pid:
        return None
    return [[winget, "install", "--id", pid, "--exact"] + WINGET_ACEITE]


def definir_status(linha, ver, ciclo, acao):
    eol = ciclo is not None and em_eol(ciclo)
    if ciclo and menor(ver, ciclo["latest"]):
        linha.update(status="Atualização" + (" (sem suporte)" if eol else ""),
                     tag="eol" if eol else "update", acao=acao)
        if acao is None:
            linha["status"] += " — manual"
    elif eol:
        linha.update(status="Sem suporte (EOL)", tag="eol", acao=None)
    elif ciclo is None:
        linha.update(status="Não verificado", tag="ok", acao=None)
    else:
        linha.update(status="Em dia", tag="ok", acao=None)


def ciclos_de(produto, log):
    try:
        return [c for c in buscar_json(EOL_URL.format(produto)) if lancado(c)]
    except Exception as e:
        log(f"Não consegui consultar as versões de {produto} ({e}).")
        return []


def checar_python(winget, log):
    res = []
    ciclos = ciclos_de("python", log)
    por = {c["cycle"]: c for c in ciclos}
    inst = pythons_instalados()
    for ciclo in sorted(inst, key=vtuple):
        ver, c = inst[ciclo], por.get(ciclo)
        direto, sem_instalador = None, False
        c_ef = c
        if c and menor(ver, c["latest"]):
            # "latest" do endoflife.date inclui versões só de código-fonte: o que vale é existir instalador no python.org
            try:
                direto = ultimo_instalador_python(ver, c["latest"])
                sem_instalador = direto is None
                c_ef = dict(c, latest=direto["versao"] if direto else ver)
            except OSError as e:
                log(f"Não consegui consultar o python.org para o Python {ciclo} ({e}).")
        linha = {"nome": f"Python {ciclo}", "id": f"Python.Python.{ciclo}", "versao": ver,
                 "disponivel": c_ef["latest"] if c_ef else "?", "tipo": "Runtime"}
        definir_status(linha, ver, c_ef, acao_winget(winget, f"Python.Python.{ciclo}"))
        if direto:
            linha["instalador_direto"] = direto
        elif sem_instalador and c:
            # não achou instalador mais novo: só aí vale oferecer a página do fabricante
            linha["pagina"] = "https://www.python.org/downloads/release/python-%s/" % c["latest"].replace(".", "")
        res.append(linha)
    if ciclos:
        novo = max(ciclos, key=lambda c: vtuple(c["cycle"]))
        if not inst or max(vtuple(k) for k in inst) < vtuple(novo["cycle"]):
            linha = {"nome": f"Python {novo['cycle']} (nova versão)", "id": f"Python.Python.{novo['cycle']}",
                     "versao": "—", "disponivel": novo["latest"],
                     "status": "Nova versão" if inst else "Não instalado", "tag": "novo", "tipo": "Runtime",
                     "acao": acao_winget(winget, f"Python.Python.{novo['cycle']}")}
            # o winget costuma demorar para ter o ciclo novo (ex.: 3.15): se o python.org tem o instalador, ele serve
            try:
                direto = ultimo_instalador_python(None, novo["latest"])
                if direto:
                    linha["instalador_direto"] = direto
                    linha["disponivel"] = direto["versao"]
            except OSError as e:
                log(f"Não consegui consultar o python.org para o Python {novo['cycle']} ({e}).")
            res.append(linha)
    if not inst:
        log("Python: nenhuma instalação encontrada.")
    return res


def _node_eh_lts(c):
    lts = c.get("lts")
    if isinstance(lts, bool):
        return lts
    d = data(lts) if lts else None
    return bool(d and d <= datetime.date.today())


def checar_node(winget, log):
    res = []
    ciclos = ciclos_de("nodejs", log)
    por = {c["cycle"]: c for c in ciclos}
    ltss = [c for c in ciclos if _node_eh_lts(c) and not em_eol(c)]
    lts = max(ltss, key=lambda c: vtuple(c["cycle"])) if ltss else None
    mais_novo = max(ciclos, key=lambda c: vtuple(c["cycle"])) if ciclos else None
    nvm = nvm_exe()
    node = node_instalado()

    def acao_node(alvo, pid):
        if nvm:
            return [[nvm, "install", alvo], [nvm, "use", alvo]]
        return acao_winget(winget, pid)

    if node:
        major = node.split(".")[0]
        c = por.get(major)
        pid = "OpenJS.NodeJS.LTS" if (c and _node_eh_lts(c)) else "OpenJS.NodeJS"
        acao = None
        if c and nvm:
            acao = acao_node(c["latest"], pid)
        elif c and ((lts and c is lts) or (mais_novo and c is mais_novo)):
            acao = acao_winget(winget, pid)  # o winget só atualiza a linha mais recente
        linha = {"nome": f"Node.js {major}" + (" (nvm)" if nvm else ""), "id": f"nvm:{major}" if nvm else pid,
                 "versao": node, "disponivel": c["latest"] if c else "?", "tipo": "Runtime"}
        definir_status(linha, node, c, acao)
        res.append(linha)
    else:
        log("Node.js: não encontrado no PATH.")

    if lts and (not node or vtuple(node.split(".")[0]) < vtuple(lts["cycle"])):
        res.append({"nome": f"Node.js {lts['cycle']} LTS (nova versão)",
                    "id": f"nvm:{lts['cycle']}" if nvm else "OpenJS.NodeJS.LTS",
                    "versao": "—", "disponivel": lts["latest"],
                    "status": "Nova versão" if node else "Não instalado", "tag": "novo", "tipo": "Runtime",
                    "acao": acao_node(lts["latest"], "OpenJS.NodeJS.LTS")})
    return res


def checar_java(winget, log):
    res = []
    ciclos = ciclos_de("eclipse-temurin", log)
    por = {c["cycle"]: c for c in ciclos}
    ltss = [c for c in ciclos if c.get("lts") is True and not em_eol(c)]
    lts = max(ltss, key=lambda c: vtuple(c["cycle"])) if ltss else None
    instalados = javas_instalados()
    maior_major = 0
    for j in sorted(instalados, key=lambda x: vtuple(x["versao"])):
        major = vtuple(j["versao"])[0]
        maior_major = max(maior_major, major)
        c = por.get(str(major))
        pid = java_winget_id(j["vendor"], major, j["home"])
        nome = f"Java {major} ({java_nome_curto(j['vendor'])})" + (" • PATH" if j["path"] else "")
        ult = None
        if pid and pid.startswith("EclipseAdoptium.Temurin.") and major >= 9:
            # a Adoptium é a fonte da verdade do Temurin (o endoflife.date cita o OpenJDK upstream, que ela ainda não publicou)
            try:
                ult = temurin_ultimo(major, jre=pid.endswith(".JRE"))
            except Exception as e:
                log(f"Não consegui consultar a Adoptium para o Java {major} ({e}).")
        c_ef = dict(c, latest=ult["versao"]) if (c and ult) else c
        linha = {"nome": nome, "id": pid or j["home"], "versao": j["versao"],
                 "disponivel": c_ef["latest"] if c_ef else "?", "tipo": "Runtime"}
        definir_status(linha, j["versao"], c_ef, acao_winget(winget, pid))
        if ult and menor(j["versao"], ult["versao"]):
            linha["instalador_direto"] = ult
        if pid and pid.startswith("EclipseAdoptium.Temurin.") and ult is None and c and menor(j["versao"], c["latest"]):
            linha["pagina"] = pagina_download({"id": pid})   # não consegui achar o instalador: aí sim oferece o site
        res.append(linha)
    if not instalados:
        log("Java: nenhum JDK/JRE encontrado.")
    if lts and maior_major < int(lts["cycle"]):
        pid = f"EclipseAdoptium.Temurin.{lts['cycle']}.JDK"
        res.append({"nome": f"Java {lts['cycle']} LTS (Temurin, nova versão)", "id": pid, "versao": "—",
                    "disponivel": lts["latest"], "status": "Nova versão" if instalados else "Não instalado",
                    "tag": "novo", "tipo": "Runtime", "acao": acao_winget(winget, pid)})
    return res


# ----------------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------------
class App:
    def __init__(self, root, minimizado=False):
        self.root = root
        root.title(APP_NOME)
        self.fila = queue.Queue()
        self.lista = []
        self.itens = {}
        self.ocupado = False
        self.atualizando = False
        self._confirmando = False              # diálogo "Atualizar N itens?" aberto
        self._abrindo_admin = False            # esperando o UAC / lançando a cópia como administrador
        self._uac_inicio = time.monotonic()
        self._uac_avisos = set()
        self.origem_verificacao = "manual"     # "manual" | "agenda" (só a agenda mostra o pop-up)
        self.aviso = None
        self.disparados = set()                # (data, "HH:MM") já avisados
        self.cfg = carregar_config()
        ign = self.cfg.get("ignorados", [])
        self.ignorados = {str(x) for x in ign} if isinstance(ign, list) else set()
        self.var_desconhecidos = tk.BooleanVar(value=bool(self.cfg.get("incluir_desconhecidos", False)))
        self.var_mostrar_ign = tk.BooleanVar(value=bool(self.cfg.get("mostrar_ignorados", False)))
        self.var_mostrar_ok = tk.BooleanVar(value=bool(self.cfg.get("mostrar_em_dia", True)))
        self.var_avisos = tk.BooleanVar(value=bool(self.cfg.get("avisos", True)))
        self.var_iniciar = tk.BooleanVar(value=iniciar_com_windows_ativo())
        try:
            self.horarios = parse_horarios(" ".join(self.cfg.get("horarios", HORARIOS_PADRAO)))
        except (ValueError, TypeError):
            self.horarios = list(HORARIOS_PADRAO)
        pul = self.cfg.get("puladas", {})
        self.puladas = {str(a): str(b) for a, b in pul.items()} if isinstance(pul, dict) else {}
        prog = self.cfg.get("programas_pausa", PROGRAMAS_PAUSA_PADRAO)
        self.programas_pausa = normalizar_programas(prog if isinstance(prog, (list, str)) else PROGRAMAS_PAUSA_PADRAO)
        self.pausado_ate = str(self.cfg.get("pausado_ate", ""))
        sef = self.cfg.get("sem_efeito", {})
        self.sem_efeito = {str(a): str(b) for a, b in sef.items()} if isinstance(sef, dict) else {}
        self.adiado_ate = None                 # "Depois" ativo até este horário
        self.reaviso = False                   # o próximo aviso é o lembrete pós-"Depois" (não oferece adiar de novo)
        self.ultimo_aviso = None               # (quando, assinatura) para não repetir a mesma lista
        self.ultima_checagem = None
        self.falhas = 0                        # falhas na última verificação (nunca mostrar como "em dia")
        self.tentativas_falha = 0
        self.k = escala(root)
        root.geometry(f"{int(1120 * self.k)}x{min(int(780 * self.k), root.winfo_screenheight() - 120)}")
        root.minsize(int(1060 * self.k), int(600 * self.k))
        # Vidro desligado por padrão: o Tk pinta com GDI (sem transparência), e o Windows SOMA essas cores ao acrílico claro
        # que está atrás, deixando tudo cinza/esbranquiçado. Quem quiser mesmo assim: "vidro": true no config.json.
        self.vidro = aplicar_vidro(root, bool(self.cfg.get("vidro", False)))
        self.c_fundo = VIDRO if self.vidro else FUNDO_SOLIDO
        root.configure(bg=self.c_fundo)
        modo_escuro_menus()
        self.bandeja = None
        self._montar()
        definir_icone_janela(root)
        try:
            self.bandeja = Bandeja(root, APP_NOME, self._mostrar_janela, self._itens_bandeja, self._acao_bandeja)
            if not self.bandeja.ok:
                self.bandeja = None
        except Exception:
            self.bandeja = None
        root.bind("<FocusIn>", self._ao_focar)
        root.bind("<Destroy>", self._ao_destruir, add="+")
        # registra todo clique que o Tk recebe: se o clique "não pega", o log mostra se ele chegou ao app
        root.bind_all("<ButtonPress-1>", self._diag_clique, add="+")
        self._batida = time.monotonic()
        self._vigia_ativo = True
        root.after(200, self._bater)
        threading.Thread(target=self._vigia, name="vigia", daemon=True).start()
        diag(f"janela montada (vidro={self.vidro}, bandeja={'sim' if self.bandeja else 'não'}, "
             f"minimizado={minimizado}, dpi={self.k:.2f})")
        root.protocol("WM_DELETE_WINDOW", self._fechar_app)
        root.after(100, self._processar_fila)
        root.after(300, lambda: self.verificar("agenda" if minimizado else "manual"))
        root.after(30000, self._tick_agenda)
        if minimizado:
            # com o ícone na bandeja não precisa ocupar a barra de tarefas; sem ele, minimiza normalmente
            root.withdraw() if self.bandeja else root.iconify()

    # ---- layout -----------------------------------------------------------
    def _montar(self):
        k, fundo = self.k, self.c_fundo
        aplicar_estilo(self.root, k)
        P = int(24 * k)

        def quadro(**pack):
            f = tk.Frame(self.root, bg=fundo)
            f.pack(**pack)
            return f

        # cabeçalho: título + resumo, e o estado de administrador à direita
        cab = quadro(fill="x", padx=P, pady=(P, int(12 * k)))
        esq = tk.Frame(cab, bg=fundo)
        esq.pack(side="left")
        tk.Label(esq, text=APP_NOME, bg=fundo, fg=TEXTO, font=fonte(k, 28, familia=FONTE_SEMI)).pack(anchor="w")
        # quebra em 2 linhas se for comprido (senão empurra e corta o aviso/botão de administrador da direita)
        self.lb_status = tk.Label(esq, text="", bg=fundo, fg=TEXTO_2, font=fonte(k, 13), justify="left",
                                  anchor="w", wraplength=int(540 * k))
        self.lb_status.pack(anchor="w", pady=(int(2 * k), 0))
        dire = tk.Frame(cab, bg=fundo)
        dire.pack(side="right")
        if eh_admin():
            tk.Label(dire, text="●  Administrador", bg=fundo, fg=VERDE, font=fonte(k, 13)).pack()
        else:
            Botao(dire, "Reabrir como admin", self._reabrir_admin, fundo=fundo).pack(side="right")
            tk.Label(dire, text="Sem admin: instaladores podem pedir permissão (UAC)", bg=fundo, fg=LARANJA,
                     font=fonte(k, 12)).pack(side="right", padx=(0, int(12 * k)))

        # ações + filtros de exibição
        acoes = quadro(fill="x", padx=P, pady=(0, int(10 * k)))
        self.bt_verificar = Botao(acoes, "Verificar agora", self.verificar, primario=True, fundo=fundo)
        self.bt_verificar.pack(side="left")
        Botao(acoes, "Marcar todos", lambda: self._marcar_todos(True), fundo=fundo).pack(side="left", padx=(int(10 * k), 0))
        Botao(acoes, "Desmarcar", lambda: self._marcar_todos(False), fundo=fundo).pack(side="left", padx=(int(8 * k), 0))
        Botao(acoes, "Histórico", self._mostrar_historico, fundo=fundo).pack(side="left", padx=(int(8 * k), 0))
        for texto, var, cmd in reversed([("Incluir versão desconhecida", self.var_desconhecidos, self._salvar_opcoes),
                                         ("Mostrar em dia", self.var_mostrar_ok, self._mudou_filtro),
                                         ("Mostrar ignorados", self.var_mostrar_ign, self._mudou_filtro)]):
            Interruptor(acoes, texto, var, cmd, fundo=fundo).pack(side="right", padx=(int(20 * k), 0))

        # avisos agendados
        avisos = quadro(fill="x", padx=P, pady=(0, int(14 * k)))
        Interruptor(avisos, "Avisar quando houver atualização", self.var_avisos, self._salvar_opcoes,
                    fundo=fundo).pack(side="left")
        self.bt_horarios = Botao(avisos, "", self._editar_horarios, fundo=fundo)
        self.bt_horarios.pack(side="left", padx=(int(10 * k), 0))
        self.bt_pausa = Botao(avisos, "", self._menu_pausa, fundo=fundo)
        self.bt_pausa.pack(side="left", padx=(int(8 * k), 0))
        Botao(avisos, "Programas…", self._editar_programas, fundo=fundo).pack(side="left", padx=(int(8 * k), 0))
        Interruptor(avisos, "Iniciar com o Windows (minimizado)", self.var_iniciar, self._alternar_inicio,
                    fundo=fundo).pack(side="right")
        self.menu_pausa = tk.Menu(self.root, tearoff=0, bg=CARTAO_2, fg=TEXTO, activebackground=AZUL,
                                  activeforeground="#ffffff", bd=0, relief="flat", font=fonte(k, 13))
        self._rotulo_horarios()
        self._rotulo_pausa()

        # tabela (cartão de vidro escuro)
        tabela = Cartao(self.root, fundo=fundo)
        tabela.pack(fill="both", expand=True, padx=P)
        meio = tabela.dentro
        # checkbox = imagem desenhada por nós (não símbolo de fonte): igual em qualquer PC e DPI
        tam_caixa = max(14, int(18 * k))
        self._caixas = {e: criar_imagem_caixa(self.root, e, tam_caixa, CARTAO) for e in ("off", "on", "parcial")}
        self._caixas_cab = {e: criar_imagem_caixa(self.root, e, tam_caixa, CARTAO_2) for e in ("off", "on", "parcial")}
        self._estado_sel = "off"
        cols = ("nome", "id", "versao", "disponivel", "status", "tipo")
        # a coluna #0 do Treeview é a da checkbox (imagem); o cabeçalho dela é o "marcar/desmarcar tudo"
        self.tree = ttk.Treeview(meio, columns=cols, show="tree headings", selectmode="browse")
        self.tree.column("#0", width=int(54 * k), minwidth=int(54 * k), stretch=False, anchor=tk.W)
        self.tree.heading("#0", text="", image=self._caixas_cab["off"], anchor=tk.W,
                          command=self._alternar_todos)  # type: ignore[arg-type]
        for c, t, w, est in [("nome", "Programa", 280, True),
                             ("id", "ID (winget)", 250, True), ("versao", "Instalada", 110, False),
                             ("disponivel", "Disponível", 110, False), ("status", "Situação", 190, False),
                             ("tipo", "Origem", 80, False)]:
            self.tree.heading(c, text=t, anchor=tk.W)
            self.tree.column(c, width=int(w * k), anchor=tk.W, stretch=est)
        sb = ttk.Scrollbar(meio, orient="vertical", command=self.tree.yview, style="Glass.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("update", foreground=TEXTO)
        self.tree.tag_configure("novo", foreground="#64b5ff")
        self.tree.tag_configure("eol", foreground=LARANJA)
        self.tree.tag_configure("ok", foreground=TEXTO_3)
        self.tree.tag_configure("ign", foreground=TEXTO_3)
        self.tree.tag_configure("runtime", font=fonte(k, 13, "bold"))
        self.tree.bind("<Button-1>", self._clique)
        self.tree.bind("<space>", lambda e: self._alternar(self.tree.focus()))
        self.tree.bind("<Button-3>", self._menu_contexto)

        self.menu = tk.Menu(self.root, tearoff=0, bg=CARTAO_2, fg=TEXTO, activebackground=AZUL,
                            activeforeground="#ffffff", bd=0, relief="flat", font=fonte(k, 13))

        base = quadro(fill="x", padx=P, pady=(int(14 * k), int(10 * k)))
        self.bt_atualizar = Botao(base, "Atualizar selecionados", self.atualizar, primario=True, fundo=fundo)
        self.bt_atualizar.pack(side="left")
        self.barra = ttk.Progressbar(base, mode="indeterminate", length=int(180 * k),
                                     style="Glass.Horizontal.TProgressbar")
        self.barra.pack(side="left", padx=int(16 * k))

        # registro (log)
        quadro_log = Cartao(self.root, fundo=fundo, cor=CARTAO_LOG, height=int(150 * k))
        quadro_log.pack(fill="x", padx=P, pady=(0, P))
        self.txt_log = tk.Text(quadro_log.dentro, font=("Consolas", -int(12 * k)), wrap="none", state="disabled",
                               bg=CARTAO_LOG, fg=TEXTO_2, relief="flat", bd=0, highlightthickness=0,
                               padx=int(10 * k), pady=int(6 * k), selectbackground=SELECAO)
        sbl = ttk.Scrollbar(quadro_log.dentro, orient="vertical", command=self.txt_log.yview,
                            style="Glass.Vertical.TScrollbar")
        self.txt_log.configure(yscrollcommand=sbl.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        sbl.pack(side="right", fill="y")

    # ---- helpers de UI ----------------------------------------------------
    def _log(self, texto):
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", texto + "\n")
        linhas = int(self.txt_log.index("end-1c").split(".")[0])
        if linhas > 4000:
            self.txt_log.delete("1.0", f"{linhas - 4000}.0")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _set_ocupado(self, sim):
        self.ocupado = sim
        estado = "disabled" if sim else "normal"
        self.bt_verificar.configure(state=estado)
        self.bt_atualizar.configure(state=estado)
        if sim:
            self.barra.start(12)
        else:
            self.barra.stop()

    def _mudou_filtro(self):
        """Mostrar em dia / Mostrar ignorados: a escolha do usuário fica guardada para a próxima vez."""
        self._salvar_opcoes()
        self._redesenhar()

    def _salvar_opcoes(self):
        self.cfg["mostrar_em_dia"] = self.var_mostrar_ok.get()
        self.cfg["mostrar_ignorados"] = self.var_mostrar_ign.get()
        self.cfg["incluir_desconhecidos"] = self.var_desconhecidos.get()
        self.cfg["ignorados"] = sorted(self.ignorados)
        self.cfg["avisos"] = self.var_avisos.get()
        self.cfg["horarios"] = self.horarios
        self.cfg["puladas"] = self.puladas
        self.cfg["programas_pausa"] = self.programas_pausa
        self.cfg["pausado_ate"] = self.pausado_ate
        self.cfg["sem_efeito"] = self.sem_efeito
        salvar_config(self.cfg)

    # ---- avisos agendados -------------------------------------------------
    def _rotulo_horarios(self):
        self.bt_horarios.configure(text="Horários: " + (", ".join(self.horarios) or "nenhum") + "  ✎")

    def _editar_horarios(self):
        txt = pedir_texto(self.root, "Horários dos avisos",
                          "Digite os horários (HH:MM) separados por vírgula.\nDeixe vazio para não avisar.",
                          ", ".join(self.horarios))
        if txt is None:
            return
        try:
            self.horarios = parse_horarios(txt)
        except ValueError as e:
            avisar(self.root, "Horário inválido", f"“{e}” não é um horário válido.\nUse o formato 09:00, 13:30, 17:00.",
                   erro=True)
            return
        self.disparados.clear()
        self._rotulo_horarios()
        self._salvar_opcoes()

    def _alternar_inicio(self):
        if not definir_iniciar_com_windows(self.var_iniciar.get()):
            self.var_iniciar.set(not self.var_iniciar.get())
            avisar(self.root, APP_NOME, "Não consegui alterar a inicialização com o Windows.", erro=True)

    def _tick_agenda(self):
        """A cada 30 s confere se chegou a hora de um aviso. Nunca deixa de se reagendar."""
        try:
            agora = datetime.datetime.now()
            self._rotulo_pausa()
            if self.var_avisos.get() and self.horarios and not self.ocupado:
                h = horario_devido(agora, self.horarios, self.disparados)
                if h and not (self.adiado_ate and agora < self.adiado_ate):
                    self.disparados.add((datetime.date.today().isoformat(), h))
                    self.verificar("agenda")
        except Exception as e:
            self._log(f"Agenda: erro ({e})")
        finally:
            self.root.after(30000, self._tick_agenda)

    def _usuario_olhando(self):
        return self.root.state() == "normal" and self.root.focus_displayof() is not None

    def _avisar_se_pendente(self, tentativa=0):
        """Mostra o pop-up só se houver algo a atualizar e as regras permitirem:
        sem pausa/adiamento, sem reunião ou apresentação aberta, sem repetir a mesma lista em 30 min,
        e com a janela principal fora de vista."""
        if not self.var_avisos.get():
            return
        agora = datetime.datetime.now()
        pend = pendencias(self.lista, self.ignorados, self.puladas)
        if not pend or self._usuario_olhando():
            return
        if pausado(self.pausado_ate, agora) or (self.adiado_ate and agora < self.adiado_ate):
            return
        em_uso = programas_em_uso(self.programas_pausa, processos_em_execucao())
        if em_uso:
            if tentativa < TENTATIVAS_EM_USO:
                self._log(f"Aviso adiado: {', '.join(em_uso)} em uso (nova tentativa em {ESPERA_EM_USO_MIN} min).")
                self.root.after(ESPERA_EM_USO_MIN * 60000, lambda: self._avisar_se_pendente(tentativa + 1))
            return
        sig = assinatura(pend)
        if self.ultimo_aviso:
            desde = agora - self.ultimo_aviso[0]
            if desde < datetime.timedelta(seconds=INTERVALO_MIN_AVISO_S):
                return  # trava geral contra pop-ups em sequência, seja qual for a lista
            if self.ultimo_aviso[1] == sig and desde < datetime.timedelta(minutes=REPETICAO_MIN):
                return
        self.ultimo_aviso = (agora, sig)
        criticos = [i for i in pend if eh_critico(i)]
        nomes = [self._nome_curto(i["nome"]) for i in (criticos or pend)]
        sub = ", ".join(nomes[:2]) + (f" +{len(nomes) - 2}" if len(nomes) > 2 else "")
        if criticos:
            titulo = TEXTOS["critico"] if len(pend) == 1 else TEXTOS["varios"].format(n=len(pend))
            sub = TEXTOS["critico_sub"].format(nomes=sub)
        else:
            titulo = TEXTOS["um"] if len(pend) == 1 else TEXTOS["varios"].format(n=len(pend))
        if self.aviso is not None and not self.aviso.fechado:
            self.aviso.fechar()
        reaviso, self.reaviso = self.reaviso, False
        try:
            self.aviso = Aviso(self.root, titulo, sub, lambda motivo: self._acao_aviso(motivo, pend),
                               permitir_depois=not reaviso, permitir_pular=not criticos, fixo=bool(criticos))
        except Exception as e:
            self._log(f"Aviso: não foi possível exibir o pop-up ({e})")

    def _acao_aviso(self, motivo, pend):
        """Tudo o que o usuário faz no pop-up é registrado no histórico."""
        nomes = ", ".join(i["nome"] for i in pend[:5]) + (f" +{len(pend) - 5}" if len(pend) > 5 else "")
        registrar_historico({"abrir": "aberto", "depois": "adiado", "pular": "pulado",
                             "dispensar": "dispensado", "expirou": "expirou"}.get(motivo, motivo), item=nomes)
        if motivo == "abrir":
            self._mostrar_janela()
        elif motivo == "depois":
            self.adiado_ate = datetime.datetime.now() + datetime.timedelta(minutes=ADIAR_MIN)
            self.root.after(ADIAR_MIN * 60000, self._fim_adiamento)
            self._log(f"Aviso adiado por {ADIAR_MIN // 60} h.")
        elif motivo == "pular":
            for i in pend:
                if not eh_critico(i) and i.get("disponivel"):
                    self.puladas[chave_item(i)] = i["disponivel"]
                    i["marcado"] = False
            self._salvar_opcoes()
            self._redesenhar()
            self._log("Versões atuais puladas. Voltam a avisar quando sair uma mais nova.")

    def _fim_adiamento(self):
        """Lembrete único depois do 'Depois': confere de novo e avisa sem oferecer adiar outra vez."""
        self.adiado_ate = None
        if self.ocupado:
            self.root.after(60000, self._fim_adiamento)
            return
        self.reaviso = True
        self.verificar("agenda")

    # ---- pausa, programas em uso e histórico -------------------------------
    def _rotulo_pausa(self):
        agora = datetime.datetime.now()
        if pausado(self.pausado_ate, agora):
            ate = datetime.datetime.fromisoformat(self.pausado_ate)
            quando = ate.strftime("%H:%M") if ate.date() == agora.date() else ate.strftime("%d/%m %H:%M")
            self.bt_pausa.configure(text=f"⏸ Pausado até {quando}")
        else:
            self.bt_pausa.configure(text="Pausar avisos ▾")

    def _menu_pausa(self):
        m = self.menu_pausa
        m.delete(0, "end")
        m.add_command(label="Pausar por 2 horas", command=lambda: self._definir_pausa(2))
        m.add_command(label="Pausar até amanhã às 08:00", command=lambda: self._definir_pausa(None))
        if pausado(self.pausado_ate, datetime.datetime.now()):
            m.add_separator()
            m.add_command(label="Retomar avisos", command=lambda: self._definir_pausa(0))
        m.tk_popup(self.bt_pausa.winfo_rootx(), self.bt_pausa.winfo_rooty() + self.bt_pausa.winfo_height())

    def _definir_pausa(self, horas):
        agora = datetime.datetime.now()
        if horas == 0:
            self.pausado_ate = ""
            registrar_historico("retomado")
        else:
            ate = agora + datetime.timedelta(hours=horas) if horas else proxima_manha(agora)
            self.pausado_ate = ate.isoformat(timespec="seconds")
            registrar_historico("pausado", detalhe=f"até {ate:%d/%m %H:%M}")
        self._salvar_opcoes()
        self._rotulo_pausa()

    def _editar_programas(self):
        txt = pedir_texto(self.root, "Programas que seguram o aviso",
                          "Se algum destes estiver aberto (reunião, apresentação, gravação), o pop-up espera "
                          "e tenta de novo.\nNomes dos processos, separados por vírgula (ex.: teams, zoom, powerpnt).",
                          ", ".join(self.programas_pausa))
        if txt is None:
            return
        self.programas_pausa = normalizar_programas(txt)
        self._salvar_opcoes()

    def _mostrar_historico(self):
        regs = ler_historico(200)
        avisar(self.root, "Histórico", formatar_historico(regs) if regs else "Nenhuma ação registrada ainda.")

    def _diag_clique(self, ev):
        try:
            diag(f"mouse: botão esquerdo em tela=({ev.x_root},{ev.y_root}) widget={ev.widget.winfo_class()} "
                 f"{str(ev.widget)[-34:]} janela={self.root.state()}")
        except Exception:
            pass

    def _bater(self):
        """Batimento: a thread da interface marca a hora a cada 200 ms. Se parar, o vigia percebe."""
        self._batida = time.monotonic()
        if self._vigia_ativo:
            self.root.after(200, self._bater)

    def _vigia(self):
        """Roda em outra thread. Se a interface ficar sem responder, grava no log ONDE ela está parada
        (pilha da thread principal) e, quando voltar, quanto tempo durou."""
        principal = threading.main_thread().ident
        travada_desde, ultimo_relato = None, 0.0
        while self._vigia_ativo:
            time.sleep(0.25)
            parada = time.monotonic() - self._batida
            if parada > TRAVA_LIMITE_S:
                if travada_desde is None:
                    travada_desde = time.monotonic() - parada
                if parada - ultimo_relato >= 3 or ultimo_relato == 0.0:
                    ultimo_relato = parada
                    quadro = sys._current_frames().get(principal) if principal is not None else None
                    pilha = "".join(traceback.format_stack(quadro)[-6:]).strip() if quadro else "?"
                    diag(f"INTERFACE TRAVADA há {parada:.1f}s. Onde está parada:\n{pilha}")
            elif travada_desde is not None:
                diag(f"interface voltou a responder (ficou {time.monotonic() - travada_desde:.1f}s parada)")
                travada_desde, ultimo_relato = None, 0.0

    def _ao_focar(self, ev):
        """Ao voltar para a janela, reconfere se a última verificação já está velha."""
        if ev.widget is not self.root or self.ocupado or self.ultima_checagem is None:
            return
        if datetime.datetime.now() - self.ultima_checagem > datetime.timedelta(hours=REVERIFICAR_HORAS):
            self.verificar("manual")

    def _versao_pulada(self, item):
        return versao_pulada(item, self.puladas)

    def _oculto(self, item):
        """Ignorado, com a versão atual pulada, ou fora do alcance do winget: não entra em seleção, resumo nem avisos
        e some da lista (aparece de novo com 'Mostrar ignorados')."""
        return chave_item(item) in self.ignorados or self._versao_pulada(item) or bool(item.get("fora_winget"))

    @staticmethod
    def _nome_curto(nome):
        nome = re.sub(r"\s*\(.*?\)|\s+\d[\d.]*(-x64)?$", "", nome).strip()
        return nome if len(nome) <= 22 else nome[:21] + "…"

    def _mostrar_janela(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def _fechar_app(self):
        """Botão X. Com 'manter na bandeja' ligado (menu do ícone), só esconde a janela; senão encerra o app."""
        if self.bandeja and self.cfg.get("bandeja_ao_fechar", False):
            self.root.withdraw()
            if not self.cfg.get("aviso_bandeja_visto"):
                self.cfg["aviso_bandeja_visto"] = True
                salvar_config(self.cfg)
                self.bandeja.balao(APP_NOME, "Continua aberto na bandeja. Clique com o botão direito no ícone para sair.")
            return
        self._sair()

    def _sair(self):
        diag(f"_sair chamado (atualizando={self.atualizando})")
        if self.atualizando and not perguntar(
                self.root, "Atualização em andamento",
                "Se fechar agora, o instalador atual pode ficar incompleto e o programa afetado pode não abrir.",
                ok="Fechar mesmo assim", cancelar="Continuar aqui"):
            return
        self.root.destroy()

    def _chave(self, item):
        return chave_item(item)

    def _redesenhar(self):
        self.tree.delete(*self.tree.get_children())
        for item in self.lista:
            ign = self._oculto(item)
            if ign and not self.var_mostrar_ign.get():
                continue
            if item["tag"] == "ok" and not item.get("acao") and not self.var_mostrar_ok.get():
                continue
            imagem = self._caixas["on" if item["marcado"] else "off"] if item.get("acao") and not ign else ""
            tags = ["ign" if ign else item["tag"]]
            if item["tipo"] == "Runtime":
                tags.append("runtime")
            status = item["status"] + (" (ignorado)" if self._chave(item) in self.ignorados
                                       else " (versão pulada)" if self._versao_pulada(item) else "")
            self.tree.insert("", "end", iid=item["iid"], tags=tags, image=imagem,
                             values=(item["nome"], item.get("id", ""), item["versao"],
                                     item["disponivel"], status, item["tipo"]))
        self._atualizar_resumo()

    def _selecionaveis(self):
        return [i for i in self.lista if i.get("acao") and not self._oculto(i)]

    def _atualizar_cabecalho_sel(self):
        """Caixa do cabeçalho: vazia = nada marcado, azul com visto = tudo, azul com traço = alguns."""
        itens = self._selecionaveis()
        marcados = sum(1 for i in itens if i["marcado"])
        self._estado_sel = "off" if marcados == 0 else "on" if marcados == len(itens) else "parcial"
        self.tree.heading("#0", image=self._caixas_cab[self._estado_sel])

    def _alternar_todos(self):
        """Clique no checkbox do cabeçalho: se já está tudo marcado, desmarca; senão marca tudo."""
        if self.ocupado:
            return
        itens = self._selecionaveis()
        self._marcar_todos(not (itens and all(i["marcado"] for i in itens)))

    def _atualizar_resumo(self):
        self._atualizar_cabecalho_sel()
        visiveis = [i for i in self.lista if not self._oculto(i)]
        upd = sum(1 for i in visiveis if i["status"].startswith("Atualiza"))
        novo = sum(1 for i in visiveis if i["tag"] == "novo")
        ok = sum(1 for i in visiveis if i["status"] == "Em dia")
        marc = sum(1 for i in visiveis if i["marcado"])
        texto = (f"{upd} atualização(ões) • {novo} nova(s) versão(ões) • "
                 f"{ok} runtime(s) em dia • {marc} marcado(s)")
        fora = sum(1 for i in self.lista if i.get("fora_winget") and chave_item(i) not in self.ignorados)
        if fora:
            texto += f" • {fora} fora do winget (ocultos)"
        if self.falhas:
            # uma falha na consulta nunca pode parecer "tudo em dia"
            texto += f"  •  ⚠ verificação incompleta ({self.falhas} falha(s))"
        self.lb_status.configure(text=texto, fg=LARANJA if self.falhas else TEXTO_2)
        if self.bandeja:
            self.bandeja.dica(f"{APP_NOME}\n{upd} atualização(ões) • {novo} nova(s) versão(ões)")

    # ---- bandeja do sistema -----------------------------------------------
    def _itens_bandeja(self):
        pausa = pausado(self.pausado_ate, datetime.datetime.now())
        return [(1, "Abrir", False), (2, "Verificar agora", False), (0, None, False),
                (4, "Retomar avisos", False) if pausa else (3, "Pausar avisos por 2 horas", False),
                (5, "Fechar a janela mantém na bandeja", bool(self.cfg.get("bandeja_ao_fechar", False))),
                (0, None, False), (9, "Sair", False)]

    def _acao_bandeja(self, id_):
        if id_ == 1:
            self._mostrar_janela()
        elif id_ == 2:
            self.verificar("manual")
        elif id_ == 3:
            self._definir_pausa(2)
        elif id_ == 4:
            self._definir_pausa(0)
        elif id_ == 5:
            self.cfg["bandeja_ao_fechar"] = not self.cfg.get("bandeja_ao_fechar", False)
            salvar_config(self.cfg)
        elif id_ == 9:
            self._sair()

    def _ao_destruir(self, ev):
        """Garante que o ícone sai da bandeja quando a janela principal é destruída (senão fica um 'fantasma')."""
        if ev.widget is self.root:
            self._vigia_ativo = False
            diag("janela principal destruída")
            if self.bandeja:
                self.bandeja.remover()

    def _clique(self, ev):
        # a coluna da checkbox é a #0; clicar nela (imagem ou o espaço ao lado) marca/desmarca a linha
        if self.tree.identify("region", ev.x, ev.y) not in ("tree", "cell") or self.tree.identify_column(ev.x) != "#0":
            return
        iid = self.tree.identify_row(ev.y)
        if iid:
            self._alternar(iid)

    def _alternar(self, iid):
        item = self.itens.get(iid)
        if not item or not item.get("acao") or self.ocupado or self._oculto(item):
            return
        item["marcado"] = not item["marcado"]
        self.tree.item(iid, image=self._caixas["on" if item["marcado"] else "off"])
        self._atualizar_resumo()

    def _marcar_todos(self, valor):
        for item in self.lista:
            if item.get("acao") and not self._oculto(item):
                item["marcado"] = valor
        self._redesenhar()

    def _menu_contexto(self, ev):
        iid = self.tree.identify_row(ev.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        item = self.itens[iid]
        chave = self._chave(item)
        self.menu.delete(0, "end")
        if chave in self.ignorados:
            self.menu.add_command(label="Deixar de ignorar", command=lambda: self._ignorar(chave, False))
        else:
            self.menu.add_command(label="Ignorar este programa", command=lambda: self._ignorar(chave, True))
        disp = item.get("disponivel")
        if disp and disp not in ("?", "—") and not eh_critico(item):
            if versao_pulada(item, self.puladas):
                self.menu.add_command(label="Deixar de pular esta versão",
                                      command=lambda: self._pular_versao(item, False))
            else:
                self.menu.add_command(label=f"Pular a versão {disp}", command=lambda: self._pular_versao(item, True))
        if item.get("acao"):
            direto = item["acao"][0][0] == "@direto"
            cmd = item["acao"][0][1] if direto else " && ".join(self._exibir_cmd(c) for c in item["acao"])
            self.menu.add_command(label="Copiar endereço do instalador" if direto else "Copiar comando",
                                  command=lambda: self._copiar(cmd))
        if item.get("pagina") and (not item.get("acao") or item.get("falhou")):
            # só aparece quando NÃO se achou instalador (nem no winget, nem no fabricante): aí sim o site é o caminho
            self.menu.add_command(label="Abrir a página de download (não achei instalador)",
                                  command=lambda: self._abrir_pagina(item))
        self.menu.tk_popup(ev.x_root, ev.y_root)

    def _abrir_pagina(self, item):
        url = item.get("pagina", "")
        if url.startswith("https://"):
            registrar_historico("pagina", item=item["nome"], detalhe=url)
            self._log(f"Abrindo a página do fabricante: {url}")
            webbrowser.open(url)

    def _ignorar(self, chave, sim):
        (self.ignorados.add if sim else self.ignorados.discard)(chave)
        for item in self.lista:
            if self._chave(item) == chave:
                if sim:
                    item["marcado"] = False
                registrar_historico("ignorado" if sim else "retomado", item=item["nome"])
        self._salvar_opcoes()
        self._redesenhar()

    def _pular_versao(self, item, sim):
        chave = self._chave(item)
        if sim:
            self.puladas[chave] = item["disponivel"]
            item["marcado"] = False
            registrar_historico("pulado", item=item["nome"], para=item["disponivel"])
        else:
            self.puladas.pop(chave, None)
            registrar_historico("retomado", item=item["nome"], para=item["disponivel"])
        self._salvar_opcoes()
        self._redesenhar()

    def _copiar(self, texto):
        self.root.clipboard_clear()
        self.root.clipboard_append(texto)
        self._log(f"Copiado: {texto}")

    @staticmethod
    def _exibir_cmd(cmd):
        if cmd[0] == "@direto":
            return f"baixar e instalar {cmd[5]} do site oficial (hash e assinatura conferidos)"
        prog = os.path.splitext(os.path.basename(cmd[0]))[0]
        return " ".join([prog] + cmd[1:])

    def _reabrir_admin(self):
        diag(f"CLIQUE em 'Reabrir como admin' (abrindo_admin={self._abrindo_admin}, atualizando={self.atualizando}, "
             f"ocupado={self.ocupado})")
        if self._abrindo_admin:
            return
        if self.atualizando:
            avisar(self.root, "Atualização em andamento",
                   "Espere a atualização terminar antes de reabrir como administrador.")
            return
        # --reabrir: a cópia nova espera esta fechar (instância única) em vez de achar que "já tem um aberto"
        if getattr(sys, "frozen", False):
            exe, params = sys.executable, "--reabrir"
        else:
            exe, params = sys.executable, f'"{os.path.abspath(__file__)}" --reabrir'
        self._abrindo_admin = True
        self._uac_inicio = time.monotonic()
        self._uac_avisos = set()
        self._tick_uac()
        # o aviso do UAC bloqueia quem o chamou: na thread da interface a janela ficaria "sem resposta"
        threading.Thread(target=self._tarefa_admin, args=(exe, params), daemon=True).start()

    def _tick_uac(self):
        """Enquanto espera o UAC (que desiste sozinho em 120 s): mostra os segundos e onde procurar o aviso,
        que costuma ficar escondido atrás de jogo ou app em tela cheia."""
        if not self._abrindo_admin:
            return
        s = int(time.monotonic() - self._uac_inicio)
        texto = f"Aguardando a confirmação do Windows (UAC)…  {s}s de 120s"
        if s >= 8:
            texto += ("   •   não apareceu? Pode estar atrás de um jogo/app em tela cheia (Alt+Tab). "
                      "Ou: botão direito no .exe › Executar como administrador")
        self.lb_status.configure(text=texto, fg=LARANJA if s >= 8 else TEXTO_2)
        for marco in (15, 30, 60, 100):
            if s >= marco and marco not in self._uac_avisos:
                self._uac_avisos.add(marco)
                diag(f"UAC ainda sem resposta há {s}s (o Windows desiste em 120s)")
        self.root.after(1000, self._tick_uac)

    def _tarefa_admin(self, exe, params):
        t0 = time.monotonic()
        diag(f"ShellExecute(runas) chamado: exe={exe!r} params={params!r}")
        try:
            r = lancar_como_admin(exe, params)
        except Exception as e:
            r = -1
            diag(f"ShellExecute levantou {e!r}")
        diag(f"ShellExecute retornou {r} após {time.monotonic() - t0:.1f}s "
             f"({'abriu' if r > 32 else 'UAC negado' if r == 5 else 'falhou'})")
        self.fila.put(("admin", r))

    def _fim_admin(self, r):
        diag(f"_fim_admin(r={r}): {'fechando esta cópia' if r > 32 else 'mantendo esta cópia aberta'}")
        self._abrindo_admin = False
        if r > 32 and self.atualizando:
            # o usuário começou uma atualização durante a espera do UAC: NÃO fechar no meio dela
            self._log("A cópia administradora abriu, mas esta janela está atualizando programas: feche-a quando "
                      "terminar.")
            liberar_instancia()
            return
        if r > 32:
            # a cópia administradora já foi lançada: soltar a trava e o ícone da bandeja para ela assumir
            liberar_instancia()
            if self.bandeja:
                self.bandeja.remover()
            self.root.destroy()
            return
        self._atualizar_resumo()
        espera = time.monotonic() - self._uac_inicio
        if r == 5 and espera >= 115:
            # o Windows cancela o aviso sozinho após 120 s: ninguém respondeu (o aviso provavelmente ficou escondido)
            aviso = ("O Windows cancelou o pedido de administrador: o aviso do UAC ficou 120 s sem resposta. "
                     "Provavelmente apareceu atrás de um jogo ou app em tela cheia. Tente de novo com o app à frente.")
            self._log(aviso)
            self.lb_status.configure(text="⚠ " + aviso, fg=LARANJA)
        else:
            self._log("Reabertura como administrador cancelada." if r == 5
                      else f"Não foi possível reabrir como administrador (código {r}).")

    def _processar_fila(self):
        try:
            if self.bandeja:
                try:
                    self.bandeja.processar()     # cliques/menu do ícone da bandeja
                except Exception as e:
                    self._log(f"Bandeja: erro ({e!r})")
            while True:
                try:
                    tipo, dado = self.fila.get_nowait()
                except queue.Empty:
                    break
                # um handler com erro não pode derrubar o laço (senão a UI fica "ocupada" para sempre)
                try:
                    if tipo == "log":
                        self._log(dado)
                    elif tipo == "status":
                        self.lb_status.configure(text=dado)
                    elif tipo == "admin":
                        self._fim_admin(int(dado))
                    elif tipo == "sem_efeito":
                        self._marcar_sem_efeito(*dado)
                    elif tipo == "pagina":
                        self._definir_pagina(*dado)
                    elif tipo == "falhas":
                        self.falhas = int(dado)
                    elif tipo == "itens":
                        self._carregar_itens(dado)
                    elif tipo == "fim":
                        if not self.atualizando:      # uma verificação que termina no meio de uma atualização não libera os botões
                            self._set_ocupado(False)
                        origem, self.origem_verificacao = self.origem_verificacao, "manual"
                        if origem == "agenda" and not self.atualizando:
                            self._avisar_se_pendente()
                        self._nova_tentativa_se_falhou(origem)
                    elif tipo == "reverificar":
                        self.atualizando = False
                        self._set_ocupado(False)
                        self.verificar()
                except Exception as e:
                    if tipo in ("fim", "reverificar"):
                        self.atualizando = False
                        self._set_ocupado(False)
                    try:
                        self._log(f"Erro interno ({tipo}): {e!r}")
                    except Exception:
                        pass
        finally:
            self.root.after(100, self._processar_fila)

    def _carregar_itens(self, itens):
        self.lista, self.itens = [], {}
        self.ultima_checagem = datetime.datetime.now()
        for n, item in enumerate(itens):
            item["iid"] = f"i{n}"
            item.setdefault("acao", None)
            if item.get("disponivel") and self.sem_efeito.get(chave_item(item)) == item["disponivel"]:
                # o winget já respondeu que não tem esta versão: não oferece de novo nem avisa (até sair outra)
                marcar_fora_do_winget(item)
            item["marcado"] = bool(item["acao"]) and item["status"].startswith("Atualiza") \
                and not self._oculto(item)
            self.lista.append(item)
            self.itens[item["iid"]] = item
        self._redesenhar()

    def _definir_pagina(self, chave, url):
        """Depois de o servidor do fabricante recusar o download: guarda a página dele (se o winget souber) para o menu."""
        for item in self.lista:
            if chave_item(item) == chave:
                item["falhou"] = True
                self.sem_efeito[chave] = str(item.get("disponivel", ""))   # não oferecer de novo e falhar toda vez
                marcar_fora_do_winget(item)
                if url:
                    item["pagina"] = url
                self._salvar_opcoes()
                self._log(f"{item['nome']}: o servidor do fabricante recusou o download automático; item ocultado até sair "
                          "outra versão (veja com 'Mostrar ignorados'). "
                          + ("Use o botão direito na linha > 'Abrir a página de download'." if url
                             else "Baixe o instalador manualmente no site do fabricante."))
        self._redesenhar()

    def _marcar_sem_efeito(self, chave, disponivel, nome, instalada, tag):
        """O winget terminou sem erro mas não instalou nada: explica e deixa de oferecer essa versão."""
        self.sem_efeito[chave] = disponivel
        self._salvar_opcoes()
        extra = ("\n\nEssa versão já saiu de suporte (EOL): considere migrar para uma versão suportada."
                 if tag == "eol" else "")
        texto = (f"O winget não conseguiu instalar a versão {disponivel} de {nome} (você tem a {instalada}). Nada foi "
                 f"instalado.\n\nIsso acontece quando o winget não tem essa versão ou não gerencia a instalação "
                 f"(ex.: duas versões do mesmo programa instaladas). Para esta versão, só baixando do site do "
                 f"fabricante.{extra}\n\nEste item sai da lista e não será oferecido de novo até sair uma versão "
                 f"diferente. Para vê-lo, ligue \"Mostrar ignorados\".")
        self._log(f"Sem efeito: {nome} {disponivel} não pôde ser instalado pelo winget; item ocultado.")
        self.root.after(300, lambda: avisar(self.root, "Nada foi atualizado", texto))

    def _nova_tentativa_se_falhou(self, origem):
        """Checagem agendada que falhou (rede/winget): erro só no log, e tenta de novo em 15, 30 e 45 min."""
        if origem != "agenda" or not self.falhas:
            if not self.falhas:
                self.tentativas_falha = 0
            return
        if self.tentativas_falha >= MAX_TENTATIVAS_FALHA:
            self._log("Verificação agendada falhou várias vezes; vai tentar de novo no próximo horário.")
            self.tentativas_falha = 0
            return
        self.tentativas_falha += 1
        espera = 15 * self.tentativas_falha
        self._log(f"Verificação incompleta ({self.falhas} falha(s)); nova tentativa em {espera} min.")
        self.root.after(espera * 60000, self._tentar_de_novo)

    def _tentar_de_novo(self):
        if self.ocupado:
            self.root.after(60000, self._tentar_de_novo)
        else:
            self.verificar("agenda")

    # ---- verificação ------------------------------------------------------
    def verificar(self, origem="manual"):
        if self.ocupado or self._confirmando:
            return
        self.origem_verificacao = origem if origem in ("manual", "agenda") else "manual"
        self._set_ocupado(True)
        self.lb_status.configure(text="Verificando...")
        self._log(f"\n[{datetime.datetime.now():%H:%M:%S}] Verificando atualizações...")
        threading.Thread(target=self._tarefa_verificar, args=(self.var_desconhecidos.get(),), daemon=True).start()

    def _tarefa_verificar(self, incluir_desconhecidos):
        falhas = [0]

        def log(t):
            # mensagens de falha da consulta são contadas: a tela nunca pode mostrar "em dia" quando a consulta falhou
            if t.startswith(("Não consegui consultar", "winget não respondeu", "Erro inesperado")) \
                    or "erro na checagem" in t:
                falhas[0] += 1
            self.fila.put(("log", t))
        try:
            winget = shutil.which("winget")
            itens_winget, ids_winget = [], set()
            lista_valida = False       # o winget devolveu uma lista confiável? (senão não dá para concluir "ele não tem")
            if winget:
                log("Consultando o winget (pode levar alguns segundos)...")
                args = [winget, "upgrade", "--accept-source-agreements"]
                if incluir_desconhecidos:
                    args.append("--include-unknown")
                saida = rodar(args, timeout=600)
                if saida is None or not saida.strip():
                    log("winget não respondeu (tempo esgotado ou sem saída).")
                pacotes = parse_winget(saida or "")
                lista_valida = bool(pacotes) or bool(re.search(r"nenhum|no installed package|no applicable|no available",
                                                               saida or "", re.I))
                if saida and saida.strip() and not lista_valida:
                    log("winget não respondeu uma lista válida (saída inesperada); não vou concluir nada sobre ele.")
                resolver_ids_truncados(winget, pacotes, log)
                for p in pacotes:
                    desconhecida = p["versao"].lower() in ("unknown", "desconhecido", "desconhecida") \
                        or p["versao"].startswith("<")
                    acao = None
                    if p["id"] and not id_truncado(p["id"]):
                        acao = [[winget, "upgrade", "--id", p["id"], "--exact"] + WINGET_ACEITE
                                + (["--include-unknown"] if desconhecida else [])]
                    itens_winget.append({"nome": p["nome"], "id": p["id"], "versao": p["versao"],
                                         "disponivel": p["disponivel"],
                                         "status": "Atualização" if acao else "Atualização — ID abreviado",
                                         "tag": "update", "tipo": p["origem"] or "winget", "acao": acao})
                    ids_winget.add(p["id"].lower())
                log(f"winget: {len(itens_winget)} programa(s) com atualização.")
            else:
                log("winget não encontrado. Instale o 'Instalador de Aplicativo' (App Installer) "
                    "pela Microsoft Store. Os runtimes serão checados mesmo assim, mas sem botão de atualizar.")

            runtimes = []
            for nome, func in (("Python", checar_python), ("Node.js", checar_node), ("Java", checar_java)):
                log(f"Checando {nome}...")
                try:
                    runtimes += func(winget, log)
                except Exception as e:
                    log(f"{nome}: erro na checagem ({e})")
            # Se o winget já listou a mesma atualização, não duplica
            runtimes = [r for r in runtimes
                        if not (r["status"].startswith("Atualiza") and str(r.get("id", "")).lower() in ids_winget)]
            # A fonte da "versão nova" dos runtimes (endoflife.date) costuma estar à frente do winget: se o winget não
            # lista essa atualização, ele não consegue instalá-la -> não oferece (nem avisa) algo impossível.
            # Quando há instalador OFICIAL do fabricante (achado e conferido antes), a atualização vira "instalador direto".
            for r in runtimes:
                a, d = r.get("acao"), r.get("instalador_direto")
                if not r["status"].startswith("Atualiza"):
                    continue
                sem_winget = not a or (a[0][0] == winget and str(r.get("id", "")).lower() not in ids_winget
                                       and lista_valida)
                if d and sem_winget:
                    r["acao"] = [["@direto", d["url"], d.get("sha256", ""), d["tipo"], d.get("assinante", ""), d["nome"]]]
                    r["status"] = r["status"].replace(" — manual", "")
                    log(f"{r['nome']}: o winget não oferece a {r['disponivel']}, mas há instalador oficial em "
                        f"{urllib.parse.urlparse(d['url']).hostname}; será baixado e verificado antes de instalar.")
                elif a and lista_valida and a[0][0] == winget and str(r.get("id", "")).lower() not in ids_winget:
                    log(f"{r['nome']}: a {r['disponivel']} existe no site oficial, mas o winget não a oferece "
                        f"(oculto; use \"Mostrar ignorados\" para ver).")
                    marcar_fora_do_winget(r)
            # "Nova versão": o winget precisa conhecer o pacote (ex.: Python 3.15 ainda não está lá). Se não conhece:
            # usa o instalador oficial do fabricante, se existir; senão esconde (não adianta oferecer o que dá erro).
            for r in runtimes:
                a, d = r.get("acao"), r.get("instalador_direto")
                if r.get("tag") != "novo" or r.get("fora_winget"):
                    continue
                if (not a and d) or (winget and a and a[0][0] == winget and not winget_conhece(winget, r["id"])):
                    if d:
                        r["acao"] = [["@direto", d["url"], d.get("sha256", ""), d["tipo"], d.get("assinante", ""), d["nome"]]]
                        log(f"{r['nome']}: o winget não tem esse pacote, mas há instalador oficial em "
                            f"{urllib.parse.urlparse(d['url']).hostname}; será baixado e verificado antes de instalar.")
                    elif a:
                        log(f"{r['nome']}: o winget não tem esse pacote e não achei instalador oficial (oculto).")
                        marcar_fora_do_winget(r)
            self.fila.put(("falhas", falhas[0]))
            self.fila.put(("itens", runtimes + itens_winget))
        except Exception as e:
            log(f"Erro inesperado: {e}")
            self.fila.put(("falhas", falhas[0]))
        finally:
            self.fila.put(("fim", None))

    # ---- atualização ------------------------------------------------------
    def atualizar(self):
        if self.ocupado or self._abrindo_admin or self._confirmando:
            return
        escolhidos = [i for i in self.lista if i["marcado"] and i.get("acao") and not self._oculto(i)]
        if not escolhidos:
            avisar(self.root, APP_NOME, "Marque pelo menos um programa na primeira coluna.")
            return
        nomes = "\n".join(f"• {i['nome']}  ({i['versao']} → {i['disponivel']})" for i in escolhidos[:25])
        if len(escolhidos) > 25:
            nomes += f"\n... e mais {len(escolhidos) - 25}"
        abertos = programas_abertos(escolhidos)
        if abertos:
            nomes += ("\n\nParecem estar abertos agora:\n" + "\n".join(f"• {a}" for a in abertos[:10])
                      + "\nSalve seu trabalho e feche-os antes, para o instalador não fechar à força.")
        proprio = [i["nome"] for i in escolhidos if atualiza_o_proprio_python(i)]
        if proprio:
            nomes += (f"\n\nAtenção: {', '.join(proprio)} é o Python que está executando este programa. "
                      "Atualizá-lo daqui pode falhar ou fechar o app no meio; prefira usar o .exe "
                      "ou rodar o comando manualmente.")
        diretos = [i["nome"] for i in escolhidos if i["acao"] and i["acao"][0][0] == "@direto"]
        if diretos:
            nomes += ("\n\nInstalador oficial do fabricante (o winget não tem): " + ", ".join(diretos)
                      + ".\nSerá baixado, e o hash e a assinatura digital são conferidos ANTES de instalar.")
        # enquanto o diálogo está aberto (laço aninhado), nenhuma verificação pode começar e trocar a lista por baixo
        self._confirmando = True
        try:
            confirmou = perguntar(self.root, f"Atualizar {len(escolhidos)} item(ns)?",
                                  f"{nomes}\n\nFeche os programas que serão atualizados (VS Code, navegadores etc).",
                                  ok="Atualizar", cancelar="Cancelar")
        finally:
            self._confirmando = False
        if not confirmou or self.ocupado or self._abrindo_admin:
            return
        self.atualizando = True
        self._set_ocupado(True)
        try:
            threading.Thread(target=self._tarefa_atualizar, args=(escolhidos,), daemon=True).start()
        except Exception as e:
            self.atualizando = False
            self._set_ocupado(False)
            self._log(f"Não consegui iniciar a atualização: {e}")

    def _tarefa_atualizar(self, escolhidos):
        log = lambda t: self.fila.put(("log", t))
        ok = falha = sem = 0
        try:
            for n, item in enumerate(escolhidos, 1):
                self.fila.put(("status", f"[{n}/{len(escolhidos)}] Atualizando {item['nome']}..."))
                log(f"\n=== {item['nome']} ===")
                sucesso, sem_efeito, detalhe = True, False, ""
                try:
                    for cmd in item["acao"]:
                        log("> " + self._exibir_cmd(cmd))
                        # "@direto" = instalador oficial do fabricante (baixa, confere hash/assinatura e instala)
                        code = instalar_direto(cmd, log) if cmd[0] == "@direto" else executar_streaming(cmd, log)
                        c32 = code & 0xFFFFFFFF
                        disp = str(item.get("disponivel", ""))
                        if c32 in CODIGOS_REINICIAR:
                            detalhe = "reinício pendente"
                            log("Instalado, mas o Windows pede para reiniciar o computador para concluir.")
                            continue
                        if c32 in CODIGOS_SEM_EFEITO and len(cmd) > 2 and cmd[0] != "@direto" and cmd[1] == "upgrade" \
                                and "--version" not in cmd and disp not in ("", "?", "—"):
                            # o winget listou a atualização mas "não achou nada aplicável" (ex.: duas versões do mesmo
                            # programa instaladas): tenta uma vez pedindo a versão exata
                            cmd2 = list(cmd) + ["--version", disp]
                            log(f"O winget respondeu 'nenhuma atualização aplicável'; tentando de novo pedindo a versão "
                                f"exata {disp}...")
                            log("> " + self._exibir_cmd(cmd2))
                            code = executar_streaming(cmd2, log)
                            c32 = code & 0xFFFFFFFF
                        if c32 == 0x8A150014 and cmd[0] != "@direto" and len(cmd) > 2 and cmd[1] == "upgrade":
                            # O winget LISTA o pacote como atualizável, mas o filtro --id não o acha. Acontece quando a
                            # instalação é de OUTRO TIPO que o manifesto do winget (ex.: PowerShell instalado como MSI,
                            # manifesto MSIX). NÃO instalar "por cima": isso cria uma SEGUNDA cópia ao lado da atual
                            # (aconteceu de verdade com o PowerShell 7.6.9).
                            detalhe = ("o winget lista o pacote mas não o encontra pelo ID: a sua instalação é de outro tipo que "
                                       "a do winget; instalar criaria uma segunda cópia")
                            sem_efeito = True
                            log("Nada foi instalado: o winget lista este pacote, mas não consegue atualizá-lo. Provável causa: "
                                "ele foi instalado de outro jeito (ex.: MSI) que o pacote do winget (ex.: MSIX), e instalar "
                                "criaria uma SEGUNDA cópia. Atualize pelo instalador do mesmo tipo, no site do fabricante.")
                            break
                        if c32 in CODIGOS_SEM_EFEITO:
                            detalhe = descrever_codigo(code)
                            sem_efeito = True
                            log(f"Nada foi instalado: {detalhe}")
                            break
                        if c32 not in CODIGOS_OK:
                            detalhe = descrever_codigo(code)
                            log(f"Falhou: {detalhe}")
                            sucesso = False
                            if falha_de_download_http(code) and cmd[0] != "@direto":
                                # o servidor do fabricante recusou o download: só aí vale procurar a página dele
                                self.fila.put(("pagina", (chave_item(item), pagina_do_winget(cmd[0], str(item.get("id", ""))))))
                            break
                except Exception as e:  # um item com problema não interrompe os demais
                    detalhe = repr(e)
                    log(f"Erro inesperado em {item['nome']}: {e!r}")
                    sucesso = False
                if sucesso and sem_efeito:
                    # "terminou sem erro" não é "atualizou": guarda para não oferecer de novo a mesma versão
                    registrar_historico("sem_efeito", item=item["nome"], de=item["versao"],
                                        para=item["disponivel"], detalhe=detalhe)
                    self.fila.put(("sem_efeito", (chave_item(item), item["disponivel"], item["nome"],
                                                  item["versao"], item["tag"])))
                    sem += 1
                else:
                    registrar_historico("atualizado" if sucesso else "falhou", item=item["nome"],
                                        de=item["versao"], para=item["disponivel"], detalhe=detalhe)
                    ok += sucesso
                    falha += not sucesso
            log(f"\nConcluído: {ok} atualizado(s), {sem} sem efeito, {falha} com erro. Verificando de novo...")
            if any(i["tipo"] == "Runtime" for i in escolhidos):
                log("Dica: feche e reabra terminais/VS Code para o PATH novo valer.")
        finally:
            self.fila.put(("reverificar", None))


def main():
    if os.name != "nt":
        print("Este programa é só para Windows.")
        return
    instalar_ganchos_de_erro()
    reabrindo = "--reabrir" in sys.argv[1:]   # veio do botão "Reabrir como admin": espera a cópia antiga fechar
    diag(f"==== INÍCIO argv={sys.argv[1:]} frozen={getattr(sys, 'frozen', False)} exe={sys.executable} "
         f"cwd={os.getcwd()} admin={eh_admin()}")
    if outra_instancia_aberta(espera_s=15 if reabrindo else 0):
        diag("saindo: já existe outra cópia aberta")
        return                                # já existe um aberto: ele mostra a janela e este sai
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    if not getattr(sys, "frozen", False):
        try:  # rodando o .py: agrupa na barra de tarefas com o ícone do app, não com o do python.exe
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Gian.AtualizadorApps")
        except Exception:
            pass
    root = tk.Tk()
    try:  # ícone da janela (no .exe o arquivo vai embutido; rodando o .py usa o app.ico da mesma pasta)
        root.iconbitmap(caminho_icone())
    except Exception:
        pass
    t0 = time.monotonic()
    App(root, minimizado="--minimizado" in sys.argv[1:])
    diag(f"interface pronta em {time.monotonic() - t0:.1f}s; entrando no laço principal")
    root.mainloop()
    diag("==== FIM (laço principal encerrado normalmente)")


if __name__ == "__main__":
    main()
