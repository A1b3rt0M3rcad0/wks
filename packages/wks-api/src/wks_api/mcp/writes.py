"""Scoped knowledge mutations: identity administration and tenant selection stay outside tools."""

from anyio import to_thread
from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import JsonValue
from wks_core.domain.models import Error

from wks_api.contracts import responses as contracts
from wks_api.contracts.schemas import SourceBody, VersionBody


def install(mcp, service, boundary, resolve):
    def context(ctx, action):
        p = resolve(service, ctx)
        request = ctx.request_context.request
        hint = request.headers.get("x-wks-namespace")
        with service.sessions() as db:
            grant = service.grant(db, p, action)
            namespace_id = hint or (grant.namespace_id if grant else None)
        if not namespace_id:
            raise Error(
                "auth.context_required",
                "Configure the namespace in the trusted MCP connection",
                403,
            )
        return service.bind_context(p, namespace_id), namespace_id

    write = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    destructive = ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    )
    readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

    @mcp.tool(annotations=write)
    @boundary
    async def create_source(
        title: str,
        text: str,
        operation_key: str,
        ctx: Context,
        description: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, JsonValue] | None = None,
        external_ref: str | None = None,
    ) -> contracts.ReceiptResponse:
        """Create text in the server-selected namespace; use a stable operation key."""
        p, namespace_id = context(ctx, "source:create")
        body = SourceBody(
            namespace_id=namespace_id,
            kind="text",
            title=title,
            text=text,
            description=description,
            tags=tags or [],
            metadata=metadata or {},
            external_ref=external_ref,
        ).model_dump(mode="json", exclude_none=True)
        result = await to_thread.run_sync(lambda: service.register(p, operation_key, body))
        return contracts.ReceiptResponse.model_validate(result)

    @mcp.tool(annotations=write)
    @boundary
    async def prepare_upload(
        title: str,
        filename: str,
        byte_size: int,
        checksum_sha256: str,
        operation_key: str,
        ctx: Context,
        declared_media_type: str = "application/octet-stream",
    ) -> contracts.ReceiptResponse:
        """Create an out-of-band upload session; transfer bytes through authenticated HTTP, never base64 tool arguments."""
        p, namespace_id = context(ctx, "source:create")
        body = SourceBody(
            namespace_id=namespace_id,
            kind="upload",
            title=title,
            upload={
                "filename": filename,
                "byte_size": byte_size,
                "checksum_sha256": checksum_sha256,
                "declared_media_type": declared_media_type,
            },
        ).model_dump(mode="json", exclude_none=True)
        result = await to_thread.run_sync(lambda: service.register(p, operation_key, body))
        return contracts.ReceiptResponse.model_validate(
            {
                **result,
                "transfer": {
                    "method": "PUT",
                    "path": f"/v1/uploads/{result['upload_id']}/parts",
                    "protocol": "offset-sha256-v1",
                    "authentication": "use the existing transport credentials outside the model",
                    "inline_bytes_supported": False,
                },
            }
        )

    @mcp.tool(annotations=write)
    @boundary
    async def commit_upload(
        upload_ref: str, operation_key: str, ctx: Context
    ) -> contracts.ReceiptResponse:
        """Commit a complete verified upload after its out-of-band transfer."""
        p, _ = context(ctx, "upload:commit")
        from wks_api.mcp.server import ref_id

        identity = ref_id(upload_ref, "uploads")
        result = await to_thread.run_sync(lambda: service.commit(p, operation_key, identity))
        return contracts.ReceiptResponse.model_validate(result)

    @mcp.tool(annotations=write)
    @boundary
    async def create_version(
        source_ref: str, text: str, operation_key: str, ctx: Context
    ) -> contracts.ReceiptResponse:
        """Create an explicit immutable text revision without altering the old original."""
        p, _ = context(ctx, "source:version")
        from wks_api.mcp.server import ref_id

        sid = ref_id(source_ref, "sources")
        body = VersionBody(text=text).model_dump(mode="json", exclude_none=True)
        result = await to_thread.run_sync(lambda: service.new_version(p, operation_key, sid, body))
        return contracts.ReceiptResponse.model_validate(result)

    @mcp.tool(annotations=write)
    @boundary
    async def update_source(
        source_ref: str,
        revision: int,
        operation_key: str,
        ctx: Context,
        title: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> contracts.MetadataResponse:
        """Update source metadata using an optimistic revision; original bytes remain immutable."""
        p, _ = context(ctx, "source:update")
        from wks_api.mcp.server import ref_id

        body = {
            key: value
            for key, value in {
                "title": title,
                "description": description,
                "tags": tags,
                "metadata": metadata,
            }.items()
            if value is not None
        }
        result = await to_thread.run_sync(
            lambda: service.metadata_patch(
                p, operation_key, "source", ref_id(source_ref, "sources"), body, revision
            )
        )
        return contracts.MetadataResponse.model_validate(result)

    @mcp.tool(annotations=write)
    @boundary
    async def process_source(
        source_ref: str, operation_key: str, ctx: Context, source_version_ref: str | None = None
    ) -> contracts.ReceiptResponse:
        """Request processing under the installation's pinned policy; no processor credentials are tool inputs."""
        p, _ = context(ctx, "source:process")
        from wks_api.mcp.server import ref_id

        body = (
            {"source_version_id": ref_id(source_version_ref, "sources", tail=True)[1]}
            if source_version_ref
            else {}
        )
        result = await to_thread.run_sync(
            lambda: service.process(p, operation_key, ref_id(source_ref, "sources"), body)
        )
        return contracts.ReceiptResponse.model_validate(result)

    @mcp.tool(annotations=write)
    @boundary
    async def create_collection(
        title: str, operation_key: str, ctx: Context
    ) -> contracts.CollectionResponse:
        """Create a collection inside the trusted namespace scope."""
        p, namespace_id = context(ctx, "collection:create")
        result = await to_thread.run_sync(
            lambda: service.collection_create(
                p, operation_key, {"namespace_id": namespace_id, "title": title}
            )
        )
        return contracts.CollectionResponse.model_validate(result)

    @mcp.tool(annotations=write)
    @boundary
    async def link_source(
        collection_ref: str, source_ref: str, operation_key: str, ctx: Context
    ) -> contracts.LinkResponse:
        """Link a source without changing its namespace or delegated scope."""
        p, _ = context(ctx, "collection:write")
        from wks_api.mcp.server import ref_id

        result = await to_thread.run_sync(
            lambda: service.collection_link(
                p,
                operation_key,
                ref_id(collection_ref, "collections"),
                ref_id(source_ref, "sources"),
            )
        )
        return contracts.LinkResponse.model_validate(result)

    @mcp.tool(annotations=write)
    @boundary
    async def rename_collection(
        collection_ref: str, title: str, revision: int, operation_key: str, ctx: Context
    ) -> contracts.MetadataResponse:
        """Rename a scoped collection with an optimistic revision."""
        p, _ = context(ctx, "collection:update")
        from wks_api.mcp.server import ref_id

        result = await to_thread.run_sync(
            lambda: service.metadata_patch(
                p,
                operation_key,
                "collection",
                ref_id(collection_ref, "collections"),
                {"title": title},
                revision,
            )
        )
        return contracts.MetadataResponse.model_validate(result)

    @mcp.tool(annotations=readonly)
    @boundary
    async def destructive_preflight(
        source_ref: str, ctx: Context, action: str = "delete"
    ) -> contracts.PreflightResponse:
        """Return exact destructive impact and a short-lived confirmation tied to the source revision and scope."""
        p, _ = context(ctx, "source:" + action)
        from wks_api.mcp.server import ref_id

        result = await to_thread.run_sync(
            lambda: service.preflight(p, ref_id(source_ref, "sources"), action)
        )
        return contracts.PreflightResponse.model_validate(result)

    @mcp.tool(annotations=destructive)
    @boundary
    async def delete_source(
        source_ref: str, confirmation: str, operation_key: str, ctx: Context
    ) -> contracts.LifecycleResponse:
        """Purge source content only after a matching destructive preflight."""
        p, _ = context(ctx, "source:delete")
        from wks_api.mcp.server import ref_id

        result = await to_thread.run_sync(
            lambda: service.revoke(
                p,
                operation_key,
                ref_id(source_ref, "sources"),
                purge=True,
                confirmation_token=confirmation,
            )
        )
        return contracts.LifecycleResponse.model_validate(result)

    @mcp.tool(annotations=destructive)
    @boundary
    async def revoke_source(
        source_ref: str, confirmation: str, operation_key: str, ctx: Context
    ) -> contracts.LifecycleResponse:
        """Revoke access after an explicit scope-bound preflight; preserve original bytes."""
        p, _ = context(ctx, "source:revoke")
        from wks_api.mcp.server import ref_id

        result = await to_thread.run_sync(
            lambda: service.revoke(
                p,
                operation_key,
                ref_id(source_ref, "sources"),
                confirmation_token=confirmation,
            )
        )
        return contracts.LifecycleResponse.model_validate(result)

    @mcp.tool(annotations=write)
    @boundary
    async def create_export(
        representation_ref: str, operation_key: str, ctx: Context, format: str = "markdown-single"
    ) -> contracts.ExportResponse:
        """Schedule a full immutable export; read pages are bounded previews."""
        p, _ = context(ctx, "export:create")
        from wks_api.mcp.server import ref_id

        result = await to_thread.run_sync(
            lambda: service.export_create(
                p, operation_key, ref_id(representation_ref, "representations"), format
            )
        )
        return contracts.ExportResponse.model_validate(result)

    @mcp.tool(annotations=readonly)
    @boundary
    async def get_export(export_ref: str, ctx: Context) -> contracts.ExportResponse:
        """Inspect export coverage/status and its authenticated content path."""
        p = resolve(service, ctx)
        from wks_api.mcp.server import ref_id

        result = await to_thread.run_sync(
            lambda: service.export_get(p, ref_id(export_ref, "exports"))
        )
        return contracts.ExportResponse.model_validate(result)

    @mcp.tool(annotations=readonly)
    @boundary
    async def get_write_receipt(operation_key: str, ctx: Context) -> contracts.ReceiptResponse:
        """Look up an ambiguous write without repeating the mutation."""
        p = resolve(service, ctx)
        result = await to_thread.run_sync(lambda: service.receipt(p, operation_key))
        if result is None:
            raise Error("source.not_found", "Receipt unavailable", 404)
        return contracts.ReceiptResponse.model_validate(result)

    @mcp.tool(annotations=write)
    @boundary
    async def create_bookmark(
        title: str, external_uri: str, operation_key: str, ctx: Context
    ) -> contracts.ReceiptResponse:
        """Store an external reference without fetching it; capture requires another explicit action."""
        p, namespace_id = context(ctx, "source:create")
        body = SourceBody(
            namespace_id=namespace_id, kind="bookmark", title=title, external_uri=external_uri
        ).model_dump(mode="json", exclude_none=True)
        result = await to_thread.run_sync(lambda: service.register(p, operation_key, body))
        return contracts.ReceiptResponse.model_validate(result)

    @mcp.tool(annotations=write)
    @boundary
    async def capture_bookmark(
        source_ref: str, operation_key: str, ctx: Context
    ) -> contracts.ReceiptResponse:
        """Explicitly capture a bookmark under the server's public-egress policy."""
        p, _ = context(ctx, "source:capture")
        from wks_core.storage.capture import capture_source

        from wks_api.mcp.server import ref_id

        result = await to_thread.run_sync(
            lambda: capture_source(service, p, operation_key, ref_id(source_ref, "sources"))
        )
        return contracts.ReceiptResponse.model_validate(result)

    @mcp.tool(annotations=destructive)
    @boundary
    async def abort_upload(
        upload_ref: str, operation_key: str, ctx: Context
    ) -> contracts.UploadResponse:
        """Abort an unfinished out-of-band upload without altering a committed original."""
        p, _ = context(ctx, "upload:abort")
        from wks_api.mcp.server import ref_id

        result = await to_thread.run_sync(
            lambda: service.upload_abort(p, operation_key, ref_id(upload_ref, "uploads"))
        )
        return contracts.UploadResponse.model_validate(result)

    @mcp.tool(annotations=destructive)
    @boundary
    async def unlink_source(
        collection_ref: str, source_ref: str, operation_key: str, ctx: Context
    ) -> contracts.LinkResponse:
        """Remove a collection link while retaining the source and its original bytes."""
        p, _ = context(ctx, "collection:write")
        from wks_api.mcp.server import ref_id

        result = await to_thread.run_sync(
            lambda: service.collection_link(
                p,
                operation_key,
                ref_id(collection_ref, "collections"),
                ref_id(source_ref, "sources"),
                unlink=True,
            )
        )
        return contracts.LinkResponse.model_validate(result)

    actions = {
        "create_bookmark": "source:create",
        "capture_bookmark": "source:capture",
        "abort_upload": "upload:abort",
        "unlink_source": "collection:write",
        "create_source": "source:create",
        "prepare_upload": "source:create",
        "commit_upload": "upload:commit",
        "create_version": "source:version",
        "update_source": "source:update",
        "process_source": "source:process",
        "create_collection": "collection:create",
        "link_source": "collection:write",
        "rename_collection": "collection:update",
        "destructive_preflight": "source:delete",
        "delete_source": "source:delete",
        "revoke_source": "source:revoke",
        "create_export": "export:create",
        "get_export": "export:read",
        "get_write_receipt": "operation:read",
    }
    legacy = {"search", "list_sources", "outline", "read", "read_asset", "get_processing_status"}

    @mcp._mcp_server.list_tools()
    async def scoped_tools():
        p = resolve(service, mcp.get_context())
        with service.sessions() as db:
            grant = service.grant(db, p)
            allowed = set(grant.allowed_actions) if grant else set(actions.values())
            namespace_available = (
                bool(grant.namespace_id) if grant else bool(p.context_namespace_id)
            )
        tools = await mcp.list_tools()
        for tool in tools:
            if tool.name == "read_asset":
                tool.outputSchema = contracts.AssetResponse.model_json_schema()
        return [
            tool
            for tool in tools
            if tool.name in legacy
            or (
                actions.get(tool.name) in allowed
                and (namespace_available or tool.name in {"get_export", "get_write_receipt"})
            )
        ]
