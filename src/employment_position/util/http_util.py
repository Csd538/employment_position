import hashlib
import os
import ssl
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import contextlib

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from util.config import (
    DEFAULT_TIMEOUT,
    RETRY_ATTEMPTS,
    RETRY_WAIT_SECONDS,
)

# 允许旧版 TLS 重协商（部分政府站点需要）
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# 兼容旧版 TLS 重协商
# Python 3.12+ 有 ssl.OP_LEGACY_SERVER_CONNECT
# Python 3.11 没有，所以用 0x4 兜底
with contextlib.suppress(Exception):
    _SSL_CTX.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)

# 尽量放开加密套件限制，兼容弱证书、弱 DH、弱签名算法等
# 同时降低安全级别，解决 BAD_ECPOINT 等椭圆曲线兼容性问题
# 某些政府站点的 SSL 证书不兼容 Python 3.10+ 的严格检查
with contextlib.suppress(Exception):
    _SSL_CTX.set_ciphers("ALL:@SECLEVEL=0")

# 放开 TLS 最低版本，兼容 TLSv1 / TLSv1.1 老站
with contextlib.suppress(Exception):
    _SSL_CTX.minimum_version = ssl.TLSVersion.TLSv1

# 最大 TLS 版本使用系统支持的最高版本
with contextlib.suppress(Exception):
    _SSL_CTX.maximum_version = ssl.TLSVersion.MAXIMUM_SUPPORTED

# 设置兼容的椭圆曲线，避免 BAD_ECPOINT 错误
with contextlib.suppress(Exception):
    _SSL_CTX.set_ecdh_curve("prime256v1")

_SSL_CTX.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
TUNNEL_PROXY_SERVER = os.getenv("TUNNEL_PROXY_SERVER")
TUNNEL_PROXY_USERNAME = os.getenv("TUNNEL_PROXY_USERNAME")
TUNNEL_PROXY_PASSWORD = os.getenv("TUNNEL_PROXY_PASSWORD")
TUNNEL_PROXY = (
    f"http://{TUNNEL_PROXY_USERNAME}:{TUNNEL_PROXY_PASSWORD}@{TUNNEL_PROXY_SERVER}"
    if all([TUNNEL_PROXY_SERVER, TUNNEL_PROXY_USERNAME, TUNNEL_PROXY_PASSWORD])
    else None
)


@retry(
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=wait_fixed(RETRY_WAIT_SECONDS),
)
async def async_request_with_proxy(
    method,
    url,
    headers=None,
    data=None,
    cookies=None,
    json=None,
    params=None,
    proxy=TUNNEL_PROXY,
    trust_env=False,
    _verify: bool | Any = False,
    timeout=DEFAULT_TIMEOUT,
    follow_redirects=True,
):
    async with httpx.AsyncClient(
        proxy=proxy,
        timeout=timeout,
        trust_env=trust_env,
        verify=_SSL_CTX,
        cookies=cookies,
        follow_redirects=follow_redirects,
    ) as client:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            data=data,
            json=json,
            params=params,
        )
        response.raise_for_status()
        return response


def md5(string: str) -> str:
    return hashlib.md5(string.encode()).hexdigest() if string else ""
