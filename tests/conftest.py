import pytest

import photo_store


@pytest.fixture(autouse=True)
def _no_provider_pauses():
    """A test that simulates Deezer/Spotify refusing us must not pause the
    providers for the tests that run after it."""
    photo_store.reset_pauses()
    yield
    photo_store.reset_pauses()
