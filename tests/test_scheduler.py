from types import SimpleNamespace

from backend import scheduler


def test_scheduler_polls_immediately_and_does_not_overlap(monkeypatch):
    captured = {}

    class FakeScheduler:
        running = False

        def add_job(self, **kwargs):
            captured.update(kwargs)

        def start(self):
            self.running = True

    monkeypatch.setattr(scheduler, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(scheduler.settings, "scraper_poll_interval_seconds", 30.5)

    created = scheduler.start_scheduler()

    assert created.running is True
    assert captured["next_run_time"] is not None
    assert captured["max_instances"] == 1
    assert captured["coalesce"] is True
    assert captured["misfire_grace_time"] == 31


def _fake_cycle(monkeypatch, *, polled, flush):
    """Run one polling cycle over categories 1 and 2 with polling and sending replaced."""

    class Session:
        def query(self, *_):
            return SimpleNamespace(all=lambda: [SimpleNamespace(id=1), SimpleNamespace(id=2)])

        def close(self):
            pass

    calls = []
    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    monkeypatch.setattr(scheduler, "poll_category", lambda category_id: polled(category_id))
    monkeypatch.setattr(scheduler, "process_new_jobs", lambda jobs, category_id: calls.append(("record", category_id)))
    monkeypatch.setattr(scheduler, "flush_notifications", lambda: (calls.append(("flush",)), flush())[1])
    scheduler.run_scraper_job()
    return calls


def test_a_cycle_flushes_once_after_every_category(monkeypatch):
    calls = _fake_cycle(monkeypatch, polled=lambda category_id: [object()], flush=dict)

    assert calls == [("record", 1), ("record", 2), ("flush",)]


def test_a_cycle_with_nothing_new_still_flushes_for_the_digests(monkeypatch):
    calls = _fake_cycle(monkeypatch, polled=lambda category_id: [], flush=dict)

    assert calls == [("flush",)]


def test_a_failing_category_or_flush_does_not_break_the_cycle(monkeypatch):
    def polled(category_id):
        if category_id == 1:
            raise RuntimeError("Mostaql is down")
        return [object()]

    def flush():
        raise RuntimeError("disk full")

    calls = _fake_cycle(monkeypatch, polled=polled, flush=flush)

    assert calls == [("record", 2), ("flush",)]
    assert scheduler._scraper_run_lock.acquire(blocking=False)  # the lock was released
    scheduler._scraper_run_lock.release()
