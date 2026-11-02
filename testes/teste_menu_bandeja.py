# -*- coding: utf-8 -*-
import importlib.util, sys, os, subprocess, tempfile, time, threading, ctypes
import ctypes.wintypes as wt
import tkinter as tk

alvo, destino = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("atualizador_apps", alvo)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.CONFIG_DIR = tempfile.mkdtemp(); m.CONFIG_PATH = os.path.join(m.CONFIG_DIR, "config.json")
m.HISTORICO_PATH = os.path.join(m.CONFIG_DIR, "h.jsonl")
m.definir_iniciar_com_windows = lambda a: True
try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception: pass
m.App._tarefa_verificar = lambda self, inc: self.fila.put(("fim", None))

root = tk.Tk(); app = m.App(root); root.geometry("+-3500+100")
def bomb(n=20):
    for _ in range(n): root.update(); time.sleep(0.02)
bomb(10)
u32 = ctypes.windll.user32
u32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
pt = wt.POINT(); u32.GetCursorPos(ctypes.byref(pt))
# garante que o menu apareça num lugar visível (cursor no meio da tela principal)
u32.SetCursorPos(1700, 900)
u32.GetCursorPos(ctypes.byref(pt))

resultado = {}
def fechar_depois():
    time.sleep(1.5)
    ps = ("Add-Type -AssemblyName System.Drawing;"
          f"$r=New-Object System.Drawing.Rectangle({pt.x-20},{pt.y-260},330,300);"
          "$b=New-Object System.Drawing.Bitmap($r.Width,$r.Height);"
          "$g=[System.Drawing.Graphics]::FromImage($b);$g.CopyFromScreen($r.Location,[System.Drawing.Point]::Empty,$r.Size);"
          f"$b.Save('{destino}');")
    subprocess.run(["pwsh", "-NoProfile", "-Command", ps])
    u32.keybd_event(0x1B, 0, 0, 0); u32.keybd_event(0x1B, 0, 2, 0)    # Esc fecha o menu
    resultado["fechou"] = True
threading.Thread(target=fechar_depois, daemon=True).start()

u32.PostMessageW(app.bandeja.hwnd, m.WM_APP_BANDEJA, 1, 0x205)         # WM_RBUTTONUP -> abre o menu
t0 = time.time()
while time.time() - t0 < 8 and not resultado.get("fechou"):
    root.update(); time.sleep(0.02)
bomb(30)
print("menu abriu e fechou sem crash:", resultado.get("fechou", False), "| app vivo:", root.winfo_exists())
print("print salvo:", os.path.exists(destino))
root.destroy()
