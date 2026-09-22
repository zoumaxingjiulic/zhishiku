"""Per-tool grants and actual network deadlines (no network connections)."""
import json
import threading
import time
from dataclasses import replace

import httpcore
import httpx
import pytest

from test_assistant_review_fixes import locked_catalog_database
from test_assistant_runtime import USER


@pytest.mark.parametrize('mutation', ['cross_tool', 'original', 'one_original_left', 'disabled', 'acl'])
@pytest.mark.parametrize('locked', [False, True])
def test_each_tool_retains_its_own_original_grant_edges(mutation, locked):
    from app.domains.assistant.repository import AssistantRepository
    from app.domains.assistant.capabilities import CapabilityCatalog
    from app.domains.assistant.schemas import CapabilityCatalogSnapshot, CapabilitySelection
    from app.core.errors import AuthorizationError
    db, Cursor, _ = locked_catalog_database()
    db.execute("UPDATE agent SET launch_mode='workflow' WHERE id=7")
    db.execute("INSERT INTO agent(id,code,name,status,launch_mode,settings_json) VALUES(42,'FORM','Form','active','form','{}')")
    db.execute("INSERT INTO agent_department_acl VALUES(42,2,'use')")
    db.execute("INSERT INTO connector_tool SELECT 12,connector_id,'other','Other',description,input_schema_json,output_schema_json,annotations_json,status FROM connector_tool WHERE id=11")
    db.execute("INSERT INTO agent_connector_tool VALUES(42,12,'read')")
    if mutation == 'one_original_left': db.execute("INSERT INTO agent_connector_tool VALUES(42,11,'read')")
    cursor = Cursor()
    repo = AssistantRepository(cursor)
    snapshot = CapabilityCatalog(repo).for_user(USER)
    snapshot = CapabilityCatalogSnapshot.model_validate_json(snapshot.model_dump_json())
    if mutation in {'cross_tool', 'one_original_left'}:
        db.execute('DELETE FROM agent_connector_tool WHERE agent_id=7 AND connector_tool_id=11')
    if mutation == 'cross_tool': db.execute("INSERT INTO agent_connector_tool VALUES(42,11,'read')")
    if mutation == 'disabled': db.execute("UPDATE agent SET status='disabled' WHERE id=7")
    if mutation == 'acl': db.execute('DELETE FROM agent_department_acl WHERE agent_id=7')
    try:
        if locked: repo.lock_authority(8, snapshot, 99)
        catalog = CapabilityCatalog(repo)
        catalog.validate_selection(USER, snapshot, CapabilitySelection(tool_ids=[12]))
        if mutation in {'original', 'one_original_left'}:
            catalog.validate_selection(USER, snapshot, CapabilitySelection(tool_ids=[11]))
        else:
            with pytest.raises(AuthorizationError):
                catalog.validate_selection(USER, snapshot, CapabilitySelection(tool_ids=[11]))
        if locked:
            expected = [11, 12] if mutation in {'original', 'one_original_left'} else [12]
            assert [row.id for row in catalog.for_user(USER).tools] == expected
        assert snapshot.agents == ()
    finally:
        cursor.release()
        db.close()


def test_per_tool_mapping_is_frozen_json_compatible_and_legacy_is_readable():
    from app.domains.assistant.schemas import CapabilityCatalogSnapshot
    snapshot = CapabilityCatalogSnapshot(tool_authority={'11': [7, 42], '12': [42]})
    restored = CapabilityCatalogSnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored.tool_authority == {'11': (7, 42), '12': (42,)}
    with pytest.raises(TypeError): restored.tool_authority['11'] = (99,)
    with pytest.raises(TypeError): restored.tool_authority['11'][0] = 99
    assert CapabilityCatalogSnapshot.model_validate_json('{}').tool_authority == {}


class DripStream:
    """Real HTTP wire bytes; each body fragment arrives before scalar timeout."""
    def __init__(self, body):
        self.chunks = [b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n']
        self.chunks += [body[i:i + 2] for i in range(0, len(body), 2)]
        self.closed = threading.Event()
        self.reads = 0
    def read(self, n, timeout=None):
        self.reads += 1
        if not self.chunks: return b''
        delay = 0.01 if self.reads > 1 else 0
        if timeout is not None and timeout < delay:
            time.sleep(max(0, timeout))
            raise httpcore.ReadTimeout('deadline')
        time.sleep(delay)
        return self.chunks.pop(0)
    def write(self, data, timeout=None): pass
    def close(self): self.closed.set()
    def start_tls(self, *args, **kwargs): return self
    def get_extra_info(self, name): return False if name == 'is_readable' else None


@pytest.mark.parametrize('route', ['embedding', 'keyword', 'rerank'])
def test_retrieval_http_drip_cannot_extend_absolute_deadline(monkeypatch, route):
    from app import retrieval
    import socket
    import ssl
    import httpx._transports.default
    bodies = {'embedding': {'data': [{'embedding': [1.0] * 8}]},
              'keyword': {'hits': {'hits': [{'_source': {'content_unit_id': 123}}]}},
              'rerank': {'results': [{'index': 0, 'relevance_score': 0.99}]}}
    stream = DripStream(json.dumps(bodies[route]).encode())
    monkeypatch.setattr(httpcore.SyncBackend, 'connect_tcp', lambda *args, **kwargs: stream)
    def forbidden(*args, **kwargs): raise AssertionError('test attempted a real network connection')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    context = ssl.create_default_context()
    monkeypatch.setattr(httpx, 'create_ssl_context', lambda **kwargs: context)
    monkeypatch.setattr(httpx._transports.default, 'create_ssl_context', lambda **kwargs: context)
    monkeypatch.setattr(retrieval, 'settings', replace(retrieval.settings,
        embedding_provider='remote', embedding_base_url='http://8.8.8.8', embedding_model='embed',
        rerank_base_url='http://8.8.8.8', rerank_model='rank',
        opensearch_url='http://8.8.8.8', model_allowed_hosts=('8.8.8.8',)))
    # Existing code's httpx.post still uses our no-network backend but lacks an
    # absolute stream deadline; the real transport pipeline is otherwise intact.
    started = time.monotonic()
    with pytest.raises((TimeoutError, httpx.TimeoutException, httpcore.TimeoutException)):
        if route == 'embedding': retrieval.embedding('query', deadline=started + .05)
        elif route == 'keyword': retrieval.keyword_candidates('query', [1], [2], strict=True, deadline=started + .05)
        else: retrieval.rerank('query', [{'content_text': 'evidence'}], deadline=started + .05)
    elapsed = time.monotonic() - started
    assert elapsed < .13
    assert stream.reads < 10
    assert stream.closed.is_set()


def test_quality_deadline_does_not_join_slow_future_and_worker_exits(monkeypatch):
    from app import quality
    finished = threading.Event()
    workers = []
    def finite_slow(*args, **kwargs):
        workers.append(threading.current_thread())
        try: time.sleep(.25); return []
        finally: finished.set()
    monkeypatch.setattr(quality, 'vector_candidates', finite_slow)
    started = time.monotonic()
    config = quality.RetrievalPolicy(mode='vector').model_dump()
    try:
        with pytest.raises(TimeoutError):
            quality.retrieve('q', [1], [], None, {'is_platform_admin': True}, config, lambda *args: [], deadline=started + .05)
        assert time.monotonic() - started < .15
    finally:
        assert finished.wait(.5)
        for worker in workers:
            worker.join(.5)
            assert not worker.is_alive()


def test_legacy_tool_snapshot_reads_but_cannot_invent_original_edges():
    from app.domains.assistant.repository import AssistantRepository
    from app.domains.assistant.capabilities import CapabilityCatalog
    from app.domains.assistant.schemas import CapabilityCatalogSnapshot, CapabilitySelection
    from app.core.errors import AuthorizationError
    db, Cursor, _ = locked_catalog_database()
    cursor = Cursor()
    try:
        repo = AssistantRepository(cursor)
        current = CapabilityCatalog(repo).for_user(USER)
        legacy = current.model_dump(mode='json', exclude={'tool_authority'})
        restored = CapabilityCatalogSnapshot.model_validate_json(json.dumps(legacy))
        assert restored.tools[0].id == 11
        with pytest.raises(AuthorizationError):
            CapabilityCatalog(repo).validate_selection(USER, restored, CapabilitySelection(tool_ids=[11]))
    finally:
        cursor.release()
        db.close()


def test_admin_direct_tool_authority_needs_no_agent_grant():
    from app.domains.assistant.repository import AssistantRepository
    from app.domains.assistant.capabilities import CapabilityCatalog
    from app.domains.assistant.schemas import CapabilitySelection
    db, Cursor, _ = locked_catalog_database()
    db.execute("UPDATE department SET code='PLATFORM_ADMIN' WHERE id=2")
    db.execute('DELETE FROM agent_connector_tool')
    cursor = Cursor()
    try:
        repo = AssistantRepository(cursor)
        user = repo.load_current_user(8)
        catalog = CapabilityCatalog(repo)
        captured = catalog.for_user(user)
        assert captured.tool_authority == {}
        repo.lock_authority(8, captured, 99)
        assert catalog.validate_selection(user, captured, CapabilitySelection(tool_ids=[11])).tool_ids == [11]
    finally:
        cursor.release()
        db.close()


@pytest.mark.parametrize(('configured', 'requested', 'search', 'allowed'), [
    ('http://10.0.0.8:9200', 'http://10.0.0.8:9200/index/_search', True, True),
    ('http://10.0.0.8:9200', 'http://10.0.0.8:9300/index/_search', True, False),
    ('http://10.0.0.8:9200', 'http://8.8.8.8/index/_search', True, False),
    ('http://169.254.169.254', 'http://169.254.169.254/index/_search', True, False),
    ('http://10.0.0.8:9200', 'http://10.0.0.8/embeddings', False, False),
])
def test_deadline_transport_restricts_internal_search_origin_and_model_allowlist(monkeypatch, configured, requested, search, allowed):
    import socket
    from app import retrieval
    from app.core.errors import ValidationError
    stream = DripStream(b'{}')
    connected = []
    def connect(self, host, port, **kwargs): connected.append((host, port)); return stream
    def forbidden(*args, **kwargs): raise AssertionError('test attempted real network')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(httpcore.SyncBackend, 'connect_tcp', connect)
    monkeypatch.setattr(retrieval, 'settings', replace(retrieval.settings,
        opensearch_url=configured, model_allowed_hosts=('8.8.8.8',), model_allowed_cidrs=()))
    if allowed:
        response = retrieval._retrieval_post(requested, deadline=time.monotonic() + 1,
                                             opensearch=search, timeout=1, json={})
        assert response.json() == {}
        assert connected == [('10.0.0.8', 9200)]
    else:
        with pytest.raises(ValidationError):
            retrieval._retrieval_post(requested, deadline=time.monotonic() + 1,
                                     opensearch=search, timeout=1, json={})
        assert connected == []


def test_absolute_deadline_includes_time_before_connect_and_each_stream_read():
    from app.core.outbound import OutboundPolicy, PinnedNetworkBackend
    clock = [100.0]
    seen = []
    class Stream:
        def read(self, n, timeout=None): seen.append(('read', timeout)); return b'x'
        def close(self): pass
    class Backend:
        def connect_tcp(self, host, port, **kwargs):
            seen.append(('connect', kwargs['timeout']))
            clock[0] += 1
            return Stream()
    policy = OutboundPolicy(('8.8.8.8',), ())
    network = PinnedNetworkBackend(policy, Backend(), deadline=105, clock=lambda: clock[0])
    clock[0] = 102
    stream = network.connect_tcp('8.8.8.8', 80, timeout=90)
    stream.read(1, timeout=90)
    clock[0] = 105
    with pytest.raises(httpcore.TimeoutException): stream.read(1, timeout=90)
    assert seen == [('connect', 3), ('read', 2)]
