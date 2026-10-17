import asyncio
import copy
import sqlite3
import threading
import time
import uuid

import pytest

from backend import config
from backend.core.errors import AppError
from backend.lab_adapter import get_adapter, reset_adapter
from backend.services import lab_service, session_store
from vendor.blindspot_lab.examples.adaptive_host import AdaptiveHost
from vendor.blindspot_lab.lab.adaptive import AdaptiveLab, adaptive_context
from vendor.blindspot_lab.lab.content import digest
from tests.test_dynamic_questions import valid_bundle


@pytest.fixture
def pending_generation(monkeypatch):
    monkeypatch.setattr(config, 'LAB_ADAPTER', 'blindspot')
    monkeypatch.setattr(config, 'LAB_BLINDSPOT_MODE', 'mock')
    reset_adapter()
    created = session_store.create_session({'daily_minutes': 60})
    sid = created['session_id']
    context = adaptive_context(sid, 'mock')
    context.update(dynamic_followups_enabled=True, max_rounds=5,
                   dynamic_bank=[], dynamic_bank_digest=digest([]))
    host, lab = AdaptiveHost(context), AdaptiveLab()
    result = host.execute(lab, 'start', 'seed-start', host.context['revision'])
    for index in range(3):
        qid = result['lab_state']['question']['question_id']
        result = host.execute(lab, 'submit', 'seed-'+str(index), host.context['revision'],
                              question_id=qid, answer=(lab.questions | lab.core.questions)[qid]['correct_option_id'])
    session_store.update_fields(sid, learning_state={'blindspot': host.export_json(),
        'adapter':'blindspot', 'runtime_mode':'mock', 'lab_version':'lab.v2',
        'revision':host.context['revision'], 'lab':{}}, expected_revision=created['revision'])

    class ControlledGenerator:
        runtime_mode = 'live'
        def __init__(self):
            self.entered, self.release = threading.Event(), threading.Event()
            self.calls, self.fail = 0, False
        def generate(self, request, timeout_seconds):
            self.calls += 1
            self.entered.set()
            if self.fail:
                raise RuntimeError('synthetic provider failure')
            assert self.release.wait(5), 'test did not release generator'
            return copy.deepcopy(valid_bundle())
    generator = ControlledGenerator()
    get_adapter()._impl._lab.core.runners['live'].model = generator
    try:
        yield sid, session_store.snapshot(sid)['revision'], generator
    finally:
        generator.release.set()
        reset_adapter()


def test_concurrent_same_request_generates_once_and_then_replays(pending_generation):
    sid, revision, generator = pending_generation
    rid = uuid.uuid4().hex
    async def run():
        first = asyncio.create_task(lab_service.execute(sid, 'continue', {}, request_id=rid, expected_revision=revision))
        assert await asyncio.to_thread(generator.entered.wait, 3)
        try:
            with pytest.raises(AppError, match='处理中'):
                await lab_service.execute(sid, 'continue', {}, request_id=rid, expected_revision=revision)
            with pytest.raises(AppError, match='不同参数'):
                await lab_service.execute(sid, 'stop', {}, request_id=rid, expected_revision=revision)
            assert generator.calls == 1
        finally:
            generator.release.set()
        result = await first
        replay = await lab_service.execute(sid, 'continue', {}, request_id=rid, expected_revision=revision)
        assert replay == result and generator.calls == 1
        assert result['state']['lab_state']['question']['question_id'].startswith('q_dyn_')
        assert session_store.snapshot(sid)['revision'] == revision + 1
        assert session_store.get_conn().execute('SELECT COUNT(*) FROM lab_requests WHERE session_id=? AND request_id=?', (sid,rid)).fetchone()[0] == 1
        assert session_store.get_conn().execute('SELECT COUNT(*) FROM lab_inflight WHERE session_id=?', (sid,)).fetchone()[0] == 0
    asyncio.run(run())


def test_generation_failure_releases_claim_for_same_request_retry(pending_generation):
    sid, revision, generator = pending_generation
    rid = uuid.uuid4().hex
    async def run():
        generator.fail = True
        with pytest.raises(AppError):
            await lab_service.execute(sid, 'continue', {}, request_id=rid, expected_revision=revision)
        assert session_store.snapshot(sid)['revision'] == revision
        assert session_store.get_conn().execute('SELECT COUNT(*) FROM lab_inflight WHERE session_id=?', (sid,)).fetchone()[0] == 0
        generator.fail = False; generator.release.set()
        result = await lab_service.execute(sid, 'continue', {}, request_id=rid, expected_revision=revision)
        assert result['state']['revision'] == revision+1 and generator.calls == 2
    asyncio.run(run())


def test_http_caller_cancellation_does_not_release_running_model_claim(pending_generation):
    sid, revision, generator = pending_generation
    rid = uuid.uuid4().hex
    async def run():
        first = asyncio.create_task(lab_service.execute(sid,'continue',{},request_id=rid,expected_revision=revision))
        assert await asyncio.to_thread(generator.entered.wait,3)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        try:
            with pytest.raises(AppError,match='处理中'):
                await lab_service.execute(sid,'continue',{},request_id=rid,expected_revision=revision)
            assert generator.calls == 1
        finally:
            generator.release.set()
        for _ in range(200):
            if session_store.snapshot(sid)['revision'] == revision+1:break
            await asyncio.sleep(.01)
        result = await lab_service.execute(sid,'continue',{},request_id=rid,expected_revision=revision)
        assert result['state']['revision']==revision+1 and generator.calls==1
    asyncio.run(run())


def test_sql_reservation_is_shared_with_other_connections_and_fences_expired_owner():
    sid=session_store.create_session()['session_id']
    owner,_=session_store.reserve_lab_request(sid,'old','fingerprint')
    with sqlite3.connect(config.DB_PATH) as other:
        assert other.execute('SELECT owner FROM lab_inflight WHERE session_id=?',(sid,)).fetchone()[0]==owner
        other.execute('UPDATE lab_inflight SET expires_at=? WHERE session_id=?',(time.time()-1,sid))
    replacement,_=session_store.reserve_lab_request(sid,'new','new-fingerprint')
    assert replacement!=owner
    assert not session_store.renew_lab_request(sid,owner)
    assert session_store.renew_lab_request(sid,replacement)
    with pytest.raises(AppError,match='占用已失效'):
        session_store.commit_lab_request(sid,'old','fingerprint',1,{}, {},claim_owner=owner)
    session_store.release_lab_request(sid,owner)
    assert session_store.get_conn().execute('SELECT owner FROM lab_inflight WHERE session_id=?',(sid,)).fetchone()[0]==replacement
    session_store.release_lab_request(sid,replacement)


@pytest.mark.parametrize('timeout_kind',['outer','model'])
def test_timeout_keeps_claim_until_actual_worker_exit(pending_generation,monkeypatch,timeout_kind):
    sid,revision,generator=pending_generation
    rid=uuid.uuid4().hex
    if timeout_kind=='outer':
        real_wait_for=asyncio.wait_for
        async def short_outer(awaitable,timeout):
            return await real_wait_for(awaitable, min(timeout,.03))
        monkeypatch.setattr('backend.lab_adapter.asyncio.wait_for',short_outer)
    else:
        get_adapter()._impl._lab.core.runners['live'].timeout=.03

    async def run():
        with pytest.raises(AppError):
            await lab_service.execute(sid,'continue',{},request_id=rid,expected_revision=revision)
        assert await asyncio.to_thread(generator.entered.wait,3)
        try:
            assert session_store.snapshot(sid)['revision']==revision
            assert session_store.get_conn().execute('SELECT COUNT(*) FROM lab_inflight WHERE session_id=?',(sid,)).fetchone()[0]==1
            with pytest.raises(AppError,match='处理中'):
                await lab_service.execute(sid,'continue',{},request_id=rid,expected_revision=revision)
            assert generator.calls==1
        finally:
            generator.release.set()
        for _ in range(300):
            remaining=session_store.get_conn().execute('SELECT COUNT(*) FROM lab_inflight WHERE session_id=?',(sid,)).fetchone()[0]
            if remaining==0:break
            await asyncio.sleep(.01)
        assert remaining==0
        assert generator.calls==1
        # A late successful thread result must not commit after a timeout response.
        assert session_store.snapshot(sid)['revision']==revision
    asyncio.run(run())
