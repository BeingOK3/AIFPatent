from __future__ import annotations

import unittest
from contextlib import contextmanager

from landscape.task_queue import PostgreSQLTaskQueue, TaskQueueError, TaskState


class Cursor:
    def __init__(self, rows=None, row=None): self.rows=rows or ([] if row is None else [row]); self.row=row
    def fetchone(self): return self.row if self.row is not None else (self.rows[0] if self.rows else None)
    def fetchall(self): return self.rows


class Connection:
    def __init__(self): self.rows={}
    def execute(self, sql, params=()):
        n=" ".join(sql.split())
        if n.startswith("INSERT INTO landscape_v4_tasks"):
            if params[0] not in self.rows:
                keys=("task_id","run_id","task_key","task_type","payload_hash","state","attempts","max_attempts","created_at","updated_at")
                values=params[:5]+("QUEUED",0,params[5],params[6],params[7])
                self.rows[params[0]]=dict(zip(keys,values,strict=True))
            return Cursor()
        if "WHERE (state='QUEUED'" in n:
            values=[row for row in self.rows.values() if row["state"]=="QUEUED" or (row["state"]=="LEASED" and row["lease_expires_at"] <= params[0])]
            return Cursor(rows=sorted(values,key=lambda row:(row["created_at"],row["task_id"]))[:params[1]])
        if n.startswith("UPDATE landscape_v4_tasks SET state='LEASED'"):
            row=self.rows[params[3]]; row.update(state="LEASED",attempts=row["attempts"]+1,lease_owner=params[0],lease_expires_at=params[1],updated_at=params[2]); return Cursor()
        if n.startswith("UPDATE landscape_v4_tasks SET state='SUCCEEDED'"):
            row=self.rows[params[1]]; row.update(state="SUCCEEDED",lease_owner=None,lease_expires_at=None,updated_at=params[0]); return Cursor()
        if n.startswith("UPDATE landscape_v4_tasks SET state=%s"):
            row=self.rows[params[3]]; row.update(state=params[0],lease_owner=None,lease_expires_at=None,last_error=params[1],updated_at=params[2]); return Cursor()
        if "WHERE run_id=%s AND task_key=%s" in n:
            return Cursor(row=next((row for row in self.rows.values() if row["run_id"]==params[0] and row["task_key"]==params[1]),None))
        if "WHERE task_id=%s" in n: return Cursor(row=self.rows.get(params[0]))
        raise AssertionError(n)


class LandscapeTaskQueueTests(unittest.TestCase):
    def setUp(self):
        self.connection=Connection()
        @contextmanager
        def connect(): yield self.connection
        self.queue=PostgreSQLTaskQueue("postgresql://fixture",connect=connect,lease_ms=1000)

    def test_enqueue_is_idempotent_and_claim_success_is_not_reclaimed(self):
        task=self.queue.enqueue("run","AU-1","ABSTRACT","1"*64)
        self.assertEqual(self.queue.enqueue("run","AU-1","ABSTRACT","1"*64),task)
        claimed=self.queue.claim("worker")
        self.assertEqual(len(claimed),1)
        self.assertEqual(self.queue.succeed(task.task_id,"worker").state,TaskState.SUCCEEDED)
        self.assertEqual(self.queue.claim("worker"),())

    def test_different_payload_and_wrong_owner_fail_closed(self):
        task=self.queue.enqueue("run","AU-1","ABSTRACT","1"*64)
        with self.assertRaisesRegex(TaskQueueError,"different immutable"):
            self.queue.enqueue("run","AU-1","ABSTRACT","2"*64)
        self.queue.claim("worker")
        with self.assertRaisesRegex(TaskQueueError,"not owned"):
            self.queue.succeed(task.task_id,"other")

    def test_failure_requeues_then_isolates_after_max_attempts(self):
        task=self.queue.enqueue("run","AU-1","ABSTRACT","1"*64,max_attempts=2)
        self.queue.claim("worker")
        self.assertEqual(self.queue.fail(task.task_id,"worker","timeout").state,TaskState.QUEUED)
        self.queue.claim("worker")
        self.assertEqual(self.queue.fail(task.task_id,"worker","timeout").state,TaskState.UNRESOLVED)


if __name__ == "__main__": unittest.main()
