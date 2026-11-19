"""2026-11-20 (使用者：專門研究透過 AI Agent 做股票投資的策略 AI；建立 AI 代理人最好的交易環境或模擬環境；做最深入的 AI 交易
決策的研究、回測、前向觀察；完整規劃好架構，放在 Stock Research 的某一個模組分類裡面): the agent lab — everything about
language-model trading agents in one package. Simulated accounts only (D-001, D-007); the plan and the phases are in
docs/agent_lab.md, the criteria and results in docs/research/results.md「AI 代理人實驗室」.

- ``variants``     the cohort: each agent design with the one question it answers and what it is compared with;
- ``desk``         what an agent may read on a day (every tool gated at the decision time): the six tools of the first
                   agent (research/llm_agent.py) and the lab's — headlines, the market's mood, the research knowledge
                   base, a review of its own closed trades, a check of a list it is about to hold;
- ``runner``       one variant's decision on one day (one agent in steps, three runs voting, a veto on the mechanical
                   list, analysts and a manager), the append-only stores, the cohort's day and a replay (never evidence);
- ``environment``  the simulation's guarantees: a panel blinded after the day and the check that no tool reads past it,
                   scripted agents that cost nothing, and the random agents an agent's picks are ranked among;
- ``evaluate``     the cohort's board from the platform's own engine (D-017): each variant's account from its first
                   live decision against its twin, 0050 and the first agent — both kinds of money (D-020, D-045);
- ``probe``        how much of each past month the model already knows (asked without data): where a replay is
                   contaminated.
"""

VERSION = "agent-lab-1.0.0"
