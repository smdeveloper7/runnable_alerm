from monitor import AlertTracker, Heartbeat, next_delay
from runable_api import EventStock


def ev(stock: int, id_: int = 1, name: str = "HALF") -> EventStock:
    return EventStock(id=id_, name=name, stock=stock, quantity=5, price=80000, is_use=True)


def test_first_availability_alerts_immediately():
    t = AlertTracker(cooldown=300)
    alert, sold = t.decide([ev(0)], now=0)
    assert alert == [] and sold == []
    alert, sold = t.decide([ev(2)], now=10)
    assert [e.id for e in alert] == [1] and sold == []


def test_no_repeat_alert_within_cooldown_then_repeat_after():
    t = AlertTracker(cooldown=300)
    t.decide([ev(2)], now=0)
    alert, _ = t.decide([ev(2)], now=100)
    assert alert == []
    alert, _ = t.decide([ev(2)], now=301)
    assert [e.id for e in alert] == [1]


def test_sold_out_transition_reported_once_and_resets_cooldown():
    t = AlertTracker(cooldown=300)
    t.decide([ev(2)], now=0)
    alert, sold = t.decide([ev(0)], now=5)
    assert alert == [] and [e.id for e in sold] == [1]
    alert, sold = t.decide([ev(0)], now=6)
    assert alert == [] and sold == []
    # 다시 재고가 생기면 쿨다운과 무관하게 즉시 알림
    alert, _ = t.decide([ev(1)], now=7)
    assert [e.id for e in alert] == [1]


def test_events_tracked_independently():
    t = AlertTracker(cooldown=300)
    alert, _ = t.decide([ev(1, 1, "HALF"), ev(0, 2, "10K")], now=0)
    assert [e.name for e in alert] == ["HALF"]
    alert, _ = t.decide([ev(1, 1, "HALF"), ev(3, 2, "10K")], now=1)
    assert [e.name for e in alert] == ["10K"]


def test_heartbeat_due_after_interval_from_start_then_from_last_sent():
    hb = Heartbeat(interval=60, started_at=1000)
    assert not hb.due(1030)
    assert hb.due(1060)
    hb.record(True); hb.record(True); hb.record(False)
    assert hb.mark_sent(1060) == (2, 1)
    assert hb.checks == 0 and hb.errors == 0
    assert not hb.due(1100)
    assert hb.due(1120)


def test_heartbeat_disabled_when_interval_zero():
    hb = Heartbeat(interval=0, started_at=0)
    assert not hb.due(10**9)


def test_next_delay_range_and_backoff():
    for _ in range(50):
        d = next_delay(4, 2)
        assert 4 <= d <= 6
    assert next_delay(4, 0, backoff_step=2) == 16
    assert next_delay(4, 0, backoff_step=10) == 60
