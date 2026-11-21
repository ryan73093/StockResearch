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
    parser.add_argument("command", choices=("decide", "board", "leakcheck", "probe", "advise"))
    parser.add_argument("--day", help="哪一天收盤後（YYYY-MM-DD，預設今天）")
    parser.add_argument("--from", dest="first", help="decide --run：從這一天起補做到 --day")
    parser.add_argument("--run", help="補做的名稱（存在 agent_lab/replay/<名稱>/，不是證據）")
    parser.add_argument("--only", help="只做這些設計（逗號分隔的代號）")
    parser.add_argument("--list", dest="list_day", help="decide --run：每天都讀這一份名單（YYYY-MM-DD；補做第一份名單以前的期間用，"
                        "名單是事後挑的，候選偏樂觀）")
    parser.add_argument("--scripted", action="store_true", help="用腳本代理人，不呼叫模型")
    parser.add_argument("--months", type=int, default=36, help="probe：問最近幾個月")
    parser.add_argument("--holdings", default="", help="advise：現在的持股，代號:股數[:每股成本]，逗號分隔（例 2330:1000:950,2317:2000）")
    parser.add_argument("--cash", type=float, default=0.0, help="advise：現金（元）")
    parser.add_argument("--base", default=str(DEFAULT_BASE))
    args = parser.parse_args()

    from quant_platform.config.settings import Settings
    from quant_platform.research import daily
    from quant_platform.research.agent_lab import environment, evaluate, probe, runner
    from quant_platform.research.jobs import JobLog

    base = Path(args.base)
    day = date.fromisoformat(args.day) if args.day else datetime.now(TAIPEI).date()
    if args.list_day:
        if not args.run:
            parser.error("--list 只能和 --run 一起用（真的決定只讀當月的名單）")
        from quant_platform.research.agent_lab import desk as desk_module

        desk_module.LIST_OVERRIDE = RESEARCH / "rolling" / f"{args.list_day}.json"
        if not desk_module.LIST_OVERRIDE.is_file():
            parser.error(f"沒有這一份名單：{desk_module.LIST_OVERRIDE}")
    command = "python -m quant_platform.research.agent_lab " + " ".join(sys.argv[1:])

    def client():
        if args.scripted:
            return environment.ScriptedClient(environment.top_of_consensus())
        return runner.build_client(Settings.from_env(), RESEARCH, replay=bool(args.run))

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
            model = client()
            if args.run:
                made = runner.replay(fp, base, RESEARCH, date.fromisoformat(args.first or day.isoformat()), day, args.run,
                                     model, keys, job)
            else:
                made = runner.decide_day(fp, base, RESEARCH, day, model, None, keys, job) if fp.index.get(day) is not None else []
            job.payload["summary"] = runner.summary(made)
            try:
                result = evaluate.board(base, RESEARCH, args.run, loaded=(data, fp), through=day if args.run else None)
                evaluate.write(RESEARCH, result)
            except Exception as error:  # noqa: BLE001 - the decisions are stored; the board can be made again
                job.payload["summary"] += f"；看板沒算出來（{str(error)[:80]}）"
            # 2026-11-20 (D-051): a model the lab has not probed yet is probed once, on its first live day — what it
            # remembers of each past month decides which replays mean anything (about US$0.05)
            if not args.run and not args.scripted and model is not None and not probe.done(RESEARCH, model.model):
                from quant_platform.research.stock_forward import _names

                job.update(current=f"第一次用 {model.model}：記憶檢測（最近 36 個月）", force=True)
                try:
                    probed = probe.run(fp, base, RESEARCH, model, 36, _names(base, fp.sessions[-1].year))
                    answered = [row for row in probed["months"] if "error" not in row]
                    job.payload["summary"] += f"；{model.model} 第一次用，做了記憶檢測（{len(answered)} 個月）"
                except Exception as error:  # noqa: BLE001 - the decisions are stored; the probe runs again tomorrow
                    job.payload["summary"] += f"；記憶檢測沒做完（{str(error)[:80]}）"
        print(job.payload["summary"], flush=True)
        return 0
    if args.command == "advise":
        # 2026-11-21 (使用者：我會跟 AI 講我現在持股狀況，它要協助我買賣): advice only — nothing is ordered (D-001)
        holdings: dict[str, dict] = {}
        for part in [part.strip() for part in args.holdings.split(",") if part.strip()]:
            fields = part.split(":")
            holdings[fields[0].strip()] = {"shares": int(float(fields[1])) if len(fields) > 1 else 0,
                                           "cost": float(fields[2]) if len(fields) > 2 else None}
        _data, fp = daily.load(base)
        day = date.fromisoformat(args.day) if args.day else fp.sessions[-1]
        model = runner.build_client(Settings.from_env(), RESEARCH)
        if model is None:
            print("沒有 AI API 平台的專案金鑰，無法給建議", flush=True)
            return 1
        result = runner.advise(fp, base, RESEARCH, day, holdings, args.cash, model)
        print(f"{result['date']} 收盤後的建議（{result['model']}；只是建議，平台不下單）", flush=True)
        print(f"市場看法：{result['view']}", flush=True)
        for order in result["orders"]:
            price = f"{order['close']:,.2f}" if order["close"] else "—"
            print(f"  {order['side']} {order['name']} {order['shares']:,} 股（收盤 {price} 元）：{order['reason']}", flush=True)
        if not result["orders"]:
            print("  今天不用買賣。", flush=True)
        print("續抱：" + ("、".join(result["keep"]) or "（沒有）"), flush=True)
        if result["unknown"]:
            print("平台沒有資料、沒辦法判斷的：" + "、".join(result["unknown"]), flush=True)
        print(f"做完後的現金約 {result['cash_after']:,} 元（每檔買進最多放帳戶的二十分之一）", flush=True)
        print(f"模型花費 US${result['cost_usd']:.4f}", flush=True)
        return 0
    if args.command == "board":
        result = evaluate.board(base, RESEARCH, args.run, through=day if args.run and args.day else None)
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
