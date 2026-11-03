class ChatError(ValueError):
    """可向聊天接口公开的规范化业务错误。"""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
