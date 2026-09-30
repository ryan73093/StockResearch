from quant_platform.application.universe import ResearchUniverseService
from quant_platform.database.engine import Database
from quant_platform.database.repositories import SqlAlchemyResearchUniverseRepository


def test_default_universe_startup_is_read_only_after_initial_seed(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'universe.db'}")
    database.create_schema()
    repository = SqlAlchemyResearchUniverseRepository(database.session_factory)
    service = ResearchUniverseService(repository)

    service.ensure_default_universe()
    first = repository.get("0050.TW")
    assert first is not None

    service.ensure_default_universe()
    second = repository.get("0050.TW")

    assert second is not None
    assert second.metadata_updated_at == first.metadata_updated_at
