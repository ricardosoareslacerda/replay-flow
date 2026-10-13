class ReplayFlowError(Exception):
    """Erro com mensagem pronta para a interface."""


class ToolMissingError(ReplayFlowError):
    pass


class CancelledError(ReplayFlowError):
    def __init__(self) -> None:
        super().__init__("Processamento cancelado pelo usuário.")
