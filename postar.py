# -*- coding: utf-8 -*-
"""
Baralho do Zeca - publica os carrosseis no Instagram pela API oficial da Meta.

Uso (no terminal, dentro desta pasta):
    py postar.py testar               confere token, conta e se as imagens estao acessiveis
    py postar.py status               mostra o que ja saiu e qual e o proximo
    py postar.py proximo              publica o proximo post da fila
    py postar.py proximo --simular    mostra o que seria publicado, sem publicar
    py postar.py publicar 07          publica uma pasta especifica (numero ou nome)
    py postar.py story 07             publica so a story de uma pasta (se ainda nao saiu)
    py postar.py reel                 publica o Reel da carta do ultimo carrossel publicado
    py postar.py reel 07              publica o Reel de uma pasta especifica
    py postar.py metricas             anota seguidores e posts de hoje no metricas.csv
    py postar.py insights             coleta as metricas de cada carrossel e Reel da ultima semana
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
METRICAS = os.path.join(AQUI, "metricas.csv")
MARCOS = [1000, 5000, 10000]
METRICAS_POSTS = os.path.join(AQUI, "metricas-posts.csv")
RESUMO = os.path.join(AQUI, "metricas", "resumo.json")
METRICAS_CONTA = os.path.join(AQUI, "metricas-conta.csv")
PUBLICO = os.path.join(AQUI, "metricas", "publico.json")
CAMPOS = ["coleta", "pasta", "tipo", "media_id", "publicado", "horas", "views", "reach", "likes",
          "comments", "shares", "saved", "interacoes", "follows", "profile_visits", "profile_activity",
          "replies", "navigation", "tempo_medio_s", "tempo_total_min", "duracao_s", "erro"]
METRICAS_TIPO = {   # tudo o que a API do Instagram entrega por tipo de post
    "carrossel": ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions",
                  "follows", "profile_visits", "profile_activity"],
    "reel": ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions",
             "ig_reels_avg_watch_time", "ig_reels_video_view_total_time"],
    "story": ["views", "reach", "replies", "shares", "total_interactions", "follows",
              "profile_visits", "profile_activity", "navigation"],
}
METRICAS_CONTA_LISTA = ["reach", "views", "accounts_engaged", "total_interactions", "likes", "comments",
                        "shares", "saves", "replies", "follows_and_unfollows", "profile_links_taps"]               # seguidores: abre um aviso quando passar de cada um
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
    for k, env in [("TOKEN", "IG_TOKEN"), ("URL_BASE", "URL_BASE"), ("API", "IG_API"), ("RUPLOAD", "IG_RUPLOAD")]:
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
    slides = sorted(f for f in os.listdir(dirp) if re.match(r"^\d+\.jpe?g$", f, re.I))
    if not slides:
        sair(f"A pasta {pasta} nao tem imagens .jpg")
    if len(slides) > 10:
        sair(f"A pasta {pasta} tem {len(slides)} slides; o limite do carrossel e 10")
    leg = os.path.join(dirp, "legenda.txt")
    legenda = open(leg, encoding="utf-8").read().split(SEPARADOR_ALT)[0].strip() if os.path.exists(leg) else ""
    if len(legenda) > 2200:
        sair(f"A legenda de {pasta} passa de 2200 caracteres")
    urls = [cfg["URL_BASE"] + urllib.parse.quote(f"{pasta}/{s}") for s in slides]
    story = None
    if os.path.exists(os.path.join(dirp, "story.jpg")):
        story = cfg["URL_BASE"] + urllib.parse.quote(f"{pasta}/story.jpg")
    return {"pasta": pasta, "urls": urls, "legenda": legenda, "story": story}

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

    if post["story"]:
        print(f"   story: {post['story']}")
    todas = post["urls"] + ([post["story"]] if post["story"] else [])
    ruins = [(u, t) for u in todas for ok, t in [url_acessivel(u)] if not ok]
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
    if post["story"]:
        publicar_story(cfg, pasta, post["story"], ig)
    return mid

def publicar_story(cfg, pasta, url, ig=None):
    """Publica a story de chamada. Se falhar, o carrossel continua registrado."""
    reg = registro()
    try:
        ig = ig or minha_conta(cfg)[0]
        sid = chamar(cfg, "POST", f"{ig}/media", {"image_url": url, "media_type": "STORIES"})["id"]
        esperar_pronto(cfg, sid, "da story")
        smid = chamar(cfg, "POST", f"{ig}/media_publish", {"creation_id": sid})["id"]
        reg.setdefault(pasta, {})["story_id"] = smid
        reg[pasta].pop("story_erro", None)
        salvar_registro(reg)
        print(f"   STORY publicada (id {smid})")
    except RuntimeError as e:
        reg.setdefault(pasta, {})["story_erro"] = str(e)[:300]
        salvar_registro(reg)
        print(f"\n[ERRO] O carrossel saiu, mas a story nao: {e}")
        print(f"       Para tentar de novo: py postar.py story {pasta}")
        sys.exit(2)


# ---------------- reels ----------------
def arquivos_reel(pasta):
    dirp = pastas()[pasta]
    mp4 = os.path.join(dirp, "reel.mp4")
    if not os.path.exists(mp4):
        return None
    leg = os.path.join(dirp, "reel-legenda.txt")
    info = os.path.join(dirp, "reel.json")
    return {"mp4": mp4,
            "legenda": open(leg, encoding="utf-8").read().strip() if os.path.exists(leg) else "",
            "info": json.load(open(info, encoding="utf-8")) if os.path.exists(info) else {}}

def enviar_video(cfg, cid, caminho):
    """Upload direto do arquivo para a Meta (nao depende de hospedar o video)."""
    versao = cfg["API"].rstrip("/").rsplit("/", 1)[-1]
    base = cfg.get("RUPLOAD", "https://rupload.facebook.com/ig-api-upload").rstrip("/")
    dados = open(caminho, "rb").read()
    req = urllib.request.Request(f"{base}/{versao}/{cid}", data=dados, method="POST", headers={
        "Authorization": f"OAuth {cfg['TOKEN']}", "offset": "0", "file_size": str(len(dados))})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            resp = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Upload do video falhou ({e.code}): {e.read().decode()[:300]}")
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"Upload do video falhou: {e}")
    if not resp.get("success", True):
        raise RuntimeError(f"Upload do video falhou: {resp}")

def publicar_reel(cfg, pasta, simular=False, forcar=False):
    reg = registro()
    if reg.get(pasta, {}).get("reel_id") and not forcar:
        print(f"O Reel de {pasta} ja saiu em {reg[pasta].get('reel_quando', '?')}. Nada a fazer.")
        return None
    r = arquivos_reel(pasta)
    if not r:
        print(f"{pasta} nao tem reel.mp4. Nada a fazer.")
        return None
    mb = os.path.getsize(r["mp4"]) / 1e6
    print(f"\n> Reel de {pasta}  ({mb:.1f} MB, formato {r['info'].get('formato', '?')})")
    print("   legenda: " + r["legenda"].splitlines()[0][:90])
    if len(r["legenda"]) > 2200:
        sair(f"A legenda do Reel de {pasta} passa de 2200 caracteres")
    if simular:
        print("   (simulacao: nada foi publicado)")
        return None
    ig, usuario = minha_conta(cfg)
    dados = {"media_type": "REELS", "upload_type": "resumable", "caption": r["legenda"],
             "share_to_feed": False}           # so na aba Reels: a grade fica so com os carrosseis
    if r["info"].get("thumb_offset"):
        dados["thumb_offset"] = int(r["info"]["thumb_offset"])
    try:
        try:
            cid = chamar(cfg, "POST", f"{ig}/media", dados)["id"]
            enviar_video(cfg, cid, r["mp4"])
        except RuntimeError as e:      # plano B: a Meta baixa o video do link publico
            print(f"   upload direto falhou ({e}); tentando pelo link do repositorio...")
            dados.pop("upload_type")
            dados["video_url"] = cfg["URL_BASE"] + urllib.parse.quote(f"{pasta}/reel.mp4")
            cid = chamar(cfg, "POST", f"{ig}/media", dados)["id"]
        print("   video enviado, a Meta esta processando...")
        esperar_pronto(cfg, cid, "do Reel", limite_s=900, intervalo=20)
        mid = chamar(cfg, "POST", f"{ig}/media_publish", {"creation_id": cid})["id"]
    except RuntimeError as e:
        reg.setdefault(pasta, {})["reel_erro"] = str(e)[:300]
        salvar_registro(reg)
        print(f"\n[ERRO] O Reel nao saiu: {e}")
        print(f"       Para tentar de novo: py postar.py reel {pasta}")
        sys.exit(2)
    reg = registro()
    reg.setdefault(pasta, {})["reel_id"] = mid
    reg[pasta]["reel_quando"] = datetime.now(TZ_BR).isoformat(timespec="minutes")
    reg[pasta].pop("reel_erro", None)
    salvar_registro(reg)
    print(f"   REEL PUBLICADO em @{usuario} (id {mid})")
    return mid

def ultimo_carrossel():
    reg = registro()
    feitos = [p for p in fila() if reg.get(p, {}).get("media_id")]
    return feitos[-1] if feitos else None


# ---------------- metricas ----------------
def cmd_metricas(cfg):
    eu = chamar(cfg, "GET", "me", consulta={"fields": "username,followers_count,follows_count,media_count"})
    seg = int(eu.get("followers_count", 0))
    agora = datetime.now(TZ_BR)
    linhas = open(METRICAS, encoding="utf-8").read().splitlines() if os.path.exists(METRICAS) else []
    if not linhas:
        linhas = ["data,hora,seguidores,seguindo,posts,obs"]
    hist = []
    for l in linhas[1:]:
        partes = l.split(",")
        try:
            hist.append((partes[0], int(partes[2])))
        except (IndexError, ValueError):
            pass
    hoje = agora.strftime("%Y-%m-%d")
    nova = f"{hoje},{agora.strftime('%H:%M')},{seg},{eu.get('follows_count', '')},{eu.get('media_count', '')},"
    if len(linhas) > 1 and linhas[-1].startswith(hoje + ",") and linhas[-1].endswith(","):
        linhas[-1] = nova                  # mesmo dia: atualiza (a linha do ponto de partida nunca muda)
    else:
        linhas.append(nova)
    open(METRICAS, "w", encoding="utf-8").write("\n".join(linhas) + "\n")
    inicio = hist[0] if hist else (hoje, seg)
    print(f"@{eu.get('username', '?')}: {seg} seguidores (comecamos em {inicio[0]} com {inicio[1]}: {seg - inicio[1]:+d})")
    maior_antes = max([v for _, v in hist] or [0])
    for m in MARCOS:
        if seg >= m > maior_antes:
            msg = (f"Bateu {m:,} seguidores em {agora.strftime('%d/%m/%Y')}! ".replace(",", ".") +
                   f"Comecamos em {datetime.strptime(inicio[0], '%Y-%m-%d').strftime('%d/%m/%Y')} com {inicio[1]}. "
                   "Hora do post de agradecimento.")
            print("MARCO: " + msg)
            saida = os.environ.get("GITHUB_OUTPUT")
            if saida:
                with open(saida, "a", encoding="utf-8") as fh:
                    fh.write(f"marco={m}\nmensagem={msg}\n")


# ---------------- insights dos posts ----------------
def _valor(d):
    v = (d.get("values") or [{}])[0].get("value") if d.get("values") else None
    if v is None:
        v = (d.get("total_value") or {}).get("value")
    if isinstance(v, dict):                 # metricas com detalhamento (ex.: navigation)
        v = sum(x for x in v.values() if isinstance(x, (int, float)))
    return v

def ler_insights(cfg, mid, tipo):
    """Pede todas as metricas do tipo; se a Meta recusar alguma, pede uma a uma e fica com as que vierem."""
    metricas = METRICAS_TIPO[tipo]
    try:
        resp = chamar(cfg, "GET", f"{mid}/insights", consulta={"metric": ",".join(metricas)})
        return {d["name"]: _valor(d) for d in resp.get("data", [])}, None
    except RuntimeError as e:
        primeiro = str(e)
    out, erros = {}, []
    for m in metricas:
        try:
            resp = chamar(cfg, "GET", f"{mid}/insights", consulta={"metric": m})
            for d in resp.get("data", []):
                out[d["name"]] = _valor(d)
        except RuntimeError as e:
            erros.append(m)
    return out, (None if out else primeiro)

def cmd_conta(cfg, ig):
    """Metricas da conta no dia anterior inteiro + publico (idade, genero, cidade)."""
    hoje0 = datetime.now(TZ_BR).replace(hour=0, minute=0, second=0, microsecond=0)
    ontem0 = hoje0 - timedelta(days=1)
    base = {"period": "day", "metric_type": "total_value",
            "since": int(ontem0.timestamp()), "until": int(hoje0.timestamp())}
    vals = {}
    try:
        resp = chamar(cfg, "GET", f"{ig}/insights", consulta={**base, "metric": ",".join(METRICAS_CONTA_LISTA)})
        vals = {d["name"]: _valor(d) for d in resp.get("data", [])}
    except RuntimeError:
        for m in METRICAS_CONTA_LISTA:
            try:
                resp = chamar(cfg, "GET", f"{ig}/insights", consulta={**base, "metric": m})
                vals.update({d["name"]: _valor(d) for d in resp.get("data", [])})
            except RuntimeError:
                pass
    import csv
    dia = ontem0.strftime("%Y-%m-%d")
    campos = ["dia"] + METRICAS_CONTA_LISTA
    linhas = list(csv.DictReader(open(METRICAS_CONTA, encoding="utf-8"))) if os.path.exists(METRICAS_CONTA) else []
    linhas = [l for l in linhas if l.get("dia") != dia] + [{"dia": dia, **{m: vals.get(m, "") for m in METRICAS_CONTA_LISTA}}]
    with open(METRICAS_CONTA, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=campos); w.writeheader()
        for l in linhas: w.writerow({k: ("" if l.get(k) is None else l.get(k, "")) for k in campos})
    print(f"Conta em {dia}: " + ", ".join(f"{k} {v}" for k, v in vals.items()) if vals else
          f"Conta em {dia}: a Meta nao entregou metricas da conta")
    publico = {}
    for nome in ("follower_demographics", "engaged_audience_demographics"):
        for corte in ("age", "gender", "city", "country"):
            try:
                resp = chamar(cfg, "GET", f"{ig}/insights", consulta={
                    "metric": nome, "period": "lifetime", "metric_type": "total_value",
                    "timeframe": "this_month", "breakdown": corte})
                res = {}
                for d in resp.get("data", []):
                    for b in (d.get("total_value") or {}).get("breakdowns", []):
                        for r in b.get("results", []):
                            res[" / ".join(r.get("dimension_values", []))] = r.get("value")
                publico.setdefault(nome, {})[corte] = dict(sorted(res.items(), key=lambda x: -(x[1] or 0))[:15])
            except RuntimeError as e:
                publico.setdefault(nome, {})[corte] = f"indisponivel: {str(e)[:120]}"
    os.makedirs(os.path.dirname(PUBLICO), exist_ok=True)
    json.dump({"coleta": datetime.now(TZ_BR).isoformat(timespec="minutes"), **publico},
              open(PUBLICO, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

def mediana(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return None
    k = len(xs) // 2
    return xs[k] if len(xs) % 2 else (xs[k-1] + xs[k]) / 2

def cmd_insights(cfg, dias=8):
    reg = registro(); agora = datetime.now(TZ_BR)
    itens = []
    for p, v in reg.items():
        for tipo, kid, kq in (("carrossel", "media_id", "quando"), ("reel", "reel_id", "reel_quando"),
                              ("story", "story_id", "quando")):
            if v.get(kid) and v.get(kq):
                d = datetime.fromisoformat(v[kq]); d = d if d.tzinfo else d.replace(tzinfo=TZ_BR)
                horas = (agora - d).total_seconds() / 3600
                if horas <= (30 if tipo == "story" else dias * 24):    # story some em 24h
                    itens.append((p, tipo, v[kid], v[kq], horas))
    novos, erros = [], []
    for p, tipo, mid, quando, horas in sorted(itens, key=lambda x: x[3]):
        m, erro = ler_insights(cfg, mid, tipo)
        dur = None
        if tipo == "reel":
            r = arquivos_reel(p) if p in pastas() else None
            dur = (r or {}).get("info", {}).get("duracao_s")
        tm = m.get("ig_reels_avg_watch_time"); tt = m.get("ig_reels_video_view_total_time")
        linha = {"coleta": agora.isoformat(timespec="minutes"), "pasta": p, "tipo": tipo, "media_id": mid,
                 "publicado": quando, "horas": f"{horas:.1f}", "views": m.get("views"), "reach": m.get("reach"),
                 "likes": m.get("likes"), "comments": m.get("comments"), "shares": m.get("shares"),
                 "saved": m.get("saved"), "interacoes": m.get("total_interactions"),
                 "follows": m.get("follows"), "profile_visits": m.get("profile_visits"),
                 "profile_activity": m.get("profile_activity"), "replies": m.get("replies"),
                 "navigation": m.get("navigation"),
                 "tempo_medio_s": f"{tm/1000:.1f}" if tm else "", "tempo_total_min": f"{tt/60000:.1f}" if tt else "",
                 "duracao_s": dur or "", "erro": (erro or "")[:160]}
        novos.append(linha)
        if erro: erros.append(f"{p} ({tipo}): {erro}")
        print(f"{p:24s} {tipo:9s} {horas:5.1f}h  views {linha['views']}  alcance {linha['reach']}  "
              f"compart. {linha['shares']}  salvos {linha['saved']}" + (f"  [erro: {erro}]" if erro else ""))
    novo_arq = not os.path.exists(METRICAS_POSTS)
    with open(METRICAS_POSTS, "a", encoding="utf-8", newline="") as fh:
        import csv
        w = csv.DictWriter(fh, fieldnames=CAMPOS)
        if novo_arq: w.writeheader()
        for l in novos: w.writerow({k: ("" if l.get(k) is None else l[k]) for k in CAMPOS})
    # resumo: foto mais recente de cada post + sinais de que vale turbinar
    seg = None
    try:
        seg = int(open(METRICAS, encoding="utf-8").read().splitlines()[-1].split(",")[2])
    except Exception:
        pass
    import csv
    hist = list(csv.DictReader(open(METRICAS_POSTS, encoding="utf-8")))
    def num(x):
        try: return float(x)
        except (TypeError, ValueError): return None
    resumo = []
    for l in novos:
        mesmos = [h for h in hist if h["tipo"] == l["tipo"] and h["pasta"] != l["pasta"]
                  and 20 <= (num(h["horas"]) or 0) <= 48]
        base_v = mediana([num(h["views"]) or num(h["reach"]) for h in mesmos])
        base_s = mediana([(num(h["shares"]) or 0) + (num(h["saved"]) or 0) for h in mesmos])
        v = num(l["views"]) or num(l["reach"]) or 0
        alc = num(l["reach"]) or 0
        cs = (num(l["shares"]) or 0) + (num(l["saved"]) or 0)
        sinais = []
        if base_v and v >= 3 * base_v and v >= 300:
            sinais.append(f"{int(v)} views, {v/base_v:.1f}x a mediana dos outros {l['tipo']}s")
        if not base_v and seg and alc >= 2 * seg:
            sinais.append(f"alcance {int(alc)}, mais que o dobro dos seguidores ({seg})")
        if alc and cs >= max(5, 0.03 * alc):
            sinais.append(f"{int(cs)} compartilhamentos+salvamentos ({cs/alc:.0%} do alcance)")
        tm, dur = num(l["tempo_medio_s"]), num(l["duracao_s"])
        if tm and dur and tm >= 0.6 * dur:
            sinais.append(f"retencao alta: {tm:.1f}s assistidos em media de {dur:.0f}s")
        resumo.append({**l, "mediana_views_tipo": base_v, "seguidores": seg, "sinais": sinais,
                       "candidato_turbinar": len(sinais) >= 2 and l["tipo"] != "story"})
    os.makedirs(os.path.dirname(RESUMO), exist_ok=True)
    json.dump({"coleta": agora.isoformat(timespec="minutes"), "seguidores": seg, "itens": resumo},
              open(RESUMO, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    try:
        cmd_conta(cfg, minha_conta(cfg)[0])
    except RuntimeError as e:
        print(f"(metricas da conta indisponiveis: {e})")
    cands = [r for r in resumo if r["candidato_turbinar"]]
    for r in cands:
        print(f"CANDIDATO A TURBINAR: {r['pasta']} ({r['tipo']}): " + "; ".join(r["sinais"]))
    if erros and all(not r["views"] and not r["reach"] for r in resumo):
        print("\n[AVISO] A Meta nao entregou metricas. Se o erro fala de permissao, gere um token novo com a "
              "permissao instagram_business_manage_insights marcada e atualize o segredo IG_TOKEN.")


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
        extra = "  + reel" if reg[p].get("reel_id") else ("  (reel com erro)" if reg[p].get("reel_erro") else "")
        print(f"   {reg[p]['quando']}  {p}{extra}")
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
        elif cmd == "story" and len(args) > 1:
            p = achar_pasta(args[1])
            reg = registro()
            if reg.get(p, {}).get("story_id") and "--forcar" not in flags:
                sair(f"A story de {p} ja saiu. Use --forcar para publicar de novo.")
            post = montar_post(cfg, p)
            if not post["story"]:
                sair(f"A pasta {p} nao tem story.jpg")
            ok, tipo = url_acessivel(post["story"])
            if not ok:
                sair(f"A story nao abre como JPEG: {post['story']} ({tipo})")
            if "--simular" in flags:
                print(f"   (simulacao) story de {p}: {post['story']}"); return
            publicar_story(cfg, p, post["story"])
        elif cmd == "reel":
            p = achar_pasta(args[1]) if len(args) > 1 else ultimo_carrossel()
            if not p:
                print("Nenhum carrossel publicado ainda: sem Reel para hoje."); return
            publicar_reel(cfg, p, simular="--simular" in flags, forcar="--forcar" in flags)
        elif cmd == "metricas":
            cmd_metricas(cfg)
        elif cmd == "insights":
            cmd_insights(cfg)
        elif cmd == "renovar-token":
            cmd_renovar(cfg)
        else:
            print(__doc__)
    except RuntimeError as e:
        sair(str(e))

if __name__ == "__main__":
    main()
