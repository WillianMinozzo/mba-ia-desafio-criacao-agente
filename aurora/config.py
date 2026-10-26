"""Caminhos e configuração lidos do ambiente (.env)."""

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

DADOS = RAIZ / "dados"
VAR = RAIZ / "var"
VAR.mkdir(exist_ok=True)

# Estado do condomínio (reservas e visitantes) e sessões do ADK em bancos separados:
# a gravação de eventos de uma sessão não disputa o lock da gravação de reservas.
DB_CONDOMINIO = VAR / "condominio.db"
DB_SESSOES = VAR / "sessoes.db"

APP_NAME = "residencial_aurora"
USER_ID = "morador"

# Vazio no .env = usa o padrão. gemini-3.1-flash-lite: o fluxo completo passou com ele, e
# no nível gratuito o gemini-3.5-flash (padrão do ADK 2.9.1) tem só 20 requisições/dia.
MODELO_PRINCIPAL = os.getenv("MODELO_PRINCIPAL") or "gemini-3.1-flash-lite"
MODELO_ESPECIALISTA = os.getenv("MODELO_ESPECIALISTA") or MODELO_PRINCIPAL
