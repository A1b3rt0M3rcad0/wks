"""Revisioned metadata, scoped administration and replayable client outcomes."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import func, select
from wks_core.application.utilities import digest
from wks_core.domain.canonical import validate_metadata
from wks_core.domain.models import Error, not_found
from wks_core.storage.database import (
    Client,
    Collection,
    CollectionSource,
    Event,
    Grant,
    Namespace,
    ProcessingRun,
    Representation,
    Source,
    SourceVersion,
    now,
)
from wks_core.storage.stabilization import (
    ClientCredential,
    DestructivePreflight,
    SourceMetadataRevision,
)


def revision_match(row, expected):
    if expected is None:
        raise Error("operation.precondition_required", "If-Match revision required", 428)
    try:
        revision = int(str(expected).strip('"'))
    except ValueError:
        revision = -1
    if revision != row.revision:
        raise Error("operation.revision_conflict", "Resource revision changed", 412)


class ManagementService:
    def bulk_process(self, p, key, namespace_id, body):
        from sqlalchemy import exists, or_
        from wks_core.storage.database import Segment

        size = min(max(body.get("page_size", 25), 1), 25)
        context = {
            "op": "bulk_process",
            "namespace_id": namespace_id,
            **{field: value for field, value in body.items() if field not in {"cursor", "dry_run"}},
        }
        state = self.cursor(p, context, body.get("cursor"))

        def select_and_schedule(db, schedule):
            self.write(db, p, namespace_id, action="source:process")
            clauses, _ = self.visible(db, p, "source:process")
            query = (
                select(Source, SourceVersion)
                .join(SourceVersion, SourceVersion.id == Source.current_version_id)
                .outerjoin(
                    Representation, Representation.id == SourceVersion.current_representation_id
                )
                .where(*clauses, Source.namespace_id == namespace_id, SourceVersion.committed)
            )
            if state["position"]:
                query = query.where(Source.id > state["position"])
            if body.get("processor_version"):
                query = query.where(
                    Representation.producer["version"].astext == body["processor_version"]
                )
            if body.get("incomplete_only"):
                query = query.where(
                    or_(
                        Representation.id.is_(None),
                        Representation.coverage["text"].astext != "complete",
                        Representation.availability.in_(
                            ["metadata_only", "unsupported_processing"]
                        ),
                    )
                )
            if body.get("missing_index_only"):
                query = query.where(
                    ~exists(
                        select(Segment.id).where(
                            Segment.representation_id == SourceVersion.current_representation_id
                        )
                    )
                )
            rows = db.execute(query.order_by(Source.id).limit(size + 1)).all()
            items = []
            for source, version in rows[:size]:
                run = self.enqueue(db, source, version, "bulk:" + key) if schedule else None
                items.append(
                    {
                        "source_id": source.id,
                        "source_version_id": version.id,
                        "operation_id": run.id if run else None,
                        "scheduled": schedule,
                    }
                )
            complete = len(rows) <= size
            return {
                "namespace_id": namespace_id,
                "items": items,
                "complete": complete,
                "next_cursor": None
                if complete
                else self.cursor(p, context, None, position=rows[size - 1][0].id),
                "dry_run": not schedule,
                "mutated": schedule,
            }

        if body.get("dry_run", True):
            with self.sessions() as db:
                return select_and_schedule(db, False)
        return self.mutate(
            p,
            key,
            {"op": "bulk_process", "namespace_id": namespace_id, **body},
            lambda db: select_and_schedule(db, True),
        )

    def metadata_patch(self, p, key, kind, identity, body, expected):
        allowed = {
            "source": {"title", "description", "tags", "metadata"},
            "namespace": {"title", "state", "policy"},
            "collection": {"title", "state"},
        }
        if kind not in allowed or not body or set(body) - allowed[kind]:
            raise Error("contract.invalid", "Unsupported metadata fields")
        for field in ("metadata", "policy"):
            if field in body:
                try:
                    validate_metadata(body[field])
                except ValueError as exc:
                    raise Error("contract.invalid", "Invalid bounded metadata") from exc
        if "title" in body and (
            not isinstance(body["title"], str)
            or not body["title"].strip()
            or len(body["title"]) > (500 if kind == "source" else 200)
        ):
            raise Error("contract.invalid", "Invalid title")
        if "description" in body and (
            not isinstance(body["description"], str) or len(body["description"]) > 10000
        ):
            raise Error("contract.invalid", "Invalid description")
        if "tags" in body and (
            not isinstance(body["tags"], list)
            or len(body["tags"]) > 50
            or any(not isinstance(t, str) or len(t) > 100 for t in body["tags"])
        ):
            raise Error("contract.invalid", "Invalid tags")
        if "state" in body and body["state"] not in {"active", "archived"}:
            raise Error("contract.invalid", "State must be active or archived")

        def op(db):
            if kind == "source":
                row = self.source(db, p, identity, writing=True)
            elif kind == "namespace":
                row = self.write(db, p, identity)
            else:
                row = db.get(Collection, identity)
                if not row:
                    raise not_found()
                self.write(db, p, row.namespace_id)
                g = self.grant(db, p)
                if g and g.collection_id and g.collection_id != identity:
                    raise not_found()
            db.refresh(row, with_for_update=True)
            if kind != "source" and row.state == "deleted":
                raise not_found()
            revision_match(row, expected)
            from wks_core.domain.namespace_policy import validate_policy, validate_source_metadata

            try:
                if kind == "namespace" and "policy" in body:
                    validate_policy(body["policy"])
                if kind == "source" and "metadata" in body:
                    validate_source_metadata(
                        body["metadata"], db.get(Namespace, row.namespace_id).policy
                    )
            except ValueError as exc:
                raise Error("contract.invalid", "Metadata or policy is invalid") from exc
            for field, value in body.items():
                setattr(row, "meta" if field == "metadata" else field, value)
            row.revision += 1
            if kind == "source":
                row.updated_at = now()
                namespace = db.get(Namespace, row.namespace_id)
                namespace.index_generation += 1
                db.add(
                    SourceMetadataRevision(
                        source_id=row.id,
                        revision=row.revision,
                        namespace_id=row.namespace_id,
                        value={
                            "title": row.title,
                            "description": row.description,
                            "tags": row.tags,
                            "metadata": row.meta,
                            "origin": "client",
                        },
                        generation=namespace.index_generation,
                    )
                )
                self.event(db, row, "source.metadata_updated", revision=row.revision)
            else:
                namespace_id = row.id if kind == "namespace" else row.namespace_id
                db.add(
                    Event(
                        namespace_id=namespace_id,
                        type=f"wks.{kind}.metadata_updated.v1",
                        payload={"id": row.id, "revision": row.revision, "schema_version": 1},
                    )
                )
            return {"id": row.id, "revision": row.revision, **body, "reindex_required": False}

        return self.mutate(
            p, key, {"op": kind + "_update", "id": identity, "expected": expected, **body}, op
        )

    def preflight(self, p, sid, action="delete"):
        if action not in {"delete", "revoke"}:
            raise Error("contract.invalid", "Invalid destructive action")
        with self.sessions.begin() as db:
            db.info["wks_action"] = "source:" + action
            source = self.source(db, p, sid, writing=True)
            g = self.grant(db, p)
            row = DestructivePreflight(
                client_id=p.client_id,
                action=action,
                resource_id=sid,
                revision=source.revision,
                expires_at=now() + timedelta(minutes=5),
            )
            db.add(row)
            db.flush()
            signature = hmac.new(
                self.cursor_key.encode(),
                digest(
                    [row.id, p.client_id, g.scope_hash if g else None, sid, action, row.revision]
                ).encode(),
                hashlib.sha256,
            ).hexdigest()
            count = db.scalar(
                select(func.count(SourceVersion.id)).where(SourceVersion.source_id == sid)
            )
            return {
                "source_id": sid,
                "revision": source.revision,
                "versions": count,
                "action": action,
                "confirmation_token": row.id + "." + signature,
                "expires_at": row.expires_at.isoformat(),
                "effect": "purge originals and derived content"
                if action == "delete"
                else "deny access; retain original",
            }

    def confirm_preflight(self, db, p, source, action, token):
        if not token:
            raise Error(
                "operation.confirmation_required",
                "Destructive preflight confirmation required",
                428,
            )
        try:
            identity, signature = token.split(".")
        except ValueError:
            raise Error("operation.confirmation_invalid", "Invalid confirmation", 412) from None
        row = db.get(DestructivePreflight, identity, with_for_update=True)
        g = self.grant(db, p)
        expected = hmac.new(
            self.cursor_key.encode(),
            digest(
                [
                    identity,
                    p.client_id,
                    g.scope_hash if g else None,
                    source.id,
                    action,
                    source.revision,
                ]
            ).encode(),
            hashlib.sha256,
        ).hexdigest()
        if (
            not row
            or row.client_id != p.client_id
            or row.resource_id != source.id
            or row.action != action
            or row.revision != source.revision
            or row.expires_at <= now()
            or row.consumed
            or not hmac.compare_digest(expected, signature)
        ):
            raise Error(
                "operation.confirmation_invalid", "Confirmation expired or resource changed", 412
            )
        row.consumed = True

    def collection_get(self, p, identity):
        with self.sessions() as db:
            row = db.get(Collection, identity)
            if not row:
                raise not_found()
            self.write(db, p, row.namespace_id, action="collection:read")
            delegation = self.grant(db, p)
            if delegation and delegation.collection_id and delegation.collection_id != row.id:
                raise not_found()
            return {
                "id": row.id,
                "namespace_id": row.namespace_id,
                "title": row.title,
                "revision": row.revision,
                "state": row.state,
            }

    def grants_list(self, p, page_size=50, cursor=None):
        size = min(max(page_size, 1), 100)
        context = {"op": "grants", "page_size": size}
        state = self.cursor(p, context, cursor)
        with self.sessions() as db:
            self.grant(db, p)
            if p.grant_id or p.role != "client":
                raise Error("auth.forbidden", "Issuer context required", 403)
            query = select(Grant).where(Grant.issuer == p.client_id)
            if state["position"]:
                query = query.where(Grant.id > state["position"])
            rows = db.scalars(query.order_by(Grant.id).limit(size + 1)).all()
            return {
                "items": [
                    {
                        "id": g.id,
                        "namespace_id": g.namespace_id,
                        "collection_id": g.collection_id,
                        "source_version_ids": g.source_version_ids,
                        "allowed_actions": g.allowed_actions,
                        "caller_binding": g.caller_binding,
                        "audience": g.audience,
                        "purpose": g.purpose,
                        "expires_at": g.expires_at.isoformat(),
                        "revoked": bool(g.revoked_at),
                        "scope_hash": g.scope_hash,
                    }
                    for g in rows[:size]
                ],
                "next_cursor": self.cursor(p, context, None, position=rows[size - 1].id)
                if len(rows) > size
                else None,
            }

    def container_preflight(self, p, kind, identity):
        if kind not in {"namespace", "collection"}:
            raise Error("contract.invalid", "Invalid container kind")
        with self.sessions.begin() as db:
            self.grant(db, p, kind + ":delete")
            row = db.get(Namespace if kind == "namespace" else Collection, identity)
            if not row:
                raise not_found()
            self.write(
                db, p, row.id if kind == "namespace" else row.namespace_id, action=kind + ":delete"
            )
            count = (
                db.scalar(
                    select(func.count(Source.id)).where(
                        Source.namespace_id == row.id, Source.state != "deleted"
                    )
                )
                if kind == "namespace"
                else db.scalar(
                    select(func.count(CollectionSource.source_id)).where(
                        CollectionSource.collection_id == row.id
                    )
                )
            )
            record = DestructivePreflight(
                client_id=p.client_id,
                action=kind + ":delete",
                resource_id=identity,
                revision=row.revision,
                expires_at=now() + timedelta(minutes=5),
            )
            db.add(record)
            db.flush()
            grant = self.grant(db, p)
            signature = hmac.new(
                self.cursor_key.encode(),
                digest(
                    [
                        record.id,
                        p.client_id,
                        grant.scope_hash if grant else None,
                        identity,
                        kind + ":delete",
                        row.revision,
                    ]
                ).encode(),
                hashlib.sha256,
            ).hexdigest()
            return {
                "id": identity,
                "kind": kind,
                "revision": row.revision,
                "source_count": count,
                "confirmation_token": record.id + "." + signature,
                "expires_at": record.expires_at.isoformat(),
                "effect": "delete empty namespace metadata only"
                if kind == "namespace"
                else "remove collection links; preserve source bytes",
            }

    def container_delete(self, p, key, kind, identity, confirmation):
        def op(db):
            row = db.get(
                Namespace if kind == "namespace" else Collection, identity, with_for_update=True
            )
            if not row:
                raise not_found()
            self.write(db, p, row.id if kind == "namespace" else row.namespace_id)
            self.confirm_preflight(db, p, row, kind + ":delete", confirmation)
            if kind == "namespace" and db.scalar(
                select(Source.id)
                .where(Source.namespace_id == identity, Source.state != "deleted")
                .limit(1)
            ):
                raise Error(
                    "namespace.not_empty",
                    "Purge or move sources explicitly before deleting namespace metadata",
                    409,
                )
            self.privacy_ledger.append(kind + "_deleted", identity)
            if kind == "collection":
                from sqlalchemy import delete

                db.execute(
                    delete(CollectionSource).where(CollectionSource.collection_id == identity)
                )
            row.state = "deleted"
            row.title = "[deleted]"
            row.revision += 1
            if kind == "namespace":
                row.external_ref = None
                row.policy = {}
            return {
                "id": identity,
                "state": "deleted",
                "source_bytes_deleted": False,
                "revision": row.revision,
            }

        return self.mutate(p, key, {"op": kind + "_delete", "id": identity}, op)

    def current_client(self, p):
        with self.sessions() as db:
            self.grant(db, p)
            row = db.get(Client, p.client_id)
            return {
                "id": row.id,
                "name": row.name,
                "role": row.role,
                "audience": row.audience,
                "operator": row.operator,
            }

    def grant_get(self, p, identity):
        with self.sessions() as db:
            self.grant(db, p)
            row = db.get(Grant, identity)
            if not row or row.issuer != p.client_id or p.grant_id:
                raise not_found()
            return {
                "id": row.id,
                "namespace_id": row.namespace_id,
                "collection_id": row.collection_id,
                "source_version_ids": row.source_version_ids,
                "allowed_actions": row.allowed_actions,
                "caller_binding": row.caller_binding,
                "audience": row.audience,
                "purpose": row.purpose,
                "expires_at": row.expires_at.isoformat(),
                "revoked": bool(row.revoked_at),
                "scope_hash": row.scope_hash,
            }

    def credentials_list(self, p, client_id):
        with self.sessions() as db:
            self.operator(db, p)
            if not db.get(Client, client_id):
                raise not_found()
            rows = db.scalars(
                select(ClientCredential)
                .where(ClientCredential.client_id == client_id)
                .order_by(ClientCredential.created_at.desc())
                .limit(100)
            ).all()
            return {
                "items": [
                    {
                        "id": row.id,
                        "client_id": row.client_id,
                        "description": row.description,
                        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                        "revoked": bool(row.revoked_at),
                    }
                    for row in rows
                ],
                "next_cursor": None,
                "complete": len(rows) < 100,
            }

    def deletion_status(self, p, sid):
        from wks_core.storage.retirement import BlobRetirement

        with self.sessions() as db:
            row = db.get(Source, sid)
            if not row:
                raise not_found()
            self.write(db, p, row.namespace_id, action="source:delete")
            delegation = self.grant(db, p)
            if (
                delegation
                and delegation.collection_id
                and not db.get(CollectionSource, (delegation.collection_id, sid))
            ):
                raise not_found()
            count = db.scalar(
                select(func.count(BlobRetirement.blob_id)).where(
                    BlobRetirement.source_id == sid, BlobRetirement.state == "pending"
                )
            )
            return {
                "source_id": sid,
                "state": row.state,
                "cleanup_state": "pending"
                if count
                else "complete"
                if row.deleted_at
                else "not_requested",
                "pending_objects": count,
            }

    def operator(self, db, p):
        self.grant(db, p)
        caller = db.get(Client, p.client_id)
        if p.grant_id or p.role != "client" or not caller.operator:
            raise Error("auth.forbidden", "Operator identity required", 403)
        return caller

    def client_create(self, p, key, body):
        if (
            not isinstance(body.get("name"), str)
            or not 1 <= len(body["name"]) <= 200
            or body.get("role", "client") not in {"client", "executor"}
            or not 1 <= len(body.get("audience", "wks")) <= 100
        ):
            raise Error("contract.invalid", "Invalid integration identity")

        def op(db):
            self.operator(db, p)
            if db.scalar(select(Client.id).where(Client.name == body["name"])):
                raise Error("client.name_conflict", "Integration name already exists", 409)
            client = Client(
                name=body["name"],
                token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                role=body.get("role", "client"),
                audience=body.get("audience", "wks"),
                operator=False,
            )
            db.add(client)
            db.flush()
            return {
                "id": client.id,
                "name": client.name,
                "role": client.role,
                "audience": client.audience,
            }

        return self.mutate(p, key, {"op": "client_create", **body}, op)

    def credential_create(
        self, p, key, client_id, description="integration credential", ttl_seconds=86400
    ):
        if not 1 <= ttl_seconds <= 31536000 or not 1 <= len(description) <= 200:
            raise Error("contract.invalid", "Invalid credential lifetime or description")

        def op(db):
            self.operator(db, p)
            caller = db.get(Client, client_id)
            if not caller or not caller.active:
                raise not_found()
            token = secrets.token_urlsafe(48)
            row = ClientCredential(
                client_id=client_id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                description=description,
                expires_at=now() + timedelta(seconds=ttl_seconds),
            )
            db.add(row)
            db.flush()
            # Return the raw credential once; the receipt stores only its public identity.
            return {
                "id": row.id,
                "client_id": client_id,
                "token": token,
                "expires_at": row.expires_at.isoformat(),
            }

        # Credentials intentionally have no replayable secret receipt. A key reserves issuance once.
        with self.sessions() as db:
            self.operator(db, p)
        previous = self.receipt(
            p,
            key,
            {
                "op": "credential_create",
                "client_id": client_id,
                "description": description,
                "ttl_seconds": ttl_seconds,
            },
        )
        if previous:
            return {**previous, "token": None, "secret_available": False}
        secret = {}

        def redacted(db):
            result = op(db)
            secret["token"] = result.pop("token")
            return {**result, "secret_available": True}

        result = self.mutate(
            p,
            key,
            {
                "op": "credential_create",
                "client_id": client_id,
                "description": description,
                "ttl_seconds": ttl_seconds,
            },
            redacted,
        )
        return {**result, "token": secret.get("token"), "secret_available": bool(secret)}

    def credential_revoke(self, p, key, identity):
        command = {"op": "credential_revoke", "id": identity}
        previous = self.receipt(p, key, command)
        if previous:
            return previous
        with self.sessions() as db:
            self.operator(db, p)
            row = db.get(ClientCredential, identity)
            if not row:
                raise not_found()

        def op(db):
            self.operator(db, p)
            row = db.get(ClientCredential, identity)
            if not row:
                raise not_found()
            self.privacy_ledger.append(
                "credential_revoked", row.client_id, {"token_hash": row.token_hash}
            )
            row.revoked_at = now()
            return {"id": identity, "revoked": True}

        return self.mutate(p, key, command, op)

    def events_list(self, p, namespace_id, page_size=50, cursor=None):
        size = min(max(page_size, 1), 100)
        context = {"op": "events", "namespace_id": namespace_id, "page_size": size}
        state = self.cursor(p, context, cursor)
        with self.sessions() as db:
            self.write(db, p, namespace_id, action="events:read")
            g = self.grant(db, p)
            query = select(Event).where(Event.namespace_id == namespace_id)
            if g and g.collection_id:
                query = query.where(
                    Event.payload["source_id"].astext.in_(
                        select(CollectionSource.source_id).where(
                            CollectionSource.collection_id == g.collection_id
                        )
                    )
                )
            if state["position"]:
                date, identity = state["position"]
                from datetime import datetime

                from sqlalchemy import tuple_

                query = query.where(
                    tuple_(Event.created_at, Event.id) > (datetime.fromisoformat(date), identity)
                )
            rows = db.scalars(query.order_by(Event.created_at, Event.id).limit(size + 1)).all()
            return {
                "items": [
                    {
                        "id": e.id,
                        "type": e.type,
                        "payload": e.payload,
                        "occurred_at": e.created_at.isoformat(),
                    }
                    for e in rows[:size]
                ],
                "next_cursor": self.cursor(
                    p,
                    context,
                    None,
                    position=[rows[size - 1].created_at.isoformat(), rows[size - 1].id],
                )
                if len(rows) > size
                else None,
            }

    def source_history(self, p, sid, page_size=50, cursor=None):
        from datetime import datetime

        from sqlalchemy import tuple_

        size = min(max(page_size, 1), 100)
        context = {"op": "source_history", "source_id": sid, "page_size": size}
        state = self.cursor(p, context, cursor)
        position = state["position"] or {}
        watermark = state["watermark"] or now().isoformat()
        with self.sessions() as db:
            self.source(db, p, sid)
            g = self.grant(db, p)
            versions = select(SourceVersion).where(SourceVersion.source_id == sid)
            if g and not g.namespace_id:
                versions = versions.where(SourceVersion.id.in_(g.source_version_ids))
            ids = versions.with_only_columns(SourceVersion.id)
            pages = {}
            following = {}
            for name, model in (("representations", Representation), ("operations", ProcessingRun)):
                query = select(model).where(
                    model.source_version_id.in_(ids),
                    model.created_at <= datetime.fromisoformat(watermark),
                )
                previous = position.get(name)
                if previous:
                    query = query.where(
                        tuple_(model.created_at, model.id)
                        > (datetime.fromisoformat(previous[0]), previous[1])
                    )
                rows = (
                    []
                    if position.get(name + "_complete")
                    else db.scalars(
                        query.order_by(model.created_at, model.id).limit(size + 1)
                    ).all()
                )
                pages[name] = rows[:size]
                following[name] = (
                    [
                        rows[min(len(rows), size) - 1].created_at.isoformat(),
                        rows[min(len(rows), size) - 1].id,
                    ]
                    if rows
                    else previous
                )
                following[name + "_complete"] = len(rows) <= size
            reps, runs = pages["representations"], pages["operations"]
            complete = following["representations_complete"] and following["operations_complete"]
            return {
                "source_id": sid,
                "representations": [
                    {
                        "id": r.id,
                        "source_version_id": r.source_version_id,
                        "schema_version": r.schema_version,
                        "availability": r.availability,
                    }
                    for r in reps
                ],
                "operations": [
                    {
                        "id": r.id,
                        "source_version_id": r.source_version_id,
                        "status": r.status,
                        "error_code": r.error_code,
                    }
                    for r in runs
                ],
                "bounded": True,
                "complete": complete,
                "next_cursor": None
                if complete
                else self.cursor(p, context, None, position=following, watermark=watermark),
            }

    def versions_list(self, p, sid, page_size=50, cursor=None):
        size = min(max(page_size, 1), 100)
        context = {"op": "versions", "source_id": sid, "page_size": size}
        state = self.cursor(p, context, cursor)
        with self.sessions() as db:
            self.source(db, p, sid)
            grant = self.grant(db, p)
            query = select(SourceVersion).where(SourceVersion.source_id == sid)
            if grant and not grant.namespace_id:
                query = query.where(SourceVersion.id.in_(grant.source_version_ids))
            watermark = state["watermark"]
            if watermark is None:
                watermark = (
                    db.scalar(query.with_only_columns(func.max(SourceVersion.revision_no))) or 0
                )
            query = query.where(SourceVersion.revision_no <= watermark)
            if state["position"] is not None:
                query = query.where(SourceVersion.revision_no > state["position"])
            rows = db.scalars(query.order_by(SourceVersion.revision_no).limit(size + 1)).all()
            items = [
                {
                    "id": row.id,
                    "revision_number": row.revision_no,
                    "committed": row.committed,
                    "representation_id": row.current_representation_id,
                }
                for row in rows[:size]
            ]
            complete = len(rows) <= size
            return {
                "items": items,
                "complete": complete,
                "next_cursor": None
                if complete
                else self.cursor(
                    p, context, None, position=rows[size - 1].revision_no, watermark=watermark
                ),
            }
