import pytest

from services.worker.app import main as worker


class Recorder:
    def __init__(self, source: str, events: list[str], fail: bool = False) -> None:
        self.source = source
        self.events = events
        self.fail = fail

    def delete_document(self, document_id: int) -> None:
        self.events.append(f"{self.source}:{document_id}")
        if self.fail:
            raise RuntimeError(f"{self.source} unavailable")


class ObjectRecorder:
    def __init__(self, events: list[str], fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    def delete_object(self, object_key: str) -> None:
        self.events.append(f"minio:{object_key}")
        if self.fail:
            raise RuntimeError("minio unavailable")


def stores(events: list[str], failing: str | None = None):
    from services.worker.app.external_stores import ExternalDeleteStores

    return ExternalDeleteStores(
        milvus=Recorder("milvus", events, failing == "milvus"),
        opensearch=Recorder("opensearch", events, failing == "opensearch"),
        objects=ObjectRecorder(events, failing == "minio"),
    )


@pytest.mark.parametrize(
    ("failing", "expected"),
    [
        ("milvus", ["milvus:31"]),
        ("opensearch", ["milvus:31", "opensearch:31"]),
        ("minio", ["milvus:31", "opensearch:31", "minio:documents/file.pdf"]),
    ],
)
def test_external_delete_reports_the_failing_source_after_partial_success(failing, expected) -> None:
    """Catches a partial cleanup losing which external system must be retried."""
    from services.worker.app.external_stores import ExternalStoreDeleteError

    events: list[str] = []
    with pytest.raises(ExternalStoreDeleteError) as caught:
        stores(events, failing).delete_document(31, "documents/file.pdf")
    assert caught.value.source == failing
    assert events == expected


def test_external_delete_is_safe_to_repeat_after_success() -> None:
    """Catches retry orchestration skipping a store because a prior attempt partly completed."""
    events: list[str] = []
    adapter = stores(events)
    adapter.delete_document(31, "documents/file.pdf")
    adapter.delete_document(31, "documents/file.pdf")
    assert events == [
        "milvus:31",
        "opensearch:31",
        "minio:documents/file.pdf",
        "milvus:31",
        "opensearch:31",
        "minio:documents/file.pdf",
    ]


def test_worker_delete_keeps_content_until_all_external_stores_succeed(monkeypatch) -> None:
    """Catches content metadata disappearing after only some external stores were cleaned."""
    events: list[str] = []
    monkeypatch.setattr(worker, "db", lambda: pytest.fail("content must remain retryable"))
    with pytest.raises(Exception):
        worker.delete_document(
            {
                "document_id": 31,
                "document_version_id": 41,
                "object_key": "documents/file.pdf",
            },
            external_stores=stores(events, "opensearch"),
        )
    assert events == ["milvus:31", "opensearch:31"]


class RecoveryCursor:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.rowcount = 2

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, statement, parameters=()):
        self.events.append(" ".join(statement.split()))


class RecoveryConnection:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def cursor(self):
        return RecoveryCursor(self.events)

    def commit(self):
        self.events.append("commit")


def test_single_worker_startup_recovers_abandoned_running_jobs(monkeypatch) -> None:
    """Catches process crashes leaving ingestion jobs permanently unclaimable."""
    events: list[str] = []
    monkeypatch.setattr(worker, "db", lambda: RecoveryConnection(events))
    assert worker.recover_abandoned_jobs() == 2
    assert events == [
        "UPDATE ingestion_job SET status='queued',started_at=NULL "
        "WHERE status='running' AND job_type IN ('extract','reindex','delete')",
        "commit",
    ]


def test_worker_recovers_before_attempting_the_next_claim(monkeypatch) -> None:
    """Catches claim starting before crashed running jobs are made available again."""
    events: list[str] = []

    class StopWorker(BaseException):
        pass

    monkeypatch.setattr(worker, "recover_abandoned_jobs", lambda: events.append("recover"))
    monkeypatch.setattr(worker, "claim", lambda: events.append("claim") or None)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: (_ for _ in ()).throw(StopWorker()))
    with pytest.raises(StopWorker):
        worker.main()
    assert events == ["recover", "claim"]


def test_worker_failure_persists_stable_code_and_never_logs_external_error(monkeypatch, caplog) -> None:
    """External parser/model errors must not become durable or logged secret-bearing text."""
    secret = "Bearer SECRET-UPSTREAM-BODY"
    job = {"job_id": 9, "job_type": "extract"}
    finished = []

    class StopWorker(BaseException):
        pass

    claims = iter([job])
    monkeypatch.setattr(worker, "recover_abandoned_jobs", lambda: 0)
    monkeypatch.setattr(worker, "claim", lambda: next(claims, None))
    monkeypatch.setattr(worker, "run", lambda current: (_ for _ in ()).throw(RuntimeError(secret)))
    monkeypatch.setattr(worker, "finish", lambda current, ok, error=None: finished.append((current, ok, error)))
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: (_ for _ in ()).throw(StopWorker()))

    with caplog.at_level("ERROR"), pytest.raises(StopWorker):
        worker.main()

    assert finished == [(job, False, "INGESTION_FAILED")]
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text


def test_finish_rejects_secret_bearing_error_text_before_database_write(monkeypatch) -> None:
    parameters = []

    class Cursor(RecoveryCursor):
        def execute(self, statement, values=()):
            parameters.append(values)

    class Connection(RecoveryConnection):
        def cursor(self):
            return Cursor(self.events)

    monkeypatch.setattr(worker, "db", lambda: Connection([]))
    worker.finish(
        {"job_id": 9, "job_type": "extract", "document_version_id": 11},
        False,
        "Bearer SECRET-UPSTREAM-BODY",
    )

    assert parameters[0] == ("INGESTION_FAILED", 9)
