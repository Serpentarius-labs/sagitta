# tests/conftest.py
import asyncio, pytest
from dotenv import load_dotenv
load_dotenv()
from sagitta.Config.loader import ConfigLoader
from sagitta.Config.setting import build_settings, set_settings
from sagitta.Log.backend import LogBackend
from sagitta.Core.inject import Container

@pytest.fixture(scope="session", autouse=True)
def bootstrap_test():
    raw = ConfigLoader.instance().reload()
    raw.setdefault("app", {})["env"] = "test"
    raw.setdefault("app", {})["debug"] = True
    raw.setdefault("log", {})["level"] = "DEBUG"
    raw.setdefault("log", {})["console"] = False
    settings = build_settings(raw)
    set_settings(settings)
    LogBackend.init(settings.log.backend, settings.log)
    yield
    Container.reset_for_test()