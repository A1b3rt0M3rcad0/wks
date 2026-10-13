"""Process liveness, decoder readiness and authorized queue progress are different facts."""

from datetime import timedelta

from conftest import register_text, upload
from wks_core.storage.database import ProcessingRun, now
from wks_core.storage.stabilization import WorkerHeartbeat
from wks_worker.health import probe, write_identity
from wks_worker.worker import Worker, heartbeat_interval


def queue(report, name):
    return next(row for row in report["queues"] if row["queue"] == name)


def test_diagnostics_distinguish_stalled_queue_stale_process_and_missing_capability(env):
    s = env["s"]
    item = register_text(env)
    worker = Worker(s, queues=("native",))
    with s.sessions.begin() as db:
        db.get(ProcessingRun, item["operation_id"]).updated_at = now() - timedelta(seconds=600)
    report = env["http"].get("/v1/workers/status", headers=env["headers"])
    assert report.status_code == 200
    assert queue(report.json(), "native")["state"] == "stalled"
    assert queue(report.json(), "native")["process_alive"] is True
    Worker(s, queues=("media",))
    assert queue(s.worker_status(env["a"]), "media")["state"] == "capability_absent"
    assert worker.run_once()
    status = s.worker_status(env["a"])
    assert queue(status, "native")["state"] == "idle"
    own = next(row for row in status["workers"] if row["worker_id"] == worker.owner)
    assert own["last_success_at"] and own["last_operation"]["parser"] == "text"
    assert own["last_operation"]["coverage"]["text"] == "complete"
    with s.sessions.begin() as db:
        db.get(WorkerHeartbeat, worker.owner).updated_at = now() - timedelta(seconds=100)
    assert queue(s.worker_status(env["a"]), "native")["state"] == "worker_unavailable"


def test_queue_diagnostics_never_expose_other_clients_jobs(env):
    register_text(env, user="b")
    worker = Worker(env["s"], queues=("native",))
    assert worker.run_once()
    response = env["s"].worker_status(env["a"])
    assert queue(response, "native")["counts"] == {}
    assert all(
        row["last_success_at"] is None and row["last_operation"] is None
        for row in response["workers"]
    )
    assert env["http"].get("/v1/workers/status").status_code == 401


def test_exact_version_grant_filters_queue_and_parser_history(env):
    from test_mcp import grant

    a = register_text(env, key="allowed")
    register_text(env, key="other")
    gid = grant(env, a)
    principal = env["s"].authenticate(env["executor_token"], gid)
    assert queue(env["s"].worker_status(principal), "native")["counts"] == {"queued": 1}


def test_long_leases_refresh_process_heartbeat_and_probe_only_that_worker(env):
    s = env["s"]
    register_text(env)
    worker = Worker(s, queues=("native",))
    lease = worker.claim()
    identity = env["tmp"] / "native-health.json"
    write_identity(identity, worker.owner, worker.queues)
    with s.sessions.begin() as db:
        db.get(WorkerHeartbeat, worker.owner).updated_at = now() - timedelta(seconds=100)
    assert probe(s, identity, worker.queues)["liveness"] == "stale"
    Worker(s, queues=("native",))  # A healthy peer cannot make this process's probe pass.
    assert probe(s, identity, worker.queues)["liveness"] == "stale"
    worker.last_announce -= 31
    assert worker.heartbeat(lease)
    assert probe(s, identity, worker.queues)["liveness"] == "alive"
    assert probe(s, identity, ("media",))["liveness"] == "unavailable"
    assert heartbeat_interval(360) < 90 / 3
    assert heartbeat_interval(1) < 1


def test_capabilities_use_api_policy_and_actual_docling_routing(env):
    s = env["s"]
    Worker(s, queues=("native",))
    with s.sessions.begin() as db:
        db.add(
            WorkerHeartbeat(
                owner="synthetic-media-readiness",
                queues=["media"],
                capabilities={
                    "ready": True,
                    "ready_queues": ["media"],
                    "asr_enabled": True,
                    "video_enabled": True,
                    "profiles": {"media": "test"},
                },
                updated_at=now(),
            )
        )
    response = s.capabilities(env["a"])
    assert not next(f for f in response["formats"] if f["format"] == "wav")["can_extract"]
    assert response["modalities"]["asr"] == {
        "configured": False,
        "worker_ready": True,
        "scope": "probabilistic_speech_transcription; not_audio_event_semantics",
    }
    s.settings.extraction_profile = "docling"
    response = s.capabilities(env["a"])
    pdf = next(f for f in response["formats"] if f["format"] == "pdf")
    assert pdf["queue"] == "docling" and pdf["can_extract"] is False
    assert response["modalities"]["visual_description"]["configured"] is False


def test_retry_and_failure_diagnostics_preserve_codes_and_attempts(env):
    upload(env, "tests/fixtures/speech.wav", "audio/wav")
    worker = Worker(env["s"], queues=("media",))
    claim = worker.claim()
    worker.fail(claim, "processing.timeout", retryable=True)
    row = next(
        row
        for row in env["s"].worker_status(env["a"])["workers"]
        if row["worker_id"] == worker.owner
    )
    assert row["last_operation"]["state"] == "retry_wait"
    assert row["last_operation"]["error_code"] == "processing.timeout"
    assert row["last_operation"]["attempt"] == 1 and row["last_operation"]["next_retry_at"]


def test_occupied_peer_is_not_reported_as_stalled_or_exposed(env):
    other = register_text(env, user="b", key="busy")
    own = register_text(env, key="waiting")
    worker = Worker(env["s"], queues=("native",))
    assert worker.claim()["id"] == other["operation_id"]
    with env["s"].sessions.begin() as db:
        db.get(ProcessingRun, own["operation_id"]).updated_at = now() - timedelta(seconds=600)
    status = env["s"].worker_status(env["a"])
    assert queue(status, "native")["state"] == "waiting_capacity"
    assert queue(status, "native")["counts"] == {"queued": 1}
    assert all(row["last_operation"] is None for row in status["workers"])


def test_only_live_capable_owners_contribute_to_busy_capacity(env):
    other = register_text(env, user="b", key="busy")
    own = register_text(env, key="waiting")
    busy = Worker(env["s"], queues=("native",))
    assert busy.claim()["id"] == other["operation_id"]
    unavailable = Worker(env["s"], queues=("native",))
    with env["s"].sessions.begin() as db:
        db.get(ProcessingRun, own["operation_id"]).updated_at = now() - timedelta(seconds=600)
        row = db.get(WorkerHeartbeat, unavailable.owner)
        row.capabilities = {"ready": False, "ready_queues": []}
    assert queue(env["s"].worker_status(env["a"]), "native")["state"] == "waiting_capacity"
    with env["s"].sessions.begin() as db:
        db.get(WorkerHeartbeat, busy.owner).updated_at = now() - timedelta(seconds=100)
    Worker(env["s"], queues=("native",))
    assert queue(env["s"].worker_status(env["a"]), "native")["state"] == "stalled"
