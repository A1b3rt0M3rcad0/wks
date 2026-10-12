"""Shared policy/idempotency boundary; transports never provide authority."""

import hashlib
from dataclasses import replace

from sqlalchemy import exists, or_, select, text
from wks_core.application.utilities import digest
from wks_core.domain.models import Error, Principal, not_found
from wks_core.storage.database import (
    Client,
    CollectionSource,
    Grant,
    Namespace,
    Receipt,
    Source,
    SourceVersion,
    now,
)
from wks_core.storage.stabilization import ClientCredential

ACTION_ALIASES = {"read": "source:read", "search": "search:query", "asset_read": "asset:read"}
COMMAND_ACTIONS = {
    "namespace": "namespace:create",
    "collection": "collection:create",
    "link": "collection:write",
    "register": "source:create",
    "version": "source:version",
    "commit": "upload:commit",
    "process": "source:process",
    "bulk_process": "source:process",
    "source_update": "source:update",
    "namespace_update": "namespace:update",
    "namespace_delete": "namespace:delete",
    "collection_delete": "collection:delete",
    "collection_update": "collection:update",
    "grant": "grant:create",
    "grant_revoke": "grant:revoke",
    "revoke": "source:revoke",
    "delete": "source:delete",
    "export": "export:create",
    "upload_abort": "upload:abort",
    "capture": "source:capture",
    "credential_create": "client:manage",
    "credential_revoke": "client:manage",
    "client_create": "client:manage",
}


class AccessControlService:
    def authenticate(self, token, grant_id=None):
        if not token:
            raise Error("auth.unauthenticated", "Authentication required", 401)
        with self.sessions() as db:
            hashed = hashlib.sha256(token.encode()).hexdigest()
            credential = db.scalar(
                select(ClientCredential).where(
                    ClientCredential.token_hash == hashed,
                    ClientCredential.revoked_at.is_(None),
                    or_(ClientCredential.expires_at.is_(None), ClientCredential.expires_at > now()),
                )
            )
            caller = (
                db.get(Client, credential.client_id)
                if credential
                else db.scalar(
                    select(Client).where(
                        Client.token_hash == hashed,
                        Client.active,
                        ~exists(
                            select(ClientCredential.id).where(ClientCredential.token_hash == hashed)
                        ),
                    )
                )
            )
            if not caller:
                raise Error("auth.unauthenticated", "Authentication required", 401)
            p = Principal(
                caller.id,
                caller.role,
                caller.audience,
                grant_id,
                hashed,
                auth_generation=caller.auth_generation,
            )
            self.grant(db, p)
            return p

    def grant(self, db, p, action=None):
        if self.restore_blocked or self.restore_marker.exists():
            raise Error(
                "operation.restore_incomplete",
                "Restore must be verified before serving traffic",
                503,
                True,
            )
        caller = db.get(Client, p.client_id)
        if (
            not caller
            or not caller.active
            or caller.role != p.role
            or caller.audience != p.audience
            or (p.auth_generation is not None and caller.auth_generation != p.auth_generation)
        ):
            raise Error("auth.forbidden", "Caller unavailable", 403)
        if p.credential_hash:
            credential = db.scalar(
                select(ClientCredential).where(ClientCredential.token_hash == p.credential_hash)
            )
            if credential and (
                credential.client_id != p.client_id
                or credential.revoked_at
                or (credential.expires_at and credential.expires_at <= now())
            ):
                raise Error("auth.forbidden", "Credential unavailable", 403)
            if not credential and caller.token_hash != p.credential_hash:
                raise Error("auth.forbidden", "Credential unavailable", 403)
        if p.role == "executor" and not p.grant_id:
            raise Error("auth.forbidden", "Delegation required", 403)
        if not p.grant_id:
            return None
        g = db.get(Grant, p.grant_id)
        if not g or g.caller_binding != p.client_id or g.audience != p.audience or g.revoked_at:
            raise Error("auth.forbidden", "Delegation unavailable", 403)
        if g.expires_at <= now():
            raise Error("auth.grant_expired", "Delegation expired", 403)
        issuer = db.get(Client, g.issuer)
        if not issuer or not issuer.active:
            raise Error("auth.forbidden", "Delegation unavailable", 403)
        accepted = {action, ACTION_ALIASES.get(action, action)}
        if action == "operation:read" and not g.namespace_id:
            accepted |= {"read", "source:read"}
        if action and not (accepted & set(g.allowed_actions)):
            raise Error("auth.forbidden", "Action unavailable", 403)
        return g

    def bind_context(self, p, namespace_id):
        if not namespace_id:
            return p
        with self.sessions() as db:
            grant = self.grant(db, p)
            if grant and grant.namespace_id:
                if grant.namespace_id != namespace_id:
                    raise not_found()
            elif grant:
                if not db.scalar(
                    select(SourceVersion.id)
                    .where(
                        SourceVersion.namespace_id == namespace_id,
                        SourceVersion.id.in_(grant.source_version_ids),
                    )
                    .limit(1)
                ):
                    raise not_found()
            else:
                namespace = db.get(Namespace, namespace_id)
                if not namespace or namespace.client_id != p.client_id:
                    raise not_found()
        return replace(p, context_namespace_id=namespace_id)

    def write(self, db, p, namespace_id, action=None):
        if p.context_namespace_id and p.context_namespace_id != namespace_id:
            raise not_found()
        action = action or db.info.get("wks_action", "namespace:read")
        g = self.grant(db, p, action if p.grant_id else None)
        ns = db.get(Namespace, namespace_id)
        if g:
            if (
                not ns
                or not g.namespace_id
                or g.namespace_id != namespace_id
                or ns.client_id != g.issuer
            ):
                raise not_found()
        elif p.role != "client" or not ns or ns.client_id != p.client_id:
            raise not_found()
        if (
            db.info.get("wks_action")
            and ns.state != "active"
            and action not in {"namespace:update", "namespace:delete", "collection:delete"}
        ):
            raise Error("namespace.archived", "Namespace is archived", 409)
        return ns

    def source(self, db, p, source_id, action="read", version_id=None, writing=False):
        src = (
            db.scalar(select(Source).where(Source.id == source_id).with_for_update())
            if writing
            else db.get(Source, source_id)
        )
        if (
            not src
            or src.state in {"revoked", "deleted"}
            or (p.context_namespace_id and p.context_namespace_id != src.namespace_id)
        ):
            raise not_found()
        if writing:
            g = self.grant(db, p)
            self.write(db, p, src.namespace_id)
            if g and g.collection_id and not db.get(CollectionSource, (g.collection_id, src.id)):
                raise not_found()
        else:
            g = self.grant(db, p, action)
            if g:
                if g.namespace_id:
                    if src.namespace_id != g.namespace_id:
                        raise not_found()
                    if g.collection_id and not db.get(CollectionSource, (g.collection_id, src.id)):
                        raise not_found()
                    return src
                candidates = [version_id] if version_id else g.source_version_ids
                if version_id and version_id not in g.source_version_ids:
                    raise not_found()
                if not db.scalar(
                    select(
                        exists().where(
                            SourceVersion.source_id == src.id, SourceVersion.id.in_(candidates)
                        )
                    )
                ):
                    raise not_found()
            elif db.get(Namespace, src.namespace_id).client_id != p.client_id:
                raise not_found()
        return src

    def visible(self, db, p, action):
        g = self.grant(db, p, action)
        clauses = [Source.state == "stored", Source.deleted_at.is_(None)]
        if p.context_namespace_id:
            clauses.append(Source.namespace_id == p.context_namespace_id)
        if g:
            if g.namespace_id:
                clauses.append(Source.namespace_id == g.namespace_id)
                if g.collection_id:
                    clauses.append(
                        exists(
                            select(CollectionSource.source_id).where(
                                CollectionSource.source_id == Source.id,
                                CollectionSource.collection_id == g.collection_id,
                            )
                        )
                    )
            else:
                clauses.append(SourceVersion.id.in_(g.source_version_ids))
        else:
            clauses.append(
                Source.namespace_id.in_(
                    select(Namespace.id).where(Namespace.client_id == p.client_id)
                )
            )
        return clauses, g

    def receipt(self, p, key, command=None):
        if not key or len(key) > 180:
            raise Error("operation.idempotency_required", "Idempotency-Key required (max 180)")
        with self.sessions() as db:
            g = self.grant(
                db, p, COMMAND_ACTIONS.get(command["op"]) if command else "operation:read"
            )
            row = db.get(Receipt, (p.client_id, key))
            if not row:
                return None
            if (row.authorization_scope_hash or None) != (g.scope_hash if g else None):
                raise Error("auth.forbidden", "Receipt belongs to another authorization scope", 403)
            self.receipt_context(db, p, row.result)
            if command and row.payload_hash != digest(command):
                raise Error(
                    "operation.idempotency_conflict",
                    "Key already used for a different payload",
                    409,
                )
            return row.result

    def receipt_context(self, db, p, result):
        from wks_core.storage.database import Collection, ProcessingRun, Upload

        namespace_id, source = result.get("namespace_id"), None
        for item in result.get("items", []):
            if isinstance(item, dict) and item.get("source_id"):
                self.receipt_context(db, p, item)
        source_id = result.get("source_id")
        if source_id:
            source = db.get(Source, source_id)
            namespace_id = source.namespace_id if source else None
        elif result.get("operation_id"):
            run = db.get(ProcessingRun, result["operation_id"])
            version = db.get(SourceVersion, run.source_version_id) if run else None
            source = db.get(Source, version.source_id) if version else None
            namespace_id = run.namespace_id if run else None
        elif result.get("upload_id"):
            upload = db.get(Upload, result["upload_id"])
            version = db.get(SourceVersion, upload.source_version_id) if upload else None
            source = db.get(Source, version.source_id) if version else None
            namespace_id = version.namespace_id if version else None
        elif result.get("id"):
            source = db.get(Source, result["id"])
            resource = source or db.get(Collection, result["id"]) or db.get(Namespace, result["id"])
            namespace_id = (
                resource.id
                if isinstance(resource, Namespace)
                else resource.namespace_id
                if resource
                else None
            )
        if p.context_namespace_id and namespace_id != p.context_namespace_id:
            raise not_found()
        grant = self.grant(db, p)
        if source:
            if (
                grant
                and grant.collection_id
                and not db.get(CollectionSource, (grant.collection_id, source.id))
            ):
                raise not_found()
            if source.state in {"deleted", "revoked"} and set(result) - {
                "source_id",
                "state",
                "write_outcome",
                "cleanup_state",
            }:
                raise not_found()

    def mutate(self, p, key, command, callback):
        if not key or len(key) > 180:
            raise Error("operation.idempotency_required", "Idempotency-Key required (max 180)")
        action = COMMAND_ACTIONS.get(command["op"])
        if not action:
            raise Error("auth.forbidden", "Unknown mutation policy", 403)
        if p.role != "client" and not p.grant_id:
            raise Error("auth.forbidden", "Write unavailable", 403)
        self.checkpoint("mutation.before_transaction")
        with self.sessions.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock_shared(874201)"))
            # Serializes re-delivery before reading receipt; also bounds tenant quota races.
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:client, 0))"),
                {"client": p.client_id},
            )
            g = self.grant(db, p, action)
            if g and action in {
                "namespace:create",
                "grant:create",
                "grant:revoke",
                "client:manage",
            }:
                raise Error(
                    "auth.forbidden",
                    "Delegation cannot administer identities or create scopes",
                    403,
                )
            if g and g.collection_id and action == "collection:create":
                raise Error(
                    "auth.forbidden", "Collection scope cannot create other collections", 403
                )
            if g and not g.namespace_id:
                raise Error("auth.forbidden", "Namespace-scoped write delegation required", 403)
            db.info["wks_action"] = action
            payload_hash = digest(command)
            receipt = db.get(Receipt, (p.client_id, key))
            if receipt:
                self.receipt_context(db, p, receipt.result)
                if (receipt.authorization_scope_hash or None) != (g.scope_hash if g else None):
                    raise Error(
                        "auth.forbidden", "Receipt belongs to another authorization scope", 403
                    )
                if receipt.payload_hash != payload_hash:
                    raise Error(
                        "operation.idempotency_conflict",
                        "Key already used for a different payload",
                        409,
                    )
                return receipt.result
            self.rate_limit(db, p, action, command.get("namespace_id"))
            self.checkpoint("mutation.before_callback")
            result = {**callback(db), "write_outcome": "confirmed"}
            self.checkpoint("mutation.after_callback")
            self.checkpoint("mutation.before_receipt")
            db.add(
                Receipt(
                    client_id=p.client_id,
                    operation_key=key,
                    payload_hash=payload_hash,
                    result=result,
                    authorization_scope_hash=g.scope_hash if g else None,
                )
            )
            self.checkpoint("mutation.before_commit")
            return result
