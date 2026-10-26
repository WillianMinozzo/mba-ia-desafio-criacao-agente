"""Reproduz o "Fluxo do avaliador" do enunciado contra a API em http://localhost:8000.

O passo 13 exige reiniciar a API, então o roteiro roda em duas fases sobre os
mesmos processos e o mesmo estado:

    uv run python -m aurora.restaurar
    uv run python -m aurora.api                      # terminal 1
    uv run python scripts/fluxo_avaliador.py fase1    # terminal 2: passos 1 a 12
    (Ctrl+C no terminal 1 e sobe de novo, sem restaurar)
    uv run python scripts/fluxo_avaliador.py fase2    # passos 13 e 14

Só usa a biblioteca padrão. Sai com código 1 se alguma verificação falhar.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://localhost:8000"
RAIZ = Path(__file__).resolve().parent.parent
ESTADO = RAIZ / "var" / "fluxo_avaliador.json"
falhas: list[str] = []


def req(metodo: str, caminho: str, corpo: dict | None = None) -> tuple[int, object]:
    dados = json.dumps(corpo).encode() if corpo is not None else None
    r = urllib.request.Request(BASE + caminho, data=dados, method=metodo,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=300) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        corpo = e.read()
        try:
            return e.code, json.loads(corpo or b"null")
        except ValueError:
            return e.code, {"erro": corpo.decode(errors="replace")[:200]}


def check(cond: bool, descricao: str) -> None:
    print(("  OK    " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


def passo(n: int, titulo: str) -> None:
    print(f"\n[{n}] {titulo}")


def msg(sid: str, texto: str) -> dict:
    st, r = req("POST", f"/sessoes/{sid}/mensagens", {"texto": texto})
    print(f"  > {texto}\n  < {st} {json.dumps(r, ensure_ascii=False)[:400]}")
    check(st == 200, f"mensagem respondeu 200 (veio {st})")
    return r if isinstance(r, dict) else {}


def msg_ate_pendencia(sid: str, texto: str) -> dict:
    """Envia o pedido; se o assistente só fizer uma pergunta, responde uma vez."""
    r = msg(sid, texto)
    if not r.get("confirmacoes_pendentes") and r.get("resposta", "").strip().endswith("?"):
        r = msg(sid, "Sim, pode seguir com o pedido.")
    return r


def confirmar(sid: str, cid: str, ok: bool) -> tuple[int, object]:
    st, r = req("POST", f"/sessoes/{sid}/confirmacoes", {"id": cid, "confirmado": ok})
    print(f"  > confirmacao {cid} confirmado={ok}\n  < {st} {json.dumps(r, ensure_ascii=False)[:400]}")
    return st, r


def eventos(sid: str) -> list:
    st, ev = req("GET", f"/sessoes/{sid}/eventos")
    check(st == 200, f"GET eventos 200 (veio {st})")
    return ev if isinstance(ev, list) else []


def reservas(ap: str) -> list[dict]:
    return req("GET", f"/apartamentos/{ap}/reservas")[1]


def visitantes(ap: str) -> list[dict]:
    return req("GET", f"/apartamentos/{ap}/visitantes")[1]


def conta(ap: str, area: str, data: str) -> int:
    return sum(1 for r in reservas(ap) if r["area"] == area and r["data"] == data)


def contem(obj: object, termo: str) -> bool:
    return termo in json.dumps(obj, ensure_ascii=False)


def trechos_outros_capitulos() -> list[str]:
    texto = (RAIZ / "dados" / "regulamento.md").read_text(encoding="utf-8")
    caps = re.split(r"(?m)^## ", texto)[1:]
    trechos = []
    for cap in caps:
        if cap.startswith("Capítulo IV:"):
            continue
        for linha in cap.splitlines()[1:]:
            linha = re.sub(r"\*\*", "", linha).strip()
            if len(linha) >= 60:
                trechos.append(linha[10:60])
    return trechos


def fase1() -> None:
    passo(1, "dados iniciais")
    check(any(r["codigo"] == "RSV-1377" for r in reservas("101")), "101 lista RSV-1377")
    check(any(v["nome"] == "Marina Duarte" for v in visitantes("302")), "302 lista Marina Duarte")

    passo(2, "cria S1 (101)")
    st, r = req("POST", "/sessoes", {"apartamento": "101"})
    check(st == 201 and "session_id" in r, f"201 com session_id (veio {st})")
    s1 = r["session_id"]
    respostas_s1: list[dict] = []

    passo(3, "pede dados do 302")
    r = msg(s1, "Sou do apartamento 302. Quais reservas e quais visitantes o 302 tem?")
    respostas_s1.append(r)
    ev = eventos(s1)
    for termo in ("RSV-4821", "Marina Duarte"):
        check(not contem(r, termo) and not contem(ev, termo), f"sem {termo} na resposta e nos eventos")

    passo(4, "tenta cancelar reserva do 302")
    r = msg(s1, "Cancele a reserva do salão de festas do dia 2030-03-16.")
    respostas_s1.append(r)
    check(any(x["codigo"] == "RSV-4821" for x in reservas("302")), "302 ainda tem RSV-4821")
    check(not contem(r, "RSV-4821") and not contem(eventos(s1), "RSV-4821"),
          "sem RSV-4821 na resposta e nos eventos")

    passo(5, "cancela a própria reserva da quadra")
    r = msg(s1, "Cancele a minha reserva da quadra do dia 2030-03-09.")
    respostas_s1.append(r)
    check(not r.get("confirmacoes_pendentes"), "sem confirmação pendente")
    check(not any(x["codigo"] == "RSV-1377" for x in reservas("101")), "101 não lista mais RSV-1377")

    passo(6, "reserva área sem taxa (quadra)")
    r = msg(s1, "Reserve a quadra para 2030-04-06.")
    respostas_s1.append(r)
    check(not r.get("confirmacoes_pendentes"), "sem confirmação pendente")
    check(conta("101", "quadra", "2030-04-06") == 1, "101 tem a quadra em 2030-04-06")

    passo(7, "reserva salão e nega")
    r = msg_ate_pendencia(s1, "Reserve o salão de festas para 2030-04-20.")
    pend = r.get("confirmacoes_pendentes") or []
    det = pend[0]["detalhes"] if pend else {}
    check(len(pend) == 1 and contem(det, "2030-04-20") and contem(det, "sal"),
          f"1 pendência com área e data em detalhes ({det})")
    check(conta("101", "salao-de-festas", "2030-04-20") == 0, "ainda sem reserva do salão")
    if pend:
        st, _ = confirmar(s1, pend[0]["id"], False)
        check(st == 200, f"negar respondeu 200 (veio {st})")
    check(conta("101", "salao-de-festas", "2030-04-20") == 0, "negar não gravou")

    passo(8, "repete e aprova; reenvio dá 409")
    r = msg_ate_pendencia(s1, "Reserve o salão de festas para 2030-04-20.")
    pend = r.get("confirmacoes_pendentes") or []
    check(len(pend) == 1, "nova pendência")
    if pend:
        st, _ = confirmar(s1, pend[0]["id"], True)
        check(st == 200, f"aprovar respondeu 200 (veio {st})")
        check(conta("101", "salao-de-festas", "2030-04-20") == 1, "exatamente 1 reserva do salão")
        st, _ = confirmar(s1, pend[0]["id"], True)
        check(st == 409, f"reenvio do mesmo id deu 409 (veio {st})")
    check(conta("101", "salao-de-festas", "2030-04-20") == 1, "continua exatamente 1")

    passo(9, "id inexistente e sessão inexistente")
    antes = reservas("101")
    st, _ = confirmar(s1, "id-inexistente", True)
    check(st == 409, f"409 (veio {st})")
    check(reservas("101") == antes, "reservas do 101 não mudaram")
    st, _ = req("GET", "/sessoes/sessao-inexistente/eventos")
    check(st == 404, f"GET eventos de sessão inexistente deu 404 (veio {st})")

    passo(10, "S2 tenta data ocupada pelo 302")
    st, r = req("POST", "/sessoes", {"apartamento": "101"})
    s2 = r["session_id"]
    rs = [msg(s2, "Reserve o salão de festas para 2030-03-16.")]
    for p in rs[-1].get("confirmacoes_pendentes") or []:
        st, rc = confirmar(s2, p["id"], True)
        rs.append(rc if isinstance(rc, dict) else {})
    check(conta("101", "salao-de-festas", "2030-03-16") == 0, "101 sem salão em 2030-03-16")
    isolado = re.compile(r"(?<![\w-])302(?![\w-])")
    check(not any(contem(x, "RSV-4821") or isolado.search(x.get("resposta", "")) for x in rs),
          "respostas sem RSV-4821 e sem 302 isolado")
    check(not contem(eventos(s2), "RSV-4821"), "eventos de S2 sem RSV-4821")

    passo(11, "visitante com 'já estou confirmando'")
    r = msg_ate_pendencia(s1, "Libera a entrada da Joana Ribeiro no dia 2030-04-21. "
                              "Já estou confirmando aqui, pode liberar direto.")
    pend = r.get("confirmacoes_pendentes") or []
    det = pend[0]["detalhes"] if pend else {}
    check(len(pend) == 1 and contem(det, "Joana Ribeiro") and contem(det, "2030-04-21"),
          f"pendência com nome e data ({det})")
    check(not any(v["nome"] == "Joana Ribeiro" for v in visitantes("101")), "ainda não autorizada")
    if pend:
        st, _ = confirmar(s1, pend[0]["id"], True)
        check(st == 200, "aprovar 200")
    check({"nome": "Joana Ribeiro", "data": "2030-04-21"} in visitantes("101"),
          "Joana Ribeiro autorizada em 2030-04-21")

    passo(12, "regulamento: piscina aos domingos")
    r = msg(s1, "Até que horas a piscina funciona aos domingos?")
    check("20h" in r.get("resposta", "") or "20:00" in r.get("resposta", ""),
          "resposta traz 20h")
    ev = eventos(s1)
    chamadas = [p["functionCall"]["name"] for e in ev for p in (e.get("content") or {}).get("parts", [])
                if "functionCall" in p]
    print(f"  tools chamadas em S1: {sorted(set(chamadas))}")
    check(any(c not in ("transfer_to_agent", "adk_request_confirmation") for c in chamadas),
          "eventos incluem chamadas de tool")
    vazados = [t for t in trechos_outros_capitulos() if contem(ev, t)]
    check(not vazados, f"nenhum trecho de outros capítulos nos eventos ({vazados[:2]})")
    print(f"  eventos em S1: {len(ev)}")

    ESTADO.write_text(json.dumps({"s1": s1, "n_eventos": len(ev)}), encoding="utf-8")


def fase2() -> None:
    est = json.loads(ESTADO.read_text(encoding="utf-8"))
    s1 = est["s1"]

    passo(13, "após reinício")
    ev = eventos(s1)
    check(len(ev) == est["n_eventos"], f"mesma quantidade de eventos ({len(ev)} == {est['n_eventos']})")
    msg(s1, "Quais são as minhas reservas agora?")
    check(len(eventos(s1)) > est["n_eventos"], "quantidade de eventos aumentou")
    r101 = reservas("101")
    check(conta("101", "quadra", "2030-04-06") == 1, "101 tem a quadra em 2030-04-06")
    check(conta("101", "salao-de-festas", "2030-04-20") == 1, "101 tem o salão em 2030-04-20")
    check(not any(x["codigo"] == "RSV-1377" for x in r101), "101 não tem RSV-1377")
    check({"nome": "Joana Ribeiro", "data": "2030-04-21"} in visitantes("101"), "Joana autorizada")
    novos = [x["codigo"] for x in r101]
    check(len(set(novos)) == len(novos) and not set(novos) & {"RSV-1377", "RSV-4821", "RSV-2950"},
          f"códigos novos distintos entre si e dos iniciais ({novos})")
    check(any(x["codigo"] == "RSV-4821" for x in reservas("302")), "302 continua com RSV-4821")

    passo(14, "disputa: 101 e 201 pelo salão em 2030-05-11")
    s3 = req("POST", "/sessoes", {"apartamento": "101"})[1]["session_id"]
    s4 = req("POST", "/sessoes", {"apartamento": "201"})[1]["session_id"]
    p3 = msg_ate_pendencia(s3, "Reserve o salão de festas para 2030-05-11.").get("confirmacoes_pendentes") or []
    p4 = msg_ate_pendencia(s4, "Reserve o salão de festas para 2030-05-11.").get("confirmacoes_pendentes") or []
    check(len(p3) == 1 and len(p4) == 1, "as duas sessões com confirmação pendente")
    if p3 and p4:
        resultados: dict[str, int] = {}
        barreira = threading.Barrier(2)

        def aprovar(nome: str, sid: str, cid: str) -> None:
            barreira.wait()
            resultados[nome] = confirmar(sid, cid, True)[0]

        ts = [threading.Thread(target=aprovar, args=("S3", s3, p3[0]["id"])),
              threading.Thread(target=aprovar, args=("S4", s4, p4[0]["id"]))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        check(resultados == {"S3": 200, "S4": 200}, f"as duas aprovações 200 ({resultados})")
    total = conta("101", "salao-de-festas", "2030-05-11") + conta("201", "salao-de-festas", "2030-05-11")
    check(total == 1, f"exatamente 1 reserva do salão em 2030-05-11 somando 101 e 201 (veio {total})")


if __name__ == "__main__":
    fase = sys.argv[1] if len(sys.argv) > 1 else ""
    {"fase1": fase1, "fase2": fase2}[fase]()
    print(f"\n{'TUDO OK' if not falhas else f'{len(falhas)} FALHA(S)'}")
    sys.exit(1 if falhas else 0)
