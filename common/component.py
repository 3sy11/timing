"""BaseComponent：统一生命周期状态机基类。
所有 AppService 组件应实现 on_start / on_stop / on_reset / on_dispose。
bollydog mode.Service 已提供 start/stop，此处补充 reset/dispose 语义。"""
from bollydog.models.service import AppService


class BaseComponent(AppService):
    """扩展 AppService 的生命周期钩子。
    子类覆写 on_reset() 必须将内部所有持久状态清零。"""

    async def on_reset(self) -> None:
        """将所有内部状态字段清零（等同于刚 new 出来）。子类必须覆写。"""
        pass

    async def on_dispose(self) -> None:
        """释放所有资源，此后组件不可再 start。"""
        pass

    async def reset(self) -> None:
        await self.on_reset()

    async def dispose(self) -> None:
        await self.on_dispose()
