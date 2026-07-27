"""Database layer: all Master Spec section 11 ledgers exist and round-trip."""

import pytest
from sqlalchemy import inspect

from app.persistence.db import build_engine, build_session_factory, init_db, session_scope
from app.persistence.models import EventRecord, Round, Show

SPEC_TABLES = {
    "shows",
    "rounds",
    "participants",
    "audience_members",
    "submissions",
    "ai_requests",
    "media_assets",
    "votes",
    "events",
    "errors",
    "exports",
}


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path}/test.db")
    init_db(engine)
    yield engine
    engine.dispose()


def test_all_spec_ledgers_created(engine):
    tables = set(inspect(engine).get_table_names())
    missing = SPEC_TABLES - tables
    assert not missing, f"missing tables: {missing}"


def test_show_and_round_roundtrip(engine):
    factory = build_session_factory(engine)
    with session_scope(factory) as session:
        session.add(Show(show_id="show_1", title="Pilot", selected_games=["art_showdown"]))
        session.add(Round(round_id="round_1", show_id="show_1", game_id="art_showdown"))

    with session_scope(factory) as session:
        show = session.get(Show, "show_1")
        round_ = session.get(Round, "round_1")
        assert show.selected_games == ["art_showdown"]
        assert round_.phase == "LOBBY"


def test_event_ledger_is_strictly_ordered(engine):
    factory = build_session_factory(engine)
    with session_scope(factory) as session:
        for i in range(5):
            session.add(
                EventRecord(
                    event_id=f"evt_{i}",
                    event_type="test.event",
                    show_id="show_1",
                    actor_type="controller",
                    actor_id="controller",
                    public=i % 2 == 0,
                )
            )

    with session_scope(factory) as session:
        rows = session.query(EventRecord).order_by(EventRecord.seq).all()
        assert [r.event_id for r in rows] == [f"evt_{i}" for i in range(5)]
        seqs = [r.seq for r in rows]
        assert seqs == sorted(seqs) and len(set(seqs)) == 5


def test_rollback_on_error(engine):
    factory = build_session_factory(engine)
    with pytest.raises(RuntimeError):
        with session_scope(factory) as session:
            session.add(Show(show_id="show_x", title="Doomed"))
            raise RuntimeError("boom")

    with session_scope(factory) as session:
        assert session.get(Show, "show_x") is None
