"""Modelo falso e determinístico para testar a infraestrutura sem chamar o Gemini.

Uso: `uv run python scripts/servidor_fake.py` (sobe a mesma API com este modelo).
Ele só escolhe tools/transferências por palavras-chave; tudo o mais (sessão,
confirmação, retomada, persistência, concorrência) é o código real da entrega.
"""

from __future__ import annotations

import json
import re
from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

DATA = re.compile(r"\d{4}-\d{2}-\d{2}")


def _area(t: str) -> str | None:
    t = t.lower()
    if "salão" in t or "salao" in t:
        return "salao-de-festas"
    if "churrasqueira" in t:
        return "churrasqueira"
    if "quadra" in t:
        return "quadra"
    return None


def _fc(name: str, **args) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(name=name, args=args))]))


def _txt(t: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=t)]))


class FakeLlm(BaseLlm):
    papel: str

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield self._responder(llm_request)

    def _responder(self, req: LlmRequest) -> LlmResponse:
        contents = req.contents or []
        ultimo = contents[-1] if contents else None
        frs = [p.function_response for p in (ultimo.parts or [])
               if ultimo and p.function_response]
        if frs and frs[-1].name != "transfer_to_agent":
            return _txt(f"[{self.papel}] " + json.dumps(frs[-1].response, ensure_ascii=False))

        texto = ""
        for c in reversed(contents):
            partes = c.parts or []
            if c.role != "user" or not partes or (partes[0].text or "").startswith("For context"):
                continue
            ts = [p.text for p in partes if p.text]
            if ts:
                texto = ts[-1]
                break
        t = texto.lower()
        datas = DATA.findall(texto)
        data = datas[0] if datas else ""

        visit = any(k in t for k in ("visitante", "libera", "entrada"))
        reserva = any(k in t for k in ("reserv", "cancel", "agenda"))

        if self.papel == "assistente_aurora":
            if visit:
                return _fc("transfer_to_agent", agent_name="visitantes")
            if reserva:
                return _fc("transfer_to_agent", agent_name="reservas")
            return _fc("regulamento", request=texto)

        if self.papel == "reservas":
            if visit:
                return _fc("transfer_to_agent", agent_name="assistente_aurora")
            area = _area(t)
            if "cancel" in t and area and data:
                return _fc("cancelar_minha_reserva", area=area, data=data)
            if "reserv" in t and area and data and "quais" not in t:
                return _fc("reservar_area", area=area, data=data)
            if reserva:
                return _fc("listar_minhas_reservas")
            return _fc("transfer_to_agent", agent_name="assistente_aurora")

        if self.papel == "visitantes":
            if not visit:
                return _fc("transfer_to_agent", agent_name="assistente_aurora")
            m = re.search(r"d[ao] ([A-ZÁ-Ú][\wà-ú]+(?: [A-ZÁ-Ú][\wà-ú]+)+)", texto)
            if m and data:
                return _fc("autorizar_visitante", nome=m.group(1), data=data)
            return _fc("listar_meus_visitantes")

        if self.papel == "regulamento":
            if frs:
                pass
            return _fc("ler_capitulo_regulamento", numero=4)
        return _txt("ok")


class FakeRegulamento(FakeLlm):
    def _responder(self, req: LlmRequest) -> LlmResponse:
        ultimo = (req.contents or [])[-1]
        for p in ultimo.parts or []:
            if p.function_response and p.function_response.name == "ler_capitulo_regulamento":
                texto = p.function_response.response.get("texto", "")
                linha = next((l for l in texto.splitlines() if "domingo" in l.lower()), "")
                return _txt("Conforme o Art. 22: " + linha.strip())
        return _fc("ler_capitulo_regulamento", numero=4)
