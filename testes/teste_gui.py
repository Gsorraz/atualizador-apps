# -*- coding: utf-8 -*-
import importlib.util, sys, os, subprocess, tempfile, datetime, time
import tkinter as tk
import ctypes

alvo, saida_png = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("atualizador_apps", alvo)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.CONFIG_DIR = tempfile.mkdtemp()                       # nao toca no config real
m.CONFIG_PATH = os.path.join(m.CONFIG_DIR, "config.json")
m.definir_iniciar_com_windows = lambda a: True          # nao mexe no registro
m.HISTORICO_PATH = os.path.join(m.CONFIG_DIR, "historico.jsonl")
m.DIAG_PATH = os.path.join(m.CONFIG_DIR, "diagnostico_teste.log")   # nao polui o diagnostico.log real do usuario
m.processos_em_execucao = lambda: set()                 # nenhum "programa em uso" por padrao

try:
    m.ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# evita winget/rede reais: a "verificacao" devolve itens fixos
def falsa(self, incluir):
    self.fila.put(("itens", [
        {"nome": "Google Chrome", "id": "Google.Chrome", "versao": "1", "disponivel": "2", "status": "Atualização",
         "tag": "update", "tipo": "winget", "acao": [["x"]]},
        {"nome": "Visual Studio Code", "id": "Microsoft.VisualStudioCode", "versao": "1", "disponivel": "2",
         "status": "Atualização", "tag": "update", "tipo": "winget", "acao": [["x"]]},
        {"nome": "PowerShell 7.6.7.0-x64", "id": "Microsoft.PowerShell", "versao": "1", "disponivel": "2",
         "status": "Atualização", "tag": "update", "tipo": "winget", "acao": [["x"]]},
        {"nome": "Python 3.14 (nova versão)", "id": "Python.Python.3.14", "versao": "—", "disponivel": "3.14.1",
         "status": "Nova versão", "tag": "novo", "tipo": "Runtime", "acao": [["x"]]},
    ]))
    self.fila.put(("fim", None))
_verificar_real = m.App._tarefa_verificar               # a verdadeira, para testar a reconciliacao com o winget
m.App._tarefa_verificar = falsa

# Por padrao o teste roda FORA DA TELA (nada pisca no monitor). TESTE_VISIVEL=1 mostra tudo e tira prints.
VISIVEL = os.environ.get("TESTE_VISIVEL") == "1"
if not VISIVEL:
    _pos_orig = m.Aviso._posicionar
    def _pos_fora(self, w, h):
        _pos_orig(self, w, h)
        self.x = -4000
        self.win.geometry(f"{w}x{h}+{self.x}+{self.y_ini}")
    m.Aviso._posicionar = _pos_fora

res = []
def chk(c, n):
    print("OK   " if c else "FALHA", n); res.append(c)

root = tk.Tk()
app = m.App(root, minimizado=True)           # abre minimizado como no autostart
if not VISIVEL:
    root.geometry("+-3500+100")              # janela principal tambem fora da tela
root.update()
chk(root.state() == "withdrawn", "inicia --minimizado escondido (so o icone da bandeja)")

# 1) fila resiliente: um handler que explode nao pode travar a UI
def bomba(itens): raise RuntimeError("boom")
app._carregar_itens_orig = app._carregar_itens
app._carregar_itens = bomba
app.fila.put(("itens", []))
app.fila.put(("fim", None))
for _ in range(15):
    root.update(); root.after(20)
chk(app.ocupado is False, "UI sai de 'ocupado' mesmo com handler quebrado")
app._carregar_itens = app._carregar_itens_orig
app.fila.put(("log", "ainda processa a fila"))
for _ in range(10):
    root.update(); root.after(20)
chk("ainda processa a fila" in app.txt_log.get("1.0", "end"), "fila continua viva depois do erro")

# 2) verificacao da agenda -> pop-up com 4 pendencias
app.ocupado = False
app.verificar("agenda")
t0 = datetime.datetime.now()
while (app.aviso is None) and (datetime.datetime.now() - t0).seconds < 8:
    root.update(); root.after(20)
chk(app.aviso is not None and not app.aviso.fechado, "pop-up aparece apos verificacao agendada")

# 3) verificacao manual NAO mostra pop-up
app.aviso.fechar()
for _ in range(30):
    root.update(); root.after(20)
app.aviso = None
app.verificar("manual")
for _ in range(40):
    root.update(); root.after(20)
chk(app.aviso is None, "verificacao manual nao mostra pop-up")

# 4) mostra o pop-up de novo para o print e captura
app.ultimo_aviso = None            # senao a regra anti-repeticao (30 min) segura o segundo aviso
app.verificar("agenda")
t0 = datetime.datetime.now()
while (app.aviso is None) and (datetime.datetime.now() - t0).seconds < 8:
    root.update(); root.after(20)
for _ in range(40):
    root.update(); root.after(20)
w = app.aviso.win
x, y, ww, hh = w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()
print("toast geom:", x, y, ww, hh, "alpha", w.attributes("-alpha"))
ps = (
    "Add-Type -AssemblyName System.Drawing,System.Windows.Forms;"
    f"$r=New-Object System.Drawing.Rectangle({x-40},{y-40},{ww+80},{hh+80});"
    "$b=New-Object System.Drawing.Bitmap($r.Width,$r.Height);"
    "$g=[System.Drawing.Graphics]::FromImage($b);$g.CopyFromScreen($r.Location,[System.Drawing.Point]::Empty,$r.Size);"
    f"$b.Save('{saida_png}');"
)
if VISIVEL:
    subprocess.run(["pwsh", "-NoProfile", "-Command", ps], check=False)
    print("print salvo:", os.path.exists(saida_png))

# 5) clicar em Abrir restaura a janela
app.aviso.fechar()
app._mostrar_janela()
for _ in range(25):
    root.update(); time.sleep(0.04)
chk(root.state() == "normal", "'Abrir' restaura a janela principal")
print("vidro ativo:", app.vidro)

def captura(widget, destino, extra=40):
    if not VISIVEL:
        return
    widget.update(); time.sleep(0.3); widget.update()
    x, y, ww, hh = widget.winfo_rootx(), widget.winfo_rooty(), widget.winfo_width(), widget.winfo_height()
    ps = ("Add-Type -AssemblyName System.Drawing;"
          f"$r=New-Object System.Drawing.Rectangle({x-extra},{y-extra-30},{ww+2*extra},{hh+2*extra+30});"
          "$b=New-Object System.Drawing.Bitmap($r.Width,$r.Height);"
          "$g=[System.Drawing.Graphics]::FromImage($b);$g.CopyFromScreen($r.Location,[System.Drawing.Point]::Empty,$r.Size);"
          f"$b.Save('{destino}');")
    subprocess.run(["pwsh", "-NoProfile", "-Command", ps])

root.lift(); root.attributes("-topmost", True); root.update()
captura(root, saida_png.replace("toast", "janela"))
# tabela com dados: marca um item e captura de novo
app.tree.selection_set(app.tree.get_children()[1]); root.update()

root.attributes("-topmost", False); app.barra.start(12)
# dialogo escuro (texto longo -> caixa com rolagem)
longo = "\n".join(f"• Programa {i}  (1.0 → 2.0)" for i in range(30))
dlg = m.Caixa(root, "Atualizar 30 item(ns)?", longo + "\n\nFeche os programas que serão atualizados.", ok="Atualizar", cancelar="Cancelar")
dlg.attributes("-topmost", True)
captura(dlg, saida_png.replace("toast", "dialogo"), extra=20)
dlg._ok(); root.update()
chk(dlg.resultado is True, "dialogo escuro: OK devolve True")
dlg2 = m.Caixa(root, "Horários dos avisos", "Digite os horários (HH:MM).", ok="Salvar", cancelar="Cancelar", entrada="09:00, 13:00")
root.update()
chk(dlg2._campo.get() == "09:00, 13:00", "dialogo de texto traz o valor inicial")
dlg2._ok(); root.update()
chk(dlg2.resultado == "09:00, 13:00", "dialogo de texto devolve o texto")
dlg3 = m.Caixa(root, "x", "y", ok="OK", cancelar="Cancelar"); dlg3._cancelar(); root.update()
chk(dlg3.resultado is False, "dialogo: Cancelar devolve False")

# botao: clique valido mesmo sem o evento "mouse entrou" (cursor ja parado em cima do botao)
_cliques = []
_bt = m.Botao(app.root, "Teste", lambda: _cliques.append(1))
class _Ev: pass
_e = _Ev(); _e.x, _e.y = 10, 10
_bt._estado, _bt._press, _bt._sobre = "normal", True, False       # apertou sem ter recebido <Enter>
_bt._soltou(_e)
chk(_cliques == [1], "botao: solta DENTRO sem <Enter> previo aciona o comando")
_e.x = _bt._larg + 40
_bt._press = True; _bt._soltou(_e)
chk(_cliques == [1], "botao: solta FORA do botao nao aciona")
_bt._estado, _bt._press = "disabled", True; _e.x = 10; _bt._soltou(_e)
chk(_cliques == [1], "botao desativado nao aciona")
_bt.destroy()

# botoes em pilula respeitam state/text
app.bt_verificar.configure(state="disabled"); root.update()
chk(app.bt_verificar._estado == "disabled", "Botao aceita configure(state='disabled')")
app.bt_verificar.configure(state="normal")
app.bt_horarios.configure(text="Horários: 09:00  ✎"); root.update()
chk("09:00" in app.bt_horarios._texto, "Botao aceita configure(text=...)")
# interruptor acompanha a variavel
app.var_avisos.set(False)
for _ in range(30):
    root.update(); time.sleep(0.02)
chk(abs(app.var_avisos.get() - 0) == 0, "variavel do interruptor")
app.var_avisos.set(True)
root.attributes("-topmost", False)

# ===== icone na bandeja + icones da janela =====
chk(app.bandeja is not None and app.bandeja.ok, "bandeja: icone criado")
chk(app.bandeja.existe(), "bandeja: Windows confirma que o icone esta na area de notificacao")
u32 = ctypes.windll.user32
u32.SendMessageW.restype = ctypes.c_void_p
u32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
_hw = u32.GetParent(root.winfo_id()) or root.winfo_id()
chk(bool(u32.SendMessageW(_hw, 0x7F, 1, 0)), "janela tem icone GRANDE (barra de tarefas)")
chk(bool(u32.SendMessageW(_hw, 0x7F, 0, 0)), "janela tem icone PEQUENO (titulo)")
# clique esquerdo no icone da bandeja abre a janela (mensagem real do Windows para a janela da bandeja)
root.withdraw()
def _bomb(n=20):
    for _ in range(n):
        root.update(); time.sleep(0.02)
_bomb(5)
chk(root.state() == "withdrawn", "janela escondida antes do clique")
u32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
u32.PostMessageW(app.bandeja.hwnd, m.WM_APP_BANDEJA, 1, 0x202)       # WM_LBUTTONUP
_bomb(30)
chk(root.state() == "normal", "clique no icone da bandeja mostra a janela")
# pedido de outra instancia (segundo .exe aberto) tambem mostra a janela
root.withdraw(); _bomb(5)
u32.PostMessageW(app.bandeja.hwnd, m.WM_APP_ABRIR, 0, 0); _bomb(30)
chk(root.state() == "normal", "segunda instancia pede e a janela aparece")
if not VISIVEL:
    root.geometry("+-3500+100")
# menu da bandeja: itens e acoes
ids = [i[0] for i in app._itens_bandeja() if i[1]]
chk(ids == [1, 2, 3, 5, 9], "menu da bandeja: Abrir/Verificar/Pausar/Manter/Sair (%s)" % ids)
app._acao_bandeja(3); chk(m.pausado(app.pausado_ate, datetime.datetime.now()), "menu: pausar 2 h")
chk([i[0] for i in app._itens_bandeja() if i[1]] == [1, 2, 4, 5, 9], "menu: com pausa ativa aparece 'Retomar'")
app._acao_bandeja(4); chk(app.pausado_ate == "", "menu: retomar")
# fechar (X): por padrao encerra; com 'manter na bandeja' so esconde
app.cfg["bandeja_ao_fechar"] = True
app.cfg["aviso_bandeja_visto"] = True
app._fechar_app(); _bomb(5)
chk(root.state() == "withdrawn" and root.winfo_exists(), "X com 'manter na bandeja' esconde em vez de sair")
u32.PostMessageW(app.bandeja.hwnd, m.WM_APP_BANDEJA, 1, 0x202); _bomb(30)
chk(root.state() == "normal", "e o icone traz a janela de volta")
app.cfg["bandeja_ao_fechar"] = False
if not VISIVEL:
    root.geometry("+-3500+100")
app.bandeja.dica("teste de dica"); chk(app.bandeja.dica_atual == "teste de dica", "dica do icone atualiza")

# ===== checkbox "selecionar tudo" no cabecalho =====
app.ocupado = False
for i in app.lista:
    i["marcado"] = False
app._redesenhar()
def img_cab():
    return str(app.tree.heading("#0", "image")[0]) if isinstance(app.tree.heading("#0", "image"), tuple) else str(app.tree.heading("#0", "image"))
chk(app._estado_sel == "off" and str(app._caixas_cab["off"]) in img_cab(), "cabecalho: nada marcado = caixa vazia")
app._alternar_todos(); root.update()
sel = app._selecionaveis()
chk(sel and all(i["marcado"] for i in sel), "cabecalho: clique marca todos")
chk(app._estado_sel == "on" and str(app._caixas_cab["on"]) in img_cab(), "cabecalho: tudo marcado = caixa com visto")
sel[0]["marcado"] = False; app._atualizar_resumo()
chk(app._estado_sel == "parcial" and str(app._caixas_cab["parcial"]) in img_cab(), "cabecalho: parcial = caixa com traco")
# as linhas usam a MESMA imagem desenhada (nao simbolo de fonte): marcada x desmarcada
linha_marcada = [i for i in sel[1:] if i["marcado"]][0]
chk(str(app._caixas["on"]) in str(app.tree.item(linha_marcada["iid"], "image")), "linha marcada usa a caixa azul com visto")
app._alternar(sel[0]["iid"])
chk(str(app._caixas["on"]) in str(app.tree.item(sel[0]["iid"], "image")), "clicar na linha troca a imagem para marcada")
app._alternar(sel[0]["iid"])
chk(str(app._caixas["off"]) in str(app.tree.item(sel[0]["iid"], "image")), "clicar de novo volta para a caixa vazia")
sel[0]["marcado"] = False; app._atualizar_resumo()
app._alternar_todos(); root.update()
chk(all(i["marcado"] for i in app._selecionaveis()), "cabecalho: parcial + clique marca todos")
app._alternar_todos(); root.update()
chk(not any(i["marcado"] for i in app._selecionaveis()), "cabecalho: tudo marcado + clique desmarca todos")
app.ocupado = True; app._alternar_todos()
chk(not any(i["marcado"] for i in app._selecionaveis()), "cabecalho: ignorado enquanto ocupado")
app.ocupado = False
chk(app.tree.heading("#0", "command") != "", "cabecalho tem comando de clique")
for i in app.lista:
    i["marcado"] = bool(i.get("acao")) and i["status"].startswith("Atualiza")
app._redesenhar()

# ===== regras novas dos avisos =====
def bombear(n=15):
    for _ in range(n):
        root.update(); time.sleep(0.02)

def novo_aviso_possivel():
    """Minimiza a janela (como no autostart) e tenta avisar; devolve o aviso criado ou None."""
    app.aviso = None
    root.iconify(); bombear(5)
    app._avisar_se_pendente()
    bombear(5)
    return app.aviso

app.ocupado = False
app.ultimo_aviso = None; app.adiado_ate = None; app.pausado_ate = ""
av = novo_aviso_possivel()
chk(av is not None and not av.fixo, "aviso normal: aparece e NAO e fixo")
resta0 = av.resta
bombear(25)
chk(av.resta < resta0, "aviso normal: contagem regressiva ativa")
av.fechar(); bombear(15)

# anti-repeticao: mesma lista dentro de 30 min nao reaparece
app.ultimo_aviso = None
av = novo_aviso_possivel(); av.fechar(); bombear(15)
chk(novo_aviso_possivel() is None, "anti-repeticao: mesma lista em 30 min nao repete")

# trava geral: mesmo com lista DIFERENTE, nunca dois pop-ups com menos de 2 min de diferenca
app.ultimo_aviso = None
av = novo_aviso_possivel(); av.fechar(); bombear(15)
app.lista[0]["disponivel"] = "9.9"           # muda a lista -> assinatura diferente
chk(novo_aviso_possivel() is None, "trava geral: lista diferente em <2 min tambem nao reaparece")
app.lista[0]["disponivel"] = "2"
app.ultimo_aviso = (datetime.datetime.now() - datetime.timedelta(minutes=3), "outra")
av = novo_aviso_possivel()
chk(av is not None, "depois de 2 min, um aviso com lista diferente pode aparecer")
av.fechar(); bombear(15)

# "Depois": adia uma vez, registra e bloqueia novos avisos
app.ultimo_aviso = None
av = novo_aviso_possivel()
av._acao("depois"); bombear(15)
chk(app.adiado_ate is not None and app.adiado_ate > datetime.datetime.now(), "'Depois' adia por 2 h")
app.ultimo_aviso = None
chk(novo_aviso_possivel() is None, "durante o adiamento nao avisa")
chk(any(r["acao"] == "adiado" for r in m.ler_historico(10)), "'Depois' foi registrado no historico")
app.adiado_ate = None

# lembrete pos-"Depois" nao oferece adiar de novo
app.reaviso = True; app.ultimo_aviso = None
av = novo_aviso_possivel()
chk(av is not None and app.reaviso is False, "lembrete pos-adiamento aparece e consome a flag")
av.fechar(); bombear(15)

# pausa
app._definir_pausa(2); app.ultimo_aviso = None
chk(m.pausado(app.pausado_ate, datetime.datetime.now()) and "Pausado" in app.bt_pausa._texto, "pausa de 2 h ativa e rotulada")
chk(novo_aviso_possivel() is None, "pausado: nao avisa")
app._definir_pausa(0)
chk(app.pausado_ate == "" and "Pausar" in app.bt_pausa._texto, "retomar libera os avisos")

# programa em uso (reuniao) segura o aviso e agenda nova tentativa
m.processos_em_execucao = lambda: {"teams", "chrome"}
app.ultimo_aviso = None
chk(novo_aviso_possivel() is None, "reuniao aberta (teams): aviso espera")
chk("em uso" in app.txt_log.get("1.0", "end"), "motivo da espera registrado no log")
m.processos_em_execucao = lambda: set()

# critico: fixo e sem 'Pular versao'
app.lista.append({"nome": "Python 3.8 (EOL)", "id": "Python.Python.3.8", "versao": "3.8.1", "disponivel": "3.8.20",
                  "status": "Atualização (sem suporte)", "tag": "eol", "tipo": "Runtime", "acao": [["x"]],
                  "marcado": False, "iid": "i99"})
app.itens["i99"] = app.lista[-1]
app.ultimo_aviso = None
av = novo_aviso_possivel()
chk(av is not None and av.fixo, "atualizacao critica: aviso FIXO")
r0 = av.resta
bombear(30)
chk(av.resta == r0 and not av.fechado, "critico nao some sozinho")
chk(not app.puladas.get("Python.Python.3.8"), "critico nao e pulavel")
av.fechar(); bombear(15)
app.lista.pop(); app.itens.pop("i99")

# pular versao: some das pendencias, aparece como pulada e volta quando sai versao nova
app.ultimo_aviso = None
av = novo_aviso_possivel()
av._acao("pular"); bombear(15)
chk(app.puladas.get("Google.Chrome") == "2", "pular guarda a versao pulada")
chk(m.pendencias(app.lista, app.ignorados, app.puladas) == [], "apos 'Pular versao' nao ha pendencias")
app.var_mostrar_ign.set(True); app._redesenhar(); bombear(5)
chk("pulada" in app.tree.item(app.tree.get_children()[0], "values")[4], "linha marcada como '(versao pulada)' (coluna Situacao)")
app.var_mostrar_ign.set(False)
chk("pulado" in str([r["acao"] for r in m.ler_historico(20)]), "'Pular' registrado no historico")
app.lista[0]["disponivel"] = "3"
chk(len(m.pendencias(app.lista, app.ignorados, app.puladas)) >= 1, "versao mais nova volta a ser pendencia")
app.lista[0]["disponivel"] = "2"
app.puladas.clear(); app._redesenhar()

# falha de consulta nunca parece 'em dia'
lista_orig = [dict(i) for i in app.lista]
app.fila.put(("falhas", 2)); app.fila.put(("itens", [])); bombear(15)
chk("incompleta" in app.lb_status.cget("text"), "falha na consulta aparece no resumo (nao vira 'em dia')")
app.fila.put(("falhas", 0)); app.fila.put(("itens", [dict(i) for i in lista_orig])); bombear(15)
chk("incompleta" not in app.lb_status.cget("text"), "sem falhas, aviso de incompleta some")
chk(len(app.lista) == len(lista_orig), "lista restaurada para os testes seguintes")

# nova tentativa apos falha agendada: 15 min, depois 30...
agendado = []
app.root.after_orig = app.root.after
app.root.after = lambda ms, fn=None, *a: (agendado.append(ms), None)[1] if ms >= 60000 else app.root.after_orig(ms, fn, *a)
app.falhas = 1; app.tentativas_falha = 0
app._nova_tentativa_se_falhou("agenda"); app._nova_tentativa_se_falhou("agenda")
chk(agendado[:2] == [15 * 60000, 30 * 60000], "retry em 15 e 30 min (%s)" % agendado[:2])
app.tentativas_falha = m.MAX_TENTATIVAS_FALHA
n_antes = len(agendado)
app._nova_tentativa_se_falhou("agenda")
chk(len(agendado) == n_antes, "retry para depois de %d tentativas" % m.MAX_TENTATIVAS_FALHA)
app._nova_tentativa_se_falhou("manual")
chk(len(agendado) == n_antes, "checagem manual nao agenda retry")
app.root.after = app.root.after_orig
app.falhas = 0

# reverificar ao voltar para a janela quando a checagem esta velha
chamadas = []
app.verificar = lambda o="manual": chamadas.append(o)
app.ocupado = False
class Ev: pass
ev = Ev(); ev.widget = root
app.ultima_checagem = datetime.datetime.now() - datetime.timedelta(hours=3)
app._ao_focar(ev)
chk(chamadas == ["manual"], "foco com checagem velha (3 h) reverifica")
app.ultima_checagem = datetime.datetime.now() - datetime.timedelta(minutes=10)
app._ao_focar(ev)
chk(chamadas == ["manual"], "foco com checagem recente nao reverifica")

# captura do aviso critico e do normal para conferir o visual
app.ultima_checagem = datetime.datetime.now()
app.lista.append({"nome": "Python 3.8 (EOL)", "id": "Python.Python.3.8", "versao": "3.8.1", "disponivel": "3.8.20",
                  "status": "Atualização (sem suporte)", "tag": "eol", "tipo": "Runtime", "acao": [["x"]],
                  "marcado": False, "iid": "i98"})
app.ultimo_aviso = None
av = novo_aviso_possivel(); bombear(20)
captura(av.win, saida_png.replace("toast", "aviso_critico"), extra=30)
av.fechar(); bombear(15); app.lista.pop()
app.ultimo_aviso = None
av = novo_aviso_possivel(); bombear(20)
captura(av.win, saida_png.replace("toast", "aviso_normal"), extra=30)
av.fechar(); bombear(15)
root.deiconify(); bombear(5)

# ===== winget "nenhuma atualizacao aplicavel" NAO e sucesso (caso real: Python 3.8, so tem instalador ate a 3.8.10) =====
def item_teste(nome="Python 3.8", id_="Python.Python.3.8", disp="3.8.20", tag="eol"):
    return {"nome": nome, "id": id_, "versao": "3.8.10", "disponivel": disp, "status": "Atualização (sem suporte)",
            "tag": tag, "tipo": "Runtime", "acao": [["winget", "upgrade", "--id", id_]], "marcado": True, "iid": "i77"}
avisos_se = []
_avisar_orig = m.avisar
m.avisar = lambda *a, **k: avisos_se.append(a)
_exec_orig = m.executar_streaming
app.verificar = lambda o="manual": None                  # nao deixa o "reverificar" recarregar a lista falsa
app.ocupado = False
m.executar_streaming = lambda cmd, log: 0x8A15002B       # exatamente o codigo que o winget devolveu nesta maquina
app._tarefa_atualizar([item_teste()]); bombear(40)
ult = m.ler_historico(1)[0]
chk(ult["acao"] == "sem_efeito" and ult["item"] == "Python 3.8", "winget 'nenhuma atualizacao aplicavel' NAO vira 'atualizado' (historico: %s)" % ult["acao"])
chk(app.sem_efeito.get("Python.Python.3.8") == "3.8.20", "a versao sem instalador fica guardada")
chk(len(avisos_se) == 1 and "Nada foi atualizado" in avisos_se[0][1] and "3.8.20" in avisos_se[0][2], "usuario recebe a explicacao do que aconteceu")
app.fila.put(("itens", [item_teste()])); bombear(20)
novo = app.lista[0]
chk(novo["status"] == "Fora do winget" and not novo.get("acao"), "proxima verificacao: sem botao de atualizar")
chk(novo["iid"] not in app.tree.get_children(), "e some da lista (so aparece com 'Mostrar ignorados')")
app.var_mostrar_ign.set(True); app._redesenhar()
chk(novo["iid"] in app.tree.get_children(), "com 'Mostrar ignorados' ele aparece")
app.var_mostrar_ign.set(False); app._redesenhar()
chk("fora do winget" in app.lb_status.cget("text"), "resumo informa quantos estao fora do winget")
chk(m.pendencias(app.lista, app.ignorados, app.puladas) == [] and not m.eh_critico(novo), "e deixa de ser pendencia/critico (para de avisar)")
app.fila.put(("itens", [item_teste(disp="3.8.21")])); bombear(20)
chk(app.lista[0]["status"].startswith("Atualiza") and bool(app.lista[0].get("acao")), "uma versao MAIS NOVA volta a ser oferecida")
m.executar_streaming = lambda cmd, log: 0
app._tarefa_atualizar([item_teste(id_="X.Ok", nome="App Ok")]); bombear(20)
chk(m.ler_historico(1)[0]["acao"] == "atualizado", "codigo 0 continua sendo 'atualizado'")
m.executar_streaming = lambda cmd, log: 0x8A150101      # programa aberto: falha comum
app._tarefa_atualizar([item_teste(id_="X.Falha", nome="App Falha")]); bombear(20)
chk(m.ler_historico(1)[0]["acao"] == "falhou", "outro codigo continua sendo 'falhou'")
m.executar_streaming = lambda cmd, log: 0x8A150014      # lista mas nao acha pelo --id: NAO e falha nem sucesso, e nao instala nada
app._tarefa_atualizar([item_teste(id_="X.Outro", nome="App Outro Tipo")]); bombear(20)
chk(m.ler_historico(1)[0]["acao"] == "sem_efeito", "0x8A150014 vira 'sem efeito' (nunca tenta instalar por cima)")
time.sleep(0.5); bombear(20)           # deixa disparar o aviso (root.after 300 ms) ainda com `avisar` trocado por stub

# "nenhuma atualizacao aplicavel" -> tenta UMA vez pedindo a versao exata; se der certo, e atualizado de verdade
chamadas_v = []
def exec_retry(cmd, log):
    chamadas_v.append(list(cmd))
    return 0x8A15002B if "--version" not in cmd else 0
m.executar_streaming = exec_retry
app._tarefa_atualizar([item_teste(id_="Microsoft.SQLServer.OLEDBDriver", nome="Driver OLE DB", disp="19.4.3")]); bombear(25)
chk(len(chamadas_v) == 2 and chamadas_v[1][-2:] == ["--version", "19.4.3"], "sem efeito: tenta de novo com --version 19.4.3 (%s)" % chamadas_v[-1][-2:])
chk(m.ler_historico(1)[0]["acao"] == "atualizado", "se a 2a tentativa (versao exata) funciona, vira 'atualizado'")
chamadas_v.clear()
m.executar_streaming = lambda cmd, log: (chamadas_v.append(list(cmd)), 0x8A15002B)[1]
app._tarefa_atualizar([item_teste(id_="Outro.Pacote", nome="Outro", disp="2.0")]); bombear(25)
chk(len(chamadas_v) == 2, "sem efeito nas duas tentativas: so tenta 2 vezes (nao entra em laco)")
chk(m.ler_historico(1)[0]["acao"] == "sem_efeito", "e nas duas vira 'sem_efeito'")
app.sem_efeito.pop("Outro.Pacote", None)
m.executar_streaming = _exec_orig
m.avisar = _avisar_orig
del app.verificar
app.sem_efeito.clear(); app.atualizando = False; app.ocupado = False
app.fila.put(("itens", [dict(i) for i in lista_orig])); bombear(20)

# ===== verificacao REAL: runtime que so existe no site oficial (nao no winget) nao pode ser oferecido =====
_winget_saida = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "winget_real.txt"), encoding="utf-8").read()
_orig = (m.shutil.which, m.rodar, m.checar_python, m.checar_node, m.checar_java, m.resolver_ids_truncados)
m.shutil.which = lambda n, *a, **k: r"C:\falso\winget.exe" if n == "winget" else None
m.rodar = lambda args, timeout=300: _winget_saida                # a lista real de upgrades (4 pacotes, sem Java)
m.resolver_ids_truncados = lambda *a, **k: None
_wg = r"C:\falso\winget.exe"
def _rt(nome, id_, st, tag, versao, disp):
    return {"nome": nome, "id": id_, "versao": versao, "disponivel": disp, "status": st, "tag": tag, "tipo": "Runtime",
            "acao": [[_wg, "install", "--id", id_, "--exact"]]}
m.checar_python = lambda w, log: [_rt("Python 3.8", "Python.Python.3.8", "Atualização (sem suporte)", "eol", "3.8.10", "3.8.20"),
                                  _rt("Python 3.15 (nova versão)", "Python.Python.3.15", "Nova versão", "novo", "—", "3.15.1")]
m.checar_node = lambda w, log: []
m.checar_java = lambda w, log: [_rt("Java 21 (Temurin)", "EclipseAdoptium.Temurin.21.JDK", "Atualização", "update", "21.0.12.1", "21.0.13+9")]
app.ocupado = False
while not app.fila.empty():
    app.fila.get_nowait()
_verificar_real(app, False); bombear(30)
por_id = {i["id"]: i for i in app.lista}
chk(por_id["EclipseAdoptium.Temurin.21.JDK"]["status"] == "Fora do winget" and not por_id["EclipseAdoptium.Temurin.21.JDK"]["acao"],
    "Java 21: o winget nao oferece a 21.0.13 -> 'Fora do winget', sem botao")
chk(por_id["Python.Python.3.8"]["status"] == "Fora do winget", "Python 3.8: idem (3.8.20 so existe como codigo-fonte)")
chk(por_id["Python.Python.3.15"]["status"] == "Nova versão" and bool(por_id["Python.Python.3.15"]["acao"]),
    "Python 3.15 (versao NOVA) continua instalavel pelo winget")
chk(por_id["Microsoft.PowerShell"]["status"].startswith("Atualiza") and bool(por_id["Microsoft.PowerShell"]["acao"]),
    "o que o winget REALMENTE lista (PowerShell) continua com botao de atualizar")
chk(all(i["iid"] not in app.tree.get_children() for i in app.lista if i.get("fora_winget")), "os 'fora do winget' ficam ocultos")
chk(not any(i.get("fora_winget") and i["marcado"] for i in app.lista), "e nunca vem marcados para atualizar")
(m.shutil.which, m.rodar, m.checar_python, m.checar_node, m.checar_java, m.resolver_ids_truncados) = _orig
app.fila.put(("itens", [dict(i) for i in lista_orig])); bombear(20)

# 6) fechar durante atualizacao pede confirmacao (simulado)
perguntou = []
m.perguntar = lambda *a, **k: (perguntou.append(1), False)[1]
app.atualizando = True
app._fechar_app(); root.update()
chk(perguntou and root.winfo_exists(), "fechar durante atualizacao pergunta e respeita 'nao'")
app.atualizando = False

# 7) agenda dispara pelo tick
app.ocupado = False
disparou = []
app.verificar = lambda o="manual": disparou.append(o)
class FakeDT(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 25, 13, 0, 10)
m.datetime.datetime = FakeDT
app.horarios = ["13:00"]; app.var_avisos.set(True); app.disparados.clear()
app._tick_agenda()
chk(disparou == ["agenda"], "tick da agenda dispara a verificacao no horario")
app._tick_agenda()
chk(disparou == ["agenda"], "tick nao repete o mesmo horario")

# ===== vigia: detecta travamento da interface e grava ONDE travou =====
m.DIAG_PATH = os.path.join(m.CONFIG_DIR, "diagnostico_teste.log")
def travar_3s():
    time.sleep(3.0)                     # simula a interface presa
root.after(300, travar_3s)
t1 = time.time()
while time.time() - t1 < 6.5:
    root.update(); time.sleep(0.02)
log_diag = open(m.DIAG_PATH, encoding="utf-8").read() if os.path.exists(m.DIAG_PATH) else ""
chk("INTERFACE TRAVADA" in log_diag, "vigia: registrou a trava da interface")
chk("travar_3s" in log_diag and "sleep" in log_diag, "vigia: gravou a pilha mostrando ONDE estava parada")
chk("voltou a responder" in log_diag, "vigia: registrou quando a interface voltou")

# ===== Reabrir como admin: nao congela, respeita UAC negado e passa o bastao =====
app.ocupado = False
avisos_adm = []
m.avisar = lambda *a, **k: avisos_adm.append(a)
app.atualizando = True
m.lancar_como_admin = lambda e, p: (_ for _ in ()).throw(AssertionError("nao deveria lancar durante atualizacao"))
app._reabrir_admin()
chk(len(avisos_adm) == 1 and not app._abrindo_admin, "admin: bloqueado e avisa enquanto atualiza")
app.atualizando = False

chamadas_adm = []
def uac_negado(exe, params):
    chamadas_adm.append((exe, params)); time.sleep(1.5); return 5      # usuario demora e depois nega o UAC
m.lancar_como_admin = uac_negado
t0 = time.time(); app._reabrir_admin(); dt = time.time() - t0
chk(dt < 0.5, "admin: o clique volta na hora, sem esperar o UAC (%.2fs)" % dt)
chk("Aguardando" in app.lb_status.cget("text"), "admin: mostra 'Aguardando a confirmacao do Windows (UAC)'")
app._reabrir_admin()
chk(len(chamadas_adm) <= 1, "admin: segundo clique durante a espera e ignorado")
ciclos, t1 = 0, time.time()
while time.time() - t1 < 2.4:
    root.update(); time.sleep(0.02); ciclos += 1
chk(ciclos > 60, "admin: a interface continua respondendo durante o UAC (%d ciclos)" % ciclos)
chk(chamadas_adm and "--reabrir" in chamadas_adm[0][1], "admin: relancado com --reabrir (%s)" % (chamadas_adm[0][1] if chamadas_adm else "-"))
chk(root.winfo_exists() and not app._abrindo_admin, "admin: UAC negado mantem o app aberto")
chk("cancelada" in app.txt_log.get("1.0", "end"), "admin: cancelamento registrado no log")

# contador de espera e dica de "UAC atras do jogo"; e mensagem propria quando o Windows desiste em 120 s
m.lancar_como_admin = lambda e, p: (time.sleep(2.6), 5)[1]
app._reabrir_admin()
app._uac_inicio = time.monotonic() - 9                 # como se ja esperasse 9 s
t1 = time.time()
while time.time() - t1 < 1.4:
    root.update(); time.sleep(0.02)
txt_espera = app.lb_status.cget("text")
chk(" de 120s" in txt_espera, "admin: mostra o contador de segundos ('%s')" % txt_espera[:50].encode("ascii", "replace").decode())
chk("tela cheia" in txt_espera and "Alt+Tab" in txt_espera, "admin: apos 8 s explica onde procurar o UAC (jogo/tela cheia)")
t1 = time.time()
while time.time() - t1 < 2.6:
    root.update(); time.sleep(0.02)
chk(not app._abrindo_admin, "admin: espera encerrada")
app._abrindo_admin = True; app._uac_inicio = time.monotonic() - 125
app._fim_admin(5)
txt_fim = app.lb_status.cget("text")
chk("120 s" in txt_fim and "tela cheia" in txt_fim, "admin: UAC sem resposta em 120 s tem mensagem propria")
app._abrindo_admin = True; app._uac_inicio = time.monotonic() - 3
app._fim_admin(5)
chk("120 s" not in app.lb_status.cget("text"), "admin: 'Nao' rapido no UAC nao usa a mensagem de tempo esgotado")

# ===== achados da revisao independente =====
# aceitar o UAC no meio de uma atualizacao NAO pode fechar a janela (o winget ficaria orfao)
app.atualizando = True; app.ocupado = True; app._abrindo_admin = True
_destroy_orig = app.root.destroy; destruiu = []
app.root.destroy = lambda: destruiu.append(1)
app._fim_admin(42); root.update()
chk(not destruiu, "admin aceito DURANTE uma atualizacao: nao fecha a janela no meio")
app.root.destroy = _destroy_orig
# uma verificacao que termina no meio da atualizacao nao libera os botoes
app.fila.put(("fim", None))
for _ in range(8):
    root.update(); time.sleep(0.02)
chk(app.ocupado is True, "evento 'fim' durante a atualizacao nao libera os botoes")
app.atualizando = False; app.ocupado = False
# com o dialogo de confirmacao aberto, nenhuma verificacao comeca (e nao se inicia 2a atualizacao)
app._confirmando = True
app.verificar("manual")
chk(app.ocupado is False, "dialogo de confirmacao aberto: verificar() nao inicia")
n_antes = len(app.lista)
app.atualizar()
chk(app.atualizando is False, "dialogo de confirmacao aberto: atualizar() nao inicia outra")
app._confirmando = False
# menu do botao direito: 'Abrir pagina' so aparece quando NAO ha acao (nao se achou instalador)
paginas = []
app.menu.tk_popup = lambda *a, **k: None
_wb = m.webbrowser.open; m.webbrowser.open = lambda u: paginas.append(u)
sem_instalador = {"nome": "Python 3.8", "id": "Python.Python.3.8", "versao": "3.8.10", "disponivel": "3.8.20", "status": "Sem suporte (EOL)",
                  "tag": "eol", "tipo": "Runtime", "acao": None, "pagina": "https://www.python.org/downloads/release/python-3820/",
                  "marcado": False, "iid": "i501"}
com_acao = dict(sem_instalador, acao=[["x"]], iid="i502", nome="Com acao")
app.lista += [sem_instalador, com_acao]; app.itens.update({"i501": sem_instalador, "i502": com_acao}); app._redesenhar()
class _EvM: pass
def menu_de(iid):
    app.tree.see(iid); root.update()                      # a linha pode estar fora da parte visivel
    e = _EvM(); bx, by, bw, bh = app.tree.bbox(iid); e.y = by + bh // 2; e.x_root = e.y_root = 0
    app._menu_contexto(e)
    return [app.menu.entrycget(i, "label") for i in range(app.menu.index("end") + 1) if app.menu.type(i) == "command"]
rotulos = menu_de("i501")
chk(any("página de download" in r for r in rotulos), "sem instalador achado: o menu oferece abrir a pagina do fabricante")
chk(not any("página de download" in r for r in menu_de("i502")), "com acao de atualizar: o menu NAO oferece o site")
app._abrir_pagina(sem_instalador)
chk(paginas == ["https://www.python.org/downloads/release/python-3820/"], "abrir a pagina usa o endereco https do fabricante")
app._abrir_pagina(dict(sem_instalador, pagina="javascript:alert(1)"))
chk(len(paginas) == 1, "endereco que nao e https nunca e aberto")
m.webbrowser.open = _wb
app.lista = [i for i in app.lista if i["iid"] not in ("i501", "i502")]; app._redesenhar()

# opcoes escolhidas pelo usuario ficam guardadas no config
_ok0, _ign0 = app.var_mostrar_ok.get(), app.var_mostrar_ign.get()
app.var_mostrar_ok.set(False); app.var_mostrar_ign.set(True); app._mudou_filtro()
_cfg = m.carregar_config()
chk(_cfg.get("mostrar_em_dia") is False and _cfg.get("mostrar_ignorados") is True, "opcoes 'Mostrar em dia/ignorados' sao gravadas no config")
app.cfg = _cfg
app.var_mostrar_ok.set(not _cfg["mostrar_em_dia"])        # simula reabrir: o valor volta do config
chk(bool(_cfg.get("mostrar_em_dia", True)) is False and bool(_cfg.get("mostrar_ignorados", False)) is True, "ao reabrir o app le as opcoes gravadas")
app.var_mostrar_ok.set(_ok0); app.var_mostrar_ign.set(_ign0); app._mudou_filtro()

# HTTP 403 (ex.: Intel XTU): o item sai da lista e nao volta marcado a cada verificacao
intel = {"nome": "Intel XTU", "id": "Intel.IntelExtremeTuningUtility", "versao": "7.14.0", "disponivel": "7.14.2.69",
         "status": "Atualização disponível", "tag": "novo", "tipo": "App", "origem": "winget",
         "acao": [["winget", "upgrade", "--id", "Intel.IntelExtremeTuningUtility"]], "marcado": True, "iid": "i503"}
app.lista.append(intel); app.itens["i503"] = intel; app._redesenhar()
app._definir_pagina(m.chave_item(intel), "https://www.intel.com/xtu")
chk(intel["acao"] is None and intel["status"] == "Fora do winget" and not intel["marcado"],
    "403: o item deixa de ser oferecido (sem acao, desmarcado)")
chk(app.sem_efeito.get(m.chave_item(intel)) == "7.14.2.69", "403: a versao fica registrada como sem efeito")
chk(intel["pagina"] == "https://www.intel.com/xtu", "403: a pagina do fabricante fica disponivel no menu")
novo = dict(intel, acao=[["winget", "upgrade"]], status="Atualização disponível", tag="novo", marcado=False, fora_winget=False)
_lista_antes, _itens_antes = list(app.lista), dict(app.itens)
app._carregar_itens([novo])
chk(novo["acao"] is None and not novo["marcado"], "403: na proxima verificacao o mesmo item continua oculto (nao volta marcado)")
app.lista, app.itens = [i for i in _lista_antes if i["iid"] != "i503"], {k: v for k, v in _itens_antes.items() if k != "i503"}
app._redesenhar()

# Ocultos ao carregar: linha no log (sem "Mostrar ignorados") e texto do resumo
_ign_antes = app.var_mostrar_ign.get()
_oc = {"nome": "Zeta Teste Oculto", "id": "Zeta.Oculto", "versao": "1", "disponivel": "2", "status": "Atualização",
       "tag": "update", "tipo": "winget", "acao": [["x"]], "marcado": False}
m.marcar_fora_do_winget(_oc)
_msgs = []
_log_orig = app._log
app._log = lambda t: (_msgs.append(t), _log_orig(t))
try:
    app.var_mostrar_ign.set(False)
    app._carregar_itens([dict(_oc)])
    app._atualizar_resumo()
    _txt = app.lb_status.cget("text")
    chk(any("Ocultos da lista (1)" in t and "Zeta Teste Oculto" in t and "Mostrar ignorados" in t for t in _msgs),
        "ocultos: sem 'Mostrar ignorados', o log lista o oculto e diz como ver")
    chk("fora do winget" in _txt and "ligue \"Mostrar ignorados\"" in _txt,
        "ocultos: resumo mostra 'fora do winget (ocultos: ligue \"Mostrar ignorados\")'")
    _msgs.clear()
    app.var_mostrar_ign.set(True)
    app._carregar_itens([dict(_oc)])
    chk(not any("Ocultos da lista" in t for t in _msgs),
        "ocultos: com 'Mostrar ignorados' ligado, nao loga a linha de ocultos")
finally:
    app._log = _log_orig
    app.var_mostrar_ign.set(_ign_antes)
    app.lista, app.itens = [], {}
    app._redesenhar()

# Fora do winget (OLE DB, Intel XTU) e item "Em dia": linhas da tabela conforme "Mostrar ignorados"/"Mostrar em dia"
_ok_antes, _ign_antes2 = app.var_mostrar_ok.get(), app.var_mostrar_ign.get()
_lista_bkp, _itens_bkp = list(app.lista), dict(app.itens)
def _fora(nome, id_):
    return {"nome": nome, "id": id_, "versao": "1", "disponivel": "2", "status": "Fora do winget", "tag": "ok",
            "tipo": "App", "acao": None, "marcado": False, "fora_winget": True}
def _em_dia():
    return {"nome": "Git Em Dia", "id": "Git.Git", "versao": "2", "disponivel": "2", "status": "Em dia", "tag": "ok",
            "tipo": "App", "acao": None, "marcado": False, "fora_winget": False}
def _nomes_na_tabela():
    return {app.itens[i]["nome"] for i in app.tree.get_children() if i in app.itens}
_conjunto = [_fora("Microsoft OLE DB Driver", "Microsoft.OLEDB"), _fora("Intel XTU", "Intel.XTU"), _em_dia()]
try:
    app.var_mostrar_ign.set(True); app.var_mostrar_ok.set(False)
    app._carregar_itens([dict(i) for i in _conjunto])
    n_on = _nomes_na_tabela()
    chk({"Microsoft OLE DB Driver", "Intel XTU"} <= n_on and "Git Em Dia" not in n_on,
        "fora do winget: com 'Mostrar ignorados' ON e 'Mostrar em dia' OFF, OLE DB e Intel XTU aparecem; Em dia continua escondido")
    app.var_mostrar_ign.set(False); app.var_mostrar_ok.set(False)
    app._carregar_itens([dict(i) for i in _conjunto])
    n_off = _nomes_na_tabela()
    chk(not ({"Microsoft OLE DB Driver", "Intel XTU", "Git Em Dia"} & n_off),
        "fora do winget: com 'Mostrar ignorados' OFF nao aparecem linhas (nem Em dia)")
    app.var_mostrar_ign.set(True); app.var_mostrar_ok.set(True)
    app._carregar_itens([dict(i) for i in _conjunto])
    chk({"Microsoft OLE DB Driver", "Intel XTU", "Git Em Dia"} <= _nomes_na_tabela(),
        "fora do winget: com 'Mostrar em dia' ON, Em dia tambem aparece (controle)")
finally:
    app.var_mostrar_ok.set(_ok_antes); app.var_mostrar_ign.set(_ign_antes2)
    app.lista, app.itens = _lista_bkp, _itens_bkp
    app._redesenhar()

# titulo da janela com a versao do app
chk(root.title() == f"{m.APP_NOME} v{m.APP_VERSAO}", "janela: titulo contem o nome e a versao (vX.Y.Z)")
liberou = []
_lib = m.liberar_instancia
m.liberar_instancia = lambda: (liberou.append(1), _lib())
m.lancar_como_admin = lambda e, p: 42                                  # Windows aceitou e abriu a copia admin
_d0 = app.root.destroy; destruiu_adm = []
def _destroy_registra():
    destruiu_adm.append(1)
    _d0()
app.root.destroy = _destroy_registra             # detecta o fechamento sem depender de update() levantar TclError (varia com a versao do Tk)
app._reabrir_admin()
fechou = False
t1 = time.time()
while time.time() - t1 < 4:
    try:
        root.update(); time.sleep(0.02)
    except tk.TclError:
        fechou = True; break
    if destruiu_adm:
        fechou = True; break
chk(fechou, "admin: depois que a copia admin abre, a antiga se fecha")
chk(liberou == [1], "admin: a antiga soltou a trava de instancia unica antes de sair")

print("RESULTADO GUI:", "TUDO OK" if all(res) else "HA FALHAS")
try:
    root.destroy()
except tk.TclError:
    pass

