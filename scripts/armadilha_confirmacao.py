"""Evidência da armadilha da confirmação (ADK 2.9.1), com o modelo falso.

Roda o mesmo pedido -> aprovação em 4 combinações de
(disallow_transfer_to_parent no especialista) x (App resumable) e mostra se a
reserva foi gravada. Usa bancos temporários; não toca em var/.

    uv run python scripts/armadilha_confirmacao.py
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent), str(Path(__file__).resolve().parent)]

from aurora import config  # noqa: E402

_tmp = Path(tempfile.mkdtemp())
config.DB_CONDOMINIO = _tmp / "condominio.db"
config.DB_SESSOES = _tmp / "sessoes.db"

import servidor_fake  # noqa: E402,F401  (troca os modelos pelo FakeLlm)
from google.adk.apps import App, ResumabilityConfig  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.genai import types  # noqa: E402

from aurora import agentes, api, store  # noqa: E402


async def caso(bloqueia: bool, resumable: bool, data: str) -> None:
    agentes.reservas.disallow_transfer_to_parent = bloqueia
    app = App(name=config.APP_NAME, root_agent=agentes.assistente,
              resumability_config=ResumabilityConfig(is_resumable=True) if resumable else None)
    api.runner = Runner(app=app, session_service=api.session_service)
    s = await api.session_service.create_session(
        app_name=config.APP_NAME, user_id=config.USER_ID, state={"apartamento": "102"})
    r = await api._executar(s.id, types.Content(role="user", parts=[
        types.Part(text=f"Reserve o salão de festas para {data}.")]))
    cid = r["confirmacoes_pendentes"][0]["id"]
    r = await api._executar(s.id, types.Content(role="user", parts=[types.Part(
        function_response=types.FunctionResponse(
            id=cid, name="adk_request_confirmation", response={"confirmed": True}))]))
    gravou = any(x["data"] == data for x in store.reservas_do_apartamento("102"))
    print(f"disallow_transfer_to_parent={bloqueia!s:5}  is_resumable={resumable!s:5}  "
          f"aprovação: pendentes={len(r['confirmacoes_pendentes'])}  gravou={gravou}")


async def main() -> None:
    store.inicializar()
    for i, (b, res) in enumerate([(False, True), (False, False), (True, True), (True, False)]):
        await caso(b, res, f"2040-01-{i + 1:02d}")


asyncio.run(main())
