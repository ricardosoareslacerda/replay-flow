"""Mascara dados pessoais no pacote para o Claude (o material bruto da sessão não é alterado)."""

from __future__ import annotations

import re

# Ordem importa: CNPJ antes de CPF e cartão (os três são sequências de dígitos).
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("[e-mail]", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    # Linha digitável de boleto (47/48 dígitos em blocos): identifica pagador e conta.
    ("[linha digitável]", re.compile(r"(?<!\d)\d{5}\.?\d{5}\s+\d{5}\.?\d{6}\s+\d{5}\.?\d{6}\s+\d\s+\d{14}(?!\d)")),
    ("[CNPJ]", re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")),
    ("[CPF]", re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")),
    ("[CPF/telefone]", re.compile(r"(?<![\d_])\d{11}(?![\d_])")),  # 11 dígitos soltos: CPF ou celular com DDD
    ("[cartão]", re.compile(r"(?<!\d)(?:\d{4}[ -]?){3}\d{1,7}(?!\d)")),
)


def redact(text: str) -> tuple[str, int]:
    """Devolve (texto mascarado, quantidade de trechos mascarados)."""
    total = 0
    for label, pattern in _PATTERNS:
        text, n = pattern.subn(label, text)
        total += n
    return text, total
