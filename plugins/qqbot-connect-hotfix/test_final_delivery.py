"""Deterministic contract tests for QQ final-delivery single-flight.

These tests deliberately know nothing about QQ stream/tombstone internals.  The
operation callback is the true-external delivery seam; the broker contract is
observable through call counts, results, concurrency and admission behavior.
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import anyio


def load_streaming_module():
    path = Path(__file__).with_name("streaming.py")
    spec = importlib.util.spec_from_file_location(
        "qqbot_final_delivery_contract_test",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


streaming = load_streaming_module()


def result(success, marker):
    return SimpleNamespace(success=success, marker=marker)


class DeliveryHarness:
    def __init__(self, *, limit=8):
        self.broker = streaming._QQC2CFinalDeliveryBroker(limit=limit)
        self.calls = []
        self.inflight = 0
        self.peak = 0
        self.entered = anyio.Event()
        self.concurrent = anyio.Event()
        self.release = anyio.Event()

    async def blocked_success(self, marker="delivered"):
        self.calls.append(marker)
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        self.entered.set()
        if self.inflight >= 2:
            self.concurrent.set()
        try:
            await self.release.wait()
            return result(True, marker)
        finally:
            self.inflight -= 1

    async def run(self, key, operation=None):
        return await self.broker.run(key, operation or self.blocked_success)


async def test_same_key_success_is_shared():
    harness = DeliveryHarness()
    results = []

    async def caller():
        results.append(await harness.run(("chat", "anchor")))

    async with anyio.create_task_group() as group:
        for _ in range(100):
            group.start_soon(caller)
        await harness.entered.wait()
        harness.release.set()

    assert harness.calls == ["delivered"]
    assert harness.peak == 1
    assert len(results) == 100
    assert {item.marker for item in results} == {"delivered"}
    assert harness.broker.stats().active == 0


async def test_success_does_not_depend_on_external_tombstone():
    harness = DeliveryHarness()
    external_tombstones = {("chat", "anchor"): "owned"}
    results = []

    async def deliver_then_evict():
        harness.calls.append("delivered")
        harness.entered.set()
        await harness.release.wait()
        external_tombstones.clear()
        return result(True, "retained-on-flight")

    async def caller():
        results.append(
            await harness.run(("chat", "anchor"), deliver_then_evict)
        )

    async with anyio.create_task_group() as group:
        for _ in range(3):
            group.start_soon(caller)
        await harness.entered.wait()
        harness.release.set()

    assert external_tombstones == {}
    assert harness.calls == ["delivered"]
    assert [item.marker for item in results] == [
        "retained-on-flight",
        "retained-on-flight",
        "retained-on-flight",
    ]


async def test_definite_failure_and_exception_hand_off():
    for first_outcome in ("failure", "exception"):
        broker = streaming._QQC2CFinalDeliveryBroker(limit=1)
        attempts = []
        results = []
        errors = []

        async def attempt():
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                if first_outcome == "exception":
                    raise RuntimeError("definite failure")
                return result(False, "failed")
            return result(True, "recovered")

        async def caller():
            try:
                results.append(await broker.run(("chat", "anchor"), attempt))
            except RuntimeError as exc:
                errors.append(str(exc))

        async with anyio.create_task_group() as group:
            group.start_soon(caller)
            group.start_soon(caller)

        assert attempts == [1, 2]
        assert [item.marker for item in results] == (
            ["failed", "recovered"]
            if first_outcome == "failure"
            else ["recovered"]
        )
        assert errors == ([] if first_outcome == "failure" else ["definite failure"])
        assert broker.stats().active == 0


async def test_different_keys_are_parallel():
    harness = DeliveryHarness(limit=2)
    results = []

    async def caller(key):
        results.append(await harness.run(key))

    async with anyio.create_task_group() as group:
        group.start_soon(caller, ("chat", "a"))
        group.start_soon(caller, ("chat", "b"))
        await harness.concurrent.wait()
        harness.release.set()

    assert harness.peak == 2
    assert len(results) == 2


async def test_capacity_backpressures_new_key_but_same_key_joins():
    harness = DeliveryHarness(limit=1)
    a_results = []
    b_results = []
    b_entered = anyio.Event()

    async def a_caller():
        a_results.append(await harness.run(("chat", "a")))

    async def b_attempt():
        b_entered.set()
        return result(True, "b")

    async def b_caller():
        b_results.append(await harness.run(("chat", "b"), b_attempt))

    async with anyio.create_task_group() as group:
        group.start_soon(a_caller)
        await harness.entered.wait()
        group.start_soon(a_caller)
        group.start_soon(b_caller)
        await anyio.sleep(0)
        assert not b_entered.is_set()
        assert harness.broker.stats().active == 1
        harness.release.set()

    assert len(a_results) == 2
    assert [item.marker for item in b_results] == ["b"]
    assert harness.broker.stats().peak == 1
    assert harness.broker.stats().active == 0


async def test_same_new_key_joins_after_capacity_wakeup():
    broker = streaming._QQC2CFinalDeliveryBroker(limit=1)
    a_entered = anyio.Event()
    a_release = anyio.Event()
    b_entered = anyio.Event()
    b_release = anyio.Event()
    b_attempts = []
    b_results = []

    async def a_attempt():
        a_entered.set()
        await a_release.wait()
        return result(True, "a")

    async def b_attempt():
        b_attempts.append("b")
        b_entered.set()
        await b_release.wait()
        return result(True, "b")

    async def b_caller():
        b_results.append(await broker.run(("chat", "b"), b_attempt))

    async with anyio.create_task_group() as group:
        group.start_soon(broker.run, ("chat", "a"), a_attempt)
        await a_entered.wait()
        group.start_soon(b_caller)
        group.start_soon(b_caller)
        with anyio.fail_after(1):
            while broker.stats().waiting != 2:
                await anyio.sleep(0)
        a_release.set()
        await b_entered.wait()
        with anyio.fail_after(1):
            while broker.stats().registered != 2:
                await anyio.sleep(0)
        assert broker.stats().active == 1
        assert broker.stats().waiting == 0
        b_release.set()

    assert b_attempts == ["b"]
    assert [item.marker for item in b_results] == ["b", "b"]
    assert broker.stats().active == 0


async def test_cancelled_admission_waiter_releases_capacity():
    harness = DeliveryHarness(limit=1)
    cancelled = anyio.Event()
    scope_holder = {}

    async def wait_for_b():
        with anyio.CancelScope() as scope:
            scope_holder["scope"] = scope
            await harness.run(("chat", "b"))
        cancelled.set()

    async with anyio.create_task_group() as group:
        group.start_soon(harness.run, ("chat", "a"))
        await harness.entered.wait()
        group.start_soon(wait_for_b)
        with anyio.fail_after(1):
            while harness.broker.stats().waiting != 1:
                await anyio.sleep(0)
        scope_holder["scope"].cancel()
        await cancelled.wait()
        harness.release.set()

    follow_up = await harness.broker.run(
        ("chat", "c"),
        lambda: _immediate_success("c"),
    )
    assert follow_up.marker == "c"
    assert harness.broker.stats().active == 0


async def test_cancelled_holder_does_not_cancel_inflight_delivery():
    harness = DeliveryHarness(limit=1)
    waiter_results = []
    holder_ready = anyio.Event()
    holder_done = anyio.Event()
    scope_holder = {}

    async def holder():
        with anyio.CancelScope() as scope:
            scope_holder["scope"] = scope
            holder_ready.set()
            await harness.run(("chat", "anchor"))
        holder_done.set()

    async def waiter():
        waiter_results.append(await harness.run(("chat", "anchor")))

    async with anyio.create_task_group() as group:
        group.start_soon(holder)
        await holder_ready.wait()
        await harness.entered.wait()
        group.start_soon(waiter)
        with anyio.fail_after(1):
            while harness.broker.stats().registered != 2:
                await anyio.sleep(0)
        scope_holder["scope"].cancel()
        await holder_done.wait()
        harness.release.set()

    assert harness.calls == ["delivered"]
    assert [item.marker for item in waiter_results] == ["delivered"]
    assert harness.broker.stats().active == 0


async def test_cancelled_sole_holder_retains_bounded_replay():
    harness = DeliveryHarness(limit=1)
    holder_ready = anyio.Event()
    holder_done = anyio.Event()
    scope_holder = {}

    async def holder():
        with anyio.CancelScope() as scope:
            scope_holder["scope"] = scope
            holder_ready.set()
            await harness.run(("chat", "anchor"))
        holder_done.set()

    async with anyio.create_task_group() as group:
        group.start_soon(holder)
        await holder_ready.wait()
        await harness.entered.wait()
        scope_holder["scope"].cancel()
        await holder_done.wait()
        harness.release.set()

    with anyio.fail_after(1):
        while harness.broker.stats().active:
            await anyio.sleep(0)
    replay = await harness.broker.run(
        ("chat", "anchor"),
        lambda: _immediate_success("duplicate"),
    )
    assert replay.marker == "delivered"
    assert harness.calls == ["delivered"]
    assert harness.broker.stats().completed == 1


async def test_noncacheable_success_is_shared_but_later_call_retries():
    broker = streaming._QQC2CFinalDeliveryBroker(limit=1)
    entered = anyio.Event()
    release = anyio.Event()
    attempts = []
    results = []

    async def close_pending():
        attempts.append("close-pending")
        entered.set()
        await release.wait()
        return streaming._QQC2CFinalAttemptOutcome(
            result(True, "close-pending"),
            cache_completed=False,
        )

    async def caller():
        results.append(await broker.run(("chat", "anchor"), close_pending))

    async with anyio.create_task_group() as group:
        group.start_soon(caller)
        await entered.wait()
        group.start_soon(caller)
        with anyio.fail_after(1):
            while broker.stats().registered != 2:
                await anyio.sleep(0)
        release.set()

    assert attempts == ["close-pending"]
    assert [item.marker for item in results] == [
        "close-pending",
        "close-pending",
    ]
    assert broker.stats().completed == 0

    retried = await broker.run(
        ("chat", "anchor"),
        lambda: _immediate_success("closed"),
    )
    assert retried.marker == "closed"
    assert broker.stats().completed == 1


async def test_completed_replay_registry_is_bounded():
    broker = streaming._QQC2CFinalDeliveryBroker(
        limit=2,
        completed_limit=3,
    )
    for index in range(10):
        delivered = await broker.run(
            ("chat", str(index)),
            lambda marker=str(index): _immediate_success(marker),
        )
        assert delivered.marker == str(index)
    assert broker.stats().active == 0
    assert broker.stats().completed == 3


async def test_external_completion_uses_same_bounded_replay_registry():
    broker = streaming._QQC2CFinalDeliveryBroker(
        limit=1,
        completed_limit=2,
    )
    broker.remember_completed(("chat", "failed"), result(False, "failed"))
    assert broker.stats().completed == 0

    for marker in ("a", "b", "c"):
        broker.remember_completed(
            ("chat", marker),
            result(True, marker),
        )
    assert broker.stats().completed == 2

    replayed = await broker.run(
        ("chat", "c"),
        lambda: _immediate_success("duplicate"),
    )
    evicted = await broker.run(
        ("chat", "a"),
        lambda: _immediate_success("a-retried"),
    )
    assert replayed.marker == "c"
    assert evicted.marker == "a-retried"
    assert broker.stats().completed == 2


async def test_cleanup_coordination_waits_for_active_delivery():
    broker = streaming._QQC2CFinalDeliveryBroker(limit=1)
    delivery_entered = anyio.Event()
    delivery_release = anyio.Event()
    cleanup_entered = anyio.Event()
    results = {}

    async def delivery():
        delivery_entered.set()
        await delivery_release.wait()
        return result(True, "delivered")

    async def cleanup():
        active_completed = broker.completed_for(("chat", "anchor"))
        results["active_completed"] = active_completed
        cleanup_entered.set()
        return result(True, "cleaned")

    async def run_delivery():
        results["delivery"] = await broker.run(("chat", "anchor"), delivery)

    async def run_cleanup():
        results["cleanup"] = await broker.coordinate(
            ("chat", "anchor"),
            cleanup,
        )

    async with anyio.create_task_group() as group:
        group.start_soon(run_delivery)
        await delivery_entered.wait()
        group.start_soon(run_cleanup)
        await anyio.sleep(0)
        assert not cleanup_entered.is_set()
        delivery_release.set()

    assert results["delivery"].marker == "delivered"
    assert results["cleanup"].marker == "cleaned"
    assert results["active_completed"].marker == "delivered"
    assert cleanup_entered.is_set()
    assert broker.completed_for(("chat", "anchor")).marker == "delivered"
    assert broker.completed_for(("chat", "different")) is None
    assert broker.stats().active == 0


async def test_transient_completion_lives_until_registered_users_drain():
    broker = streaming._QQC2CFinalDeliveryBroker(limit=1)
    key = ("chat", "anchor")
    owner_entered = anyio.Event()
    owner_release = anyio.Event()
    waiter_observed = anyio.Event()
    completion = object()

    async def retain_completion():
        broker.remember_transient_completion(key, completion)
        owner_entered.set()
        await owner_release.wait()
        return result(True, "abandoned")

    async def observe_completion():
        assert broker.transient_completion_for(key) is completion
        waiter_observed.set()
        return result(True, "observed")

    async with anyio.create_task_group() as group:
        group.start_soon(broker.coordinate, key, retain_completion)
        await owner_entered.wait()
        group.start_soon(broker.coordinate, key, observe_completion)
        with anyio.fail_after(1):
            while broker.stats().registered != 2:
                await anyio.sleep(0)
        owner_release.set()

    assert waiter_observed.is_set()
    assert broker.stats().active == 0
    assert broker.stats().completed == 0
    assert broker.transient_completion_for(key) is None


async def _immediate_success(marker):
    return result(True, marker)


async def test_stress_never_exceeds_hard_limit():
    limit = 4
    broker = streaming._QQC2CFinalDeliveryBroker(limit=limit)
    gate = anyio.Event()

    async def attempt():
        await gate.wait()
        return result(True, "ok")

    async def caller(index):
        await broker.run(("chat", str(index)), attempt)

    async with anyio.create_task_group() as group:
        for index in range(200):
            group.start_soon(caller, index)
        with anyio.fail_after(1):
            while broker.stats().active < limit:
                await anyio.sleep(0)
        assert broker.stats().active == limit
        assert broker.stats().peak == limit
        gate.set()

    assert broker.stats().active == 0


async def main():
    await test_same_key_success_is_shared()
    await test_success_does_not_depend_on_external_tombstone()
    await test_definite_failure_and_exception_hand_off()
    await test_different_keys_are_parallel()
    await test_capacity_backpressures_new_key_but_same_key_joins()
    await test_same_new_key_joins_after_capacity_wakeup()
    await test_cancelled_admission_waiter_releases_capacity()
    await test_cancelled_holder_does_not_cancel_inflight_delivery()
    await test_cancelled_sole_holder_retains_bounded_replay()
    await test_noncacheable_success_is_shared_but_later_call_retries()
    await test_completed_replay_registry_is_bounded()
    await test_external_completion_uses_same_bounded_replay_registry()
    await test_cleanup_coordination_waits_for_active_delivery()
    await test_transient_completion_lives_until_registered_users_drain()
    await test_stress_never_exceeds_hard_limit()
    print("qq_c2c_final_broker_same_key_single_flight=ok")
    print("qq_c2c_final_broker_retains_success=ok")
    print("qq_c2c_final_broker_failure_handoff=ok")
    print("qq_c2c_final_broker_different_key_parallel=ok")
    print("qq_c2c_final_broker_capacity_backpressure=ok")
    print("qq_c2c_final_broker_pending_same_key_join=ok")
    print("qq_c2c_final_broker_admission_cancel_cleanup=ok")
    print("qq_c2c_final_broker_holder_cancel_isolated=ok")
    print("qq_c2c_final_broker_cancelled_sole_holder_replay=ok")
    print("qq_c2c_final_broker_close_pending_retry=ok")
    print("qq_c2c_final_broker_completed_registry_bounded=ok")
    print("qq_c2c_final_broker_external_completion_bounded=ok")
    print("qq_c2c_final_broker_cleanup_coordination=ok")
    print("qq_c2c_final_broker_transient_completion_drain=ok")
    print("qq_c2c_final_broker_stress_bound=ok")


anyio.run(main)
