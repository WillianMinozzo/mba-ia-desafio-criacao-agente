"""Sobe a API real trocando só o modelo por FakeLlm (testes sem Gemini)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_llm import FakeLlm, FakeRegulamento  # noqa: E402

from aurora import agentes  # noqa: E402

agentes.assistente.model = FakeLlm(model="fake", papel="assistente_aurora")
agentes.reservas.model = FakeLlm(model="fake", papel="reservas")
agentes.visitantes.model = FakeLlm(model="fake", papel="visitantes")
agentes.especialista_regulamento.model = FakeRegulamento(model="fake", papel="regulamento")

from aurora import api  # noqa: E402

if __name__ == "__main__":
    api.main()
