"""Picix proof 签名实现。

逆向自 https://jav.picix.us 前端（repo-*.js），签名规范：

- 算法: ECDSA P-256 (secp256r1) + SHA-256，签名为 raw r||s（各 32 字节，共 64 字节），
  base64url 无 padding 编码。
- 待签消息: 各字段以 \n 连接（站点 yE: [version,credHash,method,target,bodyHash,ts,nonce].join('\n')）
      version
      sha256_hex(credential)
      HTTP_METHOD（大写）
      request_target
      sha256_hex(body)
      unix 秒
      nonce
- version: 会话请求 `PICIX-PROOF-V1`（credential=token）；
  登录确认 `PICIX-LOGIN-V1`（credential=loginCode）；
  票据 `PICIX-TICKET-V1`（credential=ticket）。
- request_target: 完整路径含 query，去掉 `/api` 前缀，如 `/adult/Tasks/list?afterId=0`。
- body: 实际发送的 JSON 字符串（axios compact 序列化）；GET/无 body 时为空串，
  其 sha256 为空串摘要。
- nonce: 16 随机字节 base64url（22 字符）。
- 请求头: X-Picix-Proof-Time / X-Picix-Proof-Nonce / X-Picix-Proof-Signature。

getLoginCode 不需要签名；checkLoginCode 用 login kind；
其余全部用 session kind 并带 `Authorization: <token>`（裸 token，无 Bearer 前缀，无 Keyid 头）。
"""

from __future__ import annotations

import base64
import hashlib
import os
import time
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

VERSIONS = {
    "session": "PICIX-PROOF-V1",
    "login": "PICIX-LOGIN-V1",
    "ticket": "PICIX-TICKET-V1",
}

PROOF_HEADERS = ("X-Picix-Proof-Time", "X-Picix-Proof-Nonce", "X-Picix-Proof-Signature")

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def private_key_pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def load_private_key_pem(pem: str) -> ec.EllipticCurvePrivateKey:
    return serialization.load_pem_private_key(pem.encode("ascii"), password=None)


def proof_public_key_b64(key: ec.EllipticCurvePrivateKey) -> str:
    """SPKI DER 的 base64url，即前端 proofPublicKey。"""
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return b64url(spki)


def new_proof_key_id() -> str:
    """登录 proof key id：`login-` + 22 字符 base64url（16 随机字节）。"""
    return "login-" + b64url(os.urandom(16))


def sign_request(
    kind: str,
    credential: str,
    method: str,
    request_target: str,
    body: str,
    private_key: ec.EllipticCurvePrivateKey,
    now: int | None = None,
) -> dict[str, str]:
    """生成三个 proof 请求头。"""
    if kind not in VERSIONS:
        raise ValueError(f"unknown proof kind: {kind}")
    ts = int(now if now is not None else time.time())
    nonce = b64url(os.urandom(16))
    message = "\n".join(
        [
            VERSIONS[kind],
            sha256_hex(credential),
            method.upper(),
            request_target,
            sha256_hex(body) if body else EMPTY_SHA256,
            str(ts),
            nonce,
        ]
    )
    der_sig = private_key.sign(message.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_sig)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    if len(raw) != 64:
        raise ValueError("invalid signature length")
    return {
        "X-Picix-Proof-Time": str(ts),
        "X-Picix-Proof-Nonce": nonce,
        "X-Picix-Proof-Signature": b64url(raw),
    }


def compact_json(obj: Any) -> str:
    """与 axios JSON.stringify 一致的紧凑序列化。"""
    import json

    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
