"""Scoped lexical search with immutable corpus watermarks and bounded query cost."""

import secrets
import unicodedata

from sqlalchemy import (
    BigInteger,
    Double,
    Text,
    and_,
    case,
    cast,
    exists,
    func,
    literal,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from wks_core.domain.models import Error
from wks_core.domain.searching import plain_headline, snippet
from wks_core.storage.database import Representation, Segment, Source, SourceVersion
from wks_core.storage.stabilization import SourceMetadataRevision


class SearchService:
    def search(self, p, body):
        query = unicodedata.normalize("NFC", body["query"]).strip()
        if (
            not query
            or len(query) > 1000
            or body.get("mode", "lexical")
            not in {"lexical", "phrase", "exact", "fuzzy", "proximity"}
        ):
            raise Error(
                "search.query_invalid", "Use a nonempty lexical query (max 1000 characters)"
            )
        filters = body.get("filters") or {}
        mode = body.get("mode", "lexical")
        if len(query.split()) > 32:
            raise Error("search.query_invalid", "Query exceeds token budget")
        size = min(body.get("page_size", 10), self.settings.search_max_page_size)
        context = {"op": "search", **{k: v for k, v in body.items() if k != "cursor"}}
        state = self.cursor(p, context, body.get("cursor"))
        with self.sessions.begin() as budget:
            self.grant(budget, p, "search")
            self.rate_limit(budget, p, "search:query")
        with self.sessions() as db:
            clauses, g = self.visible(db, p, "search")
            db.execute(text("SET LOCAL statement_timeout = '3000ms'"))
            language = {"en": "wks_english", "simple": "wks_simple", "auto": "wks_simple"}.get(
                body.get("language"), "wks_portuguese"
            )
            query_function = (
                func.phraseto_tsquery if mode == "phrase" else func.websearch_to_tsquery
            )
            tsq = query_function(text(f"'{language}'::regconfig"), query)
            simple_query = query_function(text("'wks_simple'::regconfig"), query)
            if mode == "proximity":
                terms = query.split()
                distance = body.get("distance", 5)
                if len(terms) != 2 or not isinstance(distance, int) or not 1 <= distance <= 20:
                    raise Error(
                        "search.query_invalid",
                        "Proximity requires two ordered terms and distance 1..20",
                    )

                def near(config):
                    left = func.plainto_tsquery(config, terms[0])
                    right = func.plainto_tsquery(config, terms[1])
                    value = func.tsquery_phrase(left, right, 1)
                    for gap in range(2, distance + 1):
                        value = value.op("||")(func.tsquery_phrase(left, right, gap))
                    return value

                tsq = near(text(f"'{language}'::regconfig"))
                simple_query = near(text("'wks_simple'::regconfig"))
            if mode not in {"exact", "fuzzy"} and db.scalar(select(func.numnode(tsq))) == 0:
                return {
                    "query": query,
                    "items": [],
                    "next_cursor": None,
                    "warning": "query_not_indexable",
                    "index_kind": "lexical_postgresql_fts",
                }
            scope_watermark = (
                select(Source.namespace_id, func.max(Representation.index_generation))
                .join(SourceVersion, Representation.source_version_id == SourceVersion.id)
                .join(Source, SourceVersion.source_id == Source.id)
                .where(*clauses)
                .group_by(Source.namespace_id)
            )
            watermark = (
                state["watermark"]
                if state["watermark"] is not None
                else dict(db.execute(scope_watermark).all())
            )
            if state["watermark"] is None:
                metadata_watermark = (
                    select(Source.namespace_id, func.max(SourceMetadataRevision.generation))
                    .join(SourceVersion, SourceVersion.source_id == Source.id)
                    .join(SourceMetadataRevision, SourceMetadataRevision.source_id == Source.id)
                    .where(*clauses)
                    .group_by(Source.namespace_id)
                )
                for namespace_id, generation in db.execute(metadata_watermark):
                    watermark[namespace_id] = max(watermark.get(namespace_id, 0), generation)
            namespace_limit = cast(
                literal(watermark, type_=JSONB)[Source.namespace_id].astext, BigInteger
            )
            metadata = (
                select(SourceMetadataRevision.value)
                .where(
                    SourceMetadataRevision.source_id == Source.id,
                    SourceMetadataRevision.generation <= namespace_limit,
                )
                .order_by(
                    SourceMetadataRevision.generation.desc(), SourceMetadataRevision.revision.desc()
                )
                .limit(1)
                .correlate(Source)
                .scalar_subquery()
            )
            metadata_text = func.concat(
                func.coalesce(metadata["title"].astext, Source.title),
                " ",
                func.coalesce(metadata["description"].astext, Source.description),
                " ",
                func.coalesce(cast(metadata["tags"], Text), cast(Source.tags, Text)),
            )
            is_metadata = Segment.origin_kind == "metadata"
            content = case((is_metadata, metadata_text), else_=Segment.text)
            metadata_vector = func.to_tsvector(text("'wks_simple'::regconfig"), metadata_text)
            normal_match = or_(
                Segment.search_vector.op("@@")(tsq), Segment.simple_vector.op("@@")(simple_query)
            )
            normal_rank = func.greatest(
                func.ts_rank_cd(Segment.search_vector, tsq, 32),
                func.ts_rank_cd(Segment.simple_vector, simple_query, 32),
            )
            if mode == "fuzzy":
                db.execute(
                    text("SELECT set_config('pg_trgm.word_similarity_threshold','0.5',true)")
                )
                if len(query) < 3:
                    raise Error(
                        "search.query_invalid", "Fuzzy query requires at least three characters"
                    )
                normal_match = Segment.text.op("%>")(query)
                normal_rank = func.word_similarity(query, Segment.text)
                meta_match = metadata_text.op("%>")(query)
                meta_rank = func.word_similarity(query, metadata_text)
            elif mode == "exact":
                escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                normal_match = Segment.text.ilike("%" + escaped + "%", escape="\\")
                normal_rank = literal(1.0)
                meta_match = metadata_text.ilike("%" + escaped + "%", escape="\\")
                meta_rank = literal(1.0)
            else:
                meta_match = metadata_vector.op("@@")(simple_query)
                meta_rank = func.ts_rank_cd(
                    func.setweight(metadata_vector, text("'A'::\"char\"")), simple_query, 32
                )
            match = or_(and_(is_metadata, meta_match), and_(~is_metadata, normal_match))
            rank = cast(case((is_metadata, meta_rank), else_=normal_rank), Double).label("rank")
            stmt = (
                select(Segment, Source, SourceVersion, rank, content, metadata["title"].astext)
                .join(Representation, Segment.representation_id == Representation.id)
                .join(SourceVersion, Representation.source_version_id == SourceVersion.id)
                .join(Source, SourceVersion.source_id == Source.id)
                .where(
                    *clauses,
                    Segment.namespace_id == Source.namespace_id,
                    match,
                    Representation.published_at.is_not(None),
                )
            )
            stmt = self.filters(
                stmt,
                {key: value for key, value in filters.items() if key not in {"tags", "metadata"}},
            )
            if filters.get("tags"):
                stmt = stmt.where(metadata["tags"].contains(filters["tags"]))
            if filters.get("metadata"):
                if set(filters["metadata"]) - {"category", "document_type", "declared_language"}:
                    raise Error("search.filter_invalid", "Metadata filter is not indexed")
                stmt = stmt.where(metadata["metadata"].contains(filters["metadata"]))
            if filters.get("processor"):
                stmt = stmt.where(Representation.producer["name"].astext == filters["processor"])
            if filters.get("availability"):
                stmt = stmt.where(Representation.availability == filters["availability"])
            if filters.get("language"):
                stmt = stmt.where(Segment.locale == filters["language"])
            per_namespace_limit = cast(
                literal(watermark, type_=JSONB)[Representation.namespace_id].astext, BigInteger
            )
            stmt = stmt.where(Representation.index_generation <= per_namespace_limit)
            if filters.get("representation", "current_published") == "current_published":
                from sqlalchemy.orm import aliased

                newer = aliased(Representation)
                stmt = stmt.where(
                    ~exists(
                        select(newer.id).where(
                            newer.source_version_id == SourceVersion.id,
                            newer.index_generation <= per_namespace_limit,
                            newer.index_generation > Representation.index_generation,
                        )
                    )
                )
                if not g or g.namespace_id:
                    nv, nr = aliased(SourceVersion), aliased(Representation)
                    stmt = stmt.where(
                        ~exists(
                            select(nv.id)
                            .join(nr, nr.source_version_id == nv.id)
                            .where(
                                nv.source_id == Source.id,
                                nv.revision_no > SourceVersion.revision_no,
                                nr.index_generation <= per_namespace_limit,
                            )
                        )
                    )
            facet_stmt = stmt
            if state["position"]:
                last_rank, last_id = state["position"]
                stmt = stmt.where(
                    or_(rank < last_rank, and_(rank == last_rank, Segment.id > last_id))
                )
            rows = db.execute(stmt.order_by(rank.desc(), Segment.id).limit(size + 1)).all()
            items, chars = [], 0
            for seg, src, ver, score, matched_text, title in rows[:size]:
                excerpt, positions = snippet(matched_text, query)
                if mode in {"lexical", "phrase", "proximity"}:
                    marker = "WKS" + secrets.token_hex(16)
                    start_marker, end_marker = marker + "START", marker + "END"
                    headline_config = (
                        "wks_simple"
                        if seg.origin_kind == "metadata"
                        or body.get("language") == "auto"
                        or seg.locale != body.get("language", "pt")
                        else language
                    )
                    headline_query = simple_query if headline_config == "wks_simple" else tsq
                    marked = db.scalar(
                        select(
                            func.ts_headline(
                                text(f"'{headline_config}'::regconfig"),
                                matched_text,
                                headline_query,
                                f"StartSel={start_marker}, StopSel={end_marker}, MaxWords=40, MinWords=12, MaxFragments=1",
                            )
                        )
                    )
                    excerpt, positions = plain_headline(marked, start_marker, end_marker)
                    excerpt = excerpt[:600]
                    positions = [[lo, hi] for lo, hi in positions if hi <= len(excerpt)]

                if items and chars + len(excerpt) > self.settings.search_max_response_chars:
                    break
                chars += len(excerpt)
                items.append(
                    {
                        "source_id": src.id,
                        "source_version_id": ver.id,
                        "representation_id": seg.representation_id,
                        "segment_id": seg.id,
                        "segment_ref": f"wks://representations/{seg.representation_id}/segments/{seg.id}",
                        "title": title or src.title,
                        "snippet": excerpt,
                        "match_positions": positions,
                        "match_origin": "metadata" if seg.origin_kind == "metadata" else "text",
                        "rank": float(score),
                        "locator": seg.locator,
                        "origin_kind": seg.origin_kind,
                        "asset_refs": seg.asset_refs,
                    }
                )
            facets = None
            if body.get("include_facets"):
                facets = {"media_types": {}, "availability": {}}
                for name, column in (
                    ("media_types", SourceVersion.media_type),
                    ("availability", Representation.availability),
                ):
                    facet_rows = db.execute(
                        facet_stmt.with_only_columns(column, func.count(func.distinct(Source.id)))
                        .group_by(column)
                        .limit(100)
                    ).all()
                    facets[name] = {value or "unknown": count for value, count in facet_rows}
            nxt = None
            if items and len(rows) > len(items):
                last = rows[len(items) - 1]
                nxt = self.cursor(
                    p, context, None, position=[float(last[3]), last[0].id], watermark=watermark
                )
            return {
                "query": query,
                "scope": "resolved_server_side",
                "index_kind": "lexical_postgresql_fts",
                "items": items,
                "next_cursor": nxt,
                "complete": nxt is None,
                "index_generations": watermark,
                "content_trust": "untrusted_source_data",
                **(
                    {"facets": facets, "facets_scope": "authorized_matching_sources"}
                    if facets is not None
                    else {}
                ),
            }

    def suggestions(self, p, body):
        import difflib
        import re

        query = body["query"].strip()
        if len(query) < 3 or len(query) > 100:
            raise Error("search.query_invalid", "Suggestion term length must be 3..100")
        result = self.search(p, {**body, "mode": "fuzzy", "page_size": 20, "cursor": None})
        candidates = {}
        for hit in result["items"]:
            for word in re.findall(r"[\wÀ-ÿ]+", hit["snippet"])[:100]:
                if len(word) < 3:
                    continue
                score = difflib.SequenceMatcher(None, query.casefold(), word.casefold()).ratio()
                if score >= 0.6 and word.casefold() != query.casefold():
                    candidates[word] = max(candidates.get(word, 0), score)
        return {
            "query": query,
            "suggestions": [
                {"text": word, "score": score, "origin": "authorized_lexical_candidates"}
                for word, score in sorted(candidates.items(), key=lambda pair: (-pair[1], pair[0]))[
                    :10
                ]
            ],
            "scope": "resolved_server_side",
            "complete": result["next_cursor"] is None,
        }
