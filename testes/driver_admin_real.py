# -*- coding: utf-8 -*-
"""Teste REAL de 'Reabrir como admin' SEM depender do mouse (funciona mesmo com um jogo em tela cheia).
Abre o app de verdade (mesmo fluxo do main()), espera ~2 s e chama a MESMA função que o botão chama
(App._reabrir_admin). O Windows mostra o UAC de verdade; quem confirma é o usuário.
Escreve no diagnostico.log REAL (%APPDATA%\\AtualizadorApps). A cópia administradora fica aberta no fim.
Uso: pythonw driver_admin_real.py <atualizador_apps.py>"""
import importlib.util, sys, os, ctypes, time
import tkinter as tk

alvo = sys.argv[1]
spec = importlib.util.spec_from_file_location("atualizador_apps", alvo)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

m.instalar_ganchos_de_erro()
m.diag(f"==== INÍCIO (driver de teste) argv={sys.argv[1:]} exe={sys.executable} admin={m.eh_admin()}")
if m.outra_instancia_aberta(espera_s=0):
    m.diag("driver: já existe outra cópia aberta; saindo")
    sys.exit(0)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass
root = tk.Tk()
try:
    root.iconbitmap(m.caminho_icone())
except Exception:
    pass
app = m.App(root)
m.diag("driver: janela pronta; vou chamar _reabrir_admin() (o mesmo método do botão) em 2 s")
root.after(2000, app._reabrir_admin)
root.mainloop()
m.diag("==== FIM (driver de teste: laço principal encerrado)")
