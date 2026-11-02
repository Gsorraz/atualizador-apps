# -*- coding: utf-8 -*-
"""SONDA DE CLIQUE: SEM MOUSE, FORA DA TELA (nao incomoda jogo nem nada).
Abre o app de verdade com a janela em x=-3500 e, para cada ponto, pergunta ao Windows (WindowFromPoint) qual janela
receberia o clique. Ponto "furado" = o clique atravessa a janela e cai no que esta atras (transparente pela cor-chave).
Mostra um mapa do topo da janela e lista os controles cujo centro ou area util esta furada.
Uso: sonda_cliques.py <atualizador_apps.py> [sem-vidro]    Sai com codigo 1 se algum controle estiver furado."""
import importlib.util, sys, os, tempfile, time, ctypes
import ctypes.wintypes as wt
import tkinter as tk

alvo = sys.argv[1]
sem_vidro = len(sys.argv) > 2 and sys.argv[2] == "sem-vidro"
spec = importlib.util.spec_from_file_location("atualizador_apps", alvo)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.CONFIG_DIR = tempfile.mkdtemp(); m.CONFIG_PATH = os.path.join(m.CONFIG_DIR, "config.json")
m.DIAG_PATH = os.path.join(m.CONFIG_DIR, "d.log"); m.HISTORICO_PATH = os.path.join(m.CONFIG_DIR, "h.jsonl")
m.definir_iniciar_com_windows = lambda a: True
m.processos_em_execucao = lambda: set()
m.CLASSE_BANDEJA = "AtualizadorAppsBandeja_SONDA_%d" % os.getpid()      # nao toca na bandeja do app real
def _itens(self, inc):
    mk = lambda n, i, st="Atualização", tag="update", tipo="winget": {"nome": n, "id": i, "versao": "1", "disponivel": "2",
        "status": st, "tag": tag, "tipo": tipo, "acao": [["x"]]}
    self.fila.put(("falhas", 0))
    self.fila.put(("itens", [mk("Google Chrome", "Google.Chrome"), mk("Visual Studio Code", "Microsoft.VisualStudioCode"),
                             mk("Python 3.10", "Python.Python.3.10", "Atualização (sem suporte)", "eol", "Runtime"),
                             mk("Node.js 24", "OpenJS.NodeJS.LTS", "Em dia", "ok", "Runtime")]))
    self.fila.put(("fim", None))
m.App._tarefa_verificar = _itens
try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception: pass
if sem_vidro: m.aplicar_vidro = lambda r, a=True: False

u32 = ctypes.windll.user32
u32.WindowFromPoint.restype = ctypes.c_void_p
u32.GetAncestor.restype = ctypes.c_void_p; u32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]

root = tk.Tk(); app = m.App(root); root.geometry("+-3500+100")
def bombear(n, d=0.02):
    for _ in range(n): root.update(); time.sleep(d)
bombear(60)
meu = u32.GetAncestor(root.winfo_id(), 2) or root.winfo_id()
def pega(x, y):
    h = u32.WindowFromPoint(wt.POINT(int(x), int(y)))
    return bool(h) and (h == meu or u32.GetAncestor(h, 2) == meu)

X0, Y0, W, H = root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
print(f"vidro={app.vidro} | area util {W}x{H} | probe fora da tela em x={X0}")

# ---- mapa do topo (cada celula = 12x12 px): '#' pega o clique, '.' o clique ATRAVESSA
print("\nMAPA DO TOPO DA JANELA ('#' = pega o clique   '.' = atravessa para o que esta atras)")
for ly in range(0, 270, 12):
    print("%4d " % ly + "".join("#" if pega(X0 + lx + 6, Y0 + ly + 6) else "." for lx in range(0, min(W, 1140), 12)))

# ---- controles
def percorre(w, lista):
    for c in w.winfo_children():
        if isinstance(c, (m.Botao, m.Interruptor)): lista.append(c)
        percorre(c, lista)
    return lista
furados = []
print("\nCONTROLES (clique no centro / % da area que pega)")
for c in percorre(root, []):
    x0, y0, w, h = c.winfo_rootx(), c.winfo_rooty(), c.winfo_width(), c.winfo_height()
    centro = pega(x0 + w // 2, y0 + h // 2)
    pts = [(x0 + i * w / 8, y0 + j * h / 4) for i in range(1, 8) for j in range(1, 4)]
    pct = 100 * sum(pega(px, py) for px, py in pts) // len(pts)
    nome = getattr(c, "_texto", "")[:30]
    marca = "OK     " if centro and pct >= 60 else "FURADO "
    if marca.startswith("FURADO"): furados.append(nome)
    print(f"  {marca}{ascii(nome):34s} a {y0 - Y0:4d}px do topo | centro={'pega' if centro else 'ATRAVESSA'} | area que pega={pct:3d}%")
# ---- cobertura da janela inteira: nao pode existir NENHUM ponto em que o clique atravesse
buracos = [(lx, ly) for ly in range(0, H, 12) for lx in range(0, W, 12) if not pega(X0 + lx + 6, Y0 + ly + 6)]
total = len(range(0, H, 12)) * len(range(0, W, 12))
print(f"\nCOBERTURA: {total - len(buracos)}/{total} pontos da janela pegam o clique"
      + ("" if not buracos else f"  | {len(buracos)} buraco(s), ex.: {buracos[:5]}"))
ok_total = not furados and not buracos
print("RESULTADO SONDA:", "TUDO CLICAVEL" if ok_total else
      "%d controle(s) com clique atravessando: %s | %d buraco(s) na janela" % (len(furados), ascii(furados), len(buracos)))
root.destroy()
sys.exit(0 if ok_total else 1)
