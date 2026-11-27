"""The cohort: the agent designs observed forward side by side from the same day. Each answers one question fixed
before it starts (docs/research/results.md「AI 代理人實驗室」) and is compared with one other row, so sixty sessions are
not spent on one design at a time. No heavy import here: the names are read by the dashboard and the tests."""

from __future__ import annotations

from dataclasses import dataclass

FIRST_TOOLS = ("market", "consensus", "screen", "card", "portfolio", "journal")       # the first agent's (llm_agent.TOOLS)
# 2026-11-20 (A2, before any design's first real decision): the company events (buyback, insider filings, pledges,
# the exchange's attention and disposition lists)
LAB_TOOLS = ("headlines", "mood", "lessons", "review", "check", "events")
# MOST: the account holds MOST, each an equal part — a shorter list is filled from the agreed stocks (2026-11-20,
# llm_agent.complete; evidence in docs/research/results.md「代理人的硬規則依據」). LEAST: a vote whose runs agree on
# fewer is first filled from its first run.
MOST, LEAST = 20, 5
VETO_MOST = 6                    # stocks kept out at once
VETO_NEW, VETO_SESSIONS = 2, 10  # new vetoes a day; a vetoed stock stays out this many decisions (2026-11-20: the
#                                  replay vetoed 5.1 stocks a day afresh, 578 orders against the twin's 322)
VOTES, VOTES_NEEDED = 3, 2
# 2026-11-21 (使用者：避免串了不同的模型導致的差異): the cohort's model and effort are the lab's own, written here and
# committed — not the shared setting, which other features may change. A design's 60 sessions are one model, one
# effort, one prompt; another model is another design (``Variant.model``) running beside it from its own first day.
LAB_MODEL = "claude-haiku-5-5"
LAB_EFFORT = "medium"
TRADE_CANDIDATES = 16            # the trader: the agreed stocks not held that it may buy
CASH_LEAST = 10                  # the trader that may keep cash: the fewest stocks held (half — the platform's guard that held up halves)
SCORE_CANDIDATES = 40            # the scorer: the agreed stocks it scores (with the holdings)
ANALYST_CARDS = 16               # the stocks the analysts read: the holdings and the most agreed


@dataclass(frozen=True)
class Variant:
    key: str
    label: str                   # 繁體中文, shown to the owner
    mode: str                    # "twin" | "single" | "vote" | "veto" | "team"
    tools: tuple[str, ...]
    question: str                # the one question this row answers
    against: str                 # the row it is compared with (a variant key, "llm_agent" or "0050")
    steps: int = 8
    calls: int = 0               # model calls a day at most (the budget's arithmetic)
    since: str = "2026-11-20"    # the first session it decides live (the 16:00 check wants every one from it)
    model: str = ""              # its own model when it is not the lab's (a design that asks the model question)
    panel: str = "money"         # where its candidates come from: "money" (the lab's consensus) | "steady" (desk.steady_rows;
                                 # 2026-11-22: tried as twin2/trade2 and not adopted — docs/research/results.md「穩健候選」)
    cash: bool = False           # a trader whose places left open stay cash (CASH_LEAST stocks at least)
    wave: int = 1                # 2 and later: a design that joined after the cohort started — the board compares it in
                                 # a table of its own, every account in it (its twin too) starting at its first decision,
                                 # so the first cohort's start and judgement day do not move

    @property
    def model_name(self) -> str:
        return self.model or LAB_MODEL

    @property
    def factor(self) -> str:
        return f"lab_{self.key}"


COHORT: tuple[Variant, ...] = (
    Variant("twin", "對照組：規則共識最多的 20 檔（沒有語言模型）", "twin", (),
            "基準線：代理人起點看到的同一份共識，照固定做法持有", "0050"),
    Variant("plain", "單一代理人・中性指示（第一版的六個工具）", "single", FIRST_TOOLS,
            "第一版的指示裡有事後才知道的提示（跌市少跌、連動高跌更多）；拿掉以後還贏不贏對照組", "twin", calls=8),
    Variant("info", "單一代理人・加市場新聞、情緒、研究知識庫、交易檢討、組合檢查", "single", FIRST_TOOLS + LAB_TOOLS,
            "多給資訊（新聞、情緒、公司事件、研究知識、自己過去交易的對錯）有沒有用", "plain", calls=8),
    Variant("vote", "三次獨立決定投票（同一個設計跑三次，兩票以上才持有）", "vote", FIRST_TOOLS + LAB_TOOLS,
            "同一個代理人每次跑的結果不一樣；投票能不能去掉運氣的成分", "info", calls=24),
    Variant("veto", "只否決：從對照組的 20 檔裡最多剔掉 6 檔，由後面的遞補", "veto", ("market", "mood", "card", "headlines", "events"),
            "語言模型當「否決的人」是不是比當「選股的人」好", "twin", steps=1, calls=1),
    Variant("team", "分析師團隊：多方、空方各寫意見，經理人決定", "team", ("market", "mood", "headlines", "consensus", "card",
                                                                "events", "portfolio", "review"),
            "多個角色分工再合議，是不是比一個代理人自己查好", "info", steps=3, calls=3),
    # 2026-11-20 (docs/research/results.md「評分者設計」, written before it runs): the model scores each candidate, the
    # program holds the best 20 — the use of a language model with out-of-sample evidence, against the picker
    Variant("score", "評分者：模型替共識前 40 檔與持股逐一打分，程式持有分數最高的 20 檔", "score",
            ("market", "mood", "headlines", "card", "events"),
            "讓模型替每一檔打分、程式照分數持有（文獻上站得住的用法），是不是比讓它自己挑（中性指示）好", "plain", steps=1, calls=1,
            since="2026-11-23"),
    # 2026-11-21 (使用者：依據現有持股去做對應的交易，由 AI agent 決定怎麼買賣; docs/research/results.md「持股經理」): it
    # starts from what the account holds and answers with orders — what it does not mention stays
    Variant("trade", "持股經理：從帳戶現有的持股出發，模型只決定今天賣哪幾檔、買哪幾檔，其餘續抱", "trade",
            ("market", "mood", "headlines", "portfolio", "card", "events"),
            "從現有持股出發只下買賣單（不提的續抱），是不是比每天重新排一份名單好", "twin", steps=1, calls=1, since="2026-11-23"),
    # 2026-11-22 (使用者：先調贏 0050; docs/research/results.md「持股經理可以留現金」, written before it runs): the trader's
    # path was the market's and the low-beta rules fall less by holding cash — the one thing not tried is how much it holds
    Variant("cash", f"持股經理・可以留現金：賣掉的可以不補，空位留現金（最少持有 {CASH_LEAST} 檔）", "trade",
            ("market", "mood", "headlines", "portfolio", "card", "events"),
            "讓持股經理自己決定留多少現金，是不是比永遠滿檔好", "trade", steps=1, calls=1, since="2026-11-23", cash=True),
    # 2026-11-27 (使用者：沒達到目標不要停; docs/research/results.md「挑規則加看前向」, written before it runs): the twin's
    # rules are each family's best by backtest money alone — on 11/27 all seven were 10% to 22% behind 0050 in their own
    # chained forward records. The same twin on the rules the platform's own score takes (forward 0.7, D-045). No model.
    Variant("fwd", "對照組・規則改照前向加回測的綜合分數挑（沒有語言模型）", "twin", (),
            "共識用的規則加看前向紀錄來挑（平台自己的排序），是不是比只看回測賺最多好", "twin", since="2026-11-30",
            panel="forward", wave=2),
    # 2026-11-27 (docs/research/results.md「照規則自己的帳戶持有」, written before it runs): the same rules as fwd, held
    # as their own accounts hold (holding rule, exits, guards, cash) instead of a fully invested consensus of rankings
    Variant("own", "對照組・照同一批規則自己的帳戶實際持有的來持有，連它們留的現金（沒有語言模型）", "twin", (),
            "照規則帳戶實際持有的（含現金），是不是比拿規則的排名組共識滿檔持有好", "fwd", since="2026-11-30",
            panel="follow", cash=True, wave=2),
)
BY_KEY = {variant.key: variant for variant in COHORT}
FACTORS = {variant.factor: f"AI 代理人實驗室・{variant.label}" for variant in COHORT}
# the rows the board also shows, read from their own stores (the first agent's 60 sessions start the same day)
OUTSIDE = (("llm_agent", "第一版 AI 選股代理人（11/19 的指示）"), ("llm_agent_control", "第一版的對照組"),
           ("llm_trader", "LLM 交易員（10/20，一次問答、60 檔候選）"))


def register() -> None:
    """The lab's factors known to the rule model (``DailyRule`` checks a factor's name), for this process only: their
    scores are put into the panel by ``evaluate`` — the engine itself has no code of the lab's."""
    from quant_platform.research import daily

    for name, label in FACTORS.items():
        daily.RULE_FACTORS.setdefault(name, label)
