"""Fail-closed outbound URL policy with DNS and address-class validation."""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import ipaddress
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpcore
import httpx

from .errors import ValidationError


Resolver = Callable[..., Iterable[str]]
METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


_DNS_LOOP: asyncio.AbstractEventLoop | None = None
_DNS_READY = threading.Event()
_DNS_LOCK = threading.Lock()


def _dns_loop() -> asyncio.AbstractEventLoop:
    global _DNS_LOOP
    with _DNS_LOCK:
        if _DNS_LOOP is None:
            loop = asyncio.new_event_loop()

            def run() -> None:
                asyncio.set_event_loop(loop)
                _DNS_READY.set()
                loop.run_forever()

            threading.Thread(target=run, name="kb-dns-resolver", daemon=True).start()
            _DNS_READY.wait(1)
            _DNS_LOOP = loop
    return _DNS_LOOP


async def _async_getaddrinfo(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    rows = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({item[4][0] for item in rows})


def _resolve_all(host: str, port: int, timeout: float | None = None) -> list[str]:
    budget = 10.0 if timeout is None else timeout
    if budget <= 0:
        raise ValidationError("出站目标 DNS 解析超时")
    future = asyncio.run_coroutine_threadsafe(
        asyncio.wait_for(_async_getaddrinfo(host, port), timeout=budget), _dns_loop()
    )
    try:
        return future.result(timeout=budget)
    except (OSError, socket.gaierror, TimeoutError, asyncio.TimeoutError, concurrent.futures.TimeoutError):
        future.cancel()
        raise ValidationError("出站目标 DNS 解析失败或超时") from None


class OutboundPolicy:
    """Require an exact host allowlist and validate every resolved address."""

    def __init__(
        self,
        allowed_hosts: Iterable[str],
        allowed_cidrs: Iterable[str],
        *,
        resolver: Resolver = _resolve_all,
    ) -> None:
        self.allowed_hosts = {item.rstrip(".").lower() for item in allowed_hosts}
        try:
            self.allowed_networks = tuple(ipaddress.ip_network(item, strict=False) for item in allowed_cidrs)
        except ValueError as exc:
            raise ValidationError("出站网段允许列表配置无效") from exc
        self.resolver = resolver

    def resolve(self, url: str, *, deadline: float | None = None) -> "ResolvedTarget":
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").rstrip(".").lower()
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise ValidationError("出站地址格式无效") from exc
        if parsed.scheme not in {"http", "https"} or not host:
            raise ValidationError("出站地址仅支持 HTTP/HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValidationError("出站地址不得包含用户信息、查询参数或片段")
        return self.resolve_host(host, port, deadline=deadline)

    def _resolver_call(self, host: str, port: int, timeout: float | None) -> list[str]:
        try:
            parameter_count = len(inspect.signature(self.resolver).parameters)
        except (TypeError, ValueError):
            parameter_count = 2
        return list(self.resolver(host, port, timeout)) if parameter_count >= 3 \
            else list(self.resolver(host, port))

    def resolve_host(self, host: str, port: int, *, deadline: float | None = None) -> "ResolvedTarget":
        host = str(host).strip().strip("[]").rstrip(".").lower()
        if not host or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValidationError("出站地址格式无效")
        if host not in self.allowed_hosts:
            raise ValidationError("出站目标不在主机允许列表")
        try:
            literal = ipaddress.ip_address(host)
            addresses = [str(literal)]
        except ValueError:
            timeout = None if deadline is None else deadline - time.monotonic()
            if timeout is not None and timeout <= 0:
                raise ValidationError("出站目标 DNS 解析超时")
            addresses = self._resolver_call(host, port, timeout)
        if not addresses:
            raise ValidationError("出站目标 DNS 未返回地址")
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise ValidationError("出站目标 DNS 返回无效地址") from exc
            # IPv4-mapped IPv6 addresses must inherit the IPv4 policy.  Checking
            # only the IPv6 wrapper would miss IPv4 metadata endpoints and would
            # also prevent an explicitly allowed private IPv4 CIDR from matching.
            policy_address = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) \
                and address.ipv4_mapped is not None else address
            if (
                policy_address in METADATA_ADDRESSES or policy_address.is_loopback
                or policy_address.is_link_local or policy_address.is_multicast
                or policy_address.is_unspecified or policy_address.is_reserved
            ):
                raise ValidationError("出站目标地址属于禁止网段")
            if not policy_address.is_global and not any(
                policy_address.version == network.version and policy_address in network
                for network in self.allowed_networks
            ):
                raise ValidationError("企业私网或非公网目标不在显式允许网段")
        return ResolvedTarget(host=host, port=port, addresses=tuple(addresses))

    def validate(self, url: str, *, deadline: float | None = None) -> None:
        self.resolve(url, deadline=deadline)


@dataclass(frozen=True)
class ResolvedTarget:
    host: str
    port: int
    addresses: tuple[str, ...]


class PinnedNetworkBackend(httpcore.SyncBackend):
    """Connect only to policy-validated IPs while preserving original authority.

    HTTP Core still performs TLS on the returned stream with the original host as
    ``server_hostname`` and builds the Host header from the untouched request URL.
    """

    def __init__(self, policy: OutboundPolicy, backend=None, *, clock=time.monotonic) -> None:
        self.policy = policy
        self.backend = backend or httpcore.SyncBackend()
        self.clock = clock

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        deadline = None if timeout is None else self.clock() + max(0.0, timeout)
        try:
            target = self.policy.resolve_host(host, port, deadline=deadline)
        except ValidationError:
            raise httpcore.ConnectError("出站目标校验失败") from None
        for address in target.addresses:
            remaining = None if deadline is None else deadline - self.clock()
            if remaining is not None and remaining <= 0:
                break
            try:
                stream = self.backend.connect_tcp(
                    address, port, timeout=remaining, local_address=local_address,
                    socket_options=socket_options,
                )
                return DeadlineNetworkStream(stream, deadline, self.clock)
            except Exception:  # safe failover within one validated answer set
                pass
        raise httpcore.ConnectError("出站连接失败") from None

    def connect_unix_socket(self, *args, **kwargs):  # pragma: no cover - explicitly unsupported
        raise httpcore.ConnectError("出站连接失败") from None


class DeadlineNetworkStream:
    def __init__(self, stream, deadline: float | None, clock=time.monotonic) -> None:
        self.stream = stream
        self.deadline = deadline
        self.clock = clock

    def _timeout(self, requested):
        if self.deadline is None:
            return requested
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise httpcore.TimeoutException("出站调用超过整体时限")
        return remaining if requested is None else min(requested, remaining)

    def read(self, max_bytes, timeout=None):
        return self.stream.read(max_bytes, timeout=self._timeout(timeout))

    def write(self, buffer, timeout=None):
        return self.stream.write(buffer, timeout=self._timeout(timeout))

    def close(self):
        return self.stream.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        secured = self.stream.start_tls(
            ssl_context, server_hostname=server_hostname, timeout=self._timeout(timeout)
        )
        return DeadlineNetworkStream(secured, self.deadline, self.clock)

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)


class PinnedHTTPTransport(httpx.HTTPTransport):
    """HTTPX transport whose sockets are pinned to validated DNS answers."""

    def __init__(self, policy: OutboundPolicy, *, verify: ssl.SSLContext | str | bool = True,
                 network_backend=None) -> None:
        # Build our own pool: no environment proxy and no keepalive reuse across
        # policy/DNS decisions. The request URL is never rewritten.
        super().__init__(verify=verify, trust_env=False)
        self._pool.close()
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=verify, trust_env=False),
            max_connections=20,
            max_keepalive_connections=0,
            http1=True,
            http2=False,
            network_backend=network_backend or PinnedNetworkBackend(policy),
        )


def pinned_client(policy: OutboundPolicy, **kwargs) -> httpx.Client:
    return httpx.Client(
        transport=PinnedHTTPTransport(policy), follow_redirects=False, trust_env=False, **kwargs
    )
