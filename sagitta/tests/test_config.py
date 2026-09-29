# tests/test_config.py
"""配置装载与类型校验单元测试。

覆盖：
  * 默认值构造不崩
  * 类型校验：非法 port / workers / max_body_bytes 抛 ConfigError
  * 环境变量覆盖
  * rate_limit 配置装载
  * DB 简写形式（db.default: "postgresql://..."）
"""
import pytest

from sagitta.Config.setting import SgSettings, ConfigError, _typed_int, _typed_bool


class TestDefaults:
    """默认值构造。"""

    def test_empty_raw(self):
        s = SgSettings({})
        assert s.app.title == "Sagitta API"
        assert s.server.port == 8000
        assert s.server.workers == 1
        assert s.server.max_body_bytes == 0

    def test_rate_limit_defaults(self):
        s = SgSettings({})
        assert s.rate_limit.enabled is True
        assert s.rate_limit.redis == "default"
        assert s.rate_limit.default_requests == 100

    def test_shutdown_timeout_default(self):
        s = SgSettings({})
        assert s.app.shutdown_timeout == 10.0


class TestTypeValidation:
    """类型校验测试。"""

    def test_valid_port(self):
        s = SgSettings({"server": {"port": 9000}})
        assert s.server.port == 9000

    def test_string_port_coerced(self):
        """字符串数字被正常转换。"""
        s = SgSettings({"server": {"port": "9000"}})
        assert s.server.port == 9000

    def test_invalid_port_raises(self):
        with pytest.raises(ConfigError, match="server.port"):
            SgSettings({"server": {"port": "abc"}})

    def test_invalid_workers_raises(self):
        with pytest.raises(ConfigError, match="server.workers"):
            SgSettings({"server": {"workers": "not-a-number"}})

    def test_invalid_max_body_bytes_raises(self):
        with pytest.raises(ConfigError, match="max_body_bytes"):
            SgSettings({"server": {"max_body_bytes": "abc"}})

    def test_typed_int_none(self):
        assert _typed_int(None, "test", default=42) == 42

    def test_typed_int_empty_string(self):
        assert _typed_int("", "test", default=42) == 42

    def test_typed_bool_string(self):
        assert _typed_bool("false", "test") is False
        assert _typed_bool("true", "test") is True
        assert _typed_bool("0", "test") is False
        assert _typed_bool("1", "test") is True


class TestRateLimitConfig:
    """rate_limit 配置装载。"""

    def test_custom_values(self):
        s = SgSettings({
            "rate_limit": {
                "enabled": False,
                "ip_requests": 30,
                "ip_window": 10,
            }
        })
        assert s.rate_limit.enabled is False
        assert s.rate_limit.ip_requests == 30
        assert s.rate_limit.ip_window == 10
        # 未覆盖的用默认值
        assert s.rate_limit.user_requests == 200

    def test_invalid_ip_requests_raises(self):
        with pytest.raises(ConfigError, match="ip_requests"):
            SgSettings({"rate_limit": {"ip_requests": "abc"}})


class TestDbShorthand:
    """DB 简写形式。"""

    def test_string_url_shorthand(self):
        s = SgSettings({"db": "postgresql://localhost/test"})
        assert "default" in s.db
        assert "postgresql" in s.db["default"].url

    def test_dict_form(self):
        s = SgSettings({"db": {"default": {"url": "postgresql://localhost/test"}}})
        assert s.db["default"].type == "postgresql"


class TestEnvOverride:
    """环境变量覆盖。"""

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SG_SERVER_PORT", "9999")
        from sagitta.Config.loader import ConfigLoader
        loader = ConfigLoader(config_path=None)
        raw = loader.reload()
        # 环境变量在 _apply_env 中被应用
        assert raw.get("server", {}).get("port") == 9999
