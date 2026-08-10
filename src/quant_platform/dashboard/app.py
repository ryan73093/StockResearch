from __future__ import annotations

from dataclasses import asdict
from collections import Counter
from datetime import UTC, date, datetime, timedelta
import json
import secrets
import statistics
from zoneinfo import ZoneInfo

from flask import Flask, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import inspect, text

from quant_platform.container import Container, build_container
from quant_platform.application.analytics import DEFAULT_UNIVERSE
from quant_platform.application.odd_lot_research import OddLotAssumptions


DATABASE_TABLE_DESCRIPTIONS = {
    "research_universe": "股票名稱、產業與研究池狀態",
    "universe_memberships": "歷史股票池成員，避免只看到存活公司",
    "market_bars": "日線與盤中 OHLCV 行情",
    "feature_values": "模型可使用的特徵值",
    "label_values": "模型訓練目標與未來報酬",
    "daily_decisions": "每日股票判斷與阻擋原因",
    "model_experiments": "機器學習研究版本與成效",
    "model_predictions": "模型預測結果",
    "backtest_runs": "單股策略回測摘要",
    "backtest_trades": "單股策略歷史交易",
    "portfolio_runs": "投資組合回測摘要",
    "portfolio_allocation_points": "投資組合每次調倉權重",
    "portfolio_equity_points": "投資組合每日資金曲線",
    "paper_orders": "向前模擬交易委託",
    "paper_fills": "向前模擬實際成交",
    "scheduler_job_runs": "資料補抓與研究排程歷程",
    "news_articles": "新聞原文與時間戳",
    "point_in_time_observations": "依當時可取得時間保存的事件資料",
}


def create_app(container: Container | None = None) -> Flask:
    dependencies = container or build_container()
    app_started_at = datetime.now(UTC)
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=dependencies.settings.secret_key,
        ENV=dependencies.settings.app_env,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=dependencies.settings.auth_cookie_secure,
    )
    app.extensions["quant_container"] = dependencies
    session_cookie = "quant_session"
    oauth_nonce_cookie = "quant_oauth_nonce"

    @app.before_request
    def load_current_user() -> None:
        g.current_user = dependencies.authentication_service.authenticate(
            request.cookies.get(session_cookie)
        )
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if dependencies.authentication_service.enabled and g.current_user is None:
                flash("請先登入，才能執行會修改資料的操作。", "error")
                return redirect(url_for("account_overview"))
            if dependencies.settings.is_production and not dependencies.authentication_service.enabled:
                return (
                    "正式環境尚未設定 Google 登入，因此所有網頁寫入操作維持關閉。",
                    503,
                )

    @app.context_processor
    def authentication_context() -> dict[str, object]:
        return {
            "current_user": getattr(g, "current_user", None),
            "google_login_enabled": dependencies.authentication_service.enabled,
        }

    def system_readiness_snapshot(expansion=None) -> dict[str, int | float]:
        """Small, indexed counts shared by status pages; never runs full table scans."""
        expansion = expansion or dependencies.universe_expansion_service.overview()
        engine = dependencies.database.engine
        tables = set(inspect(engine).get_table_names())

        def scalar(connection, sql: str, table_name: str) -> int:
            if table_name not in tables:
                return 0
            return int(connection.execute(text(sql)).scalar_one() or 0)

        with engine.connect() as connection:
            decision_days = scalar(
                connection,
                "SELECT COUNT(DISTINCT DATE(event_time)) FROM daily_decisions WHERE market='TW'",
                "daily_decisions",
            )
            paper_days = scalar(
                connection,
                "SELECT COUNT(DISTINCT DATE(submitted_at)) FROM paper_orders WHERE market='TW'",
                "paper_orders",
            )
            paper_orders = scalar(
                connection, "SELECT COUNT(*) FROM paper_orders", "paper_orders"
            )
            paper_fills = scalar(
                connection, "SELECT COUNT(*) FROM paper_fills", "paper_fills"
            )

        registered = max(int(expansion.registered_assets), 0)
        price_ready = max(int(expansion.data_ready_assets), 0)
        return {
            "registered_assets": registered,
            "price_ready_assets": price_ready,
            "price_missing_assets": max(0, registered - price_ready),
            "price_percent": round(price_ready / registered * 100) if registered else 0,
            "decision_days": decision_days,
            "decision_target": 756,
            "decision_missing_days": max(0, 756 - decision_days),
            "decision_percent": min(100, round(decision_days / 756 * 100)),
            "paper_days": paper_days,
            "paper_target": 252,
            "paper_missing_days": max(0, 252 - paper_days),
            "paper_percent": min(100, round(paper_days / 252 * 100)),
            "paper_orders": paper_orders,
            "paper_fills": paper_fills,
        }

    def sparkline_points(values: list[float], width: int = 100, height: int = 36) -> str:
        if not values:
            return ""
        low, high = min(values), max(values)
        spread = high - low or 1
        divisor = max(len(values) - 1, 1)
        return " ".join(
            f"{index / divisor * width:.2f},{height - (value - low) / spread * height:.2f}"
            for index, value in enumerate(values)
        )

    app.jinja_env.globals["sparkline_points"] = sparkline_points

    def shared_chart_points(
        values: list[float], low: float, high: float, width: int = 100, height: int = 36
    ) -> str:
        if not values:
            return ""
        spread = high - low or 1
        divisor = max(len(values) - 1, 1)
        return " ".join(
            f"{index / divisor * width:.2f},{height - (value - low) / spread * height:.2f}"
            for index, value in enumerate(values)
        )

    app.jinja_env.globals["shared_chart_points"] = shared_chart_points

    feature_labels = {
        "return_1d": "一日報酬", "return_5d": "五日報酬",
        "momentum_20d": "二十日動能", "momentum_60d": "六十日動能",
        "close_to_sma_5": "相對五日均線", "close_to_sma_20": "相對二十日均線",
        "close_to_ema_12": "相對十二日指數均線", "close_to_ema_26": "相對二十六日指數均線",
        "rsi_14": "十四日相對強弱指標", "atr_14_pct": "十四日平均真實波幅",
        "volatility_20d": "二十日年化波動", "volume_zscore_20": "成交量標準分數",
        "drawdown_252d": "距一年高點回撤", "day_of_week": "星期代碼",
        "institutional_net_buy": "三大法人買賣超", "margin_purchase_balance": "融資今日餘額",
        "short_sale_balance": "融券今日餘額", "securities_lending_quantity": "借券交易數量",
        "pe_ratio": "本益比", "pb_ratio": "股價淨值比",
        "dividend_yield": "現金殖利率", "monthly_revenue": "單月營收",
        "quarterly_eps": "季度每股盈餘", "roe_annualized": "年化股東權益報酬率",
        "roa_annualized": "年化資產報酬率", "gross_margin": "營業毛利率",
        "free_cash_flow": "自由現金流", "free_cash_flow_margin": "自由現金流率",
        "sp500_return_20d": "標普500 二十日報酬", "sox_return_20d": "費城半導體二十日報酬",
        "twii_return_20d": "台灣加權二十日報酬", "gold_return_20d": "黃金二十日報酬",
        "brent_return_20d": "布蘭特原油二十日報酬", "usdtwd_return_20d": "美元兌台幣二十日變化",
        "vix_level": "VIX 指數",
        "cpi_yoy": "美國消費者物價年增率", "fed_funds_rate": "聯邦基金有效利率",
        "unemployment_rate": "美國失業率", "gdp_yoy": "美國名目 GDP 年增率",
        "treasury_10y": "美國十年期公債殖利率", "treasury_2y": "美國兩年期公債殖利率",
        "yield_curve_10y2y": "美債十年減兩年利差",
        "news_sentiment_daily": "每日新聞情緒",
    }
    app.jinja_env.globals["feature_label"] = lambda name: feature_labels.get(name, name)

    feature_help_texts = {
        "return_1d": "觀察最近一個交易日的漲跌強弱，用於辨識短線動能與反轉風險。",
        "return_5d": "衡量近五個交易日的累積報酬，用於判斷一週趨勢是否延續。",
        "momentum_20d": "衡量約一個月的價格動能，用於比較股票的中短期相對強弱。",
        "momentum_60d": "衡量約一季的價格動能，用於辨識較穩定的中期趨勢。",
        "close_to_sma_5": "比較收盤價與五日簡單移動平均線（SMA）的距離，用於判斷短線乖離。",
        "close_to_sma_20": "比較收盤價與二十日簡單移動平均線（SMA）的距離，用於判斷月線趨勢。",
        "close_to_ema_12": "比較收盤價與十二日指數移動平均線（EMA）的距離，較重視近期價格變化。",
        "close_to_ema_26": "比較收盤價與二十六日指數移動平均線（EMA）的距離，用於確認中期趨勢。",
        "rsi_14": "相對強弱指標（RSI），用於觀察超買、超賣與動能轉折；不可單獨視為買賣訊號。",
        "atr_14_pct": "平均真實波幅（ATR）占股價比例，用於估算停損距離與部位大小。",
        "volatility_20d": "近二十日報酬波動程度，用於衡量風險並調整持股比重。",
        "volume_zscore_20": "成交量相對近二十日常態的異常程度，用於確認突破是否有量能支持。",
        "drawdown_252d": "相對近一年高點的回落幅度，用於評估下行風險與套牢壓力。",
        "day_of_week": "交易日的星期資訊，用於檢查是否存在穩定的日曆效應。",
        "institutional_net_buy": "三大法人買賣超，用於觀察大型資金近期偏多或偏空。",
        "margin_purchase_balance": "融資餘額，用於觀察散戶槓桿與多方擁擠程度。",
        "short_sale_balance": "融券餘額，用於觀察放空需求、回補壓力與空方擁擠程度。",
        "securities_lending_quantity": "借券數量，用於觀察機構避險或放空需求。",
        "pe_ratio": "本益比（P/E），用於比較價格相對獲利是否昂貴，需搭配成長性判讀。",
        "pb_ratio": "股價淨值比（P/B），用於比較價格相對帳面淨值，金融與資產股較常使用。",
        "dividend_yield": "股利殖利率，用於衡量現金股利相對股價的收益水準。",
        "monthly_revenue": "月營收，用於追蹤公司營運動能與季報公布前的變化。",
        "quarterly_eps": "每股盈餘（EPS），用於衡量公司單季替每股創造的獲利。",
        "roe_annualized": "年化股東權益報酬率（ROE），用於評估公司運用股東資本的效率。",
        "roa_annualized": "年化資產報酬率（ROA），用於評估公司運用全部資產的效率。",
        "gross_margin": "毛利率，用於觀察產品定價能力、成本壓力與競爭優勢。",
        "free_cash_flow": "自由現金流，用於判斷公司在維持營運後可自由運用的現金。",
        "free_cash_flow_margin": "自由現金流占營收比例，用於比較不同規模公司的現金轉換效率。",
        "sp500_return_20d": "標普 500 指數近二十日報酬，用於判斷全球大型股風險偏好。",
        "sox_return_20d": "費城半導體指數近二十日報酬，用於評估台灣電子與半導體股的外部環境。",
        "twii_return_20d": "台灣加權指數近二十日報酬，用於衡量個股所處的大盤趨勢。",
        "gold_return_20d": "黃金近二十日報酬，用於觀察避險需求與市場不確定性。",
        "brent_return_20d": "布蘭特原油近二十日報酬，用於評估能源成本與景氣預期。",
        "usdtwd_return_20d": "美元兌新台幣近二十日變化，用於評估匯率對出口、進口與外資流向的影響。",
        "vix_level": "波動率指數（VIX），用於觀察全球市場恐慌與風險偏好。",
        "cpi_yoy": "消費者物價指數年增率（CPI），用於評估通膨與利率環境。",
        "fed_funds_rate": "美國聯邦基金利率，用於判斷資金成本與全球流動性環境。",
        "unemployment_rate": "失業率，用於觀察景氣與勞動市場是否轉弱。",
        "gdp_yoy": "國內生產毛額年增率（GDP），用於衡量整體經濟成長速度。",
        "treasury_10y": "美國十年期公債殖利率，用於評估長期資金成本與股票估值壓力。",
        "treasury_2y": "美國二年期公債殖利率，用於觀察市場對短期政策利率的預期。",
        "yield_curve_10y2y": "十年期減二年期公債殖利率差，用於觀察景氣預期與殖利率曲線倒掛。",
        "news_sentiment_daily": "每日新聞情緒分數，用於量化事件與市場敘事偏多或偏空的程度。",
    }
    app.jinja_env.globals["feature_help"] = lambda name: feature_help_texts.get(
        name,
        "此特徵提供模型與研究流程的量化輸入，需搭配其他指標與風險資訊判讀。",
    )

    feature_categories = (
        (
            "price",
            "價格與趨勢",
            "判斷短、中期方向、乖離與超買超賣。",
            {
                "return_1d", "return_5d", "momentum_20d", "momentum_60d",
                "close_to_sma_5", "close_to_sma_20", "close_to_ema_12",
                "close_to_ema_26", "rsi_14", "day_of_week",
            },
        ),
        (
            "risk",
            "波動與風險",
            "用來決定停損距離、部位大小與是否應降低曝險。",
            {"atr_14_pct", "volatility_20d", "volume_zscore_20", "drawdown_252d"},
        ),
        (
            "flow",
            "籌碼與資金流",
            "觀察法人、融資、融券與借券是否支持目前走勢。",
            {
                "institutional_net_buy", "margin_purchase_balance",
                "short_sale_balance", "securities_lending_quantity",
            },
        ),
        (
            "fundamental",
            "基本面與估值",
            "衡量獲利品質、現金流、成長與目前價格是否合理。",
            {
                "pe_ratio", "pb_ratio", "dividend_yield", "monthly_revenue",
                "quarterly_eps", "roe_annualized", "roa_annualized",
                "gross_margin", "free_cash_flow", "free_cash_flow_margin",
            },
        ),
        (
            "market",
            "大盤、跨資產與總經",
            "判斷個股所處的市場風險、利率、匯率與景氣環境。",
            {
                "sp500_return_20d", "sox_return_20d", "twii_return_20d",
                "gold_return_20d", "brent_return_20d", "usdtwd_return_20d",
                "vix_level", "cpi_yoy", "fed_funds_rate", "unemployment_rate",
                "gdp_yoy", "treasury_10y", "treasury_2y",
                "yield_curve_10y2y", "news_sentiment_daily",
            },
        ),
    )

    def group_stock_features(values: object) -> tuple[dict[str, object], ...]:
        pairs = tuple(values or ())
        grouped: list[dict[str, object]] = []
        assigned: set[str] = set()
        for key, label, description, names in feature_categories:
            items = tuple((name, value) for name, value in pairs if name in names)
            assigned.update(name for name, _ in items)
            if items:
                grouped.append({
                    "key": key, "label": label,
                    "description": description, "items": items,
                })
        other = tuple((name, value) for name, value in pairs if name not in assigned)
        if other:
            grouped.append({
                "key": "other", "label": "其他研究特徵",
                "description": "尚未歸入固定類別的研究輸入。", "items": other,
            })
        return tuple(grouped)

    def _json_list(value: str | None) -> tuple[str, ...]:
        if not value:
            return ()
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return ()
        return tuple(str(item) for item in parsed) if isinstance(parsed, list) else ()

    def stock_prediction_summary(
        predictions: object, decision: object | None
    ) -> dict[str, object]:
        values = tuple(predictions or ())
        if not values:
            return {
                "available": False,
                "headline": "目前沒有可用的五日預測",
                "explanation": "先完成資料更新與模型推論，才可產生方向判斷。",
                "action": "不要依空白預測下單。",
            }
        predicted = [float(item.predicted_value) for item in values]
        ranks = [float(item.rank_score) for item in values]
        mean = sum(predicted) / len(predicted)
        dispersion = (
            sum((item - mean) ** 2 for item in predicted) / len(predicted)
        ) ** 0.5
        positive = sum(item > 0 for item in predicted)
        positive_ratio = positive / len(predicted)
        mean_rank = sum(ranks) / len(ranks)
        if mean >= 0.01 and positive_ratio >= 0.67:
            headline = "模型整體偏多，但不是直接買進命令"
        elif mean > 0:
            headline = "模型小幅偏多，優勢有限"
        elif mean <= -0.01 and positive_ratio <= 0.33:
            headline = "模型整體偏空"
        else:
            headline = "模型分歧，方向不明確"
        status = getattr(decision, "status", None)
        if status == "候選":
            action = "已通過研究門檻，可再進入部位與風險計算。"
        elif status == "避免":
            action = "目前不建議新增部位；持有者應檢查風險與退出條件。"
        elif status == "觀察":
            action = "先觀察，不應只因預測報酬為正就下單。"
        else:
            action = "門檻未通過或資料不足，不產生交易指令。"
        return {
            "available": True,
            "headline": headline,
            "mean": mean,
            "dispersion": dispersion,
            "positive_count": positive,
            "model_count": len(predicted),
            "mean_rank": mean_rank,
            "explanation": (
                "五日報酬是各模型估計「由最新收盤價起算，未來五個交易日」"
                "的價格變化，不是保證、目標價或單獨的買賣訊號。"
            ),
            "action": action,
        }

    strategy_descriptions = {
        "trend_momentum": "順著已形成的價格趨勢交易，趨勢反轉時退出。",
        "mean_reversion": "價格偏離近期常態後，研究是否出現回歸平均的機會。",
        "regime_aware": "依多空與波動狀態調整曝險，避免所有市場使用同一套部位。",
    }

    def stock_backtest_summary(backtests: object) -> dict[str, object]:
        values = tuple(backtests or ())
        if not values:
            return {
                "available": False,
                "headline": "目前沒有可驗證的回測結果",
                "conclusion": "沒有樣本外證據時，不應把策略名稱當成交易依據。",
            }
        best = max(values, key=lambda item: (item.excess_return, item.sharpe))
        candidate_count = sum(item.promotion_gate == "CANDIDATE" for item in values)
        if candidate_count:
            headline = f"{candidate_count} 個策略通過候選門檻"
            conclusion = "可進入下一階段風險與影子交易驗證，但仍不等於保證獲利。"
        elif best.excess_return > 0:
            headline = "有策略打敗基準，但尚未通過完整門檻"
            conclusion = (
                "最佳策略仍標示為「研究中」，代表樣本外穩定性、資料品質或"
                "風險條件至少有一項未達標，不可直接轉成真實交易。"
            )
        else:
            headline = "目前沒有策略打敗買進持有基準"
            conclusion = "回測不支持使用這些策略下單，應維持觀察或重新研究。"
        return {
            "available": True,
            "headline": headline,
            "conclusion": conclusion,
            "candidate_count": candidate_count,
            "total_count": len(values),
            "best": best,
            "best_name": {
                "trend_momentum": "趨勢動能",
                "mean_reversion": "均值回歸",
                "regime_aware": "市場狀態調整",
            }.get(best.strategy_name, best.strategy_name),
        }

    def stock_decision_summary(decision: object | None) -> dict[str, object]:
        if decision is None:
            return {
                "status": "尚未產生",
                "headline": "目前沒有系統決策",
                "action": "先更新研究資料，再到「每日決策」執行台股決策。",
                "reasons": (),
                "risks": ("沒有決策快照，不能產生買賣指令。",),
                "gates": (),
            }
        status = str(decision.status)
        headlines = {
            "候選": "已通過研究候選門檻",
            "觀察": "維持觀察，暫不下單",
            "避免": "避免新增部位",
            "資料不足": "資料不足，禁止下單",
        }
        actions = {
            "候選": "下一步是計算投資組合權重、可承受股數與委託價格。",
            "觀察": "等待模型、策略整合與資料品質門檻改善。",
            "避免": "不要因單一正向數字追價；持有者應檢查風險與退出條件。",
            "資料不足": "先補齊資料與重新推論，不做任何交易。",
        }
        return {
            "status": status,
            "headline": headlines.get(status, status),
            "action": actions.get(status, "目前不產生交易指令。"),
            "score": float(decision.score),
            "weight": float(decision.suggested_weight),
            "event_time": decision.event_time,
            "reasons": _json_list(decision.reasons_json),
            "risks": _json_list(decision.risks_json),
            "gates": _json_list(decision.gate_checks_json),
        }

    app.jinja_env.globals["group_stock_features"] = group_stock_features
    app.jinja_env.globals["stock_prediction_summary"] = stock_prediction_summary
    app.jinja_env.globals["stock_backtest_summary"] = stock_backtest_summary
    app.jinja_env.globals["stock_decision_summary"] = stock_decision_summary
    app.jinja_env.globals["strategy_description"] = lambda name: strategy_descriptions.get(
        name, "使用歷史資料驗證的研究策略。"
    )

    ratio_percent_features = {
        "return_1d", "return_5d", "momentum_20d", "momentum_60d",
        "close_to_sma_5", "close_to_sma_20", "close_to_ema_12", "close_to_ema_26",
        "atr_14_pct", "volatility_20d", "drawdown_252d", "roe_annualized",
        "roa_annualized", "gross_margin", "free_cash_flow_margin",
        "sp500_return_20d", "sox_return_20d", "twii_return_20d", "gold_return_20d",
        "brent_return_20d", "usdtwd_return_20d", "cpi_yoy", "gdp_yoy",
    }
    percentage_point_features = {
        "dividend_yield", "fed_funds_rate", "unemployment_rate",
        "treasury_10y", "treasury_2y", "yield_curve_10y2y",
    }

    def feature_value_display(name: str, value: float) -> str:
        number = float(value)
        if name in ratio_percent_features:
            return f"{number * 100:+.2f}%" if number != 0 else "0.00%"
        if name in percentage_point_features:
            return f"{number:.2f}%"
        if name in {"monthly_revenue", "free_cash_flow"}:
            return f"{number / 100_000_000:+,.2f} 億元"
        if name == "quarterly_eps":
            return f"{number:.2f} 元／股"
        if name in {"pe_ratio", "pb_ratio"}:
            return f"{number:.2f} 倍"
        if name == "institutional_net_buy":
            return f"{number:+,.0f} 股"
        if name in {
            "margin_purchase_balance", "short_sale_balance",
            "securities_lending_quantity",
        }:
            return f"{number:,.0f} 張"
        if name == "volume_zscore_20":
            return f"{number:+.2f}σ"
        if name in {"rsi_14", "vix_level"}:
            return f"{number:.1f}"
        if name == "day_of_week":
            return f"星期 {int(round(number))}"
        return f"{number:,.2f}"

    app.jinja_env.globals["feature_value_display"] = feature_value_display
    model_labels = {
        "historical_mean": "歷史平均基準", "ridge_linear": "嶺迴歸",
        "bagged_stumps": "袋裝樹樁", "gradient_boosted_stumps": "梯度提升樹樁",
        "random_forest": "隨機森林", "svm_rbf": "支援向量迴歸",
    }
    app.jinja_env.globals["model_label"] = lambda name: model_labels.get(name, name)
    taipei = ZoneInfo("Asia/Taipei")

    def taipei_time(value: datetime) -> datetime:
        aware = value if value.tzinfo else value.replace(tzinfo=UTC)
        return aware.astimezone(taipei)

    taiwan_field_labels = {
        "buy": "買進", "sell": "賣出", "name": "法人類別",
        "PER": "本益比", "PBR": "股價淨值比", "dividend_yield": "殖利率",
        "revenue": "月營收", "revenue_month": "營收月份", "revenue_year": "營收年度",
        "create_time": "公告日期", "country": "國別", "volume": "借券數量",
        "type": "財報科目", "value": "財報數值", "origin_name": "原始科目名稱",
        "fee_rate": "費率", "close": "收盤價", "transaction_type": "交易類型",
        "original_return_date": "原定還券日期", "original_lending_period": "原借券期間",
        "MarginPurchaseBuy": "融資買進", "MarginPurchaseSell": "融資賣出",
        "MarginPurchaseCashRepayment": "融資現金償還", "MarginPurchaseLimit": "融資限額",
        "MarginPurchaseTodayBalance": "融資今日餘額",
        "MarginPurchaseYesterdayBalance": "融資昨日餘額",
        "ShortSaleBuy": "融券買進", "ShortSaleSell": "融券賣出",
        "ShortSaleCashRepayment": "融券現券償還", "ShortSaleLimit": "融券限額",
        "ShortSaleTodayBalance": "融券今日餘額", "ShortSaleYesterdayBalance": "融券昨日餘額",
        "OffsetLoanAndShort": "資券互抵", "Note": "備註",
    }
    taiwan_value_labels = {
        "Foreign_Investor": "外資", "Investment_Trust": "投信",
        "Dealer_self": "自營商自行買賣", "Dealer_Hedging": "自營商避險",
        "Taiwan": "台灣",
    }
    app.jinja_env.globals["taipei_time"] = taipei_time
    app.jinja_env.globals["taiwan_field_label"] = lambda name: taiwan_field_labels.get(name, name)
    app.jinja_env.globals["taiwan_value_label"] = lambda value: taiwan_value_labels.get(str(value), value)

    def taiwan_value_display(
        dataset: str, key: str, value: object, fields: dict[str, object]
    ) -> str:
        translated = taiwan_value_labels.get(str(value), value)
        try:
            number = float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return str(translated)

        def numeric(decimals: int = 0, grouping: bool = True) -> str:
            if decimals == 0 and number.is_integer():
                return f"{int(number):,}" if grouping else str(int(number))
            return (
                f"{number:,.{decimals}f}"
                if grouping
                else f"{number:.{decimals}f}"
            )

        if dataset == "TaiwanStockInstitutionalInvestorsBuySell" and key in {"buy", "sell"}:
            return f"{numeric()} 股"
        if dataset == "TaiwanStockMarginPurchaseShortSale" and (
            key.startswith("MarginPurchase") or key.startswith("ShortSale")
            or key == "OffsetLoanAndShort"
        ):
            return f"{numeric()} 張"
        if dataset == "TaiwanStockSecuritiesLending":
            if key in {"volume", "transaction_quantity", "TransactionQuantity"}:
                return f"{numeric()} 張"
            if key == "close":
                return f"{numeric(2)} 元／股"
            if key == "fee_rate":
                return f"{numeric(4)}%"
            if key == "original_lending_period":
                return f"{numeric()} 天"
        if dataset == "TaiwanStockPER":
            if key in {"PER", "PBR"}:
                return f"{numeric(2)} 倍"
            if key in {"dividend_yield", "DividendYield"}:
                return f"{numeric(2)}%"
        if dataset == "TaiwanStockMonthRevenue":
            if key == "revenue":
                return f"{numeric()} 元"
            if key == "revenue_month":
                return f"{numeric()} 月"
            if key == "revenue_year":
                return f"{numeric(grouping=False)} 年"
        if dataset in {
            "TaiwanStockFinancialStatements", "TaiwanStockBalanceSheet",
            "TaiwanStockCashFlowsStatement",
        } and key == "value":
            statement_type = str(fields.get("type", "")).upper()
            return f"{numeric(2)} 元／股" if "EPS" in statement_type else f"{numeric(2)} 元"
        return f"{numeric(4)}（來源未標示單位）"

    app.jinja_env.globals["taiwan_value_display"] = taiwan_value_display

    def promotion_value_display(value: object, unit: str) -> str:
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, (int, float)):
            if unit == "%":
                return f"{value * 100:,.2f}%"
            if unit in {"折", "筆", "天", "項"}:
                return f"{int(value):,} {unit}"
            if unit == "元":
                return f"{value:,.0f} 元"
            if unit == "無單位":
                return f"{value:,.2f}（無單位）"
        return f"{value}（{unit}）" if unit else str(value)

    app.jinja_env.globals["promotion_value_display"] = promotion_value_display

    def quality_value_display(value: object, unit: str) -> str:
        if value is None:
            return "無資料"
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, (int, float)):
            if unit == "%":
                return f"{value * 100:,.2f}%"
            if isinstance(value, int) or float(value).is_integer():
                return f"{int(value):,} {unit}".strip()
            return f"{float(value):,.4f}（{unit or '無單位'}）"
        return f"{value}（{unit}）" if unit and unit != "狀態" else str(value)

    app.jinja_env.globals["quality_value_display"] = quality_value_display

    @app.get("/")
    def index() -> str:
        overview = dependencies.research_overview_service.get_overview()
        active_symbols = set(dependencies.research_universe_service.active_symbols())
        # The command center is a compact market dashboard, not the full stock
        # screener.  Passing the expanded research universe here makes the page
        # serialize every asset's price curve and candlesticks into HTML.
        dashboard_symbols = [
            symbol for symbol in DEFAULT_UNIVERSE if symbol in active_symbols
        ]
        command_center = dependencies.quant_analytics_service.command_center(
            dashboard_symbols
        )
        data_overview = dependencies.market_data_overview_service.get_overview()
        return render_template(
            "index.html",
            overview=overview,
            command=command_center,
            data_overview=data_overview,
        )

    @app.get("/guide")
    def usage_guide() -> str:
        return render_template("guide.html")

    @app.get("/account")
    def account_overview() -> str:
        return render_template("account.html")

    @app.get("/auth/google/start")
    def google_login_start():
        browser_nonce = secrets.token_urlsafe(32)
        try:
            login = dependencies.authentication_service.begin(
                browser_nonce, request.args.get("next")
            )
        except Exception as exc:
            flash(str(exc), "error")
            return redirect(url_for("account_overview"))
        response = redirect(login.authorization_url)
        response.set_cookie(
            oauth_nonce_cookie, browser_nonce, max_age=600, httponly=True,
            secure=dependencies.settings.auth_cookie_secure, samesite="Lax",
        )
        return response

    @app.get("/auth/google/callback")
    def google_login_callback():
        try:
            if request.args.get("error"):
                raise PermissionError(f"Google 拒絕登入：{request.args['error']}")
            result = dependencies.authentication_service.complete(
                request.args.get("state", ""), request.args.get("code", ""),
                request.cookies.get(oauth_nonce_cookie, ""),
            )
            response = redirect(result.next_path)
            response.set_cookie(
                session_cookie, result.session_token,
                max_age=max(1, dependencies.settings.auth_session_days) * 86400,
                httponly=True, secure=dependencies.settings.auth_cookie_secure,
                samesite="Lax",
            )
            response.delete_cookie(oauth_nonce_cookie)
            return response
        except Exception as exc:
            app.logger.exception("Google login callback failed")
            flash(f"Google 登入失敗：{exc}", "error")
            response = redirect(url_for("account_overview"))
            response.delete_cookie(oauth_nonce_cookie)
            return response

    @app.post("/logout")
    def logout():
        dependencies.authentication_service.logout(request.cookies.get(session_cookie))
        response = redirect(url_for("account_overview"))
        response.delete_cookie(session_cookie)
        flash("已登出。", "success")
        return response

    @app.get("/roadmap")
    def development_roadmap() -> str:
        return project_progress()

    @app.get("/progress")
    def project_progress() -> str:
        memory = dependencies.daily_report_knowledge_service.status()
        expansion = dependencies.universe_expansion_service.overview()
        readiness = system_readiness_snapshot(expansion)
        validation = dependencies.after_hours_ai_service.validation_for_page()
        portfolios = dependencies.portfolio_overview_service.get_overview()
        models = dependencies.model_research_overview_service.get_overview()
        paper = dependencies.paper_trading_service.overview()

        tw_portfolios = [item for item in portfolios.runs if item.run.market == "TW"]
        latest_portfolio_time = max(
            (item.run.computed_at for item in tw_portfolios), default=None
        )
        latest_portfolios = [
            item for item in tw_portfolios if item.run.computed_at == latest_portfolio_time
        ]
        best_portfolio = max(
            latest_portfolios,
            key=lambda item: item.run.excess_to_benchmark,
            default=None,
        )
        best_excess = (
            best_portfolio.run.excess_to_benchmark if best_portfolio is not None else None
        )
        strategy_verified = bool(
            validation.strategy_gate in {"CANDIDATE", "候選"}
            and validation.excess_return is not None
            and validation.excess_return > 0
            and validation.decision_sessions >= 252
        )

        items = (
            {
                "title": "財經新聞向量記憶",
                "status": "已完成，可實際使用" if memory.news_document_count else "建置中",
                "status_key": "usable" if memory.news_document_count else "building",
                "summary": "財經新聞已在本機建立可搜尋的語意記憶。",
                "evidence": (
                    f"新聞文件 {memory.news_document_count:,} 則",
                    f"全部研究文件 {memory.document_count:,} 份",
                    f"向量片段 {memory.chunk_count:,} 段",
                    f"{memory.embedding_model}／{memory.embedding_device.upper()}／{memory.embedding_dimensions} 維",
                ),
                "next": "持續由每日資料流程加入新新聞，並保留來源與可用時間。",
                "url": "/reports",
                "action": "開啟財經記憶",
            },
            {
                "title": "OpenAI 財經問答",
                "status": "已完成，可實際使用" if memory.external_llm_enabled else "尚未啟用",
                "status_key": "usable" if memory.external_llm_enabled else "planned",
                "summary": "問題先由本機找證據，再交給 OpenAI 產生有引用的中文回答。",
                "evidence": (
                    f"回答模型：{memory.answer_model}",
                    "只傳問題與最多 4 段證據",
                    "OpenAI embedding：關閉",
                    f"已保存問答稽核 {memory.recent_query_count:,} 筆",
                ),
                "next": "加入回答品質評分與錯誤分類，持續檢查引用是否真的支持結論。",
                "url": "/reports?question=緯創近期新聞",
                "action": "實際提問驗證",
            },
            {
                "title": "GPU 離線 Embedding（向量化）",
                "status": "已完成並實測" if memory.embedding_device == "cuda" else "CPU 可用／GPU 待啟用",
                "status_key": "usable" if memory.embedding_device == "cuda" else "verifying",
                "summary": "新聞向量在公司電腦本機產生，不需要 embedding API。",
                "evidence": (
                    f"執行裝置：{memory.embedding_device.upper()}",
                    "PyTorch CUDA 11.8 已驗證",
                    "GTX 1060 6GB 已執行中文向量批次",
                    "離線模型快取已建立",
                ),
                "next": "新增每次索引的耗時、GPU 記憶體與失敗紀錄到頁面。",
                "url": "/reports",
                "action": "查看向量狀態",
            },
            {
                "title": "重複模組簡化整合",
                "status": "已整合，等待使用驗證",
                "status_key": "verifying",
                "summary": "側邊欄已縮成核心決策、進階研究、資料與記憶、執行與監控、說明設定五類。",
                "evidence": (
                    "盤後 AI＋每日決策＋盤後零股整合為同一入口",
                    "新聞＋研究報告＋向量問答整合為同一入口",
                    "模型＋解釋、回測＋交易歷程合併顯示",
                    "舊網址暫時保留，避免既有連結失效",
                ),
                "next": "依實際使用紀錄移除無人使用的舊頁，而不是只把連結藏起來。",
                "url": "/ai-trading",
                "action": "驗證整合後入口",
            },
            {
                "title": "台股研究股票池與歷史資料",
                "status": "已可研究" if expansion.data_ready_assets >= expansion.target_ready_assets else "背景建置中",
                "status_key": "usable" if expansion.data_ready_assets >= expansion.target_ready_assets else "building",
                "summary": "股票池不再只限 31 檔，背景流程持續補足台股研究樣本。",
                "evidence": (
                    f"可研究長歷史股票 {expansion.data_ready_assets:,}／目標 {expansion.target_ready_assets:,} 檔",
                    f"短歷史股票 {expansion.short_history_assets:,} 檔",
                    f"新上市事件層 {expansion.new_listing_assets:,} 檔",
                    f"決策已覆蓋 {expansion.decision_covered_assets:,} 檔",
                ),
                "next": "補除權息、下市、產業歷史與存活者偏誤資料，並顯示各來源完整率。",
                "url": "/data-pipeline",
                "action": "查看背景資料進度",
            },
            {
                "title": "盤後策略打贏 0050",
                "status": "已通過研究門檻" if strategy_verified else "研究中，尚未證明",
                "status_key": "usable" if strategy_verified else "building",
                "summary": "目標是扣除成本後穩定優於 0050；目前不能宣稱已達成。",
                "evidence": (
                    f"資料庫已保存決策快照 {readiness['decision_days']:,} 日",
                    f"目前可完整重播 {validation.decision_sessions:,} 日",
                    f"模擬委託 {validation.simulated_orders:,} 筆／完整來回 {validation.simulated_round_trips:,} 筆",
                    f"策略報酬 {validation.total_return:+.2%}",
                    "0050 報酬：" + (f"{validation.benchmark_return:+.2%}" if validation.benchmark_return is not None else "資料不足"),
                    "超額報酬：" + (f"{validation.excess_return:+.2%}" if validation.excess_return is not None else "尚未算出"),
                ),
                "next": "補齊逐日模型預測、選股排名、買賣原因與持倉重播；至少涵蓋多頭、空頭、盤整三種市場，再進行一年以上向前模擬。",
                "url": "/ai-trading",
                "action": "查看策略證據",
            },
            {
                "title": "策略比較、版本與管理",
                "status": "部分可用，仍需補強",
                "status_key": "verifying",
                "summary": "已有多種配置策略、版本、研究門檻與大盤比較，但完整模型預測交易重播尚未接通。",
                "evidence": (
                    f"投資組合研究 {portfolios.run_count:,} 組",
                    f"最新候選策略 {portfolios.candidate_count:,} 組",
                    f"模型實驗 {models.run_count:,} 組",
                    "目前最佳策略相對 0050：" + (f"{best_excess:+.2%}" if best_excess is not None else "尚無結果"),
                ),
                "next": "每次策略變更保存資料版本、參數、預測、交易、失敗門檻與前後績效差異。",
                "url": "/strategies",
                "action": "比較策略",
            },
            {
                "title": "真實時間向前模擬",
                "status": "蒐集中",
                "status_key": "building",
                "summary": "向前模擬是每天只用當時可取得資料產生決策，不能拿歷史回測替代。",
                "evidence": (
                    f"模擬帳戶委託 {len(paper.orders):,} 筆",
                    f"實際模擬成交 {len(paper.fills):,} 筆",
                    f"模擬帳戶報酬 {paper.total_return:+.2%}",
                    f"目前持倉 {len(paper.positions):,} 檔",
                ),
                "next": "每日盤後固定保存當時決策，累積至少 252 個交易日，再與同期間 0050 公平比較。",
                "url": "/paper-trading",
                "action": "查看模擬帳戶",
            },
            {
                "title": "新聞、法說與產業資訊進入選股模型",
                "status": "規劃中",
                "status_key": "planned",
                "summary": "目前新聞可搜尋、可問答，但還沒有證明它能提高策略報酬，因此尚未加入正式選股權重。",
                "evidence": (
                    "新聞記憶：已完成",
                    "新聞情緒基線：已有",
                    "Point-in-time 增量回測：未完成",
                    "加入模型前後的超額報酬比較：未完成",
                ),
                "next": "建立不含新聞與含新聞兩組完全相同的歷史實驗，只在樣本外穩定改善才接入策略。",
                "url": "/reports",
                "action": "查看目前新聞資料",
            },
            {
                "title": "雲端資料庫與正式營運",
                "status": "規劃中",
                "status_key": "planned",
                "summary": "目前 SQLite 適合單機研究；多人、長期排程與大量向量才需要 PostgreSQL。",
                "evidence": (
                    "本機 SQLite：可用",
                    "PostgreSQL／pgvector：尚未遷移",
                    "物件儲存：尚未建立",
                    "真實券商下單：維持關閉",
                ),
                "next": "先完成策略證據與備份，再決定免費方案或付費雲端，避免為尚未使用的基礎設施付費。",
                "url": "/database",
                "action": "查看目前資料庫",
            },
        )
        summary = {
            "usable": sum(item["status_key"] == "usable" for item in items),
            "verifying": sum(item["status_key"] == "verifying" for item in items),
            "building": sum(item["status_key"] == "building" for item in items),
            "planned": sum(item["status_key"] == "planned" for item in items),
        }
        try:
            tw_quality = next(
                (
                    item for item in dependencies.data_quality_service.overview().latest
                    if item.snapshot.market == "TW"
                ),
                None,
            )
        except Exception:
            tw_quality = None
        quality_checks = len(tw_quality.checks) if tw_quality else 0
        quality_passed = sum(item.passed for item in tw_quality.checks) if tw_quality else 0
        validation_gates = (
            readiness["decision_days"] >= readiness["decision_target"],
            validation.excess_return is not None and validation.excess_return > 0,
            bool(tw_quality and tw_quality.snapshot.blocking_issue_count == 0),
        )
        roadmap = (
            {
                "name": "台股長期行情",
                "current": f"{readiness['price_ready_assets']}／{readiness['registered_assets']} 檔達 252 日",
                "missing": f"還差 {readiness['price_missing_assets']} 檔",
                "percent": readiness["price_percent"],
                "url": "/database",
            },
            {
                "name": "資料品質檢查",
                "current": f"{quality_passed}／{quality_checks} 項通過" if quality_checks else "尚無品質快照",
                "missing": (
                    f"嚴重 {tw_quality.snapshot.blocking_issue_count}、警告 {tw_quality.snapshot.issue_count - tw_quality.snapshot.blocking_issue_count}"
                    if tw_quality else "等待品質流程"
                ),
                "percent": round(quality_passed / quality_checks * 100) if quality_checks else 0,
                "url": "/data-quality",
            },
            {
                "name": "歷史決策驗證",
                "current": f"{readiness['decision_days']}／{readiness['decision_target']} 個交易日",
                "missing": f"還差 {readiness['decision_missing_days']} 日",
                "percent": readiness["decision_percent"],
                "url": "/ai-trading#validation",
            },
            {
                "name": "策略可用門檻",
                "current": f"{sum(validation_gates)}／3 關通過",
                "missing": "長期歷史、贏過 0050、資料無嚴重錯誤",
                "percent": round(sum(validation_gates) / len(validation_gates) * 100),
                "url": "/strategies",
            },
            {
                "name": "真實時間模擬",
                "current": f"{readiness['paper_days']}／{readiness['paper_target']} 個交易日",
                "missing": f"還差 {readiness['paper_missing_days']} 日；{readiness['paper_fills']} 筆成交",
                "percent": readiness["paper_percent"],
                "url": "/paper-trading",
            },
        )
        return render_template(
            "progress.html", items=items, summary=summary, roadmap=roadmap,
            readiness=readiness, generated_at=datetime.now(UTC)
        )

    def requested_odd_lot_overview():
        try:
            assumptions = OddLotAssumptions(
                monthly_budget=max(1_000, request.args.get("budget", 10_000, type=int)),
                salary_day=min(28, max(1, request.args.get("salary_day", 5, type=int))),
                commission_discount=min(1.0, max(0.01, request.args.get("discount", 0.28, type=float))),
                minimum_fee=max(0, request.args.get("minimum_fee", 1, type=int)),
                slippage_bps=max(0.0, request.args.get("slippage_bps", 10.0, type=float)),
            )
            overview = dependencies.odd_lot_research_service.run(assumptions)
        except Exception as exc:
            app.logger.exception("Odd-lot research failed")
            flash(f"盤後零股研究失敗：{exc}", "error")
            overview = dependencies.odd_lot_research_service.run()
        return overview

    @app.get("/odd-lot")
    def odd_lot_research() -> str:
        return after_hours_ai_overview()

    @app.get("/paper-trading")
    def paper_trading_overview() -> str:
        return render_template(
            "paper_trading.html", overview=dependencies.paper_trading_service.overview()
        )

    @app.get("/rl-lab")
    def reinforcement_learning_lab() -> str:
        symbol = request.args.get("symbol", "2330")
        return render_template(
            "rl_lab.html", overview=dependencies.rl_environment_service.overview(symbol)
        )

    @app.post("/rl-lab/walk-forward")
    def run_reinforcement_learning_walk_forward():
        symbol = request.form.get("symbol", "2330")
        try:
            result = dependencies.rl_environment_service.run_walk_forward(symbol)
            candidates = sum(
                item.promotion_gate == "CANDIDATE" for item in result.policies
            )
            verb = "復用既有" if result.reused else "建立新"
            flash(
                f"已{verb}樣本外實驗 #{result.experiment_id}："
                f"{result.fold_count} 折、候選代理 {candidates} 個。",
                "success",
            )
        except Exception as exc:
            app.logger.exception("RL walk-forward research failed")
            flash(f"滾動樣本外研究失敗：{exc}", "error")
        return redirect(url_for("reinforcement_learning_lab", symbol=symbol))

    @app.post("/rl-lab/cpu-agent")
    def run_reinforcement_learning_cpu_agent():
        symbol = request.form.get("symbol", "2330")
        try:
            result = dependencies.rl_environment_service.run_cpu_agent(symbol)
            verb = "復用既有" if result.reused else "完成新"
            status_text = (
                "通過候選門檻" if result.summary.promotion_gate == "CANDIDATE"
                else f"未通過 {len(result.summary.failed_gates)} 項門檻"
            )
            flash(
                f"已{verb} CPU 代理實驗 #{result.experiment_id}："
                f"{result.fold_count} 折，{status_text}。",
                "success",
            )
        except Exception as exc:
            app.logger.exception("RL CPU agent training failed")
            flash(f"CPU 代理訓練失敗：{exc}", "error")
        return redirect(url_for("reinforcement_learning_lab", symbol=symbol))

    @app.post("/rl-lab/neural-agent/<algorithm>")
    def run_reinforcement_learning_neural_agent(algorithm: str):
        symbol = request.form.get("symbol", "2330")
        try:
            result = dependencies.rl_environment_service.run_neural_agent(
                algorithm, symbol
            )
            verb = "復用既有" if result.reused else "完成新"
            status_text = (
                "通過候選門檻" if result.summary.promotion_gate == "CANDIDATE"
                else f"未通過 {len(result.summary.failed_gates)} 項門檻"
            )
            flash(
                f"已{verb} {result.algorithm.upper()} 實驗 #{result.experiment_id}："
                f"{result.fold_count} 折，{status_text}。",
                "success",
            )
        except Exception as exc:
            app.logger.exception("Neural RL agent training failed")
            flash(f"{algorithm.upper()} 訓練失敗：{exc}", "error")
        return redirect(url_for("reinforcement_learning_lab", symbol=symbol))

    @app.get("/shadow-trading")
    def shadow_trading_overview() -> str:
        return render_template(
            "shadow_trading.html",
            overview=dependencies.shadow_trading_service.overview(),
        )

    @app.post("/shadow-trading/generate")
    def generate_shadow_order():
        symbol = request.form.get("symbol", "2330")
        algorithm = request.form.get("algorithm", "cpu")
        try:
            result = dependencies.shadow_trading_service.generate(symbol, algorithm)
            verb = "復用" if result.reused else "建立"
            flash(
                f"已{verb} {result.order.algorithm.upper()} 影子委託 #"
                f"{result.order.id}：{result.order.side} {result.order.quantity:,} 股。",
                "success",
            )
        except Exception as exc:
            app.logger.exception("Shadow order generation failed")
            flash(f"影子委託建立失敗：{exc}", "error")
        return redirect(url_for("shadow_trading_overview"))

    @app.post("/shadow-trading/process")
    def process_shadow_orders():
        result = dependencies.shadow_trading_service.process_pending()
        flash(
            f"影子評估完成：新增評估 {result.evaluated} 筆、"
            f"等待下一交易日 {result.pending} 筆。",
            "success",
        )
        return redirect(url_for("shadow_trading_overview"))

    @app.get("/promotions")
    def promotion_overview() -> str:
        return render_template(
            "promotions.html", overview=dependencies.promotion_service.overview(),
            shadow_orders=dependencies.shadow_trading_service.overview().orders,
        )

    @app.post("/promotions/evaluate")
    def evaluate_promotion():
        symbol = request.form.get("symbol", "2330")
        algorithm = request.form.get("algorithm", "cpu")
        try:
            result = dependencies.promotion_service.evaluate(symbol, algorithm)
            flash(
                f"晉級檢核完成：{result.passed_count}/{result.total_count} 項通過，"
                f"目前狀態為 {result.review.status.value}。", "success",
            )
        except Exception as exc:
            app.logger.exception("Promotion evaluation failed")
            flash(f"晉級檢核失敗：{exc}", "error")
        return redirect(url_for("promotion_overview"))

    @app.get("/model-governance")
    def model_governance_overview() -> str:
        overview = dependencies.model_governance_service.overview()
        selected_id = request.args.get("entry", type=int)
        selected = next(
            (item for item in overview.entries if item.entry.id == selected_id),
            next((item for item in overview.entries if item.recommended), None),
        )
        return render_template(
            "model_governance.html", overview=overview, selected=selected,
        )

    @app.get("/data-quality")
    def data_quality_overview() -> str:
        overview = dependencies.data_quality_service.overview()
        market = request.args.get("market", "TW").upper()
        selected = next(
            (item for item in overview.latest if item.snapshot.market == market), None
        )
        issue_counts = Counter(item.code for item in selected.issues) if selected else Counter()
        severity_counts = (
            Counter(item.severity.value for item in selected.issues)
            if selected else Counter()
        )
        query = request.args.get("q", "").strip().upper()
        visible_issues = tuple(
            item for item in (selected.issues if selected else ())
            if not query
            or query in (item.symbol or "").upper()
            or query in item.message.upper()
            or query in item.category.upper()
        )
        return render_template(
            "data_quality.html", overview=overview, selected=selected, market=market,
            issue_counts=issue_counts, severity_counts=severity_counts,
            visible_issues=visible_issues, issue_query=query,
            provider_status=dependencies.taiwan_data_pipeline.provider_status(),
        )

    @app.post("/data-quality/evaluate")
    def evaluate_data_quality():
        market = request.form.get("market", "TW").upper()
        stage = request.form.get("stage", "full")
        try:
            result = dependencies.data_quality_service.evaluate(market, stage=stage)
            snapshot = result.snapshot
            flash(
                f"{market} 資料品質檢查完成：{snapshot.active_asset_count} 個標的、"
                f"{snapshot.issue_count} 個問題、{snapshot.blocking_issue_count} 個嚴重問題；"
                f"研究閘門{'允許通過' if snapshot.research_allowed else '已阻擋'}。",
                "success" if snapshot.research_allowed else "error",
            )
        except Exception as exc:
            app.logger.exception("Data quality evaluation failed")
            flash(f"資料品質檢查失敗：{exc}", "error")
        return redirect(url_for("data_quality_overview", market=market))

    @app.post("/model-governance/refresh")
    def refresh_model_governance():
        try:
            result = dependencies.model_governance_service.refresh()
            flash(
                f"模型治理更新完成：登錄 {result.registered} 個、漂移快照 "
                f"{result.drift_snapshots} 筆、自動降級 {result.automatically_demoted} 個、"
                f"略過 {result.skipped} 個。", "success",
            )
        except Exception as exc:
            app.logger.exception("Model governance refresh failed")
            flash(f"模型治理更新失敗：{exc}", "error")
        return redirect(url_for("model_governance_overview"))

    @app.post("/model-governance/<int:entry_id>/promote")
    def promote_model_champion(entry_id: int):
        try:
            dependencies.model_governance_service.promote(
                entry_id, request.form.get("reviewer", ""), request.form.get("note", ""),
            )
            flash("模型已升級為研究冠軍；不具任何券商交易權限。", "success")
        except Exception as exc:
            app.logger.exception("Model champion promotion failed")
            flash(f"冠軍升級被拒絕：{exc}", "error")
        return redirect(url_for("model_governance_overview", entry=entry_id))

    @app.post("/model-governance/<int:entry_id>/demote")
    def demote_model_champion(entry_id: int):
        try:
            dependencies.model_governance_service.demote(
                entry_id, request.form.get("reviewer", ""), request.form.get("note", ""),
            )
            flash("研究冠軍已人工降級。", "success")
        except Exception as exc:
            app.logger.exception("Model champion demotion failed")
            flash(f"冠軍降級失敗：{exc}", "error")
        return redirect(url_for("model_governance_overview", entry=entry_id))

    @app.post("/promotions/<int:review_id>/review")
    def decide_promotion(review_id: int):
        try:
            result = dependencies.promotion_service.review(
                review_id, request.form.get("decision", "reject"),
                request.form.get("reviewer", ""), request.form.get("note", ""),
            )
            flash(f"人工審查已保存：{result.review.status.value}。", "success")
        except Exception as exc:
            app.logger.exception("Promotion decision failed")
            flash(f"人工審查失敗：{exc}", "error")
        return redirect(url_for("promotion_overview"))

    @app.post("/promotions/<int:review_id>/revoke")
    def revoke_promotion(review_id: int):
        try:
            dependencies.promotion_service.revoke(
                review_id, request.form.get("reviewer", ""), request.form.get("note", ""),
            )
            flash("核准已撤銷，後續沙盒送單已關閉。", "success")
        except Exception as exc:
            app.logger.exception("Promotion revocation failed")
            flash(f"撤銷失敗：{exc}", "error")
        return redirect(url_for("promotion_overview"))

    @app.post("/promotions/<int:review_id>/sandbox/<int:shadow_order_id>")
    def submit_promotion_sandbox(review_id: int, shadow_order_id: int):
        try:
            audit = dependencies.promotion_service.submit_sandbox(review_id, shadow_order_id)
            flash(
                f"本機沙盒已受理 {audit.symbol} {audit.side} {audit.quantity:,} 股；"
                "沒有送到任何外部券商。", "success",
            )
        except Exception as exc:
            app.logger.exception("Sandbox promotion submission failed")
            flash(f"沙盒送單被拒絕：{exc}", "error")
        return redirect(url_for("promotion_overview"))

    @app.post("/paper-trading/orders")
    def submit_paper_order():
        try:
            order = dependencies.paper_trading_service.submit_order(
                request.form.get("symbol", ""), request.form.get("side", "BUY"),
                request.form.get("quantity", type=int) or 0,
            )
            if order.status.value == "pending":
                flash(
                    f"{order.symbol} {order.side} {order.quantity:,} 股已送出，等待下一交易日開盤資料。",
                    "success",
                )
            else:
                flash(f"委託被風控拒絕：{order.rejection_reason}", "error")
        except Exception as exc:
            app.logger.exception("Paper order submission failed")
            flash(f"模擬委託失敗：{exc}", "error")
        return redirect(url_for("paper_trading_overview"))

    @app.post("/paper-trading/process")
    def process_paper_orders():
        result = dependencies.paper_trading_service.process_pending()
        flash(
            f"待成交檢查完成：成交 {result.filled} 筆、拒絕 {result.rejected} 筆、仍等待 {result.pending} 筆。",
            "success",
        )
        return redirect(url_for("paper_trading_overview"))

    @app.post("/paper-trading/orders/<int:order_id>/cancel")
    def cancel_paper_order(order_id: int):
        cancelled = dependencies.paper_trading_service.cancel(order_id)
        flash("模擬委託已取消。" if cancelled else "只有待成交委託可以取消。", "success" if cancelled else "error")
        return redirect(url_for("paper_trading_overview"))

    @app.get("/news")
    def news_overview() -> str:
        overview = dependencies.news_research_service.get_overview(request.args.get("symbol"))
        return render_template("news.html", overview=overview)

    @app.post("/news/update")
    def update_news():
        try:
            result = dependencies.taiwan_data_pipeline.run("TW")
            dependencies.daily_report_knowledge_service.generate("TW")
            flash(
                f"新聞與台股研究資料更新完成：新增 {result.inserted:,} 筆、失敗 {result.failed} 項；"
                "財經新聞已同步到本機向量記憶。",
                "success" if result.failed == 0 else "error",
            )
        except Exception as exc:
            app.logger.exception("News update failed")
            flash(f"新聞更新失敗：{exc}", "error")
        return redirect(url_for("reports_overview"))

    @app.get("/reports")
    def reports_overview() -> str:
        overview = dependencies.daily_report_knowledge_service.overview(request.args.get("question"))
        news = dependencies.news_research_service.get_overview(
            request.args.get("symbol"), limit=20
        )
        return render_template("reports.html", overview=overview, news=news)

    @app.post("/reports/generate/<market>")
    def generate_report(market: str):
        try:
            report = dependencies.daily_report_knowledge_service.generate(market)
            flash(f"{report.title} 已產生並同步到研究知識庫。", "success")
        except Exception as exc:
            app.logger.exception("Daily report generation failed")
            flash(f"每日報告產生失敗：{exc}", "error")
        return redirect(url_for("reports_overview"))

    @app.post("/reports/reindex")
    def reindex_reports():
        try:
            result = dependencies.daily_report_knowledge_service.sync_index()
            flash(
                f"研究索引已同步：{result.document_count:,} 份文件、"
                f"{result.chunk_count:,} 個切塊，本次更新 {result.changed_chunk_count:,} 個切塊。",
                "success",
            )
        except Exception as exc:
            app.logger.exception("Knowledge index rebuild failed")
            flash(f"研究索引同步失敗：{exc}", "error")
        return redirect(url_for("reports_overview"))

    @app.get("/automation")
    def automation_overview() -> str:
        return render_template("automation.html", overview=dependencies.automation_service.overview())

    @app.post("/automation/schedules/<job_key>")
    def update_automation_schedule(job_key: str):
        try:
            dependencies.automation_service.update_schedule(
                job_key,
                hour=request.form.get("hour", type=int),
                minute=request.form.get("minute", type=int),
                max_retries=request.form.get("max_retries", type=int),
                enabled=request.form.get("enabled") == "on",
                notify_on_success=request.form.get("notify_on_success") == "on",
                notify_on_failure=request.form.get("notify_on_failure") == "on",
            )
            flash("排程已保存；背景排程器將於下次啟動時套用。", "success")
        except Exception as exc:
            app.logger.exception("Schedule update failed")
            flash(f"排程保存失敗：{exc}", "error")
        return redirect(url_for("automation_overview"))

    @app.post("/automation/run/<job_key>")
    def run_automation(job_key: str):
        result = dependencies.automation_service.execute(job_key)
        flash(
            f"{result.market} 自動流程：{result.status}；嘗試 {result.attempts} 次；通知 {result.notification_status}。",
            "success" if result.status == "succeeded" else "error",
        )
        return redirect(url_for("automation_overview"))

    @app.post("/automation/email/test")
    def test_automation_email():
        status = dependencies.automation_service.test_email()
        flash(f"Email 測試結果：{status}", "success" if status == "sent" else "error")
        return redirect(url_for("automation_overview"))

    @app.get("/decisions")
    def decision_overview() -> str:
        market = request.args.get("market") or None
        return render_template(
            "decisions.html", overview=dependencies.decision_overview_service.get_overview(market)
        )

    @app.post("/decisions/run/<market>")
    def run_daily_decisions(market: str):
        try:
            result = dependencies.daily_decision_pipeline.run(market)
            flash(
                f"{market.upper()} 每日決策已更新：{result.decision_count} 檔，"
                f"觀察 {result.observe_count} 檔、避免 {result.avoid_count} 檔。",
                "success",
            )
        except Exception as exc:
            app.logger.exception("Manual daily decision build failed")
            flash(f"每日決策更新失敗：{exc}", "error")
        return redirect(url_for("decision_overview"))

    @app.get("/ai-trading")
    def after_hours_ai_overview() -> str:
        validation = dependencies.after_hours_ai_service.validation_for_page()
        model_validation = dependencies.after_hours_ai_service.model_validation_for_page()
        model_validation_state = dependencies.after_hours_ai_service.model_validation_status()
        plan = dependencies.after_hours_ai_service.plan_for_page()
        odd_lot = requested_odd_lot_overview()
        portfolio_overview = dependencies.portfolio_overview_service.get_overview()
        model_overview = dependencies.model_research_overview_service.get_overview()
        assets = dependencies.research_universe_service.list_all()
        expansion = dependencies.universe_expansion_service.overview()
        readiness = system_readiness_snapshot(expansion)
        asset_lookup = {item.symbol: item for item in assets}
        asset_lookup.update(
            dependencies.research_universe_service.resolve_company_profiles(
                expansion.next_symbols
            )
        )
        data_lineage = (
            ("股票池／上市下市歷史", "股票池歷史服務", "research_universe、universe_memberships", "行情、特徵、模型、回測、組合", "決定當時可投資範圍，避免存活者偏差", "已接通"),
            ("日線 OHLCV／還原價", "每日行情管線", "market_bars", "特徵、標籤、回測、組合、盤後 AI", "價格、流動性、下一開盤模擬成交", "已接通"),
            ("法人／融資融券／借券／估值", "台股資料管線", "taiwan_data_points", "特徵、因子、模型", "資金流、擁擠、放空與估值訊號", "補資料中"),
            ("月營收／季財報／現金流", "台股資料管線", "taiwan_data_points", "特徵、模型、單股研究", "營運成長、獲利品質與基本面風險", "補資料中"),
            ("總經／利率／匯率／跨資產", "總經與跨資產管線", "macro_observations、feature_values", "市場狀態、模型、因子", "調整風險曝險與市場狀態", "已接通"),
            ("盤後零股／分鐘／期權", "Point-in-time 與盤中衍生管線", "point_in_time_observations、feature_revisions", "盤中衍生特徵、成交研究", "成交可行性、短期流動性與衍生情緒", "部分接通"),
            ("新聞與市場情緒", "新聞研究服務", "research_knowledge", "每日報告", "目前尚未進模型或組合權重", "未接模型"),
            ("產業分類／上下游／同業", "股票池 metadata", "research_universe.sector", "因子、組合產業上限", "產業輪動與集中風險", "資料不完整"),
            ("法說會／管理層指引", "法說事件服務", "point_in_time_observations", "公司事件頁", "目前尚未轉成模型特徵", "未接模型"),
            ("除權息／停牌／減資／公司行動", "事件與行情來源", "point_in_time_observations、market_bars", "還原價、回測、成交模擬", "避免價格跳空被誤判為報酬", "部分接通"),
            ("1／5／20 日扣成本標籤", "特徵標籤管線", "label_values", "模型、因子、Walk-forward", "模型實際要預測的未來報酬", "已接通"),
            ("現金／持倉／稅／手續費／滑價", "模擬帳戶與盤後 AI", "paper_accounts、paper_positions、paper_orders", "投資組合、歷史重播、模擬下單", "把權重轉成可執行股數與成本", "已接通"),
        )
        method_labels = {
            "cvar": "條件風險值配置（CVaR）",
            "equal_weight": "平均配置",
            "inverse_volatility": "反波動配置",
            "minimum_variance": "最小變異配置",
            "mean_variance": "報酬風險配置",
            "risk_parity": "風險平價",
            "hierarchical_risk_parity": "階層風險平價",
            "kelly": "凱利配置（Kelly）",
            "black_litterman": "Black-Litterman 配置",
            "benchmark_core_satellite": "大盤核心＋主動選股",
        }
        portfolio_views = [
            item for item in portfolio_overview.runs if item.run.market == "TW"
        ]
        latest_portfolio_time = max(
            (item.run.computed_at for item in portfolio_views), default=None
        )
        latest_portfolio_views = [
            item
            for item in portfolio_views
            if item.run.computed_at == latest_portfolio_time
        ]
        selected_portfolio_view = max(
            latest_portfolio_views,
            key=lambda item: (item.run.excess_to_benchmark, item.run.sharpe),
            default=None,
        )
        selected_allocations = []
        if selected_portfolio_view is not None:
            for symbol, weight in sorted(
                selected_portfolio_view.latest_weights.items(),
                key=lambda item: item[1],
                reverse=True,
            ):
                if weight <= 0:
                    continue
                asset = asset_lookup.get(symbol)
                selected_allocations.append(
                    {
                        "symbol": symbol,
                        "company": (
                            asset.company_abbreviation or asset.company_name
                            if asset
                            else "公司名稱待補"
                        ),
                        "sector": asset.sector if asset and asset.sector else "產業待補",
                        "weight": weight,
                    }
                )
        benchmark_core_weight = (
            selected_portfolio_view.factor_exposure.get("benchmark_core", 0.0)
            if selected_portfolio_view is not None
            else 0.0
        )
        strategy_rebalances = []
        if selected_portfolio_view is not None and selected_portfolio_view.run.id is not None:
            selected_artifact = dependencies.portfolio_overview_service.get_artifact(
                int(selected_portfolio_view.run.id)
            )
            previous_weights: dict[str, float] = {}
            if selected_artifact is not None:
                for allocation in selected_artifact.allocations:
                    weights = {
                        symbol: float(weight)
                        for symbol, weight in json.loads(allocation.weights_json).items()
                    }
                    changes = []
                    for symbol in sorted(set(previous_weights) | set(weights)):
                        before = previous_weights.get(symbol, 0.0)
                        after = weights.get(symbol, 0.0)
                        delta = after - before
                        if abs(delta) < 0.001:
                            continue
                        asset = asset_lookup.get(symbol)
                        changes.append(
                            {
                                "symbol": symbol,
                                "company": (
                                    asset.company_abbreviation or asset.company_name
                                    if asset
                                    else "公司名稱待補"
                                ),
                                "side": "增加" if delta > 0 else "減少",
                                "before": before,
                                "after": after,
                                "delta": delta,
                            }
                        )
                    if changes or allocation.turnover > 0:
                        strategy_rebalances.append(
                            {
                                "time": allocation.event_time,
                                "changes": sorted(
                                    changes, key=lambda item: abs(item["delta"]), reverse=True
                                ),
                                "turnover": allocation.turnover,
                                "cost": allocation.cost,
                                "cash": allocation.cash_weight,
                            }
                        )
                    previous_weights = weights
        strategy_rebalances.reverse()
        automation_overview = dependencies.automation_service.overview()
        expansion_runs = []
        for run in automation_overview.recent_runs:
            if run.job_name != "tw_universe_expansion":
                continue
            try:
                metrics = json.loads(run.metrics_json)
            except (TypeError, json.JSONDecodeError):
                metrics = {}
            failures = metrics.get("failures", [])
            if not isinstance(failures, list):
                failures = []
            if (
                metrics.get("attempted", 0) == 0
                and metrics.get("inserted_bars", 0) == 0
                and not metrics.get("failures")
            ):
                continue
            expansion_runs.append(
                {
                    "id": run.id,
                    "status": run.status.value,
                    "started_at": run.started_at,
                    "completed_at": run.completed_at,
                    "attempted": metrics.get("attempted", 0),
                    "inserted_bars": metrics.get("inserted_bars", 0),
                    "kept_active": metrics.get("kept_active", 0),
                    "symbols": metrics.get("symbols", []),
                    "failures": failures,
                    "error": run.error,
                }
            )
        gate_labels = {
            "OOS<252": "樣本外未滿 252 日",
            "NO_EDGE_VS_EQUAL": "未贏過平均配置",
            "NO_EDGE_VS_BENCHMARK": "未贏過 0050",
            "SHARPE<0.75": "風險調整後報酬不足",
            "DD>20%": "最大跌幅超過 20%",
            "CVAR>5%": "極端虧損風險過高",
            "ENSEMBLE_GATE": "上游股票策略未通過",
            "SURVIVORSHIP": "歷史股票池仍不完整",
        }
        strategy_rows = [
            {
                "id": item.run.id,
                "name": method_labels.get(item.run.method, item.run.method),
                "method": "統計配置",
                "version": item.run.portfolio_version,
                "status": "可候選" if item.run.promotion_gate == "CANDIDATE" else "研究中",
                "return": item.run.total_return,
                "benchmark": item.run.benchmark_return,
                "excess": item.run.excess_to_benchmark,
                "drawdown": item.run.max_drawdown,
                "turnover": item.run.turnover,
                "observations": item.run.observation_count,
                "updated_at": item.run.computed_at,
                "failed_gates": item.failed_gates,
                "status_detail": "；".join(
                    gate_labels.get(gate, gate) for gate in item.failed_gates
                ) or "歷史門檻已通過，等待向前模擬",
            }
            for item in latest_portfolio_views
        ]
        strategy_rows.sort(key=lambda item: item["excess"], reverse=True)
        benchmark_strategy = next(
            (
                item for item in strategy_rows
                if item["name"] == "大盤核心＋主動選股"
            ),
            None,
        )
        strategy_change_log = (
            {
                "date": "2026-07-26",
                "version": "1.1.0",
                "change": "新增「大盤核心＋主動選股」",
                "reason": (
                    "舊策略只管波動，沒有把贏過 0050 當成主要目標；"
                    "新版依大盤趨勢保留 30%～80% 的 0050 核心，"
                    "其餘資金才配置主動策略。"
                ),
                "result": (
                    f"歷史測試 {benchmark_strategy['return']:+.2%}，"
                    f"0050 {benchmark_strategy['benchmark']:+.2%}，"
                    f"仍少 {abs(benchmark_strategy['excess']):.2%}，所以維持研究中。"
                    if benchmark_strategy
                    else "等待歷史研究結果。"
                ),
            },
            {
                "date": "2026-07-26",
                "version": "1.0.0",
                "change": "撤銷錯誤的 +48.13% 成交重播",
                "reason": (
                    "舊重播把策略權重誤當股票持股比例，並非同一交易方法。"
                ),
                "result": "撤下 211 筆不正確成交，只保留研究引擎正式資金曲線。",
            },
        )
        model_types = {
            "historical_mean": "統計基準",
            "ridge_linear": "統計＋機器學習",
            "bagged_stumps": "機器學習",
            "random_forest": "機器學習",
            "gradient_boosted_stumps": "機器學習",
            "svm_rbf": "機器學習",
            "torch_cuda_mlp": "深度學習／GPU",
        }
        model_rows = [
            {
                "id": view.experiment.id,
                "name": view.experiment.model_name,
                "method": model_types.get(view.experiment.model_name, "機器學習"),
                "status": (
                    "預測候選"
                    if view.experiment.promotion_gate == "CANDIDATE"
                    else "研究中"
                ),
                "r2": view.experiment.r2,
                "direction": view.experiment.directional_accuracy,
                "rank_ic": view.experiment.rank_ic,
                "updated_at": view.experiment.computed_at,
            }
            for view in model_overview.runs
            if view.experiment.market == "TW"
        ]
        paper_overview = dependencies.paper_trading_service.overview()
        model_tracking = dependencies.after_hours_ai_service.model_forward_tracking()
        if model_tracking is not None:
            history_ready = bool(
                model_validation is not None
                and model_validation.decision_sessions >= 756
                and model_validation.excess_return is not None
                and model_validation.excess_return > 0
                and model_validation.max_drawdown >= -0.25
                and len(model_validation.regime_results) == 4
                and all(item.passed for item in model_validation.regime_results)
            )
            forward_ready = bool(
                model_tracking["tracked_sessions"] >= model_tracking["target_sessions"]
                and model_tracking["paper_fills"] >= model_tracking["target_fills"]
            )
            model_tracking.update({
                "history_ready": history_ready,
                "forward_ready": forward_ready,
                "usable": history_ready and forward_ready,
                "remaining_sessions": max(
                    0,
                    int(model_tracking["target_sessions"])
                    - int(model_tracking["tracked_sessions"]),
                ),
                "overfitting_risk": (
                    "中" if history_ready and model_tracking["tracked_sessions"] >= 60
                    else "高"
                ),
            })
        model_jobs = []
        for run in automation_overview.recent_runs:
            if run.job_name != "model_zoo_research" or run.market.upper() != "TW":
                continue
            try:
                metrics = json.loads(run.metrics_json or "{}")
            except (TypeError, json.JSONDecodeError):
                metrics = {}
            model_jobs.append((run, metrics))
        gpu_jobs = [
            (run, metrics) for run, metrics in model_jobs
            if "torch_cuda_mlp" in metrics.get("requested_models", [])
            or (run.error and "torch_cuda_mlp" in run.error)
        ]
        latest_model_job, latest_model_metrics = (
            gpu_jobs[0] if gpu_jobs else (None, {})
        )
        latest_gpu_failure = next(
            (
                run for run, _metrics in gpu_jobs
                if run.error and "torch_cuda_mlp" in run.error
            ),
            None,
        )
        saved_gpu_models = [
            view.experiment for view in model_overview.runs
            if view.experiment.market == "TW"
            and view.experiment.model_name == "torch_cuda_mlp"
        ]
        latest_gpu_model = max(
            saved_gpu_models, key=lambda item: item.computed_at, default=None
        )
        gpu_research = {
            "state": "failed",
            "headline": "失敗：沒有可用的深度學習結果",
            "detail": "資料庫沒有保存任何 CUDA 深度學習實驗。",
            "time": None,
        }
        if latest_gpu_model is not None:
            gpu_research = {
                "state": "ready",
                "headline": "已完成並保存深度學習結果",
                "detail": (
                    f"方向答對率 {latest_gpu_model.directional_accuracy:.1%}；"
                    f"排名 IC {latest_gpu_model.rank_ic or 0:+.3f}；"
                    "模型已保存為挑戰版本；因組合重播跑輸研究主線，"
                    "目前不納入盤後選股。"
                ),
                "time": latest_gpu_model.computed_at,
            }
        elif latest_model_job is not None and latest_model_job.status.value == "running":
            started_at = (
                latest_model_job.started_at
                if latest_model_job.started_at.tzinfo
                else latest_model_job.started_at.replace(tzinfo=UTC)
            )
            updated_at = None
            try:
                updated_at = datetime.fromisoformat(
                    str(latest_model_metrics.get("updated_at"))
                )
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                pass
            recently_updated = bool(
                updated_at is not None
                and datetime.now(UTC) - updated_at < timedelta(minutes=10)
            )
            if started_at < app_started_at and not recently_updated:
                detail = "工作在網站服務重啟前開始，程序已不存在，不能繼續顯示執行中。"
                if latest_gpu_failure is not None:
                    detail += (
                        " 最近一次明確錯誤：CUDA（顯示卡運算介面）回報未知錯誤，"
                        "因此模型、預測與績效都沒有保存。"
                    )
                gpu_research = {
                    "state": "failed",
                    "headline": "中斷：沒有產生成績",
                    "detail": detail,
                    "time": latest_model_job.started_at,
                }
            else:
                gpu_research = {
                    "state": "running",
                    "headline": (
                        f"{latest_model_metrics.get('progress', 0)}%｜"
                        f"{latest_model_metrics.get('stage', '模型研究正在執行')}"
                    ),
                    "detail": (
                        f"股票 {latest_model_metrics.get('assets', '—')} 檔；"
                        f"樣本 {latest_model_metrics.get('observations', '建立中')}；"
                        "只有實驗、歷史預測與成績成功入庫後才算完成。"
                    ),
                    "time": latest_model_job.started_at,
                }
        elif latest_gpu_failure is not None:
            gpu_research = {
                "state": "failed",
                "headline": "失敗：沒有產生成績",
                "detail": (
                    "CUDA（顯示卡運算介面）回報未知錯誤；"
                    "該次模型、預測與績效都沒有保存。"
                ),
                "time": latest_gpu_failure.completed_at or latest_gpu_failure.started_at,
            }
        failure_cases = [
            {
                "case": item,
                "evidence": json.loads(item.evidence_json or "{}"),
            }
            for item in dependencies.research_failure_memory_service.list_recent(20)
        ]
        risk_trial_definitions = {
            "after-hours-close-proxy-20260802": (
                "盤後零股同日成交代理",
                "累積每日 14:30 實際成交價與未成交紀錄後，再做正式成交重播。",
            ),
            "portfolio-drawdown-guard-failure-20260802": (
                "組合跌幅保護",
                "改測個股退出、訊號衰退與產業集中，不再只用整體降曝險。",
            ),
            "core-only-risk-control-failure-20260802": (
                "只調整 0050 核心曝險",
                "保留個股訊號，改以個股停損與產業風險限制降低回撤。",
            ),
            "trend-total-exposure-failure-20260802": (
                "189 日趨勢總曝險",
                "避免全面縮倉造成盤整與高波動期落後，改做部位層級風控。",
            ),
            "volatility-scaling-high-vol-failure-20260802": (
                "波動率縮放",
                "高波動期仍大幅落後，單獨使用波動率縮放不再升級。",
            ),
            "risk-grid-timeout-20260802": (
                "大型風控參數網格",
                "縮小研究假設並逐條驗證，避免大量參數搜尋提高過度擬合風險。",
            ),
        }
        risk_research_trials = []
        for item in failure_cases:
            definition = risk_trial_definitions.get(item["case"].case_key)
            if definition is None and item["case"].module != "盤後 AI 風控研究":
                continue
            evidence = item["evidence"]
            trial_return = evidence.get(
                "return", evidence.get("best_return", evidence.get("screen_return"))
            )
            trial_excess = evidence.get(
                "excess", evidence.get("screen_excess", evidence.get("best_excess"))
            )
            risk_research_trials.append({
                "title": definition[0] if definition else item["case"].title,
                "status": "未採用" if not evidence.get("result_adopted") else "採用",
                "return": trial_return,
                "excess": trial_excess,
                "drawdown": evidence.get("max_drawdown"),
                "correction": definition[1] if definition else item["case"].correction,
                "time": item["case"].last_occurred_at,
            })
        strategy_history = sorted(
            [
                {
                    "time": item.run.computed_at,
                    "name": method_labels.get(item.run.method, item.run.method),
                    "version": item.run.portfolio_version,
                    "status": (
                        "可候選"
                        if item.run.promotion_gate == "CANDIDATE"
                        else "研究中"
                    ),
                    "return": item.run.total_return,
                    "benchmark": item.run.benchmark_return,
                    "id": item.run.id,
                }
                for item in portfolio_views
            ],
            key=lambda item: item["time"],
            reverse=True,
        )[:30]
        return render_template(
            "ai_trading.html",
            plan=plan,
            validation=validation,
            model_validation=model_validation,
            model_validation_state=model_validation_state,
            validation_chart=[
                {
                    "date": taipei_time(item.event_time).strftime("%Y-%m-%d"),
                    "equity": float(item.equity),
                    "cash": float(item.cash),
                    "exposure": item.gross_exposure,
                    "benchmark": (
                        float(item.benchmark_equity)
                        if item.benchmark_equity is not None else None
                    ),
                }
                for item in validation.equity_curve
            ],
            model_validation_chart=[
                {
                    "date": taipei_time(item.event_time).strftime("%Y-%m-%d"),
                    "equity": float(item.equity),
                    "benchmark": (
                        float(item.benchmark_equity)
                        if item.benchmark_equity is not None else None
                    ),
                }
                for item in (
                    model_validation.equity_curve if model_validation is not None else ()
                )
            ],
            paper=paper_overview,
            expansion=expansion,
            readiness=readiness,
            data_lineage=data_lineage,
            asset_lookup=asset_lookup,
            strategy_rows=strategy_rows,
            model_rows=model_rows,
            model_tracking=model_tracking,
            gpu_research=gpu_research,
            failure_cases=failure_cases,
            risk_research_trials=risk_research_trials[:4],
            strategy_history=strategy_history,
            strategy_change_log=strategy_change_log,
            odd_lot=odd_lot,
            selected_allocations=selected_allocations,
            benchmark_core_weight=benchmark_core_weight,
            validation_days_short=max(0, 60 - validation.decision_sessions),
            strategy_rebalances=strategy_rebalances[:80],
            expansion_runs=expansion_runs[:20],
            backfill_remaining=max(
                0, expansion.target_ready_assets - expansion.data_ready_assets
            ),
            backfill_batches=max(
                0,
                (
                    expansion.target_ready_assets
                    - expansion.data_ready_assets
                    + expansion.daily_batch_size
                    - 1
                )
                // expansion.daily_batch_size,
            ),
        )

    @app.get("/api/ai-trading/validation-status")
    def after_hours_validation_status():
        return jsonify({
            "daily": dependencies.after_hours_ai_service.validation_status(),
            "model": dependencies.after_hours_ai_service.model_validation_status(),
        })

    @app.post("/ai-trading/run")
    def run_after_hours_ai():
        try:
            result = dependencies.daily_decision_pipeline.run("TW")
            dependencies.after_hours_ai_service.invalidate_validation_cache()
            plan = dependencies.after_hours_ai_service.generate()
            submission = dependencies.after_hours_ai_service.submit_to_paper(plan=plan)
            flash(
                f"盤後 AI 已重新計算 {result.decision_count} 檔股票；"
                f"買進 {plan.buy_count} 檔、賣出 {plan.sell_count} 檔；"
                f"新增 {submission.submitted} 筆模擬委託、"
                f"沿用 {submission.reused} 筆。",
                "success",
            )
        except Exception as exc:
            app.logger.exception("After-hours AI planning failed")
            flash(f"盤後 AI 計算失敗：{exc}", "error")
        return redirect(url_for("after_hours_ai_overview"))

    @app.post("/ai-trading/expand-universe")
    def expand_after_hours_universe():
        try:
            batch_size = max(1, min(int(request.form.get("batch_size", "5")), 20))
            prices_only = request.form.get("prices_only") == "true"
            result = dependencies.universe_expansion_service.run_batch(
                batch_size=batch_size,
                fetch_auxiliary=not prices_only,
            )
            if result.skipped_reason:
                flash(f"本次沒有建立下載任務：{result.skipped_reason}。", "success")
                return redirect(url_for("after_hours_ai_overview"))
            if result.symbols and result.inserted_bars:
                dependencies.feature_label_pipeline.run(
                    "TW", symbols=list(result.symbols)
                )
            dependencies.after_hours_ai_service.invalidate_validation_cache()
            message = (
                f"股票池補資料完成：嘗試 {result.attempted} 檔，"
                f"{result.kept_active} 檔資料就緒，"
                f"{result.deactivated} 檔暫不啟用；"
                f"新增 {result.inserted_bars:,} 根日線。"
            )
            if prices_only and result.kept_active:
                message += " 財報與籌碼資料將由每日批次接續補齊。"
            if result.failures:
                message += f" 另有 {len(result.failures)} 個警告。"
            flash(message, "success" if result.kept_active else "error")
        except Exception as exc:
            app.logger.exception("TW universe expansion failed")
            flash(f"股票池補資料失敗：{exc}", "error")
        return redirect(url_for("after_hours_ai_overview"))

    @app.get("/stocks")
    def stock_research() -> str:
        symbol = request.args.get("symbol", "0050")
        return render_template("stocks.html", view=dependencies.stock_research_service.get(symbol))

    @app.get("/stocks/refresh-status")
    def stock_refresh_status():
        symbol = request.args.get("symbol", "").strip()
        return jsonify(dependencies.stock_research_service.refresh_status(symbol))

    @app.get("/explain")
    def explainability_overview() -> str:
        symbol = request.args.get("symbol", "2330")
        return render_template(
            "explain.html", overview=dependencies.explainability_service.get(symbol)
        )

    @app.get("/taiwan-data")
    def taiwan_data_overview() -> str:
        symbol = request.args.get("symbol") or None
        return render_template(
            "taiwan_data.html",
            overview=dependencies.taiwan_data_overview_service.get_overview(symbol),
            selected_symbol=symbol or "",
        )

    @app.post("/taiwan-data/run")
    def run_taiwan_data():
        try:
            result = dependencies.taiwan_data_pipeline.run("TW")
            flash(
                f"台股研究資料更新完成：收到 {result.received:,} 筆、新增 {result.inserted:,} 筆，"
                f"建立 {result.feature_values:,} 個特徵值；失敗 {result.failed} 項。",
                "success" if result.failed == 0 else "error",
            )
        except Exception as exc:
            app.logger.exception("Taiwan research data update failed")
            flash(f"台股研究資料更新失敗：{exc}", "error")
        return redirect(url_for("taiwan_data_overview"))

    @app.get("/point-in-time-data")
    def point_in_time_data_overview() -> str:
        dataset_key = request.args.get("dataset", "tw_stock_1m").strip()
        entity_id = request.args.get("entity", "2330").strip().upper()
        as_of_text = request.args.get("as_of", "").strip()
        as_of = None
        if as_of_text:
            try:
                as_of = datetime.fromisoformat(as_of_text).replace(tzinfo=taipei).astimezone(UTC)
            except ValueError:
                flash("歷史可見時間格式錯誤，已改為查詢目前可見版本。", "error")
        return render_template(
            "point_in_time_data.html",
            overview=dependencies.point_in_time_data_service.overview(
                dataset_key, entity_id, as_of=as_of
            ),
            as_of_text=as_of_text,
        )

    @app.post("/point-in-time-data/run")
    def run_point_in_time_data():
        dataset_key = request.form.get("dataset", "tw_stock_1m").strip()
        entity_id = request.form.get("entity", "2330").strip().upper()
        start_text = request.form.get("start_date", "").strip()
        end_text = request.form.get("end_date", "").strip()
        try:
            today = datetime.now(taipei).date()
            start_date = date.fromisoformat(start_text) if start_text else today - timedelta(days=7)
            end_date = date.fromisoformat(end_text) if end_text else today
            start = datetime.combine(start_date, datetime.min.time(), tzinfo=taipei).astimezone(UTC)
            end = datetime.combine(end_date, datetime.max.time(), tzinfo=taipei).astimezone(UTC)
            result = dependencies.point_in_time_data_service.ingest(
                dataset_key, entity_id, start, end
            )
            flash(
                f"時間點資料更新完成：收到 {result.received:,} 筆，新增 {result.inserted:,} 筆修訂版本。",
                "success",
            )
        except Exception as exc:
            app.logger.exception("Point-in-time data update failed")
            flash(f"時間點資料更新失敗：{exc}", "error")
        return redirect(url_for(
            "point_in_time_data_overview", dataset=dataset_key, entity=entity_id
        ))

    @app.get("/intraday-features")
    def intraday_feature_overview() -> str:
        symbol = request.args.get("symbol", "").strip() or None
        return render_template(
            "intraday_features.html",
            overview=dependencies.intraday_derivative_feature_pipeline.overview(symbol),
            selected_symbol=symbol or "",
        )

    @app.post("/intraday-features/run")
    def run_intraday_features():
        lookback = request.form.get("lookback_days", type=int) or 400
        try:
            result = dependencies.intraday_derivative_feature_pipeline.run(
                lookback_days=max(2, min(3650, lookback))
            )
            flash(
                f"盤中與衍生特徵建立完成：讀取 {result.observation_count:,} 筆觀測，"
                f"計算 {result.computed_feature_count:,} 個特徵值，新增 "
                f"{result.inserted_revision_count:,} 個修訂版本；失敗 {result.failed} 個資料集。",
                "success" if result.failed == 0 else "error",
            )
        except Exception as exc:
            app.logger.exception("Intraday derivative feature build failed")
            flash(f"盤中與衍生特徵建立失敗：{exc}", "error")
        return redirect(url_for("intraday_feature_overview"))

    @app.get("/corporate-events")
    def corporate_event_overview() -> str:
        symbol = request.args.get("symbol", "").strip()
        scope = request.args.get("scope", "all")
        if scope not in {"all", "upcoming", "past"}:
            scope = "all"
        return render_template(
            "corporate_events.html",
            overview=dependencies.earnings_call_service.overview(symbol, scope),
        )

    @app.post("/corporate-events/run")
    def run_corporate_events():
        symbol = request.form.get("symbol", "ALL").strip() or "ALL"
        try:
            result = dependencies.earnings_call_service.refresh(symbol)
            flash(
                f"法說會事件更新完成：官方來源收到 {result.received:,} 筆事件，"
                f"新增 {result.inserted:,} 個版本。",
                "success",
            )
        except Exception as exc:
            app.logger.exception("Earnings call update failed")
            flash(f"法說會事件更新失敗：{exc}", "error")
        target = "" if symbol.upper() == "ALL" else symbol
        return redirect(url_for("corporate_event_overview", symbol=target))

    @app.get("/macro-data")
    def macro_data_overview() -> str:
        return render_template(
            "macro_data.html", overview=dependencies.macro_data_overview_service.get_overview()
        )

    @app.post("/macro-data/run")
    def run_macro_data():
        try:
            result = dependencies.macro_data_pipeline.run()
            flash(
                f"FRED 總經資料更新完成：收到 {result.received:,} 筆、"
                f"新增 {result.inserted:,} 個修訂版本，建立 {result.feature_values:,} 個特徵值；"
                f"失敗 {result.failed} 項。",
                "success" if result.failed == 0 else "error",
            )
        except Exception as exc:
            app.logger.exception("FRED macro data update failed")
            flash(f"FRED 總經資料更新失敗：{exc}", "error")
        return redirect(url_for("macro_data_overview"))

    @app.route("/data", methods=["GET"])
    def data_overview() -> str:
        overview = dependencies.market_data_overview_service.get_overview()
        return render_template("data.html", overview=overview)

    @app.get("/universe")
    def universe_overview() -> str:
        assets = dependencies.research_universe_service.list_all()
        runs = dependencies.daily_market_data_pipeline.list_recent_runs()
        effective_text = request.args.get("effective_date", "")
        try:
            effective_date = date.fromisoformat(effective_text) if effective_text else date.today()
        except ValueError:
            effective_date = date.today()
            flash("歷史查詢日期格式錯誤，已改用今天。", "error")
        history = dependencies.universe_history_service.audit(effective_date)
        members = dependencies.universe_history_service.members_on(effective_date, "TW")
        return render_template(
            "universe.html", assets=assets, runs=runs, history=history, historical_members=members
        )

    @app.post("/universe/metadata/sync")
    def sync_universe_metadata():
        try:
            changed = dependencies.research_universe_service.sync_taiwan_company_metadata()
            dependencies.universe_expansion_service.refresh_market_metadata()
            flash(
                f"已用證交所／櫃買中心官方資料更新 {changed} 檔公司的名稱、產業與資本資料。",
                "success",
            )
        except Exception as exc:
            app.logger.exception("Taiwan company metadata sync failed")
            flash(f"公司基本資料更新失敗：{exc}", "error")
        return redirect(url_for("universe_overview"))

    @app.get("/features")
    def feature_store_overview() -> str:
        overview = dependencies.feature_store_overview_service.get_overview()
        definitions = dependencies.feature_store_overview_service.list_feature_definitions()
        return render_template("features.html", overview=overview, definitions=definitions)

    @app.get("/factors")
    def factor_research_overview() -> str:
        overview = dependencies.factor_research_overview_service.get_overview()
        return render_template("factors.html", overview=overview)

    @app.get("/backtests")
    def backtest_research_overview() -> str:
        selected_id = request.args.get("run", type=int)
        overview = dependencies.backtest_research_overview_service.get_overview(selected_id)
        return render_template("backtests.html", overview=overview)

    @app.get("/strategies")
    def strategy_center() -> str:
        backtests = dependencies.backtest_research_overview_service.get_overview()
        portfolios = dependencies.portfolio_overview_service.get_overview()
        models = dependencies.model_research_overview_service.get_overview()
        validation = dependencies.after_hours_ai_service.validation_for_page()

        latest_backtests: dict[tuple[str, str], object] = {}
        for view in backtests.runs:
            run = view.run
            if run.market != "TW":
                continue
            key = (run.symbol, run.strategy_name)
            current = latest_backtests.get(key)
            if current is None or run.computed_at > current.computed_at:
                latest_backtests[key] = run
        rule_rows = []
        for strategy_name in sorted({item.strategy_name for item in latest_backtests.values()}):
            runs = [
                item for item in latest_backtests.values()
                if item.strategy_name == strategy_name
            ]
            rule_rows.append({
                "name": strategy_name,
                "method": "規則＋統計驗證",
                "version": max((item.strategy_version for item in runs), default="—"),
                "status": "候選" if any(item.promotion_gate == "CANDIDATE" for item in runs) else "研究中",
                "return": statistics.median(item.total_return for item in runs),
                "benchmark": statistics.median(item.benchmark_return for item in runs),
                "excess": statistics.median(item.excess_return for item in runs),
                "sample": statistics.median(item.observation_count for item in runs),
                "updated_at": max(item.computed_at for item in runs),
                "note": f"{len(runs)} 檔股票的最新樣本外結果中位數",
                "href": "/backtests",
            })

        portfolio_views = [item for item in portfolios.runs if item.run.market == "TW"]
        if portfolio_views:
            latest_time = max(item.run.computed_at for item in portfolio_views)
            portfolio_views = [
                item for item in portfolio_views if item.run.computed_at == latest_time
            ]
        portfolio_rows = [{
            "name": item.run.method,
            "method": "統計／投資組合最佳化",
            "version": item.run.portfolio_version,
            "status": "候選" if item.run.promotion_gate == "CANDIDATE" else "研究中",
            "return": item.run.total_return,
            "benchmark": item.run.benchmark_return,
            "excess": item.run.excess_to_benchmark,
            "sample": item.run.observation_count,
            "updated_at": item.run.computed_at,
            "note": f"{item.run.asset_count} 檔資產；換手率 {item.run.turnover:.1%}",
            "href": f"/portfolios?run={item.run.id}",
        } for item in portfolio_views]

        model_rows = []
        model_types = {
            "historical_mean": "統計基準",
            "ridge_linear": "統計＋監督式機器學習",
            "bagged_stumps": "監督式機器學習",
            "random_forest": "監督式機器學習",
            "gradient_boosted_stumps": "監督式機器學習",
            "svm_rbf": "監督式機器學習",
            "torch_cuda_mlp": "深度學習／GPU",
        }
        for view in models.runs:
            item = view.experiment
            if item.market != "TW":
                continue
            model_rows.append({
                "name": item.model_name,
                "method": model_types.get(item.model_name, "監督式機器學習"),
                "version": item.model_version,
                "status": "候選" if item.promotion_gate == "CANDIDATE" else "研究中",
                "r2": item.r2,
                "direction": item.directional_accuracy,
                "rank_ic": item.rank_ic,
                "sample": item.observation_count,
                "updated_at": item.computed_at,
                "note": "預測五日報酬；模型分數不能直接視為投資組合報酬",
                "href": f"/models?run={item.id}",
            })

        history = sorted(
            [
                {
                    "time": view.run.computed_at,
                    "name": view.run.strategy_name,
                    "version": view.run.strategy_version,
                    "status": "候選" if view.run.promotion_gate == "CANDIDATE" else "研究中",
                    "kind": "規則＋統計驗證",
                    "href": f"/backtests?run={view.run.id}",
                }
                for view in backtests.runs if view.run.market == "TW"
            ] + [
                {
                    "time": view.run.computed_at,
                    "name": view.run.method,
                    "version": view.run.portfolio_version,
                    "status": "候選" if view.run.promotion_gate == "CANDIDATE" else "研究中",
                    "kind": "統計／投資組合最佳化",
                    "href": f"/portfolios?run={view.run.id}",
                }
                for view in portfolios.runs if view.run.market == "TW"
            ] + [
                {
                    "time": view.experiment.computed_at,
                    "name": view.experiment.model_name,
                    "version": view.experiment.model_version,
                    "status": "候選" if view.experiment.promotion_gate == "CANDIDATE" else "研究中",
                    "kind": model_types.get(
                        view.experiment.model_name, "監督式機器學習"
                    ),
                    "href": f"/models?run={view.experiment.id}",
                }
                for view in models.history if view.experiment.market == "TW"
            ],
            key=lambda item: item["time"],
            reverse=True,
        )[:30]
        best_portfolio = max(portfolio_rows, key=lambda item: item["return"], default=None)
        return render_template(
            "strategies.html",
            rule_rows=rule_rows,
            portfolio_rows=portfolio_rows,
            model_rows=model_rows,
            history=history,
            validation=validation,
            best_portfolio=best_portfolio,
        )

    @app.get("/ensembles")
    def ensemble_research_overview() -> str:
        selected_id = request.args.get("run", type=int)
        overview = dependencies.ensemble_overview_service.get_overview(selected_id)
        return render_template("ensembles.html", overview=overview)

    @app.post("/ensembles/run/<market>")
    def run_ensemble_research(market: str):
        try:
            result = dependencies.ensemble_research_pipeline.run(market)
            flash(
                f"{market.upper()} 策略集成完成：{result.ensemble_runs} 個標的，"
                f"{result.candidates} 個候選，{result.failed} 個失敗。",
                "success" if result.failed == 0 else "error",
            )
        except Exception as exc:
            app.logger.exception("Manual ensemble research failed")
            flash(f"策略集成失敗：{exc}", "error")
        return redirect(url_for("ensemble_research_overview"))

    @app.get("/portfolios")
    def portfolio_research_overview() -> str:
        selected_id = request.args.get("run", type=int)
        overview = dependencies.portfolio_overview_service.get_overview(selected_id)
        return render_template("portfolios.html", overview=overview)

    @app.get("/models")
    def model_research_overview() -> str:
        selected_id = request.args.get("run", type=int)
        overview = dependencies.model_research_overview_service.get_overview(selected_id)
        return render_template("models.html", overview=overview)

    @app.post("/models/run/<market>")
    def run_model_research(market: str):
        try:
            started = dependencies.model_training_coordinator.start(market)
            flash(
                f"{market.upper()} 模型研究已在背景啟動，頁面會自動更新進度。"
                if started else f"{market.upper()} 已有模型研究正在執行。",
                "success",
            )
        except Exception as exc:
            app.logger.exception("Model research launch failed")
            flash(f"無法啟動模型研究：{exc}", "error")
        return redirect(url_for("model_research_overview", training=market.upper()))

    @app.get("/models/training-status/<market>")
    def model_training_status(market: str):
        return dependencies.model_training_coordinator.get(market)

    @app.post("/portfolios/run/<market>")
    def run_portfolio_research(market: str):
        try:
            result = dependencies.portfolio_research_pipeline.run(market)
            flash(
                f"{market.upper()} Portfolio/Risk 完成：{result.portfolio_runs} 種方法，"
                f"{result.candidates} 個候選，{result.failed} 個失敗。",
                "success" if result.failed == 0 else "error",
            )
        except Exception as exc:
            app.logger.exception("Manual portfolio research failed")
            flash(f"Portfolio/Risk 研究失敗：{exc}", "error")
        return redirect(url_for("portfolio_research_overview"))

    @app.post("/backtests/run/<market>")
    def run_backtest_research(market: str):
        try:
            result = dependencies.backtest_research_pipeline.run(market)
            flash(
                f"{market.upper()} Walk-forward 完成：{result.backtest_runs} 個研究、"
                f"{result.candidates} 個候選、{result.failed} 個失敗。",
                "success" if result.failed == 0 else "error",
            )
        except Exception as exc:
            app.logger.exception("Manual backtest research failed")
            flash(f"Walk-forward 回測失敗：{exc}", "error")
        return redirect(url_for("backtest_research_overview"))

    @app.post("/factors/research/<market>")
    def run_factor_research(market: str):
        try:
            result = dependencies.factor_research_pipeline.run(market)
            flash(
                f"{market.upper()} 因子研究完成：{result.regime_values:,} 個 Regime 狀態、"
                f"{result.factor_results} 個因子研究結果。",
                "success" if result.status == "succeeded" else "error",
            )
        except Exception as exc:
            app.logger.exception("Manual factor research failed")
            flash(f"因子研究失敗：{exc}", "error")
        return redirect(url_for("factor_research_overview"))

    @app.post("/features/build/<market>")
    def build_feature_store(market: str):
        try:
            result = dependencies.feature_label_pipeline.run(market)
            flash(
                f"{market.upper()} Feature Store 完成：{result.feature_values:,} 個特徵值、"
                f"{result.label_values:,} 個標籤值，{result.failed} 個資產失敗。",
                "success" if result.failed == 0 else "error",
            )
        except Exception as exc:
            app.logger.exception("Manual feature store build failed")
            flash(f"Feature Store 建置失敗：{exc}", "error")
        return redirect(url_for("feature_store_overview"))

    @app.post("/universe/assets")
    def add_universe_asset():
        try:
            data_start_text = request.form.get("data_start", "")
            asset = dependencies.research_universe_service.add_asset(
                symbol=request.form.get("symbol", ""),
                market=request.form.get("market", ""),
                asset_type=request.form.get("asset_type", "EQUITY"),
                sector=request.form.get("sector") or None,
                benchmark_symbol=request.form.get("benchmark_symbol") or None,
                data_start=date.fromisoformat(data_start_text) if data_start_text else None,
            )
            flash(f"{asset.symbol} 已加入研究股票池。", "success")
        except Exception as exc:
            app.logger.exception("Failed to add universe asset")
            flash(f"新增失敗：{exc}", "error")
        return redirect(url_for("universe_overview"))

    @app.post("/universe/assets/<symbol>/toggle")
    def toggle_universe_asset(symbol: str):
        active = request.form.get("active", "false").lower() == "true"
        if dependencies.research_universe_service.set_active(symbol, active):
            flash(f"{symbol} 已{'啟用' if active else '停用'}。歷史資料仍保留。", "success")
        else:
            flash(f"找不到 {symbol}。", "error")
        return redirect(url_for("universe_overview"))

    @app.post("/universe/history/sync")
    def sync_universe_history():
        try:
            result = dependencies.universe_history_service.sync_taiwan()
            flash(
                f"歷史股票池更新完成：現行 {result.current_received:,} 檔、"
                f"下市櫃 {result.delisted_received:,} 檔、正式上市日 {result.official_listing_dates:,} 檔，"
                f"異動 {result.changed:,} 筆有效期間。",
                "success",
            )
        except Exception as exc:
            app.logger.exception("Universe history sync failed")
            flash(f"歷史股票池更新失敗：{exc}", "error")
        return redirect(url_for("universe_overview"))

    @app.post("/scheduler/run/<market>")
    def run_market_pipeline(market: str):
        try:
            result = dependencies.daily_research_pipeline.run(market)
            flash(
                f"{market.upper()} 每日流程完成：新增 {result.market_data.inserted:,} 筆行情，"
                f"重算 {result.feature_store.feature_values:,} 個特徵值與 "
                f"{result.feature_store.label_values:,} 個標籤值，完成 "
                f"台股研究資料新增 {result.taiwan_data.inserted:,} 筆、"
                f"{result.backtest_research.backtest_runs} 個 Walk-forward、"
                f"{result.strategy_ensemble.ensemble_runs} 個 Ensemble 與 "
                f"{result.portfolio_risk.portfolio_runs} 個 Portfolio/Risk 研究。",
                "success" if result.feature_store.failed == 0 else "error",
            )
        except Exception as exc:
            app.logger.exception("Manual market pipeline failed")
            flash(f"Pipeline 失敗：{exc}", "error")
        return redirect(url_for("universe_overview"))

    @app.post("/data/ingest")
    def ingest_data():
        symbol = request.form.get("symbol", "").strip()
        market = request.form.get("market", "US").strip().upper()
        end_date = request.form.get("end_date", "")
        start_date = request.form.get("start_date", "")
        if not symbol:
            flash("請輸入股票代號。", "error")
            return redirect(url_for("data_overview"))
        now = datetime.now(UTC)
        try:
            start = datetime.fromisoformat(start_date).replace(tzinfo=UTC) if start_date else now - timedelta(days=365)
            end = datetime.fromisoformat(end_date).replace(tzinfo=UTC) if end_date else now + timedelta(days=1)
            result = dependencies.market_data_ingestion_service.ingest_daily(
                symbol=symbol, market=market, start=start, end=end
            )
            flash(
                f"{result.symbol} 收到 {result.received} 筆，新增 {result.inserted} 筆，略過 {result.duplicates} 筆。",
                "success",
            )
        except Exception as exc:
            app.logger.exception("Market data ingestion failed")
            flash(f"資料更新失敗：{exc}", "error")
        return redirect(url_for("data_overview"))

    @app.get("/health")
    def health():
        snapshot = dependencies.health_service.check()
        code = 200 if snapshot.status == "healthy" else 503
        return jsonify(asdict(snapshot)), code

    def data_pipeline_snapshot() -> dict[str, object]:
        """Build the small read-only payload used by the live ingestion screen."""
        expansion = dependencies.universe_expansion_service.overview()
        engine = dependencies.database.engine
        counts = {
            "market_bars": 0,
            "feature_values": 0,
            "label_values": 0,
            "backtest_trades": 0,
        }
        tw_symbols = 0
        earliest_bar = None
        latest_bar = None
        with engine.connect() as connection:
            available = set(inspect(engine).get_table_names())
            for table_name in counts:
                if table_name not in available:
                    continue
                quoted = engine.dialect.identifier_preparer.quote(table_name)
                counts[table_name] = int(
                    connection.execute(text(f"SELECT COUNT(*) FROM {quoted}")).scalar_one()
                )
            if "market_bars" in available:
                row = connection.execute(
                    text(
                        "SELECT COUNT(DISTINCT symbol) AS symbol_count, "
                        "MIN(event_time) AS earliest, MAX(event_time) AS latest "
                        "FROM market_bars WHERE market = 'TW'"
                    )
                ).mappings().one()
                tw_symbols = int(row["symbol_count"] or 0)
                earliest_bar = row["earliest"]
                latest_bar = row["latest"]

        job_labels = {
            "tw_universe_expansion": "下載下一批台股歷史行情",
            "daily_market_data": "更新每日行情",
            "taiwan_research_data": "補公司、籌碼與基本面資料",
            "feature_label_build": "建立模型特徵與未來報酬標籤",
            "backtest_research": "重跑歷史策略交易",
            "walk_forward_backtest": "逐日歷史回測",
            "model_research": "訓練與驗證機器學習模型",
            "portfolio_research": "比較投資組合策略",
            "daily_decision": "產生盤後 AI 決策",
        }
        runs = []
        for run in dependencies.automation_service.overview().recent_runs:
            try:
                metrics = json.loads(run.metrics_json)
            except (TypeError, json.JSONDecodeError):
                metrics = {}
            failures = metrics.get("failures", [])
            if not isinstance(failures, list):
                failures = []
            if (
                run.job_name == "tw_universe_expansion"
                and metrics.get("attempted", 0) == 0
                and metrics.get("inserted_bars", 0) == 0
                and not metrics.get("failures")
            ):
                continue
            runs.append(
                {
                    "job": run.job_name,
                    "label": job_labels.get(run.job_name, run.job_name),
                    "status": run.status.value,
                    "started_at": taipei_time(run.started_at).strftime("%Y-%m-%d %H:%M:%S"),
                    "completed_at": (
                        taipei_time(run.completed_at).strftime("%Y-%m-%d %H:%M:%S")
                        if run.completed_at else None
                    ),
                    "attempted": metrics.get("attempted"),
                    "inserted_bars": metrics.get("inserted_bars"),
                    "kept_active": metrics.get("kept_active"),
                    "symbols": metrics.get("symbols", []),
                    "failures": failures,
                    "error": run.error,
                    "elapsed_seconds": max(
                        0,
                        int(
                            (
                                taipei_time(run.completed_at or datetime.now(UTC))
                                - taipei_time(run.started_at)
                            ).total_seconds()
                        ),
                    ),
                }
            )
        running = [item for item in runs if item["status"] == "running"]
        blockers = []
        for item in runs:
            detail = " ".join(
                [str(item.get("error") or "")]
                + [str(value) for value in item.get("failures", [])]
            )
            if "402" in detail or "Payment Required" in detail:
                blockers.append(
                    {
                        "source": "FinMind",
                        "message": "目前方案拒絕籌碼與基本面資料；已停止重試，不再阻擋價格補抓。",
                    }
                )
                break
        progress = min(
            100.0,
            expansion.data_ready_assets / max(1, expansion.target_ready_assets) * 100,
        )
        return {
            "status": "執行中" if running else expansion.status,
            "current_stage": running[0]["label"] if running else "等待下一個背景批次",
            "current_started_at": running[0]["started_at"] if running else None,
            "current_elapsed_seconds": running[0]["elapsed_seconds"] if running else 0,
            "updated_at": datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S"),
            "ready_assets": expansion.data_ready_assets,
            "target_assets": expansion.target_ready_assets,
            "registered_assets": expansion.registered_assets,
            "remaining_assets": max(
                0, expansion.target_ready_assets - expansion.data_ready_assets
            ),
            "progress": round(progress, 2),
            "batch_size": expansion.daily_batch_size,
            "next_symbols": list(expansion.next_symbols),
            "tw_symbols": tw_symbols,
            "earliest_bar": str(earliest_bar or "—"),
            "latest_bar": str(latest_bar or "—"),
            "counts": counts,
            "runs": runs[:30],
            "blockers": blockers,
        }

    @app.get("/data-pipeline")
    def data_pipeline_overview() -> str:
        return render_template("data_pipeline.html", pipeline=data_pipeline_snapshot())

    @app.get("/api/data-pipeline/status")
    def data_pipeline_status():
        return jsonify(data_pipeline_snapshot())

    @app.get("/database")
    def database_overview() -> str:
        engine = dependencies.database.engine
        recent_jobs = []
        for run in dependencies.automation_service.overview().recent_runs:
            try:
                metrics = json.loads(run.metrics_json)
            except (TypeError, json.JSONDecodeError):
                metrics = {}
            if (
                run.job_name == "tw_universe_expansion"
                and metrics.get("attempted", 0) == 0
                and metrics.get("inserted_bars", 0) == 0
                and not metrics.get("failures")
            ):
                continue
            recent_jobs.append(
                {
                    "job": run.job_name,
                    "market": run.market,
                    "status": run.status.value,
                    "started_at": run.started_at,
                    "completed_at": run.completed_at,
                    "metrics": metrics,
                    "error": run.error,
                }
            )
        expansion = dependencies.universe_expansion_service.overview()
        readiness = system_readiness_snapshot(expansion)
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        quality_blocking = quality_warnings = news_documents = knowledge_chunks = 0
        with engine.connect() as connection:
            if "data_quality_snapshots" in table_names:
                quality_row = connection.execute(text(
                    "SELECT issue_count, blocking_issue_count FROM data_quality_snapshots "
                    "WHERE market='TW' ORDER BY computed_at DESC LIMIT 1"
                )).first()
                if quality_row:
                    quality_blocking = int(quality_row[1] or 0)
                    quality_warnings = max(0, int(quality_row[0] or 0) - quality_blocking)
            if "knowledge_documents" in table_names:
                news_documents = int(connection.execute(text(
                    "SELECT COUNT(*) FROM knowledge_documents "
                    "WHERE document_type='財經新聞'"
                )).scalar_one() or 0)
            if "knowledge_chunks" in table_names:
                knowledge_chunks = int(connection.execute(text(
                    "SELECT COUNT(*) FROM knowledge_chunks"
                )).scalar_one() or 0)
        readiness_rows = (
            {
                "name": "台股長期行情",
                "current": f"{readiness['price_ready_assets']}／{readiness['registered_assets']} 檔",
                "target": "每檔至少 252 個交易日",
                "missing": f"待補 {readiness['price_missing_assets']} 檔",
                "percent": readiness["price_percent"],
                "url": "/data-pipeline",
            },
            {
                "name": "資料品質",
                "current": f"嚴重 {quality_blocking}／警告 {quality_warnings}",
                "target": "嚴重問題必須為 0",
                "missing": "警告仍需逐項補來源或標註不可用",
                "percent": 100 if quality_blocking == 0 else 0,
                "url": "/data-quality",
            },
            {
                "name": "歷史每日決策",
                "current": f"{readiness['decision_days']}／{readiness['decision_target']} 日",
                "target": "涵蓋約三年與不同市場狀態",
                "missing": f"待建立 {readiness['decision_missing_days']} 日",
                "percent": readiness["decision_percent"],
                "url": "/ai-trading#validation",
            },
            {
                "name": "向前模擬交易",
                "current": f"{readiness['paper_days']}／{readiness['paper_target']} 日",
                "target": "每日保存委託、成交與同期 0050",
                "missing": f"待累積 {readiness['paper_missing_days']} 日；目前 {readiness['paper_orders']} 委託／{readiness['paper_fills']} 成交",
                "percent": readiness["paper_percent"],
                "url": "/paper-trading",
            },
            {
                "name": "財經新聞記憶",
                "current": f"{news_documents:,} 則新聞／{knowledge_chunks:,} 段向量",
                "target": "可搜尋、保留來源與可用時間",
                "missing": "尚未證明加入選股後能增加超額報酬",
                "percent": 100 if news_documents else 0,
                "url": "/reports",
            },
        )
        raw_provider_status = dependencies.taiwan_data_pipeline.provider_status()
        finmind_status = raw_provider_status.get("finmind", {})
        provider_summary = (
            "官方行情來源可用；FinMind 目前未觸發停止呼叫。最近排程若失敗，仍以上方失敗紀錄為準。"
            if finmind_status.get("available", True)
            else "FinMind 已暫停呼叫：" + str(finmind_status.get("reason") or "存取受限")
        )
        return render_template(
            "database.html",
            database_type=engine.dialect.name,
            recent_jobs=recent_jobs,
            readiness_rows=readiness_rows,
            readiness=readiness,
            expansion=expansion,
            provider_summary=provider_summary,
        )

    @app.get("/api/database/details")
    def database_details():
        engine = dependencies.database.engine
        inspector = inspect(engine)
        table_names = inspector.get_table_names()
        table_rows = []
        bar_coverage = []
        with engine.connect() as connection:
            for table_name in sorted(table_names):
                quoted = engine.dialect.identifier_preparer.quote(table_name)
                columns = {item["name"] for item in inspector.get_columns(table_name)}
                count_sql = (
                    f"SELECT COALESCE(MAX(id), 0) FROM {quoted}"
                    if "id" in columns else f"SELECT COUNT(*) FROM {quoted}"
                )
                count = int(connection.execute(text(count_sql)).scalar_one() or 0)
                table_rows.append({
                    "name": table_name,
                    "description": DATABASE_TABLE_DESCRIPTIONS.get(
                        table_name, "系統研究資料"
                    ),
                    "count": count,
                })
            if "market_bars" in table_names:
                rows = connection.execute(text(
                    "SELECT symbol, market, COUNT(*) AS row_count, "
                    "MIN(event_time) AS data_start, MAX(event_time) AS data_end "
                    "FROM market_bars GROUP BY symbol, market "
                    "ORDER BY row_count DESC, symbol LIMIT 500"
                )).mappings()
                bar_coverage = [dict(row) for row in rows]
        table_rows.sort(key=lambda item: item["count"], reverse=True)
        return jsonify({
            "tables": table_rows,
            "total_rows": sum(item["count"] for item in table_rows),
            "bar_coverage": bar_coverage,
        })

    return app


def main() -> None:
    dependencies = build_container()
    from quant_platform.scheduler.runner import start_background_scheduler

    scheduler = (
        start_background_scheduler(dependencies)
        if dependencies.settings.scheduler_in_web else None
    )
    app = create_app(dependencies)
    try:
        app.run(
            host=dependencies.settings.web_host,
            port=dependencies.settings.web_port,
            debug=False,
            use_reloader=False,
        )
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
