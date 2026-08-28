"""Unit tests for RAG tool parsing, extract, and lexical search."""

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import RagChunk, RagCorpus
from app.services.rag_extract import RagExtractError, extract_text
from app.services.rag_indexer import index_corpus, search_corpus
from app.services.tool_parser import extract_tool_steps


class ToolParserRagTests(unittest.TestCase):
    def test_extract_rag_step(self):
        steps = extract_tool_steps("RAG: 报销流程是什么")
        self.assertTrue(any(s.action == "rag_query" for s in steps))
        rag = next(s for s in steps if s.action == "rag_query")
        self.assertEqual(rag.reply, "RAG: 报销流程是什么")


class RagExtractTests(unittest.TestCase):
    def test_txt(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("hello knowledge base")
            path = f.name
        try:
            self.assertIn("hello", extract_text(path))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_doc_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".doc", delete=False) as f:
            f.write("x")
            path = f.name
        try:
            with self.assertRaises(RagExtractError):
                extract_text(path)
        finally:
            Path(path).unlink(missing_ok=True)


class RagIndexerLexicalTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def test_index_and_search(self):
        db = self.Session()
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("公司报销需要先提交发票，再经财务审批。")
            path = f.name
        try:
            corpus = RagCorpus(
                id="c1",
                name="policy",
                file_path=path,
                creator="admin",
                visibility="private",
            )
            db.add(corpus)
            db.commit()
            n = index_corpus(db, corpus)
            self.assertGreater(n, 0)
            self.assertEqual(corpus.index_status, "ready")
            hits = search_corpus(db, "报销 发票", ["c1"], top_k=3)
            self.assertTrue(hits)
            self.assertIn("报销", hits[0]["snippet"])
            chunks = db.query(RagChunk).filter(RagChunk.corpus_id == "c1").all()
            self.assertTrue(chunks)
            tokens = json.loads(chunks[0].embedding or "[]")
            self.assertIsInstance(tokens, list)
        finally:
            db.close()
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
