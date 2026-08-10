from __future__ import annotations

from sqlalchemy import text

from quant_platform.config import get_settings
from quant_platform.container import build_container


def main() -> None:
    container = build_container(get_settings())
    statement = text("""
        WITH latest AS (
            SELECT model_name, MAX(computed_at) AS computed_at
            FROM model_experiments
            WHERE market = 'TW'
            GROUP BY model_name
        )
        SELECT
            experiment.id,
            experiment.model_name,
            experiment.experiment_version,
            experiment.data_start,
            experiment.data_end,
            experiment.observation_count,
            COUNT(prediction.id) AS prediction_rows,
            COUNT(DISTINCT DATE(prediction.event_time)) AS prediction_dates,
            MIN(prediction.event_time) AS prediction_start,
            MAX(prediction.event_time) AS prediction_end
        FROM latest
        JOIN model_experiments AS experiment
          ON experiment.model_name = latest.model_name
         AND experiment.computed_at = latest.computed_at
         AND experiment.market = 'TW'
        LEFT JOIN model_predictions AS prediction
          ON prediction.experiment_id = experiment.id
        GROUP BY experiment.id
        ORDER BY experiment.model_name
    """)
    with container.database.engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()
    for row in rows:
        print(
            f"{row['model_name']}: experiment={row['id']} "
            f"version={row['experiment_version']} OOS={row['data_start']}..{row['data_end']} "
            f"observations={row['observation_count']:,} predictions={row['prediction_rows']:,} "
            f"dates={row['prediction_dates']} "
            f"prediction_window={row['prediction_start']}..{row['prediction_end']}",
            flush=True,
        )


if __name__ == "__main__":
    main()
