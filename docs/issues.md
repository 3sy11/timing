# bollydog 测试能力改进 Issues

基于 timing 项目 Phase 6 TDD 过程中暴露的问题，针对 bollydog 框架提出以下改进建议。

---

## 覆盖率现状

```
单元测试覆盖率：28%（整体）

高覆盖模块（直接 mock 测试）：
  models/order.py          100%
  models/position.py       100%
  models/account.py        100%
  models/signal.py         100%
  strategy/models.py       100%
  data/models.py            97%
  execution/models.py       79%
  strategy/app.py           76%
  execution/broker.py       70%

零覆盖模块（E2E subprocess 无法统计 / 无独立测试）：
  analysis/app.py            0%  ← E2E 中被调用但 subprocess 不计入
  engine/command.py          0%  ← 同上
  analysis/algo/*            0%  ← 算法层未独立测试
```

**根因**：bollydog Hub 的生命周期管理不支持进程内 E2E 测试（详见 Issue #1），
导致 E2E 只能走 subprocess，coverage 无法跨进程统计。

---

## Issue #1：Hub.__aexit__ 不退出 — E2E 测试不可行

**现象**：
```python
async with Hub() as hub:
    await hub.execute(msg)
# 永远走不到这里 — __aexit__ 中 stop() 等待后台任务永不返回
```

**影响**：
- pytest-asyncio 中无法使用 `async with Hub()` 模式
- E2E 测试被迫走 subprocess（丢失覆盖率、无法 assert 内部状态）
- CLI 的 `asyncio.run()` 能工作是因为强制取消了所有 Task，而非正常退出

**根因**：
mode.Service 的 Queue consumer task 是无限循环，`stop()` 等待它完成但它永远不会自行结束。

**建议修复**：
```python
# 方案 A：Hub 增加 execute-and-exit 模式
class Hub:
    async def execute_once(self, msg):
        """启动 → 执行 → 自动 stop。适用于 CLI 和测试。"""
        await self.start()
        try:
            await self.execute(msg)
        finally:
            self._force_stop()  # 取消所有 background tasks 后 stop

# 方案 B：stop() 增加超时 + 强制取消
class Hub:
    async def stop(self, timeout=5.0):
        for task in self._background_tasks:
            task.cancel()
        await asyncio.wait(self._background_tasks, timeout=timeout)
        await super().stop()
```

**优先级**：P0（阻塞 E2E 测试）

---

## Issue #2：AppService._apps 全局注册表污染测试隔离

**现象**：
```python
# test_a.py 中 load_from_config 注册了 TimingApp
# test_b.py 再 load_from_config 会 assert 失败："_started already set"
```

**影响**：
- 无法在同一 pytest session 中运行多个 E2E 测试（服务实例残留）
- `AnalysisEngine._services` 类变量跨测试泄漏

**建议修复**：
```python
# 增加 reset 类方法
class AppService:
    @classmethod
    def reset_registry(cls):
        """测试前调用，清除所有注册的服务实例。"""
        cls._apps.clear()

# 或提供 pytest fixture
@pytest.fixture(autouse=True)
def clean_registry():
    yield
    AppService.reset_registry()
```

**优先级**：P0（阻塞多 E2E 测试）

---

## Issue #3：hub.execute() 返回 Command 对象而非结果

**现象**：
```python
result_msg = await hub.execute(GetKlines(symbol="T", interval="1d"))
# 获取真正结果需要：
actual_result = result_msg.state.result()
```

**影响**：
- 测试代码两步取值，冗余且易错
- 与 spec 中"Command.__call__ → ReturnType"的签名约定矛盾
- 业务代码中 `result = await hub.execute(cmd)` 的 `result` 不是文档说的返回值

**建议修复**：
```python
# 选项 A：execute 直接返回 __call__ 返回值
result = await hub.execute(msg)  # result 就是 __call__ 的 return

# 选项 B（向后兼容）：增加 execute_and_get
result = await hub.execute_result(msg)
```

**优先级**：P1（影响 API 一致性和 DX）

---

## Issue #4：Exchange subscriber 回调签名不直观

**现象**：
```python
class FibStrategy(AppService):
    async def on_signal(self, cmd):
        # cmd 不是 SignalEmitted 本身，而是一个 wrapper Command
        event_data = cmd.get_event()  # 返回 dict，丢失类型信息
        symbol = event_data.get("symbol", "")
```

**影响**：
- 无法对 subscriber 方法做类型标注
- `cmd.get_event()` 返回 dict 而非 Event 实例，丢失 Pydantic 校验
- 测试中构造 mock cmd 需要了解框架内部实现细节

**建议修复**：
```python
# subscriber 回调直接接收 Event 实例
async def on_signal(self, event: SignalEmitted):
    symbol = event.symbol  # 有类型提示、有自动补全

# 框架内部 Exchange._make_callback 改为：
#   handler(event_instance) 而非 handler(wrapper_cmd)
```

**优先级**：P1（影响 DX 和类型安全）

---

## Issue #5：缺少内置测试工具包

**现象**：
每个项目都要手写 mock globals + 构造 MemoryProtocol + 模拟 Exchange 回调。

**建议增加 `bollydog.testing` 模块**：
```python
from bollydog.testing import TestHub, mock_app_context, make_event_cmd

# 快速构造测试环境
async def test_command():
    async with TestHub(config="test.toml") as hub:
        result = await hub.execute_result(MyCommand(x=1))
        assert result == expected

# 模拟 app/protocol 上下文（避免手动 patch）
async def test_with_mock():
    proto = MemoryProtocol()
    await proto.start()
    with mock_app_context(MyService, protocol=proto):
        cmd = MyCommand(data="test")
        result = await cmd()

# 构造 subscriber 测试用的 event cmd
cmd = make_event_cmd(SignalEmitted(symbol="T", direction="long", strength=0.8))
await svc.on_signal(cmd)
```

**优先级**：P1（大幅降低测试成本）

---

## Issue #6：Protocol 生命周期与 mode.Service 耦合过深

**现象**：
```python
proto = SQLiteProtocol(path="test.db")
await proto.get("key")  # 报错：adapter 未初始化（需先 start）

# 必须：
await proto.start()  # 但 start 依赖 event loop + mode.Service 状态机
```

**影响**：
- 单元测试中无法独立使用 Protocol（需启动整个 Service 生命周期）
- MemoryProtocol 是唯一可以绕过的（因为它 start 是 no-op）

**建议修复**：
```python
# Protocol 增加 standalone 模式
proto = SQLiteProtocol(path="test.db", standalone=True)
# standalone=True 时 on_start 逻辑在首次 get/set 时 lazy 执行
await proto.get("key")  # 自动初始化
```

**优先级**：P2

---

## Issue #7：Command 字段类型校验不严格

**现象**：
spec 要求"Command fields 必须是原始类型"，但框架不做运行时校验：
```python
class Bad(BaseCommand):
    obj: SomeDomainModel = None  # 违反 spec，但框架不报错
```

**建议修复**：
```python
# BaseCommand.__init_subclass__ 中增加校验
ALLOWED_TYPES = (str, int, float, bool, list, dict, type(None))
for field in cls.model_fields.values():
    if field.annotation not in ALLOWED_TYPES:
        raise TypeError(f"Command field must be primitive type, got {field.annotation}")
```

**优先级**：P2（防御性编程）

---

## Issue #8：无 `--timeout` 选项用于 CLI execute

**现象**：
`bollydog execute RunBacktest` 执行完毕后进程不退出（等待后台任务），
运维和 CI 中需要外部 kill。

**建议修复**：
```bash
bollydog execute RunBacktest --config config.toml --timeout 30
# 30 秒后 force exit（覆盖 mode.Service 不退出的问题）
```

实现：
```python
@staticmethod
def execute(command: str, timeout: int = None, **kwargs):
    ...
    async def _run():
        async with hub:
            await hub.execute(msg)
    try:
        asyncio.run(_run())  # 当前行为：依赖 asyncio.run 强制退出
    except KeyboardInterrupt:
        pass
    finally:
        os._exit(0)  # 保底退出
```

**优先级**：P1（影响 CI 和运维）

---

## 汇总优先级

| 优先级 | Issue | 影响范围 |
|--------|-------|---------|
| P0 | #1 Hub 不退出 | E2E 测试不可行 |
| P0 | #2 全局注册表污染 | 多测试隔离 |
| P1 | #3 execute 返回值 | API 一致性 |
| P1 | #4 subscriber 回调签名 | 类型安全 + DX |
| P1 | #5 缺少测试工具包 | 所有使用者 |
| P1 | #8 CLI 无 timeout | CI/运维 |
| P2 | #6 Protocol 生命周期 | 独立测试 Protocol |
| P2 | #7 字段类型校验 | 防御性编程 |
