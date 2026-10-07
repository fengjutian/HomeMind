"""Stage F acceptance tests: the family document knowledge pipeline.

The design point these tests defend is the boundary between *citing* and
*asserting*. A document's contents must never become a family memory: an
agent answering "where is the warranty?" cites a chunk, while "when is
Dad's birthday?" reads a memory the family recorded. Collapsing the two
would let a forwarded message become a "fact" with no author.

Also covered: the four supported formats, an unreadable file being
recorded rather than fatal, chunk-level idempotence, and permission being
re-checked at *query* time.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.knowledge_documents import (
    DOC_STATUS_FAILED,
    DOC_STATUS_INDEXED,
    DOC_STATUS_UNSUPPORTED,
    KnowledgeRepo,
)
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.knowledge import KnowledgeManager
from homemind.infra.family.knowledge_parsers import (
    UnsupportedDocument,
    chunk_text,
    normalize_text,
    parse,
    parser_for,
)
from homemind.infra.family.manager import FamilyManager, MemberRole, SpaceType
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


class _FakeEmbedding:
    name = "fake/embedding"

    def __init__(self) -> None:
        self.calls = 0

    def embed_text(self, text: str) -> list[float]:
        self.calls += 1
        # Deterministic, dimension-stable, and different per chunk so a
        # test can tell which one was embedded.
        seed = float(len(text) % 7) or 1.0
        return [seed, seed * 2.0, seed * 3.0]


class _Ctx:
    def __init__(self, tmp_path: Path) -> None:
        self.workdir = tmp_path
        pool = SqlitePool(tmp_path / "octop.db")
        run_migrations(pool)
        run_homemind_migrations(pool)
        with pool.transaction() as conn:
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, locale, "
                "created_at) VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1), "
                "(2, 'mama', 'x', 'user', 0, 'zh', 1)"
            )
        self.owner = User(id=1, username="papa", role=Role.USER, display_name="papa")
        self.sibling = User(id=2, username="mama", role=Role.USER, display_name="mama")
        self.family = FamilyManager(FamilyRepo(pool))
        fam = self.family.create_family(
            self.owner, name="Happy", timezone="Asia/Shanghai", locale="zh"
        )
        self.family_id = fam.id
        self.services = HomeMindServices.from_pool(pool)
        self.repo = KnowledgeRepo(pool)
        self.assets = FamilyAssetManager(self.family.repo, self.services.family_asset_repo)
        self.manager = KnowledgeManager(self.family, self.assets, self.repo)
        self.pool = pool

    def seed_document(
        self,
        name: str,
        content: str,
        *,
        mime_type: str = "text/plain",
        asset_type: str = "DOCUMENT",
        space_id: str | None = None,
        visibility: str = "FAMILY",
    ) -> Any:
        path = self.workdir / name
        path.write_text(content, encoding="utf-8")
        return self.services.family_asset_repo.upsert_asset(
            family_id=self.family_id,
            source_id=None,
            space_id=space_id,
            asset_type=asset_type,
            name=name,
            uri=path.resolve().as_uri(),
            mime_type=mime_type,
            size_bytes=path.stat().st_size,
            content_hash=f"hash-{name}",
            captured_at=None,
            metadata_json="{}",
            created_by=1,
            visibility=visibility,
        )

    def close(self) -> None:
        self.pool.close()


# ------------------------------------------------------------- parsers


def test_parser_selection_follows_the_extension(tmp_path: Path) -> None:
    for name, expected in (
        ("a.txt", "text"),
        ("a.md", "text"),
        ("a.pdf", "pdf"),
        ("a.docx", "docx"),
        ("a.mp4", None),
        ("a", None),
    ):
        assert parser_for(tmp_path / name) == expected, name


def test_text_is_normalised_but_paragraphs_survive(tmp_path: Path) -> None:
    assert normalize_text("a  b\r\nc\n\n\nd   e") == "a b c\n\nd e"


def test_chunking_splits_on_paragraphs() -> None:
    text = "\n\n".join(f"Paragraph {i} with some words." for i in range(10))
    chunks = chunk_text(text, size=120, overlap=0)
    assert len(chunks) > 1
    assert all(chunk.strip() for chunk in chunks)


def test_chunking_an_oversized_paragraph_still_makes_progress() -> None:
    chunks = chunk_text("x" * 5000, size=500, overlap=50)
    assert len(chunks) >= 9
    assert all(len(chunk) <= 500 for chunk in chunks)


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_text("   \n\n  ") == []


def test_txt_parses_with_paragraph_locators(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("First note.\n\nSecond note about the warranty.", encoding="utf-8")
    parsed = parse(path)
    assert "warranty" in parsed.text
    assert parsed.chunks
    assert parsed.chunks[0].locator == "paragraph 1"


def test_docx_parses_without_python_docx(tmp_path: Path) -> None:
    """A .docx is a zip of XML; reading it directly avoids an optional
    dependency being silently unavailable on a NAS."""
    path = tmp_path / "letter.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            "<w:document><w:body>"
            "<w:p><w:r><w:t>Dear family,</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>The receipt is in the drawer &amp; safe.</w:t></w:r></w:p>"
            "</w:body></w:document>",
        )
    parsed = parse(path)
    assert "Dear family" in parsed.text
    # The XML entity must be decoded, not shown literally.
    assert "drawer & safe" in parsed.text


def test_an_unsupported_format_is_refused_clearly(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00\x01")
    with pytest.raises(UnsupportedDocument):
        parse(path)


def test_a_corrupt_docx_is_a_parse_failure(tmp_path: Path) -> None:
    path = tmp_path / "broken.docx"
    path.write_bytes(b"this is not a zip archive")
    from homemind.infra.family.knowledge_parsers import ParseFailure

    with pytest.raises(ParseFailure):
        parse(path)


# ------------------------------------------------------------ indexing


def test_indexing_produces_chunks_with_locators(tmp_path: Path) -> None:
    """Each chunk carries the locator that puts it back in the file.

    Short paragraphs are merged until the chunk target is reached — that
    is the intended behaviour, so the fixture uses paragraphs long enough
    to force a split rather than asserting a split that chunking avoids.
    """
    ctx = _Ctx(tmp_path)
    filler = " ".join(f"word{i}" for i in range(200))
    asset = ctx.seed_document(
        "notes.txt",
        f"Alpha paragraph. {filler}\n\nBeta paragraph. {filler}",
    )
    document = ctx.manager.index_asset(ctx.family_id, asset.id, ctx.owner)
    assert document.status == DOC_STATUS_INDEXED
    chunks = ctx.repo.list_chunks(document.id)
    assert document.chunk_count == len(chunks) >= 2
    assert all(c.locator is not None for c in chunks)
    assert chunks[0].locator == "paragraph 1"
    assert "Alpha" in chunks[0].text
    ctx.close()


def test_an_unsupported_file_is_recorded_not_raised(tmp_path: Path) -> None:
    """One unreadable file in a library is visible, not an outage."""
    ctx = _Ctx(tmp_path)
    asset = ctx.seed_document(
        "clip.mp4",
        "\x00\x01",
        mime_type="video/mp4",
        asset_type="VIDEO",
    )
    document = ctx.manager.index_asset(ctx.family_id, asset.id, ctx.owner)
    assert document.status == DOC_STATUS_UNSUPPORTED
    assert "not a supported format" in (document.error or "")
    ctx.close()


def test_a_missing_file_is_recorded_as_failed(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    asset = ctx.seed_document("gone.txt", "content")
    (ctx.workdir / "gone.txt").unlink()
    document = ctx.manager.index_asset(ctx.family_id, asset.id, ctx.owner)
    assert document.status == DOC_STATUS_FAILED
    assert document.error
    ctx.close()


def test_reindexing_an_unchanged_file_is_a_no_op(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    asset = ctx.seed_document("notes.txt", "Stable content here.")
    first = ctx.manager.index_asset(ctx.family_id, asset.id, ctx.owner)
    second = ctx.manager.index_asset(ctx.family_id, asset.id, ctx.owner)
    assert second.id == first.id
    assert second.updated_at >= first.updated_at
    assert len(ctx.repo.list_chunks(first.id)) == 1
    ctx.close()


def test_changing_the_file_replaces_the_chunks(tmp_path: Path) -> None:
    """No duplicates, no orphans: the swap is atomic."""
    ctx = _Ctx(tmp_path)
    filler = " ".join(f"word{i}" for i in range(200))
    asset = ctx.seed_document("notes.txt", f"Original text. {filler}")
    first = ctx.manager.index_asset(ctx.family_id, asset.id, ctx.owner)
    assert len(ctx.repo.list_chunks(first.id)) >= 1

    (ctx.workdir / "notes.txt").write_text(
        f"Rewritten.\n\nSecond paragraph. {filler}",
        encoding="utf-8",
    )
    ctx.services.family_asset_repo.upsert_asset(
        family_id=ctx.family_id,
        source_id=None,
        space_id=None,
        asset_type="DOCUMENT",
        name="notes.txt",
        uri=asset.uri,
        mime_type="text/plain",
        size_bytes=100,
        content_hash="hash-changed",
        captured_at=None,
        metadata_json="{}",
        created_by=1,
        visibility="FAMILY",
    )
    second = ctx.manager.index_asset(ctx.family_id, asset.id, ctx.owner)
    assert second.id == first.id
    chunks = ctx.repo.list_chunks(second.id)
    # The swap is a replace, not an append: the new text is present and
    # the old one is gone.
    assert "Rewritten" in chunks[0].text
    assert not any("Original text" in chunk.text for chunk in chunks)
    ctx.close()


def test_embeddings_are_stored_with_provenance(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    asset = ctx.seed_document("notes.txt", "Content worth vectorising.")
    embedding = _FakeEmbedding()
    document = ctx.manager.index_asset(
        ctx.family_id,
        asset.id,
        ctx.owner,
        embedding=embedding,
    )
    chunk = ctx.repo.list_chunks(document.id)[0]
    assert chunk.embedding is not None
    assert chunk.embedding_model == "fake/embedding"
    assert chunk.embedding_dimensions == 3
    assert embedding.calls == 1
    ctx.close()


def test_an_unchanged_chunk_reuses_its_vector(tmp_path: Path) -> None:
    """Editing one paragraph should not re-embed the untouched ones."""
    ctx = _Ctx(tmp_path)
    filler_a = " ".join(f"alpha{i}" for i in range(200))
    filler_b = " ".join(f"beta{i}" for i in range(200))
    asset = ctx.seed_document(
        "notes.txt",
        f"Keep me. {filler_a}\n\nAlso keep me. {filler_b}",
    )
    embedding = _FakeEmbedding()
    ctx.manager.index_asset(ctx.family_id, asset.id, ctx.owner, embedding=embedding)
    before = embedding.calls
    assert before >= 2

    (ctx.workdir / "notes.txt").write_text(
        f"Keep me. {filler_a}\n\nChanged paragraph. {filler_b}",
        encoding="utf-8",
    )
    ctx.manager.index_asset(ctx.family_id, asset.id, ctx.owner, embedding=embedding)
    # Only the changed chunk costs a call; the untouched one reused its vector.
    assert embedding.calls - before < before
    ctx.close()


# -------------------------------------------------------------- search


def test_search_returns_citations(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    asset = ctx.seed_document(
        "warranty.txt",
        "The television warranty expires in March 2027.",
    )
    ctx.manager.index_asset(ctx.family_id, asset.id, ctx.owner)
    hits = ctx.manager.search(ctx.family_id, ctx.owner, query="warranty")
    assert len(hits) == 1
    citation = hits[0].as_citation()
    assert citation["asset_id"] == asset.id
    assert citation["file_name"] == "warranty.txt"
    assert citation["locator"] == "paragraph 1"
    assert "warranty" in citation["excerpt"].lower()
    ctx.close()


def test_search_without_a_match_returns_nothing(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    asset = ctx.seed_document("notes.txt", "Nothing relevant in here.")
    ctx.manager.index_asset(ctx.family_id, asset.id, ctx.owner)
    assert ctx.manager.search(ctx.family_id, ctx.owner, query="warranty") == []
    ctx.close()


def test_a_private_document_is_only_visible_to_its_space_owner(tmp_path: Path) -> None:
    """Visibility is re-checked at query time, not baked in at index time.

    Filtering only on write would let any member keep retrieving a private
    space's contents from an index they can no longer read.
    """
    ctx = _Ctx(tmp_path)
    teen = ctx.family.create_member(
        ctx.family_id,
        ctx.owner,
        display_name="Teen",
        role=MemberRole.CHILD,
        user_id=ctx.sibling.id,
    )
    private = ctx.family.create_space(
        ctx.family_id,
        ctx.owner,
        name="Teen private",
        space_type=SpaceType.PRIVATE,
        owner_member_id=teen.id,
    )
    asset = ctx.seed_document(
        "secret.txt",
        "The spare key is under the mat.",
        space_id=private.id,
        visibility="PRIVATE",
    )
    ctx.manager.index_asset(ctx.family_id, asset.id, ctx.sibling)
    assert ctx.manager.search(ctx.family_id, ctx.sibling, query="spare key")

    # A family manager is still not a member of that private space, so the
    # same query comes back empty even though the index row exists.
    assert ctx.manager.search(ctx.family_id, ctx.owner, query="spare key") == []
    ctx.close()


def test_search_is_scoped_to_one_family(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    other = ctx.family.create_family(
        ctx.owner,
        name="Other",
        timezone="Asia/Shanghai",
        locale="zh",
    )
    asset = ctx.seed_document("notes.txt", "Only in this family.")
    ctx.manager.index_asset(ctx.family_id, asset.id, ctx.owner)
    assert ctx.manager.search(other.id, ctx.owner, query="only") == []
    ctx.close()


def test_forgetting_a_document_removes_its_chunks(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    asset = ctx.seed_document("notes.txt", "Forget me.")
    document = ctx.manager.index_asset(ctx.family_id, asset.id, ctx.owner)
    assert ctx.repo.list_chunks(document.id)
    assert ctx.manager.forget_asset(ctx.family_id, asset.id) is True
    assert ctx.repo.list_chunks(document.id) == []
    assert ctx.manager.search(ctx.family_id, ctx.owner, query="forget") == []
    ctx.close()


def test_documents_are_listed_for_the_files_page(tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    asset = ctx.seed_document("notes.txt", "Something to index.")
    ctx.manager.index_asset(ctx.family_id, asset.id, ctx.owner)
    documents = ctx.manager.list_documents(ctx.family_id, ctx.owner)
    assert [d.asset_id for d in documents] == [asset.id]
    assert documents[0].status == DOC_STATUS_INDEXED
    ctx.close()
