import logging
from pathlib import Path

import requests

log = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.api_base = f"https://api.telegram.org/bot{token}"
        self.file_base = f"https://api.telegram.org/file/bot{token}"

    def _post(self, method: str, **kwargs):
        response = requests.post(f"{self.api_base}/{method}", timeout=60, **kwargs)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data}")
        return data["result"]

    def _get(self, method: str, **kwargs):
        response = requests.get(f"{self.api_base}/{method}", timeout=60, **kwargs)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data}")
        return data["result"]

    def send_message(self, chat_id: int | str, text: str):
        return self._post("sendMessage", data={"chat_id": chat_id, "text": text})

    def edit_message_text(self, chat_id: int | str, message_id: int, text: str):
        return self._post(
            "editMessageText",
            data={"chat_id": chat_id, "message_id": message_id, "text": text},
        )

    def send_video(self, chat_id: int | str, video_path: str, caption: str = ""):
        with open(video_path, "rb") as video:
            return self._post(
                "sendVideo",
                data={"chat_id": chat_id, "caption": caption},
                files={"video": (Path(video_path).name, video, "video/mp4")},
            )

    def send_document(self, chat_id: int | str, document_path: str, caption: str = ""):
        with open(document_path, "rb") as document:
            return self._post(
                "sendDocument",
                data={"chat_id": chat_id, "caption": caption},
                files={"document": (Path(document_path).name, document, "video/mp4")},
            )

    def get_file_path(self, file_id: str) -> str:
        result = self._get("getFile", params={"file_id": file_id})
        return result["file_path"]

    def download_file(self, file_id: str, destination: str):
        file_path = self.get_file_path(file_id)
        url = f"{self.file_base}/{file_path}"
        with requests.get(url, stream=True, timeout=300) as response:
            response.raise_for_status()
            with open(destination, "wb") as out:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        out.write(chunk)
        log.info("downloaded Telegram file_id=%s to %s", file_id, destination)
