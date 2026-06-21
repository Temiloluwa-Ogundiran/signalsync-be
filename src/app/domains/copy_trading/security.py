from cryptography.fernet import Fernet, InvalidToken


class SessionCipher:
    def __init__(self, key: str):
        if not key:
            raise ValueError("ENCRYPTION_KEY is required for Telegram sessions.")
        self._fernet = Fernet(key.encode("ascii"))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Encrypted Telegram session is invalid.") from exc
