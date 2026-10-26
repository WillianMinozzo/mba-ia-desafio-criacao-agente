"""API HTTP do assistente do Residencial Aurora (contrato do enunciado)."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from google.adk.apps import App, ResumabilityConfig
from google.adk.flows.llm_flows.functions import REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
from google.adk.runners import Runner
from google.adk.sessions import Session
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types
from pydantic import BaseModel

from . import config, store
from .agentes import assistente
from .tools import CHAVE_APARTAMENTO

# Sessões e eventos persistidos em SQLite (Garantia 3), num arquivo separado do
# estado do condomínio.
session_service = SqliteSessionService(str(config.DB_SESSOES))

# is_resumable=True faz o Runner entregar a resposta de uma confirmação ao agente
# que a pediu (autor da function call), mesmo depois de reiniciar a API.
adk_app = App(
    name=config.APP_NAME,
    root_agent=assistente,
    resumability_config=ResumabilityConfig(is_resumable=True),
)
runner = Runner(app=adk_app, session_service=session_service)

# Uma execução por sessão de cada vez: duas chamadas na mesma sessão não se cruzam.
_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.inicializar()
    yield
    await runner.close()


app = FastAPI(title="Residencial Aurora", lifespan=lifespan)


class NovaSessao(BaseModel):
    apartamento: str


class Mensagem(BaseModel):
    texto: str


class RespostaConfirmacao(BaseModel):
    id: str
    confirmado: bool


# ------------------------------ helpers ------------------------------

async def _sessao(session_id: str) -> Session:
    sessao = await session_service.get_session(
        app_name=config.APP_NAME, user_id=config.USER_ID, session_id=session_id
    )
    if sessao is None:
        raise HTTPException(status_code=404, detail="sessão não encontrada")
    return sessao


def confirmacoes_pendentes(sessao: Session) -> list[dict]:
    """Pedidos de confirmação gravados na sessão que ainda não têm resposta.

    Derivado só dos eventos persistidos: sobrevive ao reinício e é a fonte do 409.
    """
    respondidas = {
        fr.id for ev in sessao.events for fr in ev.get_function_responses() if fr.id
    }
    pendentes = []
    for ev in sessao.events:
        for fc in ev.get_function_calls():
            if fc.name != REQUEST_CONFIRMATION_FUNCTION_CALL_NAME or fc.id in respondidas:
                continue
            args = fc.args or {}
            original = args.get("originalFunctionCall") or {}
            confirmacao = args.get("toolConfirmation") or {}
            pendentes.append({
                "id": fc.id,
                "acao": original.get("name", ""),
                "detalhes": confirmacao.get("payload") or original.get("args") or {},
            })
    return pendentes


async def _executar(session_id: str, mensagem: types.Content) -> dict:
    textos: list[str] = []
    async for evento in runner.run_async(
        user_id=config.USER_ID, session_id=session_id, new_message=mensagem
    ):
        if evento.author == "user" or evento.partial or not evento.content:
            continue
        texto = "".join(
            p.text for p in evento.content.parts or [] if p.text and not p.thought
        ).strip()
        if texto:
            textos.append(texto)
    sessao = await _sessao(session_id)
    return {
        "resposta": textos[-1] if textos else "",
        "confirmacoes_pendentes": confirmacoes_pendentes(sessao),
    }


# ------------------------------ conversa ------------------------------

@app.post("/sessoes", status_code=201)
async def criar_sessao(corpo: NovaSessao):
    if corpo.apartamento not in store.APARTAMENTOS:
        raise HTTPException(status_code=422, detail="apartamento inexistente")
    # Garantia 2: o apartamento entra no state uma única vez, aqui.
    sessao = await session_service.create_session(
        app_name=config.APP_NAME,
        user_id=config.USER_ID,
        state={CHAVE_APARTAMENTO: corpo.apartamento},
    )
    return {"session_id": sessao.id}


@app.post("/sessoes/{session_id}/mensagens")
async def enviar_mensagem(session_id: str, corpo: Mensagem):
    async with _locks[session_id]:
        await _sessao(session_id)
        mensagem = types.Content(role="user", parts=[types.Part(text=corpo.texto)])
        return await _executar(session_id, mensagem)


@app.post("/sessoes/{session_id}/confirmacoes")
async def responder_confirmacao(session_id: str, corpo: RespostaConfirmacao):
    async with _locks[session_id]:
        sessao = await _sessao(session_id)
        # Garantia 1: só aceita um id pendente nesta sessão; respondido/inexistente → 409.
        if corpo.id not in {p["id"] for p in confirmacoes_pendentes(sessao)}:
            return JSONResponse(
                status_code=409,
                content={"detail": "não existe confirmação pendente com esse id nesta sessão"},
            )
        resposta = types.Content(role="user", parts=[types.Part(
            function_response=types.FunctionResponse(
                id=corpo.id,
                name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
                response={"confirmed": corpo.confirmado},
            )
        )])
        return await _executar(session_id, resposta)


@app.get("/sessoes/{session_id}/eventos")
async def listar_eventos(session_id: str):
    sessao = await _sessao(session_id)
    return [ev.model_dump(mode="json", exclude_none=True, by_alias=True) for ev in sessao.events]


# ------------------------------ verificação ------------------------------

@app.get("/apartamentos/{numero}/reservas")
async def reservas_do_apartamento(numero: str):
    return store.reservas_do_apartamento(numero)


@app.get("/apartamentos/{numero}/visitantes")
async def visitantes_do_apartamento(numero: str):
    return store.visitantes_do_apartamento(numero)


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
