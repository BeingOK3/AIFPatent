from __future__ import annotations

import asyncio
import unittest

from idea.postgres_report import PostgreSQLReportScopeRepository


class FakeCursor:
    def __init__(self, rows=(), count=0):
        self.rows = rows
        self.count = count
        self.executions = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def execute(self, query, parameters):
        self.executions.append((query, parameters))

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return (self.count,)


class FakeConnection:
    def __init__(self, rows=(), count=0):
        self.cursor_instance = FakeCursor(rows, count)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.cursor_instance

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def close(self):
        return None


class PostgreSQLReportScopeRepositoryTests(unittest.TestCase):
    def test_scope_query_requires_ready_deep_reviewed_versions(self) -> None:
        connection = FakeConnection(rows=(("doc-1", "cv-1", "CN1A"),))

        async def connect(_dsn):
            return connection

        result = asyncio.run(
            PostgreSQLReportScopeRepository("postgresql://test", connect=connect)
            .load_ready_deep_reviewed("run-1")
        )

        sql, parameters = connection.cursor_instance.executions[0]
        self.assertIn("rdv.corpus_availability = 'READY'", sql)
        self.assertIn("rdv.deep_reviewed = TRUE", sql)
        self.assertIn("pv.state = 'READY'", sql)
        self.assertEqual(parameters, ("run-1",))
        self.assertEqual(result[0].version_id, "cv-1")


if __name__ == "__main__":
    unittest.main()
