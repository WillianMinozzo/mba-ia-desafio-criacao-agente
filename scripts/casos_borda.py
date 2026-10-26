"""Casos de borda além do fluxo do avaliador.

    uv run python scripts/casos_borda.py antes     # com a API no ar: cria pendências
    (reinicia a API)
    uv run python scripts/casos_borda.py depois    # aprova/nega pendências criadas antes do reinício
    uv run python scripts/casos_borda.py corrida   # sem API: 20 processos gravando a mesma área/data

Só biblioteca padrão + o próprio pacote `aurora` (no modo corrida).
"""

from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fluxo_avaliador import (ESTADO, check, confirmar, conta, falhas, msg,  # noqa: E402
                             passo, req, visitantes)

ARQ = ESTADO.with_name("casos_borda.json")


def antes() -> None:
    passo(1, "pendências criadas antes do reinício")
    s = req("POST", "/sessoes", {"apartamento": "202"})[1]["session_id"]
    p1 = msg(s, "Reserve a churrasqueira para 2031-01-10.")["confirmacoes_pendentes"]
    check(len(p1) == 1, "churrasqueira (taxa 80) gera pendência")
    p2 = msg(s, "Libera a entrada do Carlos Lima no dia 2031-01-11.")["confirmacoes_pendentes"]
    check(len(p2) == 2, "nova mensagem com pendência aberta: as duas aparecem")
    check(conta("202", "churrasqueira", "2031-01-10") == 0, "nada gravado ainda")

    passo(2, "outra sessão não responde pendência alheia")
    outra = req("POST", "/sessoes", {"apartamento": "202"})[1]["session_id"]
    st, _ = confirmar(outra, p1[0]["id"], True)
    check(st == 409, f"id de outra sessão → 409 (veio {st})")
    check(conta("202", "churrasqueira", "2031-01-10") == 0, "nada gravado")
    st, _ = req("POST", "/sessoes/sessao-inexistente/confirmacoes", {"id": "x", "confirmado": True})
    check(st == 404, f"confirmação em sessão inexistente → 404 (veio {st})")
    st, _ = req("POST", "/sessoes/sessao-inexistente/mensagens", {"texto": "oi"})
    check(st == 404, f"mensagem em sessão inexistente → 404 (veio {st})")

    ARQ.write_text(json.dumps({"s": s, "reserva": p1[0]["id"],
                               "visita": [p for p in p2 if p["acao"] == "autorizar_visitante"][0]["id"]}))


def depois() -> None:
    e = json.loads(ARQ.read_text())
    passo(3, "após reinício: pendências continuam e podem ser respondidas")
    _, ev = req("GET", f"/sessoes/{e['s']}/eventos")
    st, r = confirmar(e["s"], e["reserva"], True)
    check(st == 200, f"aprovar reserva pós-reinício 200 (veio {st})")
    check(conta("202", "churrasqueira", "2031-01-10") == 1, "reserva gravada uma vez")
    check([p["id"] for p in r["confirmacoes_pendentes"]] == [e["visita"]], "resta só a visita pendente")
    st, _ = confirmar(e["s"], e["visita"], False)
    check(st == 200, "negar visita 200")
    check(not any(v["nome"] == "Carlos Lima" for v in visitantes("202")), "negar não grava visita")
    st, _ = confirmar(e["s"], e["visita"], True)
    check(st == 409, f"aprovar depois de negar → 409 (veio {st})")
    check(not any(v["nome"] == "Carlos Lima" for v in visitantes("202")), "continua sem visita")


def _gravar(i: int) -> bool:
    from aurora import store
    return store.criar_reserva(str(100 + i), "churrasqueira", "2032-12-25") is not None


def corrida() -> None:
    from aurora import store
    store.inicializar()
    passo(4, "20 processos gravando churrasqueira 2032-12-25 ao mesmo tempo")
    with multiprocessing.Pool(20) as pool:
        resultados = pool.map(_gravar, range(20))
    check(sum(resultados) == 1, f"exatamente 1 gravação venceu ({sum(resultados)})")
    with store.conexao() as con:
        n = con.execute("SELECT COUNT(*) FROM reservas WHERE area='churrasqueira' "
                        "AND data='2032-12-25' AND ativa=1").fetchone()[0]
    check(n == 1, f"1 linha ativa no banco ({n})")


if __name__ == "__main__":
    {"antes": antes, "depois": depois, "corrida": corrida}[sys.argv[1]]()
    print(f"\n{'TUDO OK' if not falhas else f'{len(falhas)} FALHA(S)'}")
    sys.exit(1 if falhas else 0)
