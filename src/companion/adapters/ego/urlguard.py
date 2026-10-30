"""公开网页 URL 边界检查。

第一版只允许访问公网 HTTP/HTTPS 页面：拒绝本机、私网、链路本地、保
留地址、特殊协议、带用户信息的 URL 和无点主机名。这是**框架级的访问
边界**，在把 URL 交给浏览器桥接之前执行；导航后的实际地址（跳转目标）
同样要过检。

如实说明边界：域名解析后的私网 IP（DNS 重绑定）不在第一版校验范围；
TaskSpace 也不构成浏览器 Profile、登录态或网络隔离。
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


def validate_public_url(url: str) -> tuple[str | None, str | None]:
    """返回 (规范化 URL, None) 或 (None, 拒绝原因)。"""
    if not isinstance(url, str) or not url.strip():
        return None, "URL 为空"
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, f"只允许 http/HTTPS 地址（收到 {parsed.scheme or '无协议'}）"
    host = parsed.hostname
    if not host:
        return None, "URL 缺少主机名"
    if "@" in (parsed.netloc or ""):
        return None, "URL 不允许携带用户信息（user:pass@）"
    hostname = host.lower().strip(".")
    if hostname in ("localhost",) or hostname.endswith(_BLOCKED_HOST_SUFFIXES):
        return None, f"本机/内部域名被拒绝（{hostname}）"
    if "." not in hostname and ":" not in hostname:
        return None, f"无点主机名被拒绝（{hostname}）"
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass  # 普通域名
    else:
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return None, f"非公网 IP 地址被拒绝（{address}）"
    return url, None
