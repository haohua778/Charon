class CharonError(RuntimeError):
    """A safe code across boundaries; never include provider messages or reports."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
