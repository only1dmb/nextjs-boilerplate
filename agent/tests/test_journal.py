from datetime import date, datetime, timezone

from robinhood_agent.journal import Journal


def test_append_only(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    journal.write("a", value=1)
    journal.write("b", value=2)
    records = list(journal.read())
    assert [r["event"] for r in records] == ["a", "b"]


def test_survives_a_corrupt_line(tmp_path):
    path = tmp_path / "j.jsonl"
    journal = Journal(path)
    journal.write("good", value=1)
    with path.open("a") as handle:
        handle.write("{not json\n")
    journal.write("also_good", value=2)
    assert [r["event"] for r in journal.read()] == ["good", "also_good"]


def test_serializes_awkward_values(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    journal.write("x", when=datetime(2026, 1, 1, tzinfo=timezone.utc), where=tmp_path)
    record = next(iter(journal.read()))
    assert record["when"].startswith("2026-01-01")
    assert isinstance(record["where"], str)


def test_trades_today_counts_only_submitted_orders(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    journal.write("signal", symbol="AAPL")
    journal.write("risk_decision", symbol="AAPL", allowed=False)
    journal.write("order_submitted", symbol="AAPL", side="buy")
    assert len(journal.filled_orders_on(date.today())) == 1


def test_day_open_equity_takes_the_first_snapshot(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    journal.write("snapshot", equity=10_000.0)
    journal.write("snapshot", equity=9_000.0)
    assert journal.day_open_equity(date.today()) == 10_000.0


def test_last_trade_time_is_per_symbol(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    journal.write("order_submitted", symbol="AAPL")
    journal.write("order_submitted", symbol="MSFT")
    assert journal.last_trade_time("AAPL") is not None
    assert journal.last_trade_time("TSLA") is None


def test_missing_file_reads_empty(tmp_path):
    assert list(Journal(tmp_path / "nope.jsonl").read()) == []
