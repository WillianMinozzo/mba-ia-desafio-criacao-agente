"""Regulamento dividido por capítulo (Garantia 4).

O texto nunca é carregado inteiro em lugar nenhum: o especialista recebe só o
índice (títulos) e lê, por tool, apenas o capítulo do assunto perguntado.
"""

from __future__ import annotations

import re

from . import config

_TEXTO = (config.DADOS / "regulamento.md").read_text(encoding="utf-8")
_PARTES = re.split(r"(?m)^## ", _TEXTO)[1:]

CAPITULOS: list[dict] = []
for _i, _parte in enumerate(_PARTES, start=1):
    _titulo, _, _corpo = _parte.partition("\n")
    CAPITULOS.append({"numero": _i, "titulo": _titulo.strip(), "texto": _corpo.strip()})


def indice() -> str:
    return "\n".join(f"{c['numero']}. {c['titulo']}" for c in CAPITULOS)


def capitulo(numero: int) -> dict | None:
    try:
        n = int(numero)
    except (TypeError, ValueError):
        return None
    if 1 <= n <= len(CAPITULOS):
        return dict(CAPITULOS[n - 1])
    return None
