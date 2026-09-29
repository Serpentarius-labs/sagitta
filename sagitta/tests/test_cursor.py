# tests/test_cursor.py
"""游标编解码单元测试。

覆盖：
  * 基本类型：str / int / UUID / datetime
  * 复合游标：[sort_val, id]
  * 非法输入：空串 / 非 base64 / 结构缺失 / 未知类型
  * 往返一致性：encode → decode 值不变
"""
import datetime
import uuid

import pytest

from sagitta.Database.repository import (
    CursorError,
    decode_cursor,
    encode_cursor,
)


class TestEncodeDecode:
    """往返一致性测试。"""

    def test_string(self):
        v = "abc123"
        encoded = encode_cursor(v)
        assert isinstance(encoded, str)
        assert decode_cursor(encoded) == v

    def test_integer(self):
        v = 42
        encoded = encode_cursor(v)
        assert decode_cursor(encoded) == v

    def test_none(self):
        encoded = encode_cursor(None)
        assert decode_cursor(encoded) is None

    def test_boolean(self):
        encoded = encode_cursor(True)
        assert decode_cursor(encoded) is True

    def test_uuid(self):
        u = uuid.uuid4()
        encoded = encode_cursor(u)
        result = decode_cursor(encoded)
        assert isinstance(result, uuid.UUID)
        assert result == u

    def test_uuid7_like(self):
        """uuid7 是时间有序的，是框架主键的默认类型。"""
        u = uuid.UUID("018f6a1c-5e3b-7000-8000-000000000000")
        encoded = encode_cursor(u)
        assert decode_cursor(encoded) == u

    def test_datetime(self):
        dt = datetime.datetime(2026, 9, 29, 12, 0, 0)
        encoded = encode_cursor(dt)
        result = decode_cursor(encoded)
        assert result == dt

    def test_datetime_with_tz(self):
        dt = datetime.datetime(2026, 9, 29, 12, 0, 0, tzinfo=datetime.timezone.utc)
        encoded = encode_cursor(dt)
        result = decode_cursor(encoded)
        assert result == dt

    def test_compound_cursor(self):
        """复合游标：[sort_value, id]，用于非主键排序。"""
        u = uuid.uuid4()
        dt = datetime.datetime(2026, 1, 1)
        v = [dt, u]
        encoded = encode_cursor(v)
        result = decode_cursor(encoded)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == dt
        assert result[1] == u

    def test_nested_compound(self):
        """多层嵌套列表。"""
        v = [[1, 2], [3, 4]]
        encoded = encode_cursor(v)
        result = decode_cursor(encoded)
        assert result == v


class TestInvalidInput:
    """非法输入测试。"""

    def test_empty_string_raises(self):
        with pytest.raises(CursorError, match="为空"):
            decode_cursor("")

    def test_invalid_base64(self):
        with pytest.raises(CursorError, match="无法解析"):
            decode_cursor("!!!not-base64!!!")

    def test_truncated_cursor(self):
        """截断的 cursor 不能静默成功。"""
        valid = encode_cursor("test")
        truncated = valid[:-2]
        with pytest.raises(CursorError):
            decode_cursor(truncated)

    def test_not_json(self):
        """base64url 解出来不是 JSON 的情况。"""
        import base64
        raw = base64.urlsafe_b64encode(b"not json at all").decode().rstrip("=")
        with pytest.raises(CursorError, match="无法解析"):
            decode_cursor(raw)

    def test_missing_type_field(self):
        """缺少 t 字段。"""
        import base64, json
        payload = json.dumps({"v": "123"}).encode()
        cursor = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        with pytest.raises(CursorError, match="结构非法"):
            decode_cursor(cursor)

    def test_unknown_type(self):
        """未知类型标记。"""
        import base64, json
        payload = json.dumps({"t": "unknown", "v": "x"}).encode()
        cursor = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        with pytest.raises(CursorError, match="未知游标类型"):
            decode_cursor(cursor)

    def test_invalid_uuid_in_cursor(self):
        """游标中的 UUID 不合法。"""
        import base64, json
        payload = json.dumps({"t": "uuid", "v": "not-a-uuid"}).encode()
        cursor = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        with pytest.raises(CursorError, match="UUID 非法"):
            decode_cursor(cursor)

    def test_invalid_datetime_in_cursor(self):
        """游标中的时间不合法。"""
        import base64, json
        payload = json.dumps({"t": "dt", "v": "not-a-date"}).encode()
        cursor = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        with pytest.raises(CursorError, match="时间非法"):
            decode_cursor(cursor)
