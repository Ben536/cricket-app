"""Repository: atomic ball numbers, extended outcomes, optimistic locking."""

import pytest

from db.repository import VersionConflictError


@pytest.fixture
def session_id(repository):
    profile = repository.create_profile(name="Tester", batting_hand="right")
    session = repository.create_session(player_id=profile.id, date="2026-07-03", difficulty="medium")
    return session.id


def test_atomic_ball_number_assignment(repository, session_id):
    d1 = repository.create_delivery(session_id=session_id, ball_number=None,
                                    timestamp="t1", outcome="dot", runs=0)
    d2 = repository.create_delivery(session_id=session_id, ball_number=None,
                                    timestamp="t2", outcome="1", runs=1)
    assert (d1.ball_number, d2.ball_number) == (1, 2)


def test_extended_outcomes_accepted(repository, session_id):
    """W/wd/nb were the reason migration 003 exists - they must persist."""
    for outcome in ("W", "wd", "nb", "bowled", "lbw"):
        d = repository.create_delivery(session_id=session_id, ball_number=None,
                                       timestamp="t", outcome=outcome, runs=0,
                                       is_manual_input=True)
        assert d.outcome == outcome


def test_stale_version_update_raises(repository):
    profile = repository.create_profile(name="V", batting_hand="left")
    repository.update_profile(player_id=profile.id, version=profile.version, name="V2")
    with pytest.raises(VersionConflictError):
        repository.update_profile(player_id=profile.id, version=profile.version, name="V3")


def test_undo_deletes_highest_ball(repository, session_id):
    repository.create_delivery(session_id=session_id, ball_number=None,
                               timestamp="t1", outcome="dot", runs=0)
    repository.create_delivery(session_id=session_id, ball_number=None,
                               timestamp="t2", outcome="4", runs=4)
    assert repository.delete_last_delivery(session_id) is True
    last = repository.get_last_delivery(session_id)
    assert last.outcome == "dot"
