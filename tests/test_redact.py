from replayflow.core.redact import redact


def test_masks_personal_data_but_keeps_dates_and_file_names() -> None:
    text = ("ricardo.x@gmail.com pagou 4111 1111 1111 1111 em 07-10-2026; CPF 123.456.789-09, "
            "CNPJ 12.345.678/0001-90, cel 11987654321, arquivo fatura_lopes_ricardo_202610.pdf às 05:49")
    out, n = redact(text)

    assert out == ("[e-mail] pagou [cartão] em 07-10-2026; CPF [CPF], CNPJ [CNPJ], cel [CPF/telefone], "
                   "arquivo fatura_lopes_ricardo_202610.pdf às 05:49")
    assert n == 5
