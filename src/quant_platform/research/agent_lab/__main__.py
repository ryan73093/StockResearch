"""``python -m quant_platform.research.agent_lab <command>`` (PYTHONPATH=src from the project folder).

- ``decide``     the cohort's decisions after the day's close (the flow ``llmagent``, 15:40 on trading days), then the board;
                 ``--run NAME --from D`` decides the days from D in the replay store NAME (not evidence);
                 ``--scripted`` uses the scripted agent (no model call) — a dry run of the whole loop;
- ``board``      the cohort's accounts from the engine (``--run NAME``: a replay's);
- ``leakcheck``  every tool on a day with the panel blinded after it;
- ``probe``      how much of each past month the model remembers."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
RESEARCH = Path("instance") / "research"


def main() -> int:
    from quant_platform.research.market import DEFAULT_BASE

    parser = argparse.ArgumentParser(description="AI 代理人實驗室")
    parser.add_argument("command", choices=("decide", "board", "leakcheck", "probe"))
    parser.add_argument("--day", help="哪一天收盤後（YYYY-MM-DD，預設今天）")
    parser.add_argument("--from", dest="first", help="decide --run：從這一天起補做到 --day")
    parser.add_argument("--run", help="補做的名稱（存在 agent_lab/replay/<名稱>/，不是證據）")
    parser.add_argument("--only", help="只做這些設計（逗號分隔的代號）")
    parser.add_argument("--scripted", action="store_true", help="用腳本代理人，不呼叫模型")
    parser.add_argument("--months", type=int, default=36, help="probe：問最近幾個月")
    parser.add_argument("--base", default=str(DEFAULT_BASE))
    args = parser.parse_args()

    from quant_platform.config.settings import Settings
    from quant_platform.research import daily
    from quant_platform.research.agent_lab import environment, evaluate, probe, runner
    from quant_platform.research.jobs import JobLog

    base = Path(args.base)
    day = date.fromisoformat(args.day) if args.day else datetime.now(TAIPEI).date()
    command = "python -m quant_platform.research.agent_lab " + " ".join(sys.argv[1:])

    def client():
        if args.scripted:
            return environment.ScriptedClient(environment.top_of_consensus())
        return runner.build_client(Settings.from_env(), RESEARCH)

    if args.command == "decide":
        keys = [key.strip() for key in args.only.split(",")] if args.only else None
        title = (f"AI 代理人實驗室：補做 {args.first}～{day}（{args.run}，不是證據）" if args.run
                 else f"AI 代理人實驗室：{day} 收盤後各個設計的決定")
        with JobLog(RESEARCH).start(title, command) as job:
            loaded = None
            for attempt in range(7):                 # the day's prices may still be on their way
                loaded = daily.load(base)
                if loaded[1].index.get(day) is not None or args.run:
                    break
                job.update(current=f"還沒有 {day} 的收盤資料，5 分鐘後再試（第 {attempt + 1} 次）", force=True)
                time.sleep(300)
            data, fp = loaded
            if args.run:
                made = runner.replay(fp, base, RESEARCH, date.fromisoformat(args.first or day.isoformat()), day, args.run,
                                     client(), keys, job)
            else:
                made = runner.decide_day(fp, base, RESEARCH, day, client(), None, keys, job) if fp.index.get(day) is not None else []
            job.payload["summary"] = runner.summary(made)
            try:
                result = evaluate.board(base, RESEARCH, args.run, loaded=(data, fp))
                evaluate.write(RESEARCH, result)
            except Exception as error:  # noqa: BLE001 - the decisions are stored; the board can be made again
                job.payload["summary"] += f"；看板沒算出來（{str(error)[:80]}）"
        print(job.payload["summary"], flush=True)
        return 0
    if args.command == "board":
        result = evaluate.board(base, RESEARCH, args.run)
        print(evaluate.text(result), flush=True)
        print(f"寫到 {evaluate.write(RESEARCH, result)}", flush=True)
        return 0
    if args.command == "leakcheck":
        _data, fp = daily.load(base)
        if fp.index.get(day) is None:
            day = max(item for item in fp.sessions if item <= day)
        position = fp.index[day]
        if len(fp.sessions) - 1 - position < 5:            # nothing after the day to hide: check ten sessions back
            day = fp.sessions[position - 10]
        history = runner.records(RESEARCH, "info", args.run)
        history = [item for item in history if item["date"] < day.isoformat()]
        result = environment.leak_check(fp, base, RESEARCH, day, history)
        print(f"{result['date']}（之後還有 {result['sessions_after']} 個交易日的資料被換掉）：{result['tools']} 個工具輸出，"
              + ("都一樣，沒有讀到之後的資料" if not result["leaks"] else "不一樣的：" + "、".join(result["leaks"])), flush=True)
        return 1 if result["leaks"] else 0
    if args.command == "probe":
        from quant_platform.research.stock_forward import _names

        model = runner.build_client(Settings.from_env(), RESEARCH)
        if model is None:
            print("沒有模型金鑰，不能做記憶檢測", flush=True)
            return 1
        _data, fp = daily.load(base)
        with JobLog(RESEARCH).start(f"AI 代理人實驗室：模型記憶檢測（最近 {args.months} 個月）", command):
            result = probe.run(fp, base, RESEARCH, model, args.months, _names(base, fp.sessions[-1].year))
        print(probe.text(result), flush=True)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
