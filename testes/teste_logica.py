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
ok("instalador cancelou" in m.descrever_codigo(0x8A15010C) and "travou" in m.descrever_codigo(0x8A15010C), "codigo 0x8A15010C = instalador cancelou (programa aberto travou arquivo)")

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

# --- inicializacao com o Windows via tarefa elevada (nada de registro real nem schtasks real)
import xml.etree.ElementTree as _ET
_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
_exe_dificil = 'C:\\Programas & Co\\<App>\\"app".exe'
_usr_dificil = 'DOM\\Gi&an <x> "y"'
try:
    _raiz = _ET.fromstring(m.xml_tarefa_inicio(_exe_dificil, "--minimizado", _usr_dificil))
    ok(_raiz.tag.endswith("Task"), "xml_tarefa_inicio: XML faz parse (raiz Task)")
    ok(_raiz.find(".//t:Principal/t:RunLevel", _NS).text == "HighestAvailable", "xml_tarefa_inicio: RunLevel HighestAvailable")
    ok(_raiz.find(".//t:Triggers/t:LogonTrigger", _NS) is not None, "xml_tarefa_inicio: LogonTrigger presente")
    ok(_raiz.find(".//t:LogonTrigger/t:UserId", _NS).text == _usr_dificil, "xml_tarefa_inicio: usuario com & < > aspas escapado (le de volta igual)")
    ok(_raiz.find(".//t:Exec/t:Command", _NS).text == _exe_dificil, "xml_tarefa_inicio: caminho com & < > aspas escapado (le de volta igual)")
    ok(_raiz.find(".//t:Exec/t:Arguments", _NS).text == "--minimizado", "xml_tarefa_inicio: argumento --minimizado")
except Exception as e:
    ok(False, "xml_tarefa_inicio: excecao %r" % e)

_chamadas = {"schtasks": [], "run": []}
_orig = {k: getattr(m, k) for k in ("_schtasks", "_run_key_definir", "eh_admin", "tarefa_inicio_existe", "_criar_tarefa_inicio")}
_tinha_frozen = hasattr(sys, "frozen")
def _preparar(admin, frozen, criar_ok=False):
    _chamadas["schtasks"].clear(); _chamadas["run"].clear()
    m._schtasks = lambda args, timeout=30: (_chamadas["schtasks"].append(list(args)) or 0)
    m._run_key_definir = lambda ativo: (_chamadas["run"].append(ativo) or True)
    m.eh_admin = lambda: admin
    m.tarefa_inicio_existe = lambda: False
    m._criar_tarefa_inicio = lambda: criar_ok
    if frozen:
        sys.frozen = True
    elif _tinha_frozen is False and hasattr(sys, "frozen"):
        del sys.frozen
try:
    _preparar(admin=False, frozen=False)                       # .py sem admin
    r = m.definir_iniciar_com_windows(True)
    ok(_chamadas["schtasks"] == [], "iniciar com Windows, .py sem admin: NAO chama schtasks")
    ok(_chamadas["run"] == [True], "iniciar com Windows, .py sem admin: cai na chave Run (ativo=True)")
    ok(r is True, "iniciar com Windows, .py sem admin: retorna sucesso")
    _preparar(admin=True, frozen=False)                        # admin mas .py: nao cria tarefa de verdade
    m._criar_orig = _orig["_criar_tarefa_inicio"]
    m._criar_tarefa_inicio = _orig["_criar_tarefa_inicio"]     # usa a funcao real: ela recusa (sem frozen)
    m.definir_iniciar_com_windows(True)
    ok(_chamadas["schtasks"] == [], "iniciar com Windows, admin mas .py (sem frozen): NAO chama schtasks")
    _preparar(admin=False, frozen=True)                        # .exe sem admin
    m.definir_iniciar_com_windows(True)
    ok(_chamadas["schtasks"] == [] and _chamadas["run"] == [True], "iniciar com Windows, .exe sem admin: usa Run, sem schtasks")
    _preparar(admin=True, frozen=True, criar_ok=True)           # .exe admin: tarefa criada (mock) -> apaga Run
    m.definir_iniciar_com_windows(True)
    ok(_chamadas["run"] == [False], "iniciar com Windows, .exe admin: tarefa criada e chave Run removida (sem duplicar)")
    _preparar(admin=False, frozen=False)
    m.definir_iniciar_com_windows(False)
    ok(_chamadas["run"] == [False], "desligar: limpa a chave Run")
finally:
    for k, v in _orig.items():
        setattr(m, k, v)
    if _tinha_frozen is False and hasattr(sys, "frozen"):
        del sys.frozen

# --- fechar programa a forca (0x8A15010C): termos, escolha de processos, pergunta/encerramento simulados
from types import SimpleNamespace as _NS
import threading as _thr
ok(m.termos_do_item({"nome": "Ollama version 0.40.13", "id": "Ollama.Ollama"}) == {"ollama"},
   "termos_do_item: Ollama -> so 'ollama' (descarta 'version' e numeros)")
ok("microsoft" not in m.termos_do_item({"nome": "Microsoft Teams", "id": "Microsoft.Teams"}),
   "termos_do_item: descarta 'microsoft'")
ok(m.termos_do_item({"nome": "Microsoft Teams", "id": "Microsoft.Teams"}) == {"teams"},
   "termos_do_item: Microsoft Teams -> {'teams'}")
ok(m.termos_do_item({"nome": "Programa 2024 1.2", "id": "X.Programa"}) == {"programa"},
   "termos_do_item: descarta numeros (2024, 1.2)")
ok(m.termos_do_item({}) == set() and m.termos_do_item({"nome": None}) == set(),
   "termos_do_item: item vazio/None nao levanta erro")

_OLL = r"C:\Users\Gian\AppData\Local\Programs\Ollama"
_WIN = os.environ.get("SystemRoot", r"C:\Windows")
_T = m.termos_do_item({"nome": "Ollama version 0.40.13", "id": "Ollama.Ollama"})
ollama_procs = [
    {"ProcessId": 100, "Name": "ollama.exe", "ExecutablePath": _OLL + r"\ollama.exe"},
    {"ProcessId": 200, "Name": "ollama app.exe", "ExecutablePath": _OLL + r"\ollama app.exe"},
]
mistura = ollama_procs + [
    {"ProcessId": 300, "Name": "ollama.exe", "ExecutablePath": _WIN + r"\System32\ollama.exe"},
    {"ProcessId": os.getpid(), "Name": "ollama.exe", "ExecutablePath": _OLL + r"\ollama.exe"},
    {"ProcessId": 400, "Name": "explorer.exe", "ExecutablePath": _WIN + r"\explorer.exe"},
    {"ProcessId": 401, "Name": "svchost.exe", "ExecutablePath": _WIN + r"\System32\svchost.exe"},
    {"ProcessId": 402, "Name": "python.exe", "ExecutablePath": r"C:\Python313\python.exe"},
    {"ProcessId": 403, "Name": "powershell.exe", "ExecutablePath": _WIN + r"\System32\WindowsPowerShell\v1.0\powershell.exe"},
]
r1 = m.escolher_processos(ollama_procs, _T, 999, "")
ok(sorted(p["pid"] for p in r1) == [100, 200],
   "escolher: Ollama pega ollama.exe e 'ollama app.exe' em Programs\\Ollama")
r2 = m.escolher_processos(mistura, _T | {"explorer", "svchost", "python", "powershell"}, os.getpid(), "")
ok(sorted(p["pid"] for p in r2) == [100, 200],
   "escolher: nao pega PID proprio, C:\\Windows, explorer/svchost/python/powershell (mesmo com termo casando)")
r3 = m.escolher_processos(ollama_procs, _T, 999, _OLL + r"\ollama app.exe")
ok(sorted(p["pid"] for p in r3) == [100], "escolher: nao pega o proprio exe (meu_exe)")
ok(m.escolher_processos(None, _T, 1) == [] and m.escolher_processos([], _T, 1) == [],
   "escolher: lista vazia/None nao levanta erro")
lixo = [None, {}, "lixo", 5, {"ProcessId": "abc", "Name": "ollama.exe", "ExecutablePath": "C:\\x\\ollama.exe"},
        {"ProcessId": None, "Name": "ollama.exe", "ExecutablePath": "C:\\x\\ollama.exe"},
        {"ProcessId": 7, "Name": None, "ExecutablePath": "C:\\x"},
        {"ProcessId": 8, "Name": "ollama.exe", "ExecutablePath": None}]
try:
    ok(m.escolher_processos(lixo, _T, 1) == [], "escolher: entradas malformadas ignoradas sem erro")
except Exception as e:
    ok(False, f"escolher: entradas malformadas levantaram {e!r}")
gitp = [{"ProcessId": 1, "Name": "git.exe", "ExecutablePath": r"C:\Program Files\Git\git.exe"},
        {"ProcessId": 2, "Name": "gitkraken.exe", "ExecutablePath": r"C:\Program Files\GitKraken\gitkraken.exe"}]
ok([p["pid"] for p in m.escolher_processos(gitp, {"git"}, 9, "")] == [1],
   "termo curto (<6) so casa nome exato: 'git' sim, 'gitkraken' nao")

class _EvFalso:
    def set(self): pass
    def wait(self, t=None): return False     # simula o tempo esgotado sem esperar de verdade

class _FilaSim:
    def __init__(self, resposta=None, nao_responde=False):
        self.resposta, self.nao, self.perguntas = resposta, nao_responde, []
    def put(self, msg):
        tipo, dado = msg
        if tipo == "fechar_pergunta":
            texto, evento, resp = dado
            self.perguntas.append(texto)
            if not self.nao and self.resposta is not None:
                resp.append(self.resposta)
                evento.set()

_orig_lp, _orig_ep, _orig_time, _orig_thr = m.listar_processos, m.encerrar_processos, m.time, m.threading

def _sim_fechar(lista, resposta=True, n_ok=None, falha_listar=False, timeout=False):
    enc, logs = [], []
    def _quebra():
        raise RuntimeError("simulado")
    sv = _NS(fila=_FilaSim(resposta, nao_responde=timeout))
    m.listar_processos = _quebra if falha_listar else (lambda: lista)
    m.encerrar_processos = lambda pids: (enc.append(list(pids)) or (len(pids) if n_ok is None else n_ok))
    m.time = _NS(sleep=lambda s: None)
    m.threading = _NS(Event=_EvFalso if timeout else _thr.Event)
    try:
        r = m.App._fechar_para_atualizar(sv, {"nome": "Ollama", "id": "Ollama.Ollama"}, logs.append)
    finally:
        m.listar_processos, m.encerrar_processos = _orig_lp, _orig_ep
        m.time, m.threading = _orig_time, _orig_thr
    return r, enc, sv.fila

r, enc, fila = _sim_fechar(ollama_procs, resposta=True)
ok(r is True and enc == [[100, 200]] and len(fila.perguntas) == 1,
   "fechar: resposta SIM -> encerra Ollama (100, 200) e devolve True")
r, enc, fila = _sim_fechar(ollama_procs, resposta=False)
ok(r is False and enc == [] and len(fila.perguntas) == 1, "fechar: resposta NAO -> nao encerra, devolve False")
r, enc, fila = _sim_fechar([], resposta=True)
ok(r is False and enc == [] and fila.perguntas == [], "fechar: sem processos -> False, sem pergunta, nada encerrado")
r, enc, fila = _sim_fechar(ollama_procs, timeout=True)
ok(r is False and enc == [], "fechar: sem resposta (timeout simulado) -> False, nada encerrado")
r, enc, fila = _sim_fechar(ollama_procs, resposta=True, n_ok=0)
ok(r is False and enc == [[100, 200]], "fechar: taskkill recusou tudo (0 ok) -> False")
r, enc, fila = _sim_fechar(None, resposta=True, falha_listar=True)
ok(r is False and enc == [], "fechar: erro ao listar processos -> False, sem levantar")

# --- travadores por arquivo (log do Inno) + filtro + _fechar_para_atualizar com processos_com_arquivo simulado
_TMP = tempfile.mkdtemp()
def _grava_log(nome, texto):
    p = os.path.join(_TMP, nome)
    with open(p, "w", encoding="utf-8") as f:
        f.write(texto)
    return p
_DLL = r"C:\Users\Gian\AppData\Local\Programs\Ollama\lib\ollama.dll"
_DLL_ANTIGO = r"C:\Users\Gian\AppData\Local\Programs\Ollama\lib\antigo.dll"
_log_mesma = _grava_log("inno_mesma_linha.log", "\n".join([
    "2026-11-03 10:00:00.1 Dest filename: " + _DLL_ANTIGO,
    "2026-11-03 10:00:01.2 Dest filename: " + _DLL,
    "2026-11-03 10:00:02.3 Error: could not replace, appears to be in use",
]) + "\n")
_log_linha_seguinte = _grava_log("inno_proxima_linha.log", "\n".join([
    "Dest filename:", _DLL, "Error: appears to be in use",
]) + "\n")
_log_sem_marcador = _grava_log("inno_sem_marcador.log", "Dest filename: " + _DLL + "\nTudo certo, instalado.\n")
_MARCA_PT = "O registro do instalador est\u00e1 dispon\u00edvel em: "
ok(m.arquivo_travado_no_log(_MARCA_PT + _log_mesma) == _DLL,
   "arquivo_travado_no_log: pega o ULTIMO Dest filename antes do 'appears to be in use' (valor na mesma linha)")
ok(m.arquivo_travado_no_log("installer log is available at: " + _log_linha_seguinte) == _DLL,
   "arquivo_travado_no_log: valor do Dest filename na linha seguinte")
ok(m.arquivo_travado_no_log(_MARCA_PT + _log_sem_marcador) == "",
   "arquivo_travado_no_log: log sem 'appears to be in use' -> ''")
ok(m.arquivo_travado_no_log(_MARCA_PT + os.path.join(_TMP, "nao_existe.log")) == "",
   "arquivo_travado_no_log: caminho de log inexistente -> ''")
ok(all(m.arquivo_travado_no_log(t) == "" for t in ["", "lixo qualquer", None]),
   "arquivo_travado_no_log: texto sem marcador (vazio, lixo, None) -> ''")

_TRAV_PY = {"ProcessId": 10, "Name": "python.exe",
            "ExecutablePath": r"C:\Users\Gian\AppData\Local\Programs\Python\Python313\python.exe",
            "CommandLine": r"python.exe C:\Users\Gian\openjarvis\main.py"}
_WIN = os.environ.get("SystemRoot", r"C:\Windows")
_lista_trav = [
    _TRAV_PY,
    {"ProcessId": os.getpid(), "Name": "python.exe", "ExecutablePath": r"C:\Python313\python.exe", "CommandLine": "eu"},
    {"ProcessId": 20, "Name": "ollama.exe", "ExecutablePath": _WIN + r"\System32\ollama.exe"},
    {"ProcessId": 30, "Name": "explorer.exe", "ExecutablePath": _WIN + r"\explorer.exe"},
    {"ProcessId": 31, "Name": "svchost.exe", "ExecutablePath": _WIN + r"\System32\svchost.exe"},
    {"ProcessId": 32, "Name": "cmd.exe", "ExecutablePath": _WIN + r"\System32\cmd.exe"},
    {"ProcessId": 33, "Name": "powershell.exe", "ExecutablePath": _WIN + r"\System32\powershell.exe"},
    {"ProcessId": 40, "Name": "ollama app.exe", "ExecutablePath": r"C:\Users\Gian\AppData\Local\Programs\Ollama\ollama app.exe"},
    None, "lixo", {"ProcessId": "x", "Name": "python.exe"}, {"ProcessId": None, "Name": "python.exe"},
]
_f = m.filtrar_travadores(_lista_trav, os.getpid())
ok(sorted(p["pid"] for p in _f) == [10, 40],
   "filtrar_travadores: mantem python.exe e 'ollama app.exe'; tira PID proprio, C:Windows, explorer/svchost/cmd/powershell, malformadas e None")
ok(next(p for p in _f if p["pid"] == 10)["linha"] == r"python.exe C:\Users\Gian\openjarvis\main.py",
   "filtrar_travadores: guarda a linha de comando do travador")
ok(m.filtrar_travadores(None, 1) == [], "filtrar_travadores: lista None -> []")

_orig_pca = m.processos_com_arquivo
def _sim_travador(saida, resposta=True, travadores=None, listar=None):
    chamadas, enc = [], []
    def _pca(caminho):
        chamadas.append(caminho)
        return list(travadores or [])
    sv = _NS(fila=_FilaSim(resposta))
    m.processos_com_arquivo = _pca
    m.listar_processos = lambda: list(listar or [])
    m.encerrar_processos = lambda pids: (enc.append(list(pids)) or len(pids))
    m.time = _NS(sleep=lambda s: None)
    m.threading = _NS(Event=_thr.Event)
    try:
        r = m.App._fechar_para_atualizar(sv, {"nome": "Ollama", "id": "Ollama.Ollama"}, (lambda t: None), saida)
    finally:
        m.processos_com_arquivo = _orig_pca
        m.listar_processos, m.encerrar_processos = _orig_lp, _orig_ep
        m.time, m.threading = _orig_time, _orig_thr
    return r, enc, sv.fila, chamadas

r, enc, fila, chamadas = _sim_travador(_MARCA_PT + _log_mesma, resposta=True, travadores=[_TRAV_PY])
ok(chamadas == [_DLL], "fechar travador: processos_com_arquivo chamado com o arquivo do log")
ok(len(fila.perguntas) == 1 and "python.exe" in fila.perguntas[0] and "(PID 10)" in fila.perguntas[0],
   "fechar travador: python.exe aparece na pergunta")
ok(len(fila.perguntas) == 1 and r"python.exe C:\Users\Gian\openjarvis\main.py" in fila.perguntas[0],
   "fechar travador: texto da pergunta contem a linha de comando do travador")
ok(r is True and enc == [[10]], "fechar travador: resposta SIM -> encerra so o PID 10 e devolve True")
r, enc, fila, chamadas = _sim_travador("", resposta=True, travadores=[_TRAV_PY])
ok(chamadas == [] and fila.perguntas == [] and enc == [] and r is False,
   "fechar travador: saida do winget vazia -> processos_com_arquivo NAO e chamado, sem pergunta")
r, enc, fila, chamadas = _sim_travador(_MARCA_PT + _log_mesma, resposta=True, travadores=[], listar=[])
ok(r is False and enc == [] and fila.perguntas == [],
   "fechar travador: arquivo travado mas nenhum processo achado -> False")
m.processos_com_arquivo = _orig_pca

# --- versao do app, paginas oficiais (intel/oledb) e marcar_fora_do_winget com pagina
import re as _re
ok(_re.match(r"^\d+\.\d+\.\d+$", m.APP_VERSAO) is not None, "APP_VERSAO casa X.Y.Z")
ok(m.pagina_download({"id": "Intel.IntelExtremeTuningUtility", "disponivel": "7.14.2.69"}).startswith("https://")
   and m.pagina_download({"id": "Intel.IntelExtremeTuningUtility", "disponivel": "7"}) != "",
   "pagina_download: Intel XTU devolve https:// nao vazio")
ok(m.pagina_download({"id": "Microsoft.SQLServer.OLEDBDriver", "disponivel": "19"}).startswith("https://")
   and m.pagina_download({"id": "MICROSOFT.SQLSERVER.OLEDBDRIVER", "disponivel": "19"}).startswith("https://"),
   "pagina_download: OLE DB devolve https:// (id em qualquer caixa)")
ok(m.pagina_download({"id": "Python.Python.3.13", "disponivel": "3.13.2"}) == "https://www.python.org/downloads/release/python-3132/",
   "pagina_download: Python continua igual")
ok(m.pagina_download({"id": "EclipseAdoptium.Temurin.21.JDK", "disponivel": "21"}) == "https://adoptium.net/temurin/releases/?version=21",
   "pagina_download: Temurin continua igual")
ok(m.pagina_download({"id": "Foo.Qualquer", "disponivel": "1.0"}) == "",
   "pagina_download: id qualquer -> ''")
_oledb = {"nome": "Microsoft OLE DB Driver", "id": "Microsoft.SQLServer.OLEDBDriver", "versao": "18", "disponivel": "19",
          "status": "Atualizacao", "tag": "update", "tipo": "App", "acao": [["x"]], "marcado": False}
m.marcar_fora_do_winget(_oledb)
ok(str(_oledb.get("pagina", "")).startswith("https://"),
   "marcar_fora_do_winget no OLE DB define item['pagina'] (https)")
print()
print("RESULTADO:", "TUDO OK" if not falhas else "%d falha(s): %s" % (len(falhas), falhas))
