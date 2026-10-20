

def test_an_account_started_after_the_5th_pays_nothing_that_month_given_the_calendar():
    from datetime import date, timedelta

    from quant_platform.research.blend import scaled
    from quant_platform.research.stock_rules import SeedPlan

    calendar = [date(2026, 9, 1) + timedelta(days) for days in range(70)]
    late = [day for day in calendar if day >= date(2026, 10, 20)]
    assert SeedPlan(calendar=calendar).schedule(late, date(2026, 10, 20), date(2026, 11, 9)) == [
        (date(2026, 10, 20), 300000.0), (date(2026, 11, 5), 10000.0)]
    early = [day for day in calendar if day >= date(2026, 9, 1)]
    assert SeedPlan(calendar=calendar).schedule(early, date(2026, 9, 1), date(2026, 10, 9)) == [
        (date(2026, 9, 1), 300000.0), (date(2026, 9, 5), 10000.0), (date(2026, 10, 5), 10000.0)]
    assert SeedPlan().schedule(late, date(2026, 10, 20), date(2026, 10, 20)) == [(date(2026, 10, 20), 310000.0)]
    assert scaled(SeedPlan(calendar=calendar), 0.5).schedule(late, date(2026, 10, 20), date(2026, 10, 20)) == [
        (date(2026, 10, 20), 150000.0)]
