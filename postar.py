# -*- coding: utf-8 -*-
"""
Baralho do Zeca - publica os carrosseis no Instagram pela API oficial da Meta.

Uso (no terminal, dentro desta pasta):
    py postar.py testar               confere token, conta e se as imagens estao acessiveis
    py postar.py status               mostra o que ja saiu e qual e o proximo
    py postar.py proximo              publica o proximo post da fila
    py postar.py proximo --simular    mostra o que seria publicado, sem publicar
    py postar.py publicar 07          publica uma pasta especifica (numero ou nome)
    py postar.py renovar-token        renova o token (valido por 60 dias) e grava no config.txt

So usa a biblioteca padrao do Python: nada para instalar.
"""
import json, os, re, sys, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta, timezone

TZ_BR = timezone(timedelta(hours=-3))      # horario de Brasilia
INTERVALO_MIN_H = 12                       # nunca dois posts com menos de 12h de diferenca

try:
    sys.stdout.reconfigure(encoding="utf-8")   # acentos no terminal do Windows
except Exception:
    pass

AQUI = os.path.dirname(os.path.abspath(__file__))
POSTS = os.path.join(AQUI, "posts")
CONFIG = os.path.join(AQUI, "config.txt")
REGISTRO = os.path.join(AQUI, "publicados.json")
SEPARADOR_ALT = "\n---\nTEXTO ALTERNATIVO:"


# ---------------- configuracao ----------------
def ler_config():
    cfg = {"API": "https://graph.instagram.com/v25.0"}
    if os.path.exists(CONFIG):
        for linha in open(CONFIG, encoding="utf-8"):
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                cfg[k.strip()] = v.strip()
    # variaveis de ambiente tem prioridade (uso no GitHub Actions)
    for k, env in [("TOKEN", "IG_TOKEN"), ("URL_BASE", "URL_BASE"), ("API", "IG_API")]:
        if os.environ.get(env):
            cfg[k] = os.environ[env]
    falta = [k for k in ("TOKEN", "URL_BASE") if not cfg.get(k) or "COLE_AQUI" in cfg.get(k, "")]
    if falta:
        sair(f"Falta preencher no config.txt: {', '.join(falta)}")
    if not cfg["URL_BASE"].endswith("/"):
        cfg["URL_BASE"] += "/"
    return cfg

def gravar_token(novo):
    linhas = open(CONFIG, encoding="utf-8").read().splitlines() if os.path.exists(CONFIG) else []
    linhas = [l for l in linhas if not l.strip().startswith("TOKEN=")] + [f"TOKEN={novo}"]
    open(CONFIG, "w", encoding="utf-8").write("\n".join(linhas) + "\n")

def sair(msg, codigo=1):
    print(f"\n[ERRO] {msg}\n")
    sys.exit(codigo)


# ---------------- chamadas HTTP ----------------
def chamar(cfg, metodo, caminho, dados=None, consulta=None):
    url = cfg["API"].rstrip("/") + "/" + caminho.lstrip("/")
    if consulta:
        url += "?" + urllib.parse.urlencode(consulta)
    corpo = json.dumps(dados).encode() if dados is not None else None
    req = urllib.request.Request(url, data=corpo, method=metodo, headers={
        "Authorization": f"Bearer {cfg['TOKEN']}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get("error", {})
            msg = err.get("error_user_msg") or err.get("message") or str(err)
        except Exception:
            msg = str(e)
        raise RuntimeError(f"Meta respondeu {e.code}: {msg}")

def url_acessivel(url):
    """A Meta baixa cada imagem pela URL: ela precisa abrir sem login e ser JPEG."""
    req = urllib.request.Request(url, method="GET", headers={"Range": "bytes=0-1023"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tipo = r.headers.get("Content-Type", "")
            return ("jpeg" in tipo or "jpg" in tipo), tipo
    except Exception as e:
        return False, str(e)


# ---------------- fila de posts ----------------
_PASTAS = None
def pastas():
    """Acha as pastas de post (00a-capa, 01-as-de-copas...) dentro de posts/, mesmo um nivel abaixo."""
    global _PASTAS
    if _PASTAS is None:
        if not os.path.isdir(POSTS):
            sair(f"Nao achei a pasta de posts em {POSTS}")
        _PASTAS = {}
        for raiz, _, arqs in os.walk(POSTS):
            nome = os.path.basename(raiz)
            if re.match(r"^\d{2}[a-z]?-", nome) and any(a.lower().endswith((".jpg", ".jpeg")) for a in arqs):
                if nome in _PASTAS:
                    sair(f"A pasta {nome} aparece duas vezes dentro de posts/")
                _PASTAS[nome] = raiz
    return _PASTAS

def fila():
    return sorted(pastas())

def registro():
    return json.load(open(REGISTRO, encoding="utf-8")) if os.path.exists(REGISTRO) else {}

def salvar_registro(reg):
    json.dump(reg, open(REGISTRO, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

def montar_post(cfg, pasta):
    dirp = pastas()[pasta]
    slides = sorted(f for f in os.listdir(dirp) if f.lower().endswith((".jpg", ".jpeg")))
    if not slides:
        sair(f"A pasta {pasta} nao tem imagens .jpg")
    if len(slides) > 10:
        sair(f"A pasta {pasta} tem {len(slides)} slides; o limite do carrossel e 10")
    leg = os.path.join(dirp, "legenda.txt")
    legenda = open(leg, encoding="utf-8").read().split(SEPARADOR_ALT)[0].strip() if os.path.exists(leg) else ""
    if len(legenda) > 2200:
        sair(f"A legenda de {pasta} passa de 2200 caracteres")
    urls = [cfg["URL_BASE"] + urllib.parse.quote(f"{pasta}/{s}") for s in slides]
    return {"pasta": pasta, "urls": urls, "legenda": legenda}

def achar_pasta(alvo):
    ps = fila()
    if alvo in ps:
        return alvo
    if alvo.isdigit():                       # "7" ou "07" -> 07-sete-de-copas
        cand = [p for p in ps if p.split("-")[0] == alvo.zfill(2)]
    else:                                    # "capa", "00b", "as-de-copas"
        cand = [p for p in ps if p.startswith(alvo) or alvo in p]
    if len(cand) == 1:
        return cand[0]
    sair(f"Nao achei uma pasta unica para '{alvo}'. Opcoes: {', '.join(cand) or 'nenhuma'}")


# ---------------- publicacao ----------------
def minha_conta(cfg):
    eu = chamar(cfg, "GET", "me", consulta={"fields": "user_id,username"})
    return eu.get("user_id") or eu.get("id"), eu.get("username", "?")

def esperar_pronto(cfg, cid, rotulo, limite_s=300, intervalo=15):
    inicio = time.time()
    while True:
        st = chamar(cfg, "GET", cid, consulta={"fields": "status_code"}).get("status_code")
        if st in ("FINISHED", "PUBLISHED"):
            return
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"O container {rotulo} ficou com status {st}")
        if time.time() - inicio > limite_s:
            raise RuntimeError(f"O container {rotulo} nao ficou pronto em {limite_s // 60} minutos")
        time.sleep(intervalo)

def publicar(cfg, pasta, simular=False, forcar=False):
    reg = registro()
    if pasta in reg and not forcar:
        sair(f"{pasta} ja foi publicado em {reg[pasta]['quando']}. Use --forcar para publicar de novo.")
    post = montar_post(cfg, pasta)
    n = len(post["urls"])
    print(f"\n> {pasta}  ({n} {'imagem' if n == 1 else 'slides'})")
    for u in post["urls"]:
        print(f"   {u}")
    print("   legenda: " + post["legenda"].splitlines()[0][:90] + ("..." if post["legenda"] else ""))

    ruins = [(u, t) for u in post["urls"] for ok, t in [url_acessivel(u)] if not ok]
    if ruins:
        for u, t in ruins:
            print(f"   [X] nao abre como JPEG: {u}  ({t})")
        sair("Suba as imagens antes de publicar (ou confira o URL_BASE no config.txt).")
    print("   imagens acessiveis: ok")
    if simular:
        print("   (simulacao: nada foi publicado)")
        return None

    ig, usuario = minha_conta(cfg)
    if n == 1:
        cid = chamar(cfg, "POST", f"{ig}/media", {"image_url": post["urls"][0], "caption": post["legenda"]})["id"]
        esperar_pronto(cfg, cid, "da imagem")
    else:
        filhos = []
        for i, u in enumerate(post["urls"], 1):
            fid = chamar(cfg, "POST", f"{ig}/media", {"image_url": u, "is_carousel_item": True})["id"]
            filhos.append(fid)
            print(f"   slide {i}/{n} enviado")
        for i, fid in enumerate(filhos, 1):
            esperar_pronto(cfg, fid, f"do slide {i}")
        cid = chamar(cfg, "POST", f"{ig}/media", {"media_type": "CAROUSEL",
                                                 "children": ",".join(filhos),
                                                 "caption": post["legenda"]})["id"]
        esperar_pronto(cfg, cid, "do carrossel")
    mid = chamar(cfg, "POST", f"{ig}/media_publish", {"creation_id": cid})["id"]
    reg[pasta] = {"media_id": mid, "quando": datetime.now(TZ_BR).isoformat(timespec="minutes")}
    salvar_registro(reg)
    print(f"   PUBLICADO em @{usuario} (id {mid})")
    return mid


# ---------------- comandos ----------------
def cmd_testar(cfg):
    ig, usuario = minha_conta(cfg)
    print(f"Conta: @{usuario}  (id {ig})")
    try:
        lim = chamar(cfg, "GET", f"{ig}/content_publishing_limit", consulta={"fields": "quota_usage,config"})
        d = (lim.get("data") or [{}])[0]
        total = (d.get("config") or {}).get("quota_total", "?")
        print(f"Posts pela API nas ultimas 24h: {d.get('quota_usage', '?')} de {total}")
    except RuntimeError as e:
        print(f"(nao consegui ler o limite de publicacao: {e})")
    prox = proximo_da_fila()
    if prox:
        publicar(cfg, prox, simular=True)
    print("\nTudo certo para publicar.")

def horas_desde_ultimo():
    """Horas desde a ultima publicacao registrada (None se nada saiu ainda)."""
    datas = []
    for v in registro().values():
        try:
            d = datetime.fromisoformat(v["quando"])
            datas.append(d if d.tzinfo else d.replace(tzinfo=TZ_BR))
        except Exception:
            pass
    if not datas:
        return None
    return (datetime.now(TZ_BR) - max(datas)).total_seconds() / 3600

def proximo_da_fila():
    reg = registro()
    return next((p for p in fila() if p not in reg), None)

def cmd_status():
    reg, ps = registro(), fila()
    feitos = [p for p in ps if p in reg]
    print(f"Publicados: {len(feitos)} de {len(ps)}")
    for p in feitos[-5:]:
        print(f"   {reg[p]['quando']}  {p}")
    prox = proximo_da_fila()
    print(f"Proximo: {prox or 'nenhum - a serie terminou'}")

def cmd_renovar(cfg):
    url = "https://graph.instagram.com/refresh_access_token?" + urllib.parse.urlencode(
        {"grant_type": "ig_refresh_token", "access_token": cfg["TOKEN"]})
    if cfg.get("API_REFRESH"):
        url = cfg["API_REFRESH"] + "?" + url.split("?", 1)[1]
    with urllib.request.urlopen(url, timeout=60) as r:
        resp = json.loads(r.read().decode())
    gravar_token(resp["access_token"])
    dias = int(resp.get("expires_in", 0)) // 86400
    print(f"Token renovado e gravado no config.txt. Vale por mais {dias} dias.")

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__); return
    cmd = args[0]
    if cmd == "status":
        cmd_status(); return
    cfg = ler_config()
    try:
        if cmd == "testar":
            cmd_testar(cfg)
        elif cmd == "proximo":
            p = proximo_da_fila()
            if not p:
                print("A serie terminou: nada na fila."); return
            h = horas_desde_ultimo()
            if h is not None and h < INTERVALO_MIN_H and "--forcar" not in flags and "--simular" not in flags:
                print(f"O ultimo post saiu ha {h:.1f}h. Para evitar post em dobro, o proximo ({p}) "
                      f"so sai depois de {INTERVALO_MIN_H}h. Use --forcar para publicar mesmo assim.")
                return
            publicar(cfg, p, simular="--simular" in flags)
        elif cmd == "publicar" and len(args) > 1:
            publicar(cfg, achar_pasta(args[1]), simular="--simular" in flags, forcar="--forcar" in flags)
        elif cmd == "renovar-token":
            cmd_renovar(cfg)
        else:
            print(__doc__)
    except RuntimeError as e:
        sair(str(e))

if __name__ == "__main__":
    main()
