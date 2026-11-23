from threading import Event, get_ident

from vocabulary.list_jobs import ListJobs


def test_queries_are_thread_owned_and_only_latest_pending_request_runs(qtbot):
    started, release = Event(), Event()
    calls, successes, busy, threads, closed = [], [], [], [], []

    class Reader:
        def __init__(self, path):
            threads.append(get_ident())

        def query_page(self, category, keyword, page_number, anchor_id):
            threads.append(get_ident())
            calls.append(keyword)
            if keyword == "first":
                started.set()
                assert release.wait(5)
            return keyword

        def close(self):
            closed.append(get_ident())

    jobs = ListJobs("unused", reader_factory=Reader)
    jobs.success.connect(lambda request, value: successes.append((request, value)))
    jobs.busyChanged.connect(busy.append)
    try:
        jobs.request("all", "first")
        qtbot.waitUntil(started.is_set)
        jobs.request("all", "discarded")
        final = jobs.request("pending", "latest", revision=8, intent="enter-list")
        release.set()
        qtbot.waitUntil(lambda: len(successes) == 1 and not jobs.busy)
        assert calls == ["first", "latest"]
        assert successes == [(final, "latest")]
        assert final.revision == 8 and final.intent == "enter-list"
        assert busy == [True, False]
        assert len(set(threads)) == 1 and threads[0] != get_ident()
    finally:
        release.set()
        jobs.shutdown()
        jobs.shutdown()
    assert closed == [threads[0]]


def test_success_handler_can_request_another_page_without_false_idle(qtbot):
    class Reader:
        def __init__(self, path):
            pass

        def query_page(self, category, keyword, page_number, anchor_id):
            return page_number

        def close(self):
            pass

    jobs = ListJobs("unused", reader_factory=Reader)
    successes, busy = [], []

    def completed(request, result):
        successes.append(result)
        if result == 1:
            jobs.request("all", page_number=2)

    jobs.success.connect(completed)
    jobs.busyChanged.connect(busy.append)
    try:
        jobs.request("all")
        qtbot.waitUntil(lambda: successes == [1, 2] and not jobs.busy)
        assert busy == [True, False]
    finally:
        jobs.shutdown()


def test_cancel_rejects_a_completion_already_on_its_way_to_gui(qtbot):
    ready = Event()

    class Reader:
        def __init__(self, path):
            pass

        def query_page(self, *args):
            ready.set()
            return "old result"

        def close(self):
            pass

    jobs = ListJobs("unused", reader_factory=Reader)
    received = []
    jobs.success.connect(lambda *args: received.append(args))
    try:
        jobs.request("all")
        assert ready.wait(5)
        jobs.cancel()
        qtbot.waitUntil(lambda: not jobs.busy)
        assert not received
    finally:
        jobs.shutdown()


def test_read_error_preserves_worker_for_next_request(qtbot):
    class Reader:
        def __init__(self, path):
            pass

        def query_page(self, category, keyword, page_number, anchor_id):
            if keyword == "fail":
                raise ValueError("read failed")
            return "recovered"

        def close(self):
            pass

    jobs = ListJobs("unused", reader_factory=Reader)
    failures, successes = [], []
    jobs.failure.connect(lambda request, error: failures.append((request, error)))
    jobs.success.connect(lambda request, result: successes.append(result))
    try:
        failed = jobs.request("all", "fail")
        qtbot.waitUntil(lambda: len(failures) == 1 and not jobs.busy)
        assert failures[0][0] is failed
        assert str(failures[0][1]) == "read failed"
        jobs.request("all", "retry")
        qtbot.waitUntil(lambda: successes == ["recovered"] and not jobs.busy)
    finally:
        jobs.shutdown()


def test_qobject_destruction_stops_its_worker_thread(qtbot):
    from PySide6.QtCore import QCoreApplication, QEvent

    jobs = ListJobs("unused")
    thread = jobs._thread
    jobs.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qtbot.waitUntil(lambda: not thread.isRunning())
