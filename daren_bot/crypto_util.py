"""app_secret 的 AES-GCM 加密存储（spec 硬约束：加密存储、严禁明文落日志）。

主密钥放 data/daren-bot/master.key（0600），首启自动生成。
pycryptodome 随 lark-oapi 依赖装入，不额外引包。
"""
import base64
import os
from Crypto.Cipher import AES

from . import config


def load_or_create_master_key() -> bytes:
    if config.MASTER_KEY_PATH.exists():
        return base64.urlsafe_b64decode(config.MASTER_KEY_PATH.read_bytes())
    key = os.urandom(32)
    config.MASTER_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.MASTER_KEY_PATH.write_bytes(base64.urlsafe_b64encode(key))
    os.chmod(config.MASTER_KEY_PATH, 0o600)
    return key


def encrypt_secret(master_key: bytes, plaintext: str) -> str:
    nonce = os.urandom(12)
    ct = AES.new(master_key, AES.MODE_GCM, nonce=nonce).encrypt(plaintext.encode())
    return base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt_secret(master_key: bytes, blob: str) -> str:
    raw = base64.urlsafe_b64decode(blob)
    nonce, ct = raw[:12], raw[12:]
    return AES.new(master_key, AES.MODE_GCM, nonce=nonce).decrypt(ct).decode()
