# app/biz_code.py
"""Demo 业务码册 —— 宿主项目「业务状态码集中定义处」的标准位置。

约定：
  * 一个模块/领域一个码册类（继承 BizCodes），码名全大写下划线。
  * 每行声明同时给出「HTTP 大类 + 默认文案」，框架在类创建时自动注册、
    全局查重——同名码语义不一致会在启动期直接报错。
  * **只登记真正会抛出的码**：预声明一堆「可能用得上」的码，码册很快
    会变成无人维护的垃圾场。
  * Service/Controller 永远不手写数字状态码，只引用这里的属性。

抛出：
    raise BizError(UserCodes.USERNAME_TAKEN)

接口声明（自动进入 /docs 的响应码表）：
    @raises(UserCodes.USERNAME_TAKEN)
"""
from sagittapi.Core.biz_code import BizCode, BizCodes


class UserCodes(BizCodes):
    """用户领域业务码。"""

    USERNAME_TAKEN = BizCode(409, "用户名已被占用")


__all__ = ["UserCodes"]
