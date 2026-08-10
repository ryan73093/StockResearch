from datetime import UTC, datetime

from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import SqlAlchemyResearchExperimentRepository
from quant_platform.domain.entities import ExperimentStatus, ResearchExperiment


def test_empty_research_overview(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}"))
    overview = container.research_overview_service.get_overview()
    assert overview["experiment_count"] == 0
    assert overview["recent_experiments"] == []


def test_research_experiment_can_be_saved_and_loaded_by_type(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'saved.db'}"))
    repository = SqlAlchemyResearchExperimentRepository(container.database.session_factory)
    experiment = ResearchExperiment(
        id=None, name="rl_walk_forward:2330.TW",
        experiment_type="rl_walk_forward", status=ExperimentStatus.SUCCEEDED,
        parameters_json='{"fingerprint":"abc"}', metrics_json='{"folds":4}',
        created_at=datetime.now(UTC),
    )
    experiment_id = repository.save(experiment)
    loaded = repository.get_latest(experiment.name, experiment.experiment_type)
    assert experiment_id > 0
    assert loaded is not None
    assert loaded.id == experiment_id
    assert loaded.metrics_json == experiment.metrics_json
