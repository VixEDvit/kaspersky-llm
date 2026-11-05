import logging


class ChatAccessFilter(logging.Filter):
    def filter(self, record):
        # Uvicorn передаёт адрес клиента, метод, путь, версию HTTP и статус.
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return False

        _, method, path, _, status = args
        if method != "POST" or path.split("?", 1)[0] not in ("/v1/chat/completions", "/v1/completions"):
            return False

        record.msg = "completion_request status=%s" if path.split("?", 1)[0] == "/v1/completions" else "chat_request status=%s"
        record.args = (status,)
        return True
