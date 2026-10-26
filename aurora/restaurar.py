"""Restaura reservas e visitantes ao estado de dados/ (não apaga sessões)."""

from . import store


def main() -> None:
    store.restaurar()
    print("Dados restaurados a partir de dados/: reservas e visitantes no estado inicial.")


if __name__ == "__main__":
    main()
