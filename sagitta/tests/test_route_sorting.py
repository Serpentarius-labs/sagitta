# tests/test_route_sorting.py
"""路由排序逻辑单元测试。

覆盖：
  * 字面量路径排在参数化路径之前（避免 /{id} 拦截 /page）
  * 子类覆盖父类方法只注册一次
  * 参数占位符少的先注册
"""
import pytest

from sagitta.Flow.controller import Controller


class _CaptureController(Controller):
    """测试用控制器，不实际挂载。"""
    prefix = "/test"
    __sg_abstract__ = True


class TestRouteOrdering:
    """路由排序规则测试。"""

    def test_literal_before_param(self):
        """字面量 /page 必须排在参数化 /{id} 之前。"""
        class C(Controller):
            prefix = "/items"
            __sg_abstract__ = True

            @Controller.get("/{item_id}")
            async def get_item(self, item_id: str): pass

            @Controller.get("/page")
            async def page(self): pass

            @Controller.get("/search")
            async def search(self): pass

        routes = C._collect_routes()
        paths = [meta["path"] for _, _, meta in routes]
        # /page 和 /search（0 个占位符）必须在 /{item_id}（1 个）之前
        idx_page = paths.index("/page")
        idx_search = paths.index("/search")
        idx_param = paths.index("/{item_id}")
        assert idx_page < idx_param
        assert idx_search < idx_param

    def test_fewer_params_first(self):
        """参数少的排在参数多的前面。"""
        class C(Controller):
            prefix = "/api"
            __sg_abstract__ = True

            @Controller.get("/{a}/{b}/{c}")
            async def three_params(self, a, b, c): pass

            @Controller.get("/{a}")
            async def one_param(self, a): pass

            @Controller.get("/{a}/{b}")
            async def two_params(self, a, b): pass

        routes = C._collect_routes()
        paths = [meta["path"] for _, _, meta in routes]
        assert paths == ["/{a}", "/{a}/{b}", "/{a}/{b}/{c}"]

    def test_override_only_once(self):
        """子类覆盖的方法只注册一次。"""
        class Base(Controller):
            prefix = "/base"
            __sg_abstract__ = True

            @Controller.get("/items")
            async def items(self): pass

        class Child(Base):
            prefix = "/child"
            __sg_abstract__ = True

            @Controller.get("/items")
            async def items(self): pass

        routes = Child._collect_routes()
        names = [name for name, _, _ in routes]
        assert names.count("items") == 1  # 子类覆盖，不重复

    def test_same_param_count_by_alpha(self):
        """同参数数量时按字典序排序，保证确定性。"""
        class C(Controller):
            prefix = "/api"
            __sg_abstract__ = True

            @Controller.get("/{z_id}")
            async def z(self, z_id): pass

            @Controller.get("/{a_id}")
            async def a(self, a_id): pass

            @Controller.get("/{m_id}")
            async def m(self, m_id): pass

        routes = C._collect_routes()
        paths = [meta["path"] for _, _, meta in routes]
        assert paths == ["/{a_id}", "/{m_id}", "/{z_id}"]
