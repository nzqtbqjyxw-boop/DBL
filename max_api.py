"""
Тонкий клиент над официальным REST MAX Bot API.
Документация: https://dev.max.ru/docs-api

Без сторонних SDK — напрямую через aiohttp, чтобы не зависеть от
неофициальных обёрток и видеть точно, что уходит в запросе.
"""
import logging
import os

import aiohttp

log = logging.getLogger(__name__)

BASE_URL = "https://platform-api2.max.ru"
TOKEN = os.environ["MAX_BOT_TOKEN"]


class MaxAPI:
    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _params(self, extra: dict | None = None) -> dict:
        p = {"access_token": TOKEN}
        if extra:
            p.update(extra)
        return p

    # ---------- Апдейты (long polling) ----------

    async def get_updates(self, marker: int | None, timeout: int = 30) -> dict:
        session = await self._get_session()
        params = self._params({"timeout": timeout, "limit": 100})
        if marker is not None:
            params["marker"] = marker
        async with session.get(f"{BASE_URL}/updates", params=params, timeout=aiohttp.ClientTimeout(total=timeout + 15)) as resp:
            return await resp.json()

    # ---------- Сообщения ----------

    async def send_message(self, user_id: int | None = None, chat_id: int | None = None,
                            text: str = "", attachments: list | None = None,
                            format_: str | None = "html") -> dict:
        session = await self._get_session()
        params = self._params()
        if user_id is not None:
            params["user_id"] = user_id
        if chat_id is not None:
            params["chat_id"] = chat_id
        body = {"text": text}
        if format_:
            body["format"] = format_
        if attachments:
            body["attachments"] = attachments
        async with session.post(f"{BASE_URL}/messages", params=params, json=body) as resp:
            data = await resp.json()
            if resp.status >= 400:
                log.error("send_message error %s: %s", resp.status, data)
            return data

    async def edit_message(self, message_id: str, text: str, attachments: list | None = None) -> dict:
        session = await self._get_session()
        params = self._params({"message_id": message_id})
        body = {"text": text}
        if attachments:
            body["attachments"] = attachments
        async with session.put(f"{BASE_URL}/messages", params=params, json=body) as resp:
            return await resp.json()

    async def answer_callback(self, callback_id: str, text: str | None = None, notification: str | None = None) -> dict:
        session = await self._get_session()
        params = self._params({"callback_id": callback_id})
        body = {}
        if text:
            body["message"] = {"text": text}
        if notification:
            body["notification"] = notification
        async with session.post(f"{BASE_URL}/answers", params=params, json=body) as resp:
            return await resp.json()

    # ---------- Файлы (фото) ----------

    async def upload_image_url(self) -> str:
        """Запрашивает URL для загрузки изображения. Возвращает upload URL."""
        session = await self._get_session()
        params = self._params({"type": "image"})
        async with session.post(f"{BASE_URL}/uploads", params=params) as resp:
            data = await resp.json()
            return data["url"]

    async def upload_image_bytes(self, file_bytes: bytes, filename: str = "photo.jpg") -> dict:
        """Загружает байты изображения на сервер MAX и возвращает payload для attachments."""
        session = await self._get_session()
        upload_url = await self.upload_image_url()
        form = aiohttp.FormData()
        form.add_field("data", file_bytes, filename=filename, content_type="image/jpeg")
        async with session.post(upload_url, data=form) as resp:
            result = await resp.json()
            # Ответ вида {"photos": {"<id>": {"token": "..."}}}
            photos = result.get("photos", {})
            if not photos:
                raise RuntimeError(f"Не удалось загрузить изображение: {result}")
            first = next(iter(photos.values()))
            return {"type": "image", "payload": {"token": first["token"]}}

    async def download_file(self, url: str) -> bytes:
        session = await self._get_session()
        async with session.get(url) as resp:
            return await resp.read()
