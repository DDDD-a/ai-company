"""测试 Event Stream"""
import pytest
import asyncio
import tempfile
from pathlib import Path
from core.event_stream import EventStream
from protocols.verbs import Event, Verb


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    yield path
    path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_init_and_append(temp_db):
    stream = EventStream(temp_db)
    await stream.init()

    e = Event(verb=Verb.UPD, agent="test", task="#t1", status="50%")
    await stream.append(e)

    events = await stream.tail(10)
    assert len(events) == 1
    assert events[0].id == e.id
    assert events[0].verb == Verb.UPD

    await stream.close()


@pytest.mark.asyncio
async def test_query_by_task(temp_db):
    stream = EventStream(temp_db)
    await stream.init()

    await stream.append(Event(verb=Verb.UPD, agent="BE", task="#t1", status="start"))
    await stream.append(Event(verb=Verb.UPD, agent="BE", task="#t2", status="start"))
    await stream.append(Event(verb=Verb.DON, agent="BE", task="#t1", status="OK"))

    t1_events = await stream.query(task="#t1")
    assert len(t1_events) == 2
    t2_events = await stream.query(task="#t2")
    assert len(t2_events) == 1

    await stream.close()


@pytest.mark.asyncio
async def test_query_by_agent(temp_db):
    stream = EventStream(temp_db)
    await stream.init()

    await stream.append(Event(verb=Verb.UPD, agent="FE", task="#t1"))
    await stream.append(Event(verb=Verb.UPD, agent="BE", task="#t2"))
    await stream.append(Event(verb=Verb.DON, agent="FE", task="#t3"))

    fe_events = await stream.query(agent="FE")
    assert len(fe_events) == 2
    be_events = await stream.query(agent="BE")
    assert len(be_events) == 1

    await stream.close()


@pytest.mark.asyncio
async def test_query_by_verb(temp_db):
    stream = EventStream(temp_db)
    await stream.init()

    await stream.append(Event(verb=Verb.UPD, agent="test", task="#t1"))
    await stream.append(Event(verb=Verb.UPD, agent="test", task="#t2"))
    await stream.append(Event(verb=Verb.DON, agent="test", task="#t1"))

    don_events = await stream.query(verb=Verb.DON)
    assert len(don_events) == 1

    await stream.close()


@pytest.mark.asyncio
async def test_get_task_events(temp_db):
    stream = EventStream(temp_db)
    await stream.init()

    await stream.append(Event(verb=Verb.ASN, agent="HR", task="#t1"))
    await stream.append(Event(verb=Verb.ACK, agent="BE", task="#t1"))
    await stream.append(Event(verb=Verb.UPD, agent="BE", task="#t1"))
    await stream.append(Event(verb=Verb.DON, agent="BE", task="#t1"))

    events = await stream.get_task_events("#t1")
    assert len(events) == 4
    verbs = [e.verb for e in events]
    assert verbs == [Verb.ASN, Verb.ACK, Verb.UPD, Verb.DON]

    await stream.close()


@pytest.mark.asyncio
async def test_tail_order(temp_db):
    stream = EventStream(temp_db)
    await stream.init()

    for i in range(5):
        await stream.append(Event(verb=Verb.UPD, agent="test", task=f"#t{i}"))

    events = await stream.tail(3)
    assert len(events) == 3
    # 按 timestamp 降序取、再反转，所以第一个是最旧的
    task_ids = [e.task for e in events]
    # 应该是 #t2, #t3, #t4 (最旧的3个)
    assert task_ids == ["#t2", "#t3", "#t4"]

    await stream.close()


@pytest.mark.asyncio
async def test_count(temp_db):
    stream = EventStream(temp_db)
    await stream.init()

    await stream.append(Event(verb=Verb.UPD, agent="test", task="#t1"))
    await stream.append(Event(verb=Verb.UPD, agent="test", task="#t2"))
    await stream.append(Event(verb=Verb.DON, agent="test", task="#t1"))

    count_all = await stream.count()
    assert count_all == 3
    count_t1 = await stream.count(task="#t1")
    assert count_t1 == 2
    count_don = await stream.count(verb=Verb.DON)
    assert count_don == 1

    await stream.close()
