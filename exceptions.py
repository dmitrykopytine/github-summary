class AppError(Exception):
    def __init__(
        self,
        message: str,
        http_code: int = 500,
    ):
        super().__init__(message)
        self.message = message
        self.http_code = http_code
