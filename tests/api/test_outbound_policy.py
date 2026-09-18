import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


def test_outbound_policy_rejects_mixed_dns_answers_and_metadata_addresses():
    from app.core.errors import ValidationError
    from app.core.outbound import OutboundPolicy

    mixed = OutboundPolicy(
        allowed_hosts=("erp.internal",), allowed_cidrs=("192.168.1.0/24",),
        resolver=lambda host, port: ["192.168.1.33", "169.254.169.254"],
    )
    with pytest.raises(ValidationError, match="禁止"):
        mixed.validate("http://erp.internal/mcp")

    aliyun_metadata = OutboundPolicy(
        allowed_hosts=("metadata.internal",), allowed_cidrs=("100.64.0.0/10",),
        resolver=lambda host, port: ["100.100.100.200"],
    )
    with pytest.raises(ValidationError, match="禁止"):
        aliyun_metadata.validate("http://metadata.internal/latest")


def test_private_destination_requires_explicit_host_and_cidr():
    from app.core.errors import ValidationError
    from app.core.outbound import OutboundPolicy

    resolver = lambda host, port: ["192.168.1.33"]
    with pytest.raises(ValidationError, match="允许"):
        OutboundPolicy(allowed_hosts=(), allowed_cidrs=("192.168.1.0/24",), resolver=resolver).validate(
            "http://192.168.1.33:18001/mcp"
        )
    with pytest.raises(ValidationError, match="网段"):
        OutboundPolicy(allowed_hosts=("192.168.1.33",), allowed_cidrs=(), resolver=resolver).validate(
            "http://192.168.1.33:18001/mcp"
        )
    OutboundPolicy(
        allowed_hosts=("192.168.1.33",), allowed_cidrs=("192.168.1.0/24",), resolver=resolver,
    ).validate("http://192.168.1.33:18001/mcp")


def test_ipv4_mapped_ipv6_reuses_ipv4_private_cidr_policy():
    from app.core.outbound import OutboundPolicy

    OutboundPolicy(
        allowed_hosts=("erp.internal",), allowed_cidrs=("192.168.1.0/24",),
        resolver=lambda host, port: ["::ffff:192.168.1.33"],
    ).validate("http://erp.internal/mcp")


@pytest.mark.parametrize(
    ("address", "cidr"),
    [
        ("::ffff:100.100.100.200", "100.64.0.0/10"),
        ("::ffff:127.0.0.1", "127.0.0.0/8"),
        ("::ffff:169.254.1.1", "169.254.0.0/16"),
        ("::ffff:224.0.0.1", "224.0.0.0/4"),
        ("::ffff:0.0.0.0", "0.0.0.0/8"),
    ],
)
def test_ipv4_mapped_ipv6_cannot_bypass_ipv4_hard_denies(address, cidr):
    from app.core.errors import ValidationError
    from app.core.outbound import OutboundPolicy

    policy = OutboundPolicy(
        allowed_hosts=("mapped.internal",), allowed_cidrs=(cidr,),
        resolver=lambda host, port: [address],
    )
    with pytest.raises(ValidationError, match="禁止"):
        policy.validate("http://mapped.internal/resource")


def test_policy_re_resolves_before_each_request_to_block_dns_rebinding():
    from app.core.errors import ValidationError
    from app.core.outbound import OutboundPolicy

    answers = iter([["8.8.8.8"], ["127.0.0.1"]])
    policy = OutboundPolicy(
        allowed_hosts=("vendor.example",), allowed_cidrs=(), resolver=lambda host, port: next(answers),
    )
    policy.validate("https://vendor.example/v1")
    with pytest.raises(ValidationError, match="禁止"):
        policy.validate("https://vendor.example/v1")


def test_pinned_backend_connects_verified_ip_while_httpcore_keeps_host_and_sni():
    import httpcore

    from app.core.outbound import OutboundPolicy, PinnedNetworkBackend

    events = {"writes": []}

    class Stream:
        body = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"

        def read(self, max_bytes, timeout=None):
            chunk, self.body = self.body[:max_bytes], self.body[max_bytes:]
            return chunk

        def write(self, buffer, timeout=None):
            events["writes"].append(buffer)

        def close(self):
            events["closed"] = True

        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            events["sni"] = server_hostname
            return self

        def get_extra_info(self, info):
            return False if info == "is_readable" else None

    class Backend:
        def connect_tcp(self, host, port, **kwargs):
            events["connect"] = (host, port)
            return Stream()

    policy = OutboundPolicy(
        allowed_hosts=("vendor.example",), allowed_cidrs=(),
        resolver=lambda host, port: ["8.8.8.8"],
    )
    pool = httpcore.ConnectionPool(
        network_backend=PinnedNetworkBackend(policy, Backend()), max_keepalive_connections=0,
    )
    response = pool.request("GET", "https://vendor.example/resource")
    response.read()
    response.close()
    pool.close()

    assert events["connect"] == ("8.8.8.8", 443)
    assert events["sni"] == "vendor.example"
    sent = b"".join(events["writes"])
    assert b"Host: vendor.example" in sent
    assert b"8.8.8.8" not in sent


def test_ipv6_literal_is_normalized_without_url_rebuild_and_keeps_host_sni():
    import httpx

    from app.core.outbound import OutboundPolicy, PinnedHTTPTransport, PinnedNetworkBackend

    events = {"writes": []}

    class Stream:
        body = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"
        def read(self, n, timeout=None): chunk, self.body = self.body[:n], self.body[n:]; return chunk
        def write(self, value, timeout=None): events["writes"].append(value)
        def close(self): pass
        def start_tls(self, context, server_hostname=None, timeout=None): events["sni"] = server_hostname; return self
        def get_extra_info(self, info): return False if info == "is_readable" else None

    class Backend:
        def connect_tcp(self, host, port, **kwargs): events["connect"] = host; return Stream()

    address = "2001:4860:4860::8888"
    policy = OutboundPolicy((address,), (), resolver=lambda *args: (_ for _ in ()).throw(
        AssertionError("literal must not use DNS")
    ))
    transport = PinnedHTTPTransport(
        policy, network_backend=PinnedNetworkBackend(policy, Backend()),
    )
    with httpx.Client(transport=transport) as client:
        response = client.get(f"https://[{address}]/resource")
        assert response.status_code == 200
    assert events["connect"] == address
    assert events["sni"] == address
    assert f"Host: [{address}]".encode() in b"".join(events["writes"])


def test_dns_and_multi_ip_connect_share_one_absolute_deadline():
    import httpcore

    from app.core.outbound import OutboundPolicy, PinnedNetworkBackend, ResolvedTarget

    resolver_budgets = []
    policy = OutboundPolicy(
        ("vendor.example",), (),
        resolver=lambda host, port, timeout: resolver_budgets.append(timeout) or ["8.8.8.8"],
    )
    policy.resolve("https://vendor.example", deadline=__import__("time").monotonic() + 5)
    assert 0 < resolver_budgets[0] <= 5

    class Policy:
        def resolve_host(self, host, port, deadline=None):
            assert deadline == 10
            return ResolvedTarget(host, port, ("8.8.8.8", "1.1.1.1", "9.9.9.9"))

    class Stream:
        def read(self, *args, **kwargs): return b""
        def write(self, *args, **kwargs): pass
        def close(self): pass
        def start_tls(self, *args, **kwargs): return self
        def get_extra_info(self, info): return None

    budgets = []
    class Backend:
        def connect_tcp(self, host, port, timeout=None, **kwargs):
            budgets.append(timeout)
            if len(budgets) < 3:
                raise httpcore.ConnectError("unsafe remote detail")
            return Stream()

    times = iter((0, 1, 3, 6))
    PinnedNetworkBackend(Policy(), Backend(), clock=lambda: next(times)).connect_tcp(
        "vendor.example", 443, timeout=10
    )
    assert budgets == [9, 7, 4]


def test_connect_boundary_policy_error_is_normalized_without_target_details():
    import httpcore

    from app.core.errors import ValidationError
    from app.core.outbound import PinnedNetworkBackend

    class Policy:
        def resolve_host(self, host, port, deadline=None):
            raise ValidationError(f"blocked {host} secret-value")

    with pytest.raises(httpcore.ConnectError) as caught:
        PinnedNetworkBackend(Policy()).connect_tcp("secret.internal", 443, timeout=1)
    assert str(caught.value) == "出站目标校验失败"
    assert "secret.internal" not in str(caught.value)


def test_deadline_stream_reduces_read_write_and_tls_budgets():
    from app.core.outbound import DeadlineNetworkStream

    budgets = []
    class Stream:
        def read(self, n, timeout=None): budgets.append(("read", timeout)); return b""
        def write(self, value, timeout=None): budgets.append(("write", timeout))
        def close(self): pass
        def start_tls(self, context, server_hostname=None, timeout=None):
            budgets.append(("tls", timeout)); return self
        def get_extra_info(self, info): return None

    times = iter((2, 4, 6))
    stream = DeadlineNetworkStream(Stream(), 10, clock=lambda: next(times))
    stream.read(1, timeout=99)
    stream.write(b"x", timeout=99)
    stream.start_tls(None, server_hostname="vendor.example", timeout=99)
    assert budgets == [("read", 8), ("write", 6), ("tls", 4)]


def test_pinned_transport_matches_httpx_base_transport_contract():
    import httpx

    from app.core.outbound import OutboundPolicy, PinnedHTTPTransport

    transport = PinnedHTTPTransport(OutboundPolicy(("vendor.example",), ()))
    try:
        assert isinstance(transport, httpx.BaseTransport)
        assert callable(transport.handle_request)
    finally:
        transport.close()


def test_default_dns_resolution_is_async_and_cancelled_by_budget(monkeypatch):
    import asyncio
    import threading

    from app.core.errors import ValidationError
    from app.core import outbound

    cancelled = threading.Event()
    async def blocked(host, port):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(outbound, "_async_getaddrinfo", blocked)
    with pytest.raises(ValidationError, match="超时"):
        outbound._resolve_all("vendor.example", 443, timeout=0.01)
    assert cancelled.wait(0.5)
