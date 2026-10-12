import os

from sqlalchemy import text
from sqlalchemy.engine import make_url


def pg_command(command, url, pg_container=None):
    u = make_url(url)
    env = os.environ.copy()
    # Pass credentials through environment, never CLI args or logs.
    if pg_container:
        argv = [
            "docker",
            "exec",
            "-i",
            pg_container,
            command,
            "-U",
            u.username or "postgres",
            "-d",
            u.database,
        ]
    else:
        argv = [
            command,
            "-h",
            u.host or "localhost",
            "-p",
            str(u.port or 5432),
            "-U",
            u.username or "postgres",
            "-d",
            u.database,
        ]
        env["PGPASSWORD"] = u.password or ""
    return argv, env


def backup(service, destination, pg_container=None):
    from wks_core.storage.snapshot import backup as create_snapshot

    return create_snapshot(service, destination, pg_container)


def restore(service, source, pg_container=None, ledger_directory=None):
    from wks_core.storage.snapshot import restore as restore_snapshot

    return restore_snapshot(service, source, pg_container, ledger_directory)


def reindex(service):
    with service.sessions.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(874201)"))
        result = db.execute(
            text("""UPDATE search_segments seg
            SET search_vector=setweight(to_tsvector(CASE WHEN seg.locale='en' THEN 'wks_english' WHEN seg.locale='pt' THEN 'wks_portuguese' ELSE 'wks_simple' END::regconfig, normalize(seg.text,NFC)), CASE WHEN seg.origin_kind='metadata' THEN 'A' ELSE 'D' END::\"char\")
            FROM representations r JOIN source_versions v ON r.source_version_id=v.id JOIN sources s ON s.id=v.source_id
            WHERE seg.representation_id=r.id AND s.state='stored'""")
        )
        return {"segments_reindexed": result.rowcount, "references_preserved": True}


def garbage_collect(service, page_size=100, cursor=None, grace_seconds=None):
    from wks_core.storage.collection import garbage_collect as sweep

    return sweep(service, page_size, cursor, grace_seconds)
