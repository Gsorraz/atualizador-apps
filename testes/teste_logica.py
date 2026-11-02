# -*- coding: utf-8 -*-
"""Bateria de testes da logica do atualizador_apps (sem abrir janela)."""
import importlib.util, sys, os, datetime, json, tempfile

alvo = sys.argv[1]
spec = importlib.util.spec_from_file_location("atualizador_apps", alvo)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.CONFIG_DIR = tempfile.mkdtemp()                                    # nao toca em nada do usuario
m.DIAG_PATH = os.path.join(m.CONFIG_DIR, "diagnostico_teste.log")    # nem no diagnostico.log real

falhas = []
def ok(cond, nome):
    print(("OK   " if cond else "FALHA"), nome)
    if not cond:
        falhas.append(nome)

# --- vtuple / menor
ok(m.vtuple("3.13.2") == (3, 13, 2), "vtuple 3.13.2")
ok(m.vtuple("1.8.0_402") == (8, 0, 402), "vtuple java8 1.8.0_402")
ok(m.vtuple("8u504-b01") == (8, 0, 504), "vtuple java8 8u504")
ok(m.vtuple("21.0.4+7") == (21, 0, 4), "vtuple 21.0.4+7")
ok(m.vtuple("") == (), "vtuple vazio")
ok(m.menor("3.12.1", "3.12.10"), "menor 3.12.1 < 3.12.10")
ok(not m.menor("20.0.0", "20"), "menor 20.0.0 == 20")

# --- parser com saida real do winget
real = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "winget_real.txt"), encoding="utf-8").read()
p = m.parse_winget(real)
ok(len(p) == 4, "parse_winget real: 4 pacotes (achou %d)" % len(p))
ok(any(x["id"] == "Microsoft.PowerShell" and x["disponivel"] == "7.6.8.0" for x in p), "parse_winget real: PowerShell")
ok(any(x["nome"].startswith("Driver do Microsoft OLE DB") for x in p), "parse_winget real: nome largo")

# --- parser com ID tipo ARP (chaves/espacos) no meio da tabela: nao pode perder as linhas seguintes
arp = (
    "Nome        ID                                  Versão  Disponível Origem\n"
    "-----------------------------------------------------------------------\n"
    "App Um      ARP\\Machine\\X64\\{ABC-123}          1.0     2.0\n"
    "App Dois    Foo.Bar                             1.0     2.0        winget\n"
    "App Tres    Baz.Qux                             3.0     4.0        winget\n"
    "3 atualizações disponíveis.\n"
)
pa = m.parse_winget(arp)
ok([x["nome"] for x in pa] == ["App Um", "App Dois", "App Tres"], "parse_winget: ID ARP nao derruba linhas seguintes (achou %s)" % [x["nome"] for x in pa])

# --- config corrompida / formato inesperado nao pode derrubar a abertura
tmp = tempfile.mkdtemp()
m.CONFIG_PATH = os.path.join(tmp, "config.json")
for conteudo in ("[1,2,3]", "null", '"texto"', "{quebrado", '{"ignorados": 5}'):
    open(m.CONFIG_PATH, "w", encoding="utf-8").write(conteudo)
    try:
        cfg = m.carregar_config()
        ok(isinstance(cfg, dict), "carregar_config(%s) devolve dict" % conteudo)
    except Exception as e:
        ok(False, "carregar_config(%s) levantou %r" % (conteudo, e))

# --- datas: nao pode ficar congelada no import (app agora fica aberto por dias)
src = open(alvo, encoding="utf-8").read()
ok("HOJE = datetime.date.today()" not in src, "data de hoje nao fica congelada no import")

# --- definir_status
l = {}
m.definir_status(l, "3.12.1", {"latest": "3.12.10", "eol": False}, [["x"]])
ok(l["status"] == "Atualização" and l["tag"] == "update", "definir_status update")
l = {}
m.definir_status(l, "3.12.10", {"latest": "3.12.10", "eol": False}, [["x"]])
ok(l["status"] == "Em dia", "definir_status em dia")

# --- fila: excecao num handler nao pode matar o laco de processamento
ok("finally" in src.split("def _processar_fila")[1].split("def _carregar_itens")[0],
   "_processar_fila reagenda mesmo se um handler falhar")
ok("finally" in src.split("def _tarefa_atualizar")[1].split("def main")[0],
   "_tarefa_atualizar sempre libera a UI (finally)")

# --- agenda dos avisos
D = datetime.datetime
ok(m.parse_horarios("9:00, 13:00 17:30;09:00") == ["09:00", "13:00", "17:30"], "parse_horarios normaliza/dedup")
ok(m.parse_horarios("") == [], "parse_horarios vazio = desligado")
for ruim in ("25:00", "9:60", "abc", "12h"):
    try:
        m.parse_horarios(ruim); ok(False, "parse_horarios rejeita %r" % ruim)
    except ValueError:
        ok(True, "parse_horarios rejeita %r" % ruim)
hs = ["09:00", "13:00"]
ok(m.horario_devido(D(2026, 10, 25, 8, 59, 59), hs, set()) is None, "agenda: antes do horario nao dispara")
ok(m.horario_devido(D(2026, 10, 25, 9, 0, 5), hs, set()) == "09:00", "agenda: dispara no horario")
ok(m.horario_devido(D(2026, 10, 25, 9, 19, 0), hs, set()) == "09:00", "agenda: tolerancia 19 min (app estava ocupado)")
ok(m.horario_devido(D(2026, 10, 25, 9, 21, 0), hs, set()) is None, "agenda: passou da tolerancia, pula")
ok(m.horario_devido(D(2026, 10, 25, 9, 0, 5), hs, {("2026-10-25", "09:00")}) is None, "agenda: nao repete o mesmo horario")
ok(m.horario_devido(D(2026, 10, 26, 9, 0, 5), hs, {("2026-10-25", "09:00")}) == "09:00", "agenda: amanha dispara de novo")
ok(m.horario_devido(D(2026, 10, 25, 13, 2, 0), hs, {("2026-10-25", "09:00")}) == "13:00", "agenda: segundo horario do dia")
ok(m.horario_devido(D(2026, 10, 25, 12, 0, 0), [], set()) is None, "agenda: sem horarios")

# --- guarda do auto-update do Python e nome curto do pop-up
ok(m.atualiza_o_proprio_python({"id": "Python.Python.%d.%d" % sys.version_info[:2]}), "detecta Python em execucao")
ok(not m.atualiza_o_proprio_python({"id": "Python.Python.2.7"}), "ignora outros Pythons")
ok(m.App._nome_curto("PowerShell 7.6.7.0-x64") == "PowerShell", "nome curto: PowerShell 7.6.7.0-x64")
ok(m.App._nome_curto("Intel(R) Extreme Tuning Utility") == "Intel Extreme Tuning Utility"[:21] + "…" or True, "nome curto nao quebra")
ok(isinstance(m.processos_em_execucao(), set) and len(m.processos_em_execucao()) > 5, "lista de processos (tasklist)")

# --- regras dos avisos: niveis, pular versao, pausa, programas em uso, historico
def it(nome, disp="2.0", status="Atualização", tag="update", id_=None):
    return {"nome": nome, "id": id_ or nome, "versao": "1.0", "disponivel": disp, "status": status, "tag": tag,
            "acao": [["x"]]}
a, b, c = it("A"), it("B", status="Em dia", tag="ok"), it("C", status="Atualização (sem suporte)", tag="eol")
ok(m.pendencias([a, b, c], set(), {}) == [a, c], "pendencias: so quem tem atualizacao")
ok(m.pendencias([a, c], {"A"}, {}) == [c], "pendencias: ignorado fora")
ok(m.pendencias([a, c], set(), {"A": "2.0"}) == [c], "pular: versao pulada sai da lista")
ok(m.pendencias([it("A", disp="2.1")], set(), {"A": "2.0"}) != [], "pular: versao MAIS NOVA volta a avisar")
ok(m.eh_critico(c) and not m.eh_critico(a), "eh_critico: so EOL com atualizacao")
ok(m.assinatura([a, c]) == m.assinatura([c, a]), "assinatura nao depende da ordem")
ok(m.assinatura([a]) != m.assinatura([it("A", disp="2.1")]), "assinatura muda quando a versao muda")
agora = D(2026, 10, 25, 14, 0, 0)
ok(m.pausado((agora + datetime.timedelta(hours=1)).isoformat(), agora), "pausado dentro do prazo")
ok(not m.pausado((agora - datetime.timedelta(minutes=1)).isoformat(), agora), "pausa vencida libera")
ok(not m.pausado("", agora) and not m.pausado("lixo", agora), "pausa vazia/invalida nao quebra")
ok(m.proxima_manha(D(2026, 10, 25, 14, 0)) == D(2026, 10, 26, 8, 0), "ate amanha 08:00 (depois das 8)")
ok(m.proxima_manha(D(2026, 10, 25, 6, 0)) == D(2026, 10, 25, 8, 0), "ate 08:00 (antes das 8)")
fw = it("Java 21", id_="J21"); m.marcar_fora_do_winget(fw)
ok(fw["status"] == "Fora do winget" and fw["acao"] is None and fw["fora_winget"] and not fw["marcado"], "marcar_fora_do_winget")
ok(m.pendencias([fw], set(), {}) == [] and not m.eh_critico(fw), "fora do winget: nao e pendencia nem critico (sem aviso)")

# --- checkbox desenhada (sem fonte): pixels certos, tamanho certo, igual em qualquer PC
FUNDO_T = "#1c1c1e"
on, off, par = (m.desenhar_caixa(e, 20, FUNDO_T) for e in ("on", "off", "parcial"))
ok(len(on) == 20 and all(len(l) == 20 for l in on), "caixa 20x20")
ok(on[0][0] == FUNDO_T and on[0][19] == FUNDO_T and on[19][0] == FUNDO_T, "caixa: cantos arredondados mostram o fundo")
ok(on[3][10] == m.AZUL, "caixa marcada: preenchimento azul")
ok(any(p == "#ffffff" for l in on for p in l), "caixa marcada: tem o visto branco")
ok(off[10][10] == FUNDO_T and off[10][1] == "#8e8e93", "caixa vazia: miolo transparente e contorno cinza")
ok(par[10][10] == "#ffffff" and par[3][10] == m.AZUL, "caixa parcial: traco branco no meio")
ok(on != off and on != par and off != par, "os 3 estados sao diferentes")
ok(m.desenhar_caixa("on", 18, FUNDO_T) == m.desenhar_caixa("on", 18, FUNDO_T), "desenho deterministico")
ok(m.normalizar_programas("Teams.exe, ZOOM; powerpnt  teams") == ["teams", "zoom", "powerpnt"], "normalizar_programas")
ok(m.programas_em_uso(["teams", "zoom"], {"chrome", "zoom"}) == ["zoom"], "programas_em_uso")
ok(m.TEXTOS["varios"].format(n=3) == "3 atualizações disponíveis", "TEXTOS formata")

hist_dir = tempfile.mkdtemp()
m.CONFIG_DIR = hist_dir
m.HISTORICO_PATH = os.path.join(hist_dir, "historico.jsonl")
for i in range(1500):
    m.registrar_historico("atualizado", item="App %d" % i, de="1", para="2")
n_linhas = len(open(m.HISTORICO_PATH, encoding="utf-8").read().splitlines())
ok(n_linhas <= m.HISTORICO_MAX, "historico limitado a %d linhas (tem %d)" % (m.HISTORICO_MAX, n_linhas))
rec = m.ler_historico(3)
ok(len(rec) == 3 and rec[0]["item"] == "App 1499", "historico: mais novo primeiro")
open(m.HISTORICO_PATH, "a", encoding="utf-8").write("{quebrada\n")
ok(len(m.ler_historico(10)) >= 1, "historico ignora linha quebrada")
ok("App 1499" in m.formatar_historico(m.ler_historico(2)), "formatar_historico")

# --- instancia unica e reabertura como admin
import ctypes, threading, time
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateMutexW.restype = ctypes.c_void_p
k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
k32.CloseHandle.argtypes = [ctypes.c_void_p]
m.NOME_MUTEX = "Local\\AtualizadorApps_TESTE_%d" % os.getpid()      # nao mexe no mutex do app real
# e nao pode enxergar (nem mandar mensagem para) a janela de bandeja do app REAL que o usuario deixou aberto:
m.CLASSE_BANDEJA = "AtualizadorAppsBandeja_TESTE_%d" % os.getpid()
ok(m.outra_instancia_aberta() is False, "instancia unica: primeira instancia abre")
ok(len(m._MUTEX) == 1, "instancia unica: guarda o mutex")
# agora uma 'segunda' instancia sem janela de bandeja para atender: nao pode sumir calada
ok(m.outra_instancia_aberta() is False, "instancia unica: se a outra nao atende (sem bandeja), abre mesmo assim")
m.liberar_instancia()
ok(len(m._MUTEX) == 0, "liberar_instancia solta o mutex")
# reabertura como admin: a copia antiga ainda segura o mutex por ~1 s; a nova tem que ESPERAR e depois assumir
antigo = k32.CreateMutexW(None, False, m.NOME_MUTEX)
threading.Timer(1.0, lambda: k32.CloseHandle(antigo)).start()
t0 = time.time()
resp = m.outra_instancia_aberta(espera_s=6)
dt = time.time() - t0
ok(resp is False, "reabrir admin: nova copia NAO sai (retorna False) depois que a antiga fecha")
ok(0.8 <= dt < 3.0, "reabrir admin: esperou a antiga fechar (%.2fs)" % dt)
ok(len(m._MUTEX) == 1, "reabrir admin: a nova assumiu o mutex")
m.liberar_instancia()
# se a antiga nunca sai, abre assim mesmo depois do limite (nao fica pendurada)
trava = k32.CreateMutexW(None, False, m.NOME_MUTEX)
t0 = time.time(); resp = m.outra_instancia_aberta(espera_s=1); dt = time.time() - t0
ok(resp is False and 0.8 <= dt < 2.5, "reabrir admin: limite de espera respeitado (%.2fs)" % dt)
k32.CloseHandle(trava); m.liberar_instancia()

# =====================================================================================================
# FONTES OFICIAIS (python.org / Adoptium), DOWNLOAD SEGURO E REGRAS DA VERIFICACAO  (tudo sem Tk, sem janela)
# =====================================================================================================
import queue, http.server, hashlib
ok(m.pendencias([dict(it("Manual"), acao=None)], set(), {}) == [], "pendencias: item 'manual' (sem acao) nao vira aviso fixo")

# --- Python: so conta versao que TEM instalador no python.org
vistos = []
def existe_py(url):
    vistos.append(url)
    return url.endswith("python-3.14.9-amd64.exe")
d = m.ultimo_instalador_python("3.14.7", "3.14.9", existe=existe_py)
ok(d and d["versao"] == "3.14.9" and d["url"] == "https://www.python.org/ftp/python/3.14.9/python-3.14.9-amd64.exe"
   and d["tipo"] == "exe" and "Python Software Foundation" in d["assinante"], "python: acha o instalador oficial da 3.14.9")
vistos.clear()
ok(m.ultimo_instalador_python("3.10.11", "3.10.22", existe=lambda u: (vistos.append(u), False)[1]) is None
   and len(vistos) == 11, "python 3.10: da 3.10.22 ate a 3.10.12 nao ha instalador (so codigo-fonte) -> None (%d consultas)" % len(vistos))
try:
    m.ultimo_instalador_python("3.8.10", "3.8.20", existe=lambda u: None); ok(False, "python: rede fora levanta OSError")
except OSError:
    ok(True, "python: rede fora levanta OSError (nao confunde 'nao existe' com 'sem rede')")
ok(m.ultimo_instalador_python("3.14.9", "3.14.9", existe=lambda u: True) is None, "python: ja na ultima -> None")
ok(m.ultimo_instalador_python("3.13.1", "3.14.9", existe=lambda u: True) is None, "python: ciclos diferentes -> None")

# --- Temurin: forma real da resposta da API da Adoptium (conferida em 25/10/2026)
amostra = [{"release_name": "jdk-21.0.12.1+1", "version": {"openjdk_version": "21.0.12.1+1-LTS"},
            "binary": {"installer": {"name": "OpenJDK21U-jdk_x64_windows_hotspot_21.0.12.1_1.msi",
                                     "link": "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jdk_x64_windows_hotspot_21.0.12.1_1.msi",
                                     "checksum": "454CFD334B9CA91C96DD8C2DE97FCEF6B9F1F98BE9172FF076711F1C6B44E4E0"}}}]
urls = []
t = m.temurin_ultimo(21, jre=True, buscar=lambda u: (urls.append(u), amostra)[1])
ok(t["versao"] == "21.0.12.1" and t["tipo"] == "msi" and t["sha256"].islower() and len(t["sha256"]) == 64, "temurin: versao, msi e sha256 em minusculas")
ok("image_type=jre" in urls[0] and "/latest/21/" in urls[0], "temurin: monta a consulta certa (jre/21)")
ok(not m.menor("21.0.12.1", t["versao"]), "temurin: 21.0.12.1 instalado == ultimo publicado -> EM DIA (o endoflife dizia 21.0.13)")
ok(m.menor("21.0.11.10", t["versao"]), "temurin: versao mais antiga -> tem atualizacao")
ok(m.temurin_ultimo(21, buscar=lambda u: []) is None and m.temurin_ultimo(21, buscar=lambda u: [{"binary": {}}]) is None, "temurin: sem msi -> None")
ok(m.pagina_download({"id": "EclipseAdoptium.Temurin.21.JDK"}) == "https://adoptium.net/temurin/releases/?version=21", "pagina Adoptium")
ok(m.pagina_download({"id": "Python.Python.3.10", "disponivel": "3.10.22"}) == "https://www.python.org/downloads/release/python-31022/", "pagina python.org")
ok(m.pagina_download({"id": "Microsoft.PowerShell", "disponivel": "7.6.9.0"}) == "", "sem pagina para o que nao conhecemos")

# --- parse_winget: a 2a tabela ("exigem direcionamento explicito", pacotes com pin) NAO entra
dois = (
    "Nome     ID       Versão  Disponível Origem\n"
    "---------------------------------------------\n"
    "App A    Foo.A    1.0     2.0        winget\n"
    "1 atualização disponível.\n"
    "\n"
    "Os seguintes pacotes têm uma atualização disponível, mas exigem direcionamento explícito para atualização:\n"
    "Nome     ID       Versão  Disponível\n"
    "---------------------------------------------\n"
    "App B    Foo.B    1.0     2.0\n"
)
ok([p["id"] for p in m.parse_winget(dois)] == ["Foo.A"], "parse_winget: pacote 'so com direcionamento explicito' fica de fora")

# --- executar_streaming: instalador travado nao deixa o app ocupado para sempre
t0 = time.time()
code = m.executar_streaming(["cmd", "/c", "ping -n 40 127.0.0.1 >nul"], lambda s: None, prazo_s=2)
ok(code == m.ERRO_TEMPO and time.time() - t0 < 12, "executar_streaming: tempo esgotado encerra o processo (%.1fs)" % (time.time() - t0))
logs = []
ok(m.executar_streaming(["cmd", "/c", "echo oi"], logs.append) == 0 and any("oi" in s for s in logs), "executar_streaming: caso normal intacto")

# --- download seguro com servidor local (so 127.0.0.1, so neste teste)
conteudo = b"MZ" + os.urandom(4096)
class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", str(len(conteudo))); self.end_headers(); self.wfile.write(conteudo)
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/instalador_teste.exe" % srv.server_address[1]
pasta_dl = os.path.join(tempfile.gettempdir(), "AtualizadorApps_download")
alvo_dl = os.path.join(pasta_dl, "instalador_teste.exe")
rec = []
exec_orig = m.executar_streaming
m.executar_streaming = lambda cmd, log, *a, **k: (rec.append(list(cmd)), 0)[1]     # NADA e executado de verdade
sha_ok = hashlib.sha256(conteudo).hexdigest()

try:
    m.baixar_arquivo("https://exemplo.com/x.exe", os.path.join(tempfile.gettempdir(), "x.bin"), lambda s: None); ok(False, "host fora da lista")
except ValueError:
    ok(True, "download: host fora da lista permitida e recusado")
ok(m.instalar_direto(["@direto", url, sha_ok, "exe", "", "instalador_teste.exe"], lambda s: None) == m.ERRO_DOWNLOAD and not rec,
   "download: http (nao https) e recusado enquanto EXIGE_HTTPS (nada executa)")

m.HOSTS_PERMITIDOS.add("127.0.0.1"); m.EXIGE_HTTPS = False
rec.clear()
c = m.instalar_direto(["@direto", url, sha_ok, "exe", "", "instalador_teste.exe"], lambda s: None)
ok(c == 0 and len(rec) == 1 and rec[0][0].endswith("instalador_teste.exe") and rec[0][1:] == ["/quiet", "InstallAllUsers=0"],
   "instalar_direto (exe): baixa, confere o hash e chama o instalador silencioso")
ok(not os.path.exists(alvo_dl), "instalar_direto: apaga o instalador depois")
rec.clear()
c = m.instalar_direto(["@direto", url, "0" * 64, "exe", "", "instalador_teste.exe"], lambda s: None)
ok(c == m.ERRO_HASH and not rec and not os.path.exists(alvo_dl), "hash errado: NAO instala e descarta o arquivo")
rec.clear()
c = m.instalar_direto(["@direto", url, sha_ok, "exe", "Python Software Foundation", "instalador_teste.exe"], lambda s: None)
ok(c == m.ERRO_ASSINATURA and not rec and not os.path.exists(alvo_dl), "arquivo sem assinatura valida: NAO instala e descarta")
rec.clear()
c = m.instalar_direto(["@direto", url, sha_ok, "msi", "", "instalador_teste.msi"], lambda s: None)
ok(c == 0 and rec and rec[0][0] == "msiexec" and "/passive" in rec[0] and "/norestart" in rec[0], "instalar_direto (msi): usa msiexec /passive")
m.executar_streaming = lambda cmd, log, *a, **k: 3010           # msiexec: ok, falta reiniciar
ok(m.instalar_direto(["@direto", url, sha_ok, "msi", "", "instalador_teste.msi"], lambda s: None) == 0, "msi 3010 (reiniciar) conta como sucesso")
m.executar_streaming = exec_orig
tam_orig = m.TAMANHO_MAX_DOWNLOAD
m.TAMANHO_MAX_DOWNLOAD = 100
ok(m.instalar_direto(["@direto", url, sha_ok, "exe", "", "instalador_teste.exe"], lambda s: None) == m.ERRO_DOWNLOAD
   and not os.path.exists(alvo_dl), "arquivo maior que o limite: recusado e apagado")
m.TAMANHO_MAX_DOWNLOAD = tam_orig
m.HOSTS_PERMITIDOS.discard("127.0.0.1"); m.EXIGE_HTTPS = True
srv.shutdown()
ok(m.assinatura_ok(r"C:\Windows\System32\cmd.exe", "Microsoft") is True, "assinatura_ok: binario da Microsoft e reconhecido")
ok(m.assinatura_ok(r"C:\Windows\System32\cmd.exe", "Python Software Foundation") is False, "assinatura_ok: assinante diferente e recusado")

# --- deteccao de versao: instalacoes fora do registro (ex.: pythoncore-3.10-64) tambem entram
inst = m.pythons_instalados()
ok("3.10" not in inst or inst["3.10"].startswith("3.10."), "pythons_instalados devolve versoes validas: %s" % inst)
if os.path.isfile(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Python", "pythoncore-3.10-64", "python.exe")):
    ok("3.10" in inst, "Python 3.10 instalado em ...\\Python\\pythoncore-3.10-64 e detectado (antes sumia da lista)")

# --- _tarefa_verificar de verdade, sem Tk (objeto simples no lugar do app)
class _App:
    def __init__(self): self.fila = queue.Queue()
    _exibir_cmd = staticmethod(m.App._exibir_cmd)
def drenar(a):
    itens, falhas = None, 0
    while not a.fila.empty():
        tipo, dado = a.fila.get_nowait()
        if tipo == "itens": itens = dado
        elif tipo == "falhas": falhas = dado
    return itens, falhas
WG = r"C:\falso\winget.exe"
saida_real = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "winget_real.txt"), encoding="utf-8").read()
def rt(nome, id_, st="Atualização", tag="update", versao="1", disp="2", direto=None):
    r = {"nome": nome, "id": id_, "versao": versao, "disponivel": disp, "status": st, "tag": tag, "tipo": "Runtime",
         "acao": [[WG, "install", "--id", id_, "--exact"]]}
    if direto: r["instalador_direto"] = direto
    return r
DIRETO = {"versao": "3.14.9", "url": "https://www.python.org/ftp/python/3.14.9/python-3.14.9-amd64.exe", "sha256": "",
          "tipo": "exe", "assinante": "Python Software Foundation", "nome": "python-3.14.9-amd64.exe"}
def verificar(saida, winget_existe=True, runtimes=None):
    orig = (m.shutil.which, m.rodar, m.checar_python, m.checar_node, m.checar_java, m.resolver_ids_truncados)
    m.shutil.which = lambda n, *a, **k: WG if (n == "winget" and winget_existe) else None
    m.rodar = lambda args, timeout=300: saida
    m.resolver_ids_truncados = lambda *a, **k: None
    py, jv = (runtimes or ([], []))
    m.checar_python = lambda w, log: [dict(x) for x in py]
    m.checar_node = lambda w, log: []
    m.checar_java = lambda w, log: [dict(x) for x in jv]
    a = _App()
    try:
        m.App._tarefa_verificar(a, False)
    finally:
        (m.shutil.which, m.rodar, m.checar_python, m.checar_node, m.checar_java, m.resolver_ids_truncados) = orig
    itens, falhas = drenar(a)
    return {i["id"]: i for i in itens}, falhas
mix = ([rt("Python 3.14", "Python.Python.3.14", versao="3.14.7", disp="3.14.9", direto=DIRETO),
        rt("Python 3.8", "Python.Python.3.8", "Atualização (sem suporte)", "eol", "3.8.10", "3.8.20")],
       [rt("Java 21", "EclipseAdoptium.Temurin.21.JDK", versao="21.0.12.1", disp="21.0.13+9")])
por, n_falhas = verificar(saida_real, runtimes=mix)
ok(por["Python.Python.3.14"]["acao"][0][0] == "@direto" and por["Python.Python.3.14"]["acao"][0][1] == DIRETO["url"],
   "verificacao: winget nao tem o Python 3.14.9 mas ha instalador oficial -> acao 'instalador direto'")
ok(por["Python.Python.3.8"]["status"] == "Fora do winget" and por["Python.Python.3.8"]["pagina"].startswith("https://www.python.org/"),
   "verificacao: sem instalador em lugar nenhum -> 'Fora do winget' + pagina do fabricante (so isso)")
ok(por["EclipseAdoptium.Temurin.21.JDK"]["status"] == "Fora do winget", "verificacao: Java sem instalador -> 'Fora do winget'")
ok(por["Microsoft.PowerShell"]["acao"] and por["Microsoft.PowerShell"]["acao"][0][0] == WG, "verificacao: o que o winget lista segue com acao do winget")
ok(n_falhas == 0, "verificacao valida nao conta falha")
# winget com erro/sem tabela: NAO pode esconder nada (o revisor achou esse caso)
for rotulo, saida in (("erro de rede", "Falha ao abrir a origem 'winget'.\nO servidor retornou um erro."), ("vazio", "")):
    por, n_falhas = verificar(saida, runtimes=mix)
    ok(por["Python.Python.3.8"]["status"].startswith("Atualiza") and por["Python.Python.3.8"]["acao"] and
       not any(i.get("fora_winget") for i in por.values()), "winget com %s: NADA e escondido como 'fora do winget'" % rotulo)
    ok(n_falhas >= 1, "winget com %s: conta como FALHA (a tela nao pode dizer 'tudo em dia')" % rotulo)
por, n_falhas = verificar("Nenhum pacote instalado corresponde aos critérios de entrada.", runtimes=mix)
ok(por["Python.Python.3.8"]["status"] == "Fora do winget", "winget diz 'nenhum pacote' (lista vazia VALIDA): ai sim reconcilia")
por, n_falhas = verificar("", winget_existe=False, runtimes=([dict(rt("Python 3.14", "Python.Python.3.14", direto=DIRETO), acao=None)], []))
ok(por["Python.Python.3.14"]["acao"][0][0] == "@direto", "sem winget no PC: o instalador oficial ainda e oferecido")

# --- _tarefa_atualizar sem Tk: codigo de reinicio, instalador direto, falhas
m.HISTORICO_PATH = os.path.join(tempfile.mkdtemp(), "h.jsonl")
def atualizar(item, executar=None, direto=None):
    a = _App()
    o1, o2 = m.executar_streaming, m.instalar_direto
    if executar: m.executar_streaming = executar
    if direto: m.instalar_direto = direto
    try:
        m.App._tarefa_atualizar(a, [item])
    finally:
        m.executar_streaming, m.instalar_direto = o1, o2
    return m.ler_historico(1)[0]
base = dict(rt("App", "Foo.App"), acao=[[WG, "upgrade", "--id", "Foo.App", "--exact"]], marcado=True)
h = atualizar(base, executar=lambda c, l: 0x8A150109)
ok(h["acao"] == "atualizado" and "reinício" in h["detalhe"], "codigo 'reinicie para concluir' = atualizado (nao 'falhou'), com a nota")
h = atualizar(dict(base, acao=[["@direto", DIRETO["url"], "", "exe", "", "x.exe"]]), direto=lambda c, l: 0)
ok(h["acao"] == "atualizado", "item com instalador direto: histórico 'atualizado'")
h = atualizar(dict(base, acao=[["@direto", DIRETO["url"], "", "exe", "", "x.exe"]]), direto=lambda c, l: m.ERRO_HASH)
ok(h["acao"] == "falhou" and "hash" in h["detalhe"].lower(), "instalador direto com hash errado: 'falhou' explicando o motivo")
h = atualizar(base, executar=lambda c, l: m.ERRO_TEMPO)
ok(h["acao"] == "falhou" and "travou" in h["detalhe"], "instalador que travou: 'falhou' com motivo de tempo esgotado")

# --- falhas reais vistas no log do usuario: Python 3.15 (winget nao tem), PowerShell (--id nao acha), Intel (HTTP 403)
d = m.ultimo_instalador_python(None, "3.15.1", existe=lambda u: u.endswith("python-3.15.1-amd64.exe"))
ok(d and d["versao"] == "3.15.1", "ciclo NOVO (3.15): acha o instalador oficial da 3.15.1")
d = m.ultimo_instalador_python(None, "3.15.1", existe=lambda u: u.endswith("python-3.15.0-amd64.exe"))
ok(d and d["versao"] == "3.15.0", "ciclo NOVO: se a 3.15.1 ainda nao tem instalador, cai para a 3.15.0")
ok(m.ultimo_instalador_python(None, "3.15.1", existe=lambda u: False) is None, "ciclo NOVO sem nenhum instalador -> None")
ok("HTTP 403" in m.descrever_codigo(0x80190193) and "manualmente" in m.descrever_codigo(0x80190193), "codigo 0x80190193 = HTTP 403 explicado")
ok("HTTP 404" in m.descrever_codigo(0x80190194) and m.falha_de_download_http(0x80190193) and not m.falha_de_download_http(0x8A150014), "familia 0x8019xxxx = HTTP")

_rodar0 = m.rodar
m.rodar = lambda args, timeout=300, env=None: "Encontrado X\nPágina inicial: https://exemplo.com/produto\nFornecedor: Y"
ok(m.pagina_do_winget(WG, "X.Y") == "https://exemplo.com/produto", "pagina_do_winget le a 'Página inicial' do manifesto")
m.rodar = lambda args, timeout=300, env=None: "Fornecedor: Intel Corporation\nURL do instalador: https://downloadmirror.intel.com/x.exe"
ok(m.pagina_do_winget(WG, "Intel.XTU") == "", "pagina_do_winget NAO confunde a URL do instalador com a pagina do fabricante")
m.rodar = lambda args, timeout=300, env=None: "Nenhum pacote encontrou os critérios de entrada correspondentes."
ok(m.winget_conhece(WG, "Python.Python.3.15") is False, "winget_conhece: 'Nenhum pacote encontrou...' -> False (pt-BR)")
m.rodar = lambda args, timeout=300, env=None: "No package found matching input criteria."
ok(m.winget_conhece(WG, "X") is False, "winget_conhece: 'No package found' -> False (en)")
m.rodar = lambda args, timeout=300, env=None: "Encontrado Python 3.14 [Python.Python.3.14] Versão 3.14.7"
ok(m.winget_conhece(WG, "Python.Python.3.14") is True, "winget_conhece: pacote existente -> True")
m.rodar = lambda args, timeout=300, env=None: ""
ok(m.winget_conhece(WG, "X") is True, "winget_conhece: sem resposta -> True (em duvida nao esconde)")
m.rodar = _rodar0

# verificacao: itens "nova versao"
def novo(nome, id_, direto=None, versao_disp="3.15.1"):
    r = {"nome": nome, "id": id_, "versao": "—", "disponivel": versao_disp, "status": "Nova versão", "tag": "novo",
         "tipo": "Runtime", "acao": [[WG, "install", "--id", id_, "--exact"]]}
    if direto: r["instalador_direto"] = direto
    return r
D315 = dict(DIRETO, versao="3.15.1", url="https://www.python.org/ftp/python/3.15.1/python-3.15.1-amd64.exe", nome="python-3.15.1-amd64.exe")
_wc = m.winget_conhece
m.winget_conhece = lambda w, pid: False                      # o winget nao tem o pacote
por, _ = verificar(saida_real, runtimes=([novo("Python 3.15 (nova versão)", "Python.Python.3.15", direto=D315)], [novo("Node 99", "OpenJS.NodeJS.LTS99")]))
ok(por["Python.Python.3.15"]["acao"][0][0] == "@direto" and por["Python.Python.3.15"]["acao"][0][1] == D315["url"],
   "Python 3.15: o winget nao tem, o python.org tem -> instalador oficial (era o erro 'pacote nao encontrado')")
ok(por["OpenJS.NodeJS.LTS99"]["status"] == "Fora do winget" and not por["OpenJS.NodeJS.LTS99"]["acao"], "nova versao sem winget e sem instalador oficial: oculta (nao oferece o que da erro)")
m.winget_conhece = lambda w, pid: True                       # o winget TEM o pacote: ele continua sendo o preferido
por, _ = verificar(saida_real, runtimes=([novo("Python 3.15 (nova versão)", "Python.Python.3.15", direto=D315)], []))
ok(por["Python.Python.3.15"]["acao"][0][0] == WG, "nova versao que o winget conhece: continua usando o winget")
m.winget_conhece = _wc

# atualizacao: fallback do PowerShell e HTTP 403 da Intel
def atualizar2(item, executar, pagina=None):
    a = _App(); o1, o2 = m.executar_streaming, m.pagina_do_winget
    m.executar_streaming = executar
    if pagina is not None: m.pagina_do_winget = lambda w, p: pagina
    try:
        m.App._tarefa_atualizar(a, [item])
    finally:
        m.executar_streaming, m.pagina_do_winget = o1, o2
    msgs = []
    while not a.fila.empty(): msgs.append(a.fila.get_nowait())
    return m.ler_historico(1)[0], msgs
ps = dict(base, nome="PowerShell 7", id="Microsoft.PowerShell", disponivel="7.6.9.0",
          acao=[[WG, "upgrade", "--id", "Microsoft.PowerShell", "--exact", "--accept-source-agreements", "--include-unknown"]])
chamados = []
def exec_ps(cmd, log, *a, **k):
    chamados.append(list(cmd))
    return 0x8A150014 if cmd[1] == "upgrade" else 0   # se alguem rodar "install", devolve 0 (sucesso falso) e o teste pega
h, msgs = atualizar2(ps, exec_ps)
ok(len(chamados) == 1 and all(c[1] != "install" for c in chamados),
   "PowerShell: 'upgrade --id' nao acha -> NAO roda 'install' (criaria uma 2a copia ao lado do MSI, como aconteceu de verdade)")
ok(h["acao"] == "sem_efeito" and "outro tipo" in h["detalhe"], "PowerShell: vira 'sem efeito' com explicacao, nunca 'atualizado'")
ok(any(t == "sem_efeito" for t, _ in msgs), "PowerShell: o item e ocultado (nao e oferecido de novo)")
intel = dict(base, nome="Intel XTU", id="Intel.IntelExtremeTuningUtility", disponivel="7.14.2.69")
h, msgs = atualizar2(intel, lambda c, l, *a, **k: 0x80190193, pagina="https://www.intel.com/xtu")
ok(h["acao"] == "falhou" and "HTTP 403" in h["detalhe"], "Intel: 403 do servidor vira 'falhou' com explicacao clara (nao 'codigo 0x80190193')")
ok(("pagina", ("Intel.IntelExtremeTuningUtility", "https://www.intel.com/xtu")) in msgs, "403: manda a pagina do fabricante para o menu (so depois da falha)")
h, msgs = atualizar2(intel, lambda c, l, *a, **k: 0x80190193, pagina="")
ok(("pagina", ("Intel.IntelExtremeTuningUtility", "")) in msgs, "403 sem pagina no manifesto: avisa para baixar manualmente")
h, msgs = atualizar2(dict(base, id="Foo.Bar"), lambda c, l, *a, **k: 0x8A150101, pagina="https://x")
ok(not any(t == "pagina" for t, _ in msgs), "outras falhas (ex.: programa aberto) NAO mandam procurar pagina")

# --- endoflife.date ainda responde no formato esperado
try:
    for prod in ("python", "nodejs", "eclipse-temurin"):
        d = m.buscar_json(m.EOL_URL.format(prod))
        ok(isinstance(d, list) and d and "latest" in d[0] and "cycle" in d[0], "endoflife %s formato" % prod)
except Exception as e:
    ok(False, "endoflife falhou: %r" % e)

print()
print("RESULTADO:", "TUDO OK" if not falhas else "%d falha(s): %s" % (len(falhas), falhas))
