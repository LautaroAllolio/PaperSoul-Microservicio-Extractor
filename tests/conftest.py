import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "contract: tests that require a real downstream Extractor")
    config.addinivalue_line(
        "markers",
        "memory: performance tests that assert a bounded RSS ceiling (run with `-m memory`)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip ``memory`` tests unless the run explicitly selects the marker.

    The batch measures RSS and moves real megabytes, so the default suite
    should not pay for it; they only run when requested with ``-m memory``.
    """
    markexpr = config.getoption("-m", default=None)
    if markexpr is not None and "memory" in markexpr:
        return
    for item in items:
        if item.get_closest_marker("memory") is not None:
            item.add_marker(pytest.mark.skip(reason="memory test; run with `-m memory`"))
