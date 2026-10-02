import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "contract: tests that require a real downstream Extractor")
