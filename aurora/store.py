"""Estado do condomínio em SQLite.

Regras críticas que valem no instante da gravação (não dependem do modelo):
- índice UNIQUE parcial (area, data) WHERE ativa = 1: no máximo uma reserva ativa
  por área e data, mesmo com duas gravações simultâneas;
- tabela `codigos` (nunca apagada, nem pela restauração): um código de reserva
  jamais se repete, inclusive o de reservas canceladas;
- toda consulta e escrita recebe o apartamento da sessão como parâmetro do código,
  nunca do modelo.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import date

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS reservas (
    codigo      TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL,
    area        TEXT NOT NULL,
    data        TEXT NOT NULL,
    ativa       INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS uma_reserva_ativa_por_area_e_data
    ON reservas (area, data) WHERE ativa = 1;
CREATE TABLE IF NOT EXISTS visitantes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento TEXT NOT NULL,
    nome        TEXT NOT NULL,
    data        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS codigos (
    codigo TEXT PRIMARY KEY
);
"""


def _ler_json(nome: str):
    return json.loads((config.DADOS / nome).read_text(encoding="utf-8"))


APARTAMENTOS = {a["numero"]: a for a in _ler_json("apartamentos.json")}
AREAS = {a["id"]: a for a in _ler_json("areas.json")}


@contextmanager
def conexao():
    con = sqlite3.connect(config.DB_CONDOMINIO, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    try:
        yield con
    finally:
        con.close()


def inicializar() -> None:
    """Cria o schema; se o banco está vazio (primeira subida), carrega dados/."""
    with conexao() as con:
        con.executescript(SCHEMA)
        vazio = con.execute("SELECT COUNT(*) FROM codigos").fetchone()[0] == 0
    if vazio:
        restaurar()


def restaurar() -> None:
    """Volta reservas e visitantes ao estado de dados/. Não apaga sessões nem `codigos`."""
    reservas = _ler_json("reservas.json")
    visitantes = _ler_json("visitantes.json")
    with conexao() as con:
        con.executescript(SCHEMA)
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM reservas")
        con.execute("DELETE FROM visitantes")
        for r in reservas:
            con.execute(
                "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (?, ?, ?, ?)",
                (r["codigo"], r["apartamento"], r["area"], r["data"]),
            )
            con.execute("INSERT OR IGNORE INTO codigos (codigo) VALUES (?)", (r["codigo"],))
        for v in visitantes:
            con.execute(
                "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
                (v["apartamento"], v["nome"], v["data"]),
            )
        con.execute("COMMIT")


# ---------- normalização determinística das entradas vindas do modelo ----------

def _sem_acento(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c)).lower().strip()


def normalizar_area(area: str) -> str | None:
    """Aceita o id ou o nome da área (sem diferenciar acento/maiúsculas)."""
    chave = _sem_acento(str(area)).replace(" ", "-")
    if not chave:
        return None
    for id_, a in AREAS.items():
        nome = _sem_acento(a["nome"]).replace(" ", "-")
        if chave in (id_, nome) or nome.startswith(chave) or chave.startswith(id_):
            return id_
    return None


def normalizar_data(data: str) -> str | None:
    try:
        return date.fromisoformat(str(data).strip()).isoformat()
    except ValueError:
        return None


def area_tem_taxa(area: str) -> bool:
    id_ = normalizar_area(area)
    return bool(id_ and AREAS[id_]["taxa"] > 0)


# ---------- leitura ----------

def reservas_do_apartamento(apartamento: str) -> list[dict]:
    with conexao() as con:
        linhas = con.execute(
            "SELECT codigo, area, data FROM reservas WHERE apartamento = ? AND ativa = 1"
            " ORDER BY data, codigo",
            (apartamento,),
        ).fetchall()
    return [dict(l) for l in linhas]


def visitantes_do_apartamento(apartamento: str) -> list[dict]:
    with conexao() as con:
        linhas = con.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, id",
            (apartamento,),
        ).fetchall()
    return [dict(l) for l in linhas]


def data_livre(area: str, data: str) -> bool:
    """Só diz se está livre; nunca devolve de quem é a reserva."""
    with conexao() as con:
        return con.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND ativa = 1", (area, data)
        ).fetchone() is None


# ---------- escrita ----------

def criar_reserva(apartamento: str, area: str, data: str) -> str | None:
    """Grava a reserva e devolve o código, ou None se a data já está ocupada.

    A exclusividade é garantida pelo índice UNIQUE parcial no próprio INSERT:
    se outra transação gravou antes, o INSERT falha com IntegrityError.
    """
    with conexao() as con:
        while True:
            codigo = f"RSV-{secrets.token_hex(4).upper()}"
            try:
                con.execute("BEGIN IMMEDIATE")
                con.execute("INSERT INTO codigos (codigo) VALUES (?)", (codigo,))
            except sqlite3.IntegrityError:
                con.execute("ROLLBACK")
                continue  # código já emitido algum dia: sorteia outro
            try:
                con.execute(
                    "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (?, ?, ?, ?)",
                    (codigo, apartamento, area, data),
                )
            except sqlite3.IntegrityError:
                con.execute("ROLLBACK")
                return None  # (area, data) já tem reserva ativa
            con.execute("COMMIT")
            return codigo


def cancelar_reserva(apartamento: str, area: str, data: str) -> str | None:
    """Cancela (soft delete) a reserva ativa do PRÓPRIO apartamento. Devolve o código ou None."""
    with conexao() as con:
        con.execute("BEGIN IMMEDIATE")
        linha = con.execute(
            "SELECT codigo FROM reservas WHERE apartamento = ? AND area = ? AND data = ? AND ativa = 1",
            (apartamento, area, data),
        ).fetchone()
        if linha is None:
            con.execute("ROLLBACK")
            return None
        con.execute("UPDATE reservas SET ativa = 0 WHERE codigo = ?", (linha["codigo"],))
        con.execute("COMMIT")
        return linha["codigo"]


def autorizar_visitante(apartamento: str, nome: str, data: str) -> None:
    with conexao() as con:
        con.execute(
            "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
            (apartamento, nome, data),
        )
