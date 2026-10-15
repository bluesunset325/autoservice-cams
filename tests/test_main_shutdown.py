from app.main import stop_all


class Part:
    def __init__(self, name, calls, fail=False):
        self.name, self.calls, self.fail = name, calls, fail

    def stop(self):
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError(self.name)


def test_stop_all_isolates_failures_and_stops_go2rtc_last(caplog):
    calls = []
    stop_all(Part("archive", calls, fail=True), Part("backups", calls, fail=True), Part("engine", calls),
             Part("go2rtc", calls, fail=True))
    assert calls == ["archive", "backups", "engine", "go2rtc"]
    assert [r.exc_info[1].args[0] for r in caplog.records if r.exc_info] == ["archive", "backups", "go2rtc"]


def test_stop_all_skips_missing_parts():
    calls = []
    stop_all(None, None, None, Part("go2rtc", calls))
    assert calls == ["go2rtc"]
