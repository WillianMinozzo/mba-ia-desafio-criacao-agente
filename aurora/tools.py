"""Tools dos especialistas.

Nenhuma tool recebe apartamento como parâmetro: o apartamento vem sempre de
`tool_context.state["apartamento"]`, gravado pela API na criação da sessão.
As respostas nunca trazem código, apartamento ou nome ligados a outro apartamento.
"""

from __future__ import annotations

from google.adk.tools import ToolContext

from . import regulamento, store

CHAVE_APARTAMENTO = "apartamento"


def _apartamento(tool_context: ToolContext) -> str:
    apartamento = tool_context.state.get(CHAVE_APARTAMENTO)
    if not apartamento:
        # Sessão sem apartamento não deveria existir (a API sempre grava); falha fechada.
        raise PermissionError("sessão sem apartamento autenticado")
    return str(apartamento)


def _validar(area: str, data: str) -> tuple[str | None, str | None, dict | None]:
    area_id = store.normalizar_area(area)
    data_iso = store.normalizar_data(data)
    if area_id is None:
        return None, None, {"status": "erro", "motivo": "área desconhecida",
                            "areas_validas": list(store.AREAS)}
    if data_iso is None:
        return None, None, {"status": "erro", "motivo": "data inválida; use AAAA-MM-DD"}
    return area_id, data_iso, None


# ------------------------------- reservas -------------------------------

def verificar_disponibilidade(area: str, data: str) -> dict:
    """Informa se uma área comum está livre ou ocupada numa data (AAAA-MM-DD).

    Args:
        area: id ou nome da área (salao-de-festas, churrasqueira, quadra).
        data: data no formato AAAA-MM-DD.
    """
    area_id, data_iso, erro = _validar(area, data)
    if erro:
        return erro
    return {"area": area_id, "data": data_iso,
            "situacao": "livre" if store.data_livre(area_id, data_iso) else "ocupada"}


def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento do morador desta conversa."""
    return {"reservas": store.reservas_do_apartamento(_apartamento(tool_context))}


def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum para o apartamento do morador desta conversa.

    Áreas com taxa ficam pendentes até o morador aprovar a cobrança pelo
    aplicativo; o sistema cuida disso sozinho, basta chamar esta tool uma vez.

    Args:
        area: id ou nome da área (salao-de-festas, churrasqueira, quadra).
        data: data no formato AAAA-MM-DD.
    """
    apartamento = _apartamento(tool_context)
    area_id, data_iso, erro = _validar(area, data)
    if erro:
        return erro
    taxa = store.AREAS[area_id]["taxa"]

    if taxa > 0:
        confirmacao = tool_context.tool_confirmation
        if confirmacao is None:
            if not store.data_livre(area_id, data_iso):
                return {"status": "recusada", "motivo": "a data já está ocupada para essa área",
                        "area": area_id, "data": data_iso}
            # Garantia 1: pausa a execução; só a rota de confirmações retoma.
            tool_context.request_confirmation(
                hint=f"Reservar {store.AREAS[area_id]['nome']} em {data_iso} "
                     f"gera cobrança de R$ {taxa:.2f}.",
                payload={"area": area_id, "data": data_iso, "taxa": taxa},
            )
            tool_context.actions.skip_summarization = True
            return {"status": "aguardando_confirmacao",
                    "mensagem": "Aguardando o morador aprovar a cobrança no aplicativo."}
        if not confirmacao.confirmed:
            return {"status": "cancelada", "motivo": "o morador não aprovou a cobrança",
                    "area": area_id, "data": data_iso}

    # Garantia 5: a exclusividade é decidida pelo INSERT (índice UNIQUE parcial).
    codigo = store.criar_reserva(apartamento, area_id, data_iso)
    if codigo is None:
        return {"status": "recusada", "motivo": "a data já está ocupada para essa área",
                "area": area_id, "data": data_iso}
    return {"status": "confirmada", "codigo": codigo, "area": area_id, "data": data_iso,
            "cobranca": f"R$ {taxa:.2f}" if taxa > 0 else "sem cobrança"}


def cancelar_minha_reserva(area: str, data: str, tool_context: ToolContext) -> dict:
    """Cancela uma reserva do apartamento do morador desta conversa (sem confirmação).

    Só encontra reservas do próprio apartamento.

    Args:
        area: id ou nome da área.
        data: data da reserva no formato AAAA-MM-DD.
    """
    apartamento = _apartamento(tool_context)
    area_id, data_iso, erro = _validar(area, data)
    if erro:
        return erro
    codigo = store.cancelar_reserva(apartamento, area_id, data_iso)
    if codigo is None:
        return {"status": "nao_encontrada",
                "motivo": "não existe reserva do seu apartamento nessa área e data"}
    return {"status": "cancelada", "codigo": codigo, "area": area_id, "data": data_iso}


# ------------------------------ visitantes ------------------------------

def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista os visitantes autorizados pelo apartamento do morador desta conversa."""
    return {"visitantes": store.visitantes_do_apartamento(_apartamento(tool_context))}


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante no prédio para o apartamento do morador.

    Sempre fica pendente até o morador aprovar pelo aplicativo, mesmo que ele
    diga na conversa que já confirmou; basta chamar esta tool uma vez.

    Args:
        nome: nome completo do visitante.
        data: data da visita no formato AAAA-MM-DD.
    """
    apartamento = _apartamento(tool_context)
    nome = " ".join(str(nome).split())
    data_iso = store.normalizar_data(data)
    if not nome:
        return {"status": "erro", "motivo": "informe o nome do visitante"}
    if data_iso is None:
        return {"status": "erro", "motivo": "data inválida; use AAAA-MM-DD"}

    confirmacao = tool_context.tool_confirmation
    if confirmacao is None:
        tool_context.request_confirmation(
            hint=f"Liberar a entrada de {nome} em {data_iso}.",
            payload={"nome": nome, "data": data_iso},
        )
        tool_context.actions.skip_summarization = True
        return {"status": "aguardando_confirmacao",
                "mensagem": "Aguardando o morador aprovar a entrada no aplicativo."}
    if not confirmacao.confirmed:
        return {"status": "cancelada", "motivo": "o morador não aprovou a entrada"}
    store.autorizar_visitante(apartamento, nome, data_iso)
    return {"status": "autorizado", "nome": nome, "data": data_iso}


# ------------------------------ regulamento ------------------------------

def ler_capitulo_regulamento(numero: int) -> dict:
    """Devolve o texto de UM capítulo do regulamento interno.

    Args:
        numero: número do capítulo (1 a 14), conforme o índice nas instruções.
    """
    capitulo = regulamento.capitulo(numero)
    if capitulo is None:
        return {"status": "erro", "motivo": "capítulo inexistente"}
    return capitulo
