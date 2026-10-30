"""2026-10-22 (194 forward accounts out of step): decisions read before an account was dropped and started again are
not written back over the new ones; one writer of the forward records at a time."""
import threading
import time
from datetime import date

from quant_platform.research.decisions import DecisionStore
from quant_platform.research.stock_forward import ForwardLock


def test_a_process_that_read_before_the_restart_does_not_write_the_old_decisions_back(tmp_path):
    name = "abc@2026-09-01"
    first = DecisionStore(tmp_path)
    first.keep(name, {date(2026, 9, 1): ["1101.TW"]}, None, date(2026, 10, 21))
    first.save()
    stale = DecisionStore(tmp_path)                       # another pipeline reads the old decisions
    stale.keep(name, {date(2026, 9, 1): ["1101.TW"]}, None, date(2026, 10, 21))
    time.sleep(0.01)
    restart = DecisionStore(tmp_path)                     # the refit drops them and records again
    restart.drop({name})
    fresh = DecisionStore(tmp_path)
    fresh.keep(name, {date(2026, 9, 1): ["2330.TW"]}, None, date(2026, 10, 21))
    fresh.save()
    stale.save()                                          # the old process finishes last
    assert DecisionStore(tmp_path).fixed(name)["days"][date(2026, 9, 1)] == ["2330.TW"]
    later = DecisionStore(tmp_path)                       # read after the restart: may write
    later.keep(name, {date(2026, 9, 1): ["2330.TW"], date(2026, 10, 22): ["2317.TW"]}, None, date(2026, 10, 22))
    later.save()
    assert DecisionStore(tmp_path).fixed(name)["through"] == date(2026, 10, 22)


def test_the_forward_lock_lets_one_writer_in_at_a_time(tmp_path):
    order = []

    def writer(label):
        with ForwardLock(tmp_path):
            order.append(f"{label}-in")
            time.sleep(0.3)
            order.append(f"{label}-out")

    threads = [threading.Thread(target=writer, args=(label,)) for label in ("a", "b")]
    for thread in threads:
        thread.start()
        time.sleep(0.05)
    for thread in threads:
        thread.join()
    assert order in (["a-in", "a-out", "b-in", "b-out"], ["b-in", "b-out", "a-in", "a-out"])
    assert not (tmp_path / "forward.lock").exists()


def test_two_processes_saving_at_once_keep_both(tmp_path):
    """2026-10-30: saves from two processes at the same moment — each one's accounts are on the file afterwards."""
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    with context.Pool(2) as pool:
        pool.starmap(_save_many, [(str(tmp_path), "a"), (str(tmp_path), "b")])
    import json

    kept = json.loads((tmp_path / "forward" / "stocks" / "decisions.json").read_text(encoding="utf-8"))
    assert {name for name in kept} == {f"{side}{number}@2026-09-01" for side in "ab" for number in range(40)}


def _save_many(research: str, side: str) -> None:
    from datetime import date

    from quant_platform.research.decisions import DecisionStore

    for number in range(40):
        store = DecisionStore(research)
        store.keep(f"{side}{number}@2026-09-01", {date(2026, 9, 1): ["2330.TW"]}, None, date(2026, 9, 1))
        store.save()
