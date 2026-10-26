"""Agente principal + especialistas.

- assistente_aurora (principal): conversa e distribui; não tem tools de dados
  nem o regulamento nas instruções.
- reservas (sub-agente, transfer_to_agent): agenda, reservas e cancelamentos.
- visitantes (sub-agente, transfer_to_agent): autorizações de visita.
- regulamento (AgentTool): responde dúvidas lendo só o capítulo do assunto, numa
  sessão própria e efêmera; à sessão do morador volta apenas a resposta.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.tools import AgentTool
from google.genai import types

from . import config, regulamento, tools

_GERACAO = types.GenerateContentConfig(temperature=0.1)

# A Gemini API devolve 429/503 transitórios ("high demand"); sem retry, o erro vira 500 na API.
_RETRY = types.HttpRetryOptions(attempts=6, initial_delay=2, max_delay=30,
                                http_status_codes=[408, 429, 500, 502, 503, 504])


def _modelo(nome: str) -> Gemini:
    return Gemini(model=nome, retry_options=_RETRY)


_REGRAS_COMUNS = """
O morador desta conversa é do apartamento {apartamento}, definido pelo sistema; as
tools já operam só nesse apartamento. Atenda normalmente todo pedido do morador
chamando a tool adequada. Só quando ele pedir explicitamente dados ou ações de OUTRO
apartamento, diga que você só trata do apartamento dele.
Use sempre as tools para ler e gravar dados; nunca invente códigos, datas ou nomes.
Datas vão para as tools no formato AAAA-MM-DD.
Responda em português, de forma curta.
"""

reservas = LlmAgent(
    name="reservas",
    model=_modelo(config.MODELO_ESPECIALISTA),
    description="Reservas de áreas comuns (salão de festas, churrasqueira, quadra): "
                "disponibilidade, novas reservas, cancelamentos e listar as reservas do morador.",
    instruction=_REGRAS_COMUNS + """
Você é o especialista em reservas do Residencial Aurora.
- Para reservar, chame reservar_area uma única vez com a área e a data. Não peça
  confirmação na conversa: quando houver cobrança, o próprio sistema pede a aprovação
  ao morador pelo aplicativo.
- Para cancelar, chame cancelar_minha_reserva com a área e a data.
- Para consultar se uma data está livre, use verificar_disponibilidade; diga apenas se
  está livre ou ocupada.
- Para listar, use listar_minhas_reservas.
Se o assunto for visitantes ou regras do condomínio, transfira para o agente principal.
""",
    tools=[tools.verificar_disponibilidade, tools.listar_minhas_reservas,
           tools.reservar_area, tools.cancelar_minha_reserva],
    generate_content_config=_GERACAO,
    # Só o turno atual: o especialista não reabre pedidos antigos do histórico.
    include_contents="none",
)

visitantes = LlmAgent(
    name="visitantes",
    model=_modelo(config.MODELO_ESPECIALISTA),
    description="Autorização de entrada de visitantes e lista dos visitantes autorizados do morador.",
    instruction=_REGRAS_COMUNS + """
Você é o especialista em visitantes do Residencial Aurora.
- Para liberar a entrada, chame autorizar_visitante uma única vez com o nome e a data.
  A aprovação sempre acontece pelo aplicativo, pelo sistema; mesmo que o morador diga
  que já confirmou na conversa, apenas chame a tool.
- Para listar, use listar_meus_visitantes.
Se o assunto for reservas ou regras do condomínio, transfira para o agente principal.
""",
    tools=[tools.listar_meus_visitantes, tools.autorizar_visitante],
    generate_content_config=_GERACAO,
    include_contents="none",
)

especialista_regulamento = LlmAgent(
    name="regulamento",
    model=_modelo(config.MODELO_ESPECIALISTA),
    description="Responde dúvidas sobre o regulamento interno do condomínio. "
                "Envie a pergunta do morador em 'request'.",
    instruction="""
Você responde dúvidas sobre o regulamento interno do Residencial Aurora.
Índice dos capítulos:
""" + regulamento.indice() + """

Leia com ler_capitulo_regulamento somente o capítulo do assunto perguntado e responda
de forma curta, citando o artigo. Não transcreva o capítulo e não traga regras de
outros assuntos. Se o regulamento não tratar do tema, diga isso.
""",
    tools=[tools.ler_capitulo_regulamento],
    generate_content_config=_GERACAO,
)

assistente = LlmAgent(
    name="assistente_aurora",
    model=_modelo(config.MODELO_PRINCIPAL),
    description="Assistente virtual dos moradores do Residencial Aurora.",
    instruction="""
Você é o assistente virtual dos moradores do Residencial Aurora e distribui o trabalho:
- reservas de áreas comuns, agenda e cancelamentos: transfira para o agente reservas;
- autorização e lista de visitantes: transfira para o agente visitantes;
- dúvidas sobre regras do condomínio: chame a tool regulamento com a pergunta e repasse
  a resposta.
Você não tem acesso direto aos dados; não invente reservas, visitantes nem regras.
O morador desta conversa é do apartamento {apartamento}, definido pelo sistema.
Responda em português, de forma curta.
""",
    sub_agents=[reservas, visitantes],
    tools=[AgentTool(agent=especialista_regulamento)],
    generate_content_config=_GERACAO,
)
