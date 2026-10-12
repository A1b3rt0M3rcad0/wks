"""Keyset reads over normalized blocks, with bounded exact JSON block resolution."""

import json

from sqlalchemy import Text, func, select
from wks_core.application.utilities import markdown
from wks_core.domain.models import not_found
from wks_core.storage.database import Audit
from wks_core.storage.stabilization import RepresentationBlock


class ReadingService:
    def read(self, p, rid, page_size=20, cursor=None, outline=False):
        size = min(max(page_size, 1), self.settings.search_max_page_size)
        context = {"op": "outline" if outline else "read", "rid": rid, "page_size": size}
        state = self.cursor(p, context, cursor)
        after = state["position"] if state["position"] is not None else -1
        budget = self.settings.search_max_response_chars
        with self.sessions() as db:
            representation = self.representation(db, p, rid)
            table = RepresentationBlock
            # Large payloads are previewed in PostgreSQL before crossing the connection.
            length = func.octet_length(table.value.cast(Text))
            preview = func.jsonb_build_object(
                "id",
                table.block_id,
                "type",
                table.kind,
                "ordinal",
                table.ordinal,
                "text",
                func.left(table.value["text"].astext, min(2000, budget // 4)),
                "locator",
                table.value["locator"],
                "parent_id",
                table.parent_id,
                "asset_refs",
                table.value["asset_refs"],
                "data",
                func.jsonb_build_object(
                    "preview_only",
                    True,
                    "full_block_ref",
                    "wks://representations/" + rid + "/blocks/" + table.block_id,
                ),
            )
            from sqlalchemy import case

            value = case((length > budget // 2, preview), else_=table.value)
            rows = db.execute(
                select(table.ordinal, value)
                .where(table.representation_id == rid, table.ordinal > after)
                .order_by(table.ordinal)
                .limit(size + 1)
            ).all()
            output, consumed, last = [], 0, after
            for ordinal, block in rows[:size]:
                if outline:
                    block = {
                        k: block.get(k)
                        for k in ("id", "type", "locator", "parent_id", "asset_refs", "ordinal")
                    } | {"text": block.get("text", "")[:120]}
                length = len(json.dumps(block, ensure_ascii=False))
                if output and consumed + length > budget:
                    break
                if length > budget:
                    block = {
                        "id": block["id"],
                        "type": block["type"],
                        "ordinal": ordinal,
                        "text": "",
                        "locator": {},
                        "data": {
                            "preview_only": True,
                            "full_block_ref": f"wks://representations/{rid}/blocks/{block['id']}",
                        },
                    }
                    length = len(json.dumps(block))
                output.append(block)
                consumed += length
                last = ordinal
            more = any(ordinal > last for ordinal, _ in rows)
            next_cursor = self.cursor(p, context, None, position=last) if more else None
            db.add(
                Audit(
                    actor=p.client_id,
                    action="outline" if outline else "read",
                    resource=rid,
                    outcome="allowed",
                )
            )
            db.commit()
            return {
                "representation_id": rid,
                "source_version_id": representation.source_version_id,
                "index_generation": representation.index_generation,
                "representation_schema_version": representation.schema_version,
                "blocks": output,
                "markdown": markdown(output),
                "coverage": representation.coverage,
                "warnings": representation.warnings,
                "next_cursor": next_cursor,
                "complete": not more,
                "content_trust": "untrusted_source_data",
            }

    def representation_metadata(self, p, rid):
        with self.sessions() as db:
            representation = self.representation(db, p, rid)
            count = db.scalar(
                select(func.count(RepresentationBlock.ordinal)).where(
                    RepresentationBlock.representation_id == rid
                )
            )
            return {
                "id": rid,
                "source_version_id": representation.source_version_id,
                "schema_version": representation.schema_version,
                "producer": representation.producer,
                "artifact_digest": representation.artifact_digest,
                "index_generation": representation.index_generation,
                "availability": representation.availability,
                "coverage": representation.coverage,
                "warnings": representation.warnings,
                "block_count": count,
                "published_at": representation.published_at.isoformat(),
                "ref": f"wks://representations/{rid}",
            }

    def block(self, p, rid, block_id, offset=0, page_chars=None):
        from sqlalchemy import Text

        page_chars = page_chars or min(8000, self.settings.search_max_response_chars)
        if offset < 0 or not 1 <= page_chars <= self.settings.search_max_response_chars:
            from wks_core.domain.models import Error

            raise Error("contract.invalid", "Invalid block page bounds")
        with self.sessions() as db:
            self.representation(db, p, rid)
            table = RepresentationBlock
            raw = table.value.cast(Text)
            row = db.execute(
                select(
                    func.substr(raw, offset + 1, page_chars), func.length(raw), table.checksum
                ).where(table.representation_id == rid, table.block_id == block_id)
            ).first()
            if not row:
                raise not_found()
            value, total, checksum = row
            complete = offset + len(value) >= total
            return {
                "representation_id": rid,
                "block_id": block_id,
                "encoding": "json",
                "content": value,
                "offset": offset,
                "total_chars": total,
                "next_offset": None if complete else offset + len(value),
                "complete": complete,
                "checksum": checksum,
                "content_trust": "untrusted_source_data",
            }
