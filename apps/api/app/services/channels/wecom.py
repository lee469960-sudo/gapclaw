"""WeCom (企业微信) app callback adapter."""

from __future__ import annotations

import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from base64 import b64decode

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.services.channels.base import ChannelAdapter, InboundMessage, WebhookResult, register_adapter

logger = logging.getLogger(__name__)


def _pkcs7_unpad(data: bytes) -> bytes:
    pad = data[-1]
    if pad < 1 or pad > 32:
        return data
    return data[:-pad]


def _pkcs7_pad(data: bytes, block=32) -> bytes:
    amount = block - (len(data) % block)
    return data + bytes([amount] * amount)


@register_adapter
class WeComAdapter(ChannelAdapter):
    provider = "wecom"

    def _aes_key(self) -> bytes:
        key = self.config.get("encoding_aes_key") or ""
        return b64decode(key + "=")

    def _decrypt(self, encrypt: str) -> str:
        aes_key = self._aes_key()
        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16]))
        decryptor = cipher.decryptor()
        plain = _pkcs7_unpad(decryptor.update(b64decode(encrypt)) + decryptor.finalize())
        # random(16) + msg_len(4) + msg + receiveid
        msg_len = int.from_bytes(plain[16:20], "big")
        return plain[20:20 + msg_len].decode()

    def _verify_signature(self, token: str, timestamp: str, nonce: str, encrypt: str, signature: str) -> bool:
        items = sorted([token, timestamp, nonce, encrypt])
        digest = hashlib.sha1("".join(items).encode()).hexdigest()
        return digest == signature

    async def handle_webhook(self, *, method: str, headers: dict[str, str], query: dict[str, str], body: bytes) -> WebhookResult:
        token = self.config.get("token") or ""
        msg_sig = query.get("msg_signature") or query.get("signature") or ""
        timestamp = query.get("timestamp") or ""
        nonce = query.get("nonce") or ""
        echostr = query.get("echostr")

        # URL verification (GET)
        if method.upper() == "GET" and echostr:
            if token and not self._verify_signature(token, timestamp, nonce, echostr, msg_sig):
                return WebhookResult(status_code=403, body="invalid signature", content_type="text/plain", skip_agent=True)
            try:
                plain = self._decrypt(echostr)
            except Exception:
                plain = echostr
            return WebhookResult(body=plain, content_type="text/plain", skip_agent=True)

        # Message callback (POST XML)
        try:
            root = ET.fromstring(body.decode() or "<xml></xml>")
        except Exception:
            return WebhookResult(status_code=400, body="bad xml", content_type="text/plain", skip_agent=True)

        encrypt_node = root.find("Encrypt")
        encrypt = encrypt_node.text if encrypt_node is not None else ""
        if encrypt and token:
            if not self._verify_signature(token, timestamp, nonce, encrypt, msg_sig):
                return WebhookResult(status_code=403, body="invalid signature", content_type="text/plain", skip_agent=True)
            try:
                xml_plain = self._decrypt(encrypt)
                root = ET.fromstring(xml_plain)
            except Exception as e:
                logger.warning("wecom decrypt failed: %s", e)
                return WebhookResult(status_code=400, body="decrypt fail", content_type="text/plain", skip_agent=True)

        def _t(tag: str) -> str:
            n = root.find(tag)
            return (n.text or "") if n is not None else ""

        msg_type = _t("MsgType")
        if msg_type != "text":
            return WebhookResult(body="success", content_type="text/plain", skip_agent=True)

        text = _t("Content").strip()
        user_id = _t("FromUserName")
        chat_id = user_id  # app message to user
        msg_id = _t("MsgId") or f"wc-{int(time.time()*1000)}"
        if not text:
            return WebhookResult(body="success", content_type="text/plain", skip_agent=True)

        return WebhookResult(
            body="success",
            content_type="text/plain",
            inbound=InboundMessage(
                msg_id=msg_id,
                chat_id=chat_id,
                user_id=user_id,
                text=text,
                chat_type="p2p",
                raw={"MsgType": msg_type, "FromUserName": user_id},
            ),
        )

    async def _access_token(self) -> str:
        corp_id = self.config.get("corp_id") or ""
        secret = self.config.get("secret") or ""
        if not corp_id or not secret:
            raise RuntimeError("企业微信未配置 corp_id / secret")
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                params={"corpid": corp_id, "corpsecret": secret},
            )
            data = resp.json()
            if data.get("errcode", 0) != 0:
                raise RuntimeError(f"企业微信 token 失败: {data}")
            return data["access_token"]

    async def send_text(self, chat_id: str, text: str, *, reply_to: dict | None = None) -> None:
        token = await self._access_token()
        agent_id = int(self.config.get("agent_id") or 0)
        for chunk in self.chunk_text(text, 2000):
            payload = {
                "touser": chat_id,
                "msgtype": "text",
                "agentid": agent_id,
                "text": {"content": chunk},
            }
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}",
                    json=payload,
                )
                data = resp.json()
                if data.get("errcode", 0) != 0:
                    raise RuntimeError(f"企业微信发送失败: {data}")
