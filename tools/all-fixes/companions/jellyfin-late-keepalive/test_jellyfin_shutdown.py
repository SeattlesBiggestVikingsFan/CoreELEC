"""Deterministic regressions executing unchanged class ASTs from original and corrected addon sources."""

import ast
import json
import pathlib
import threading
import types
import unittest
from unittest.mock import Mock


SOURCE = pathlib.Path(__file__).resolve().parent


def classes(path, delay=None, capability=None, start_gate=None):
    created = []
    posts = []
    failures = []

    class Worker:
        def __init__(self, client, ws):
            self.started = False
            self.stopped = False
            created.append(self)

        def start(self):
            if start_gate:
                start_gate()
            self.started = True

        def stop(self):
            if not self.started:
                raise RuntimeError("Cannot join worker before it is started")
            self.stopped = True

    def wait(seconds):
        if seconds != 5:
            raise AssertionError("Startup capabilities delay must remain five seconds")
        if delay:
            delay()
        return False

    def post(value):
        posts.append(value)
        if capability:
            capability()

    namespace = {
        "threading": threading, "json": json, "LOG": Mock(),
        "time": types.SimpleNamespace(sleep=wait),
        "xbmc": types.SimpleNamespace(Monitor=lambda: types.SimpleNamespace(waitForAbort=wait)),
        "settings": lambda name: False,
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    code = ast.Module(body=[node for node in tree.body if isinstance(node, ast.ClassDef)],
                      type_ignores=[])
    exec(compile(code, str(path), "exec"), namespace)
    real_worker = namespace["KeepAlive"]
    namespace["KeepAlive"] = Worker
    client = namespace["WSClient"](types.SimpleNamespace(
        jellyfin=types.SimpleNamespace(post_capabilities=post),
    ))
    client.wsc = types.SimpleNamespace(close=lambda: None)

    def open_callback():
        try:
            client.on_open(client.wsc)
        except Exception as error:
            failures.append(error)

    return client, created, posts, failures, open_callback, real_worker


def pause(entered, release):
    entered.set()
    if not release.wait(2):
        raise TimeoutError("Deterministic source regression gate was not released")


def stop_during(path, phase):
    entered, release = threading.Event(), threading.Event()
    callback = lambda: pause(entered, release)
    kwargs = {phase: callback}
    client, workers, posts, errors, open_callback, _ = classes(path, **kwargs)
    thread = threading.Thread(target=open_callback)
    thread.start()
    try:
        if not entered.wait(2):
            raise AssertionError("Open callback did not enter its controlled window")
        client.stop_client()
    finally:
        release.set()
        thread.join(2)
    if thread.is_alive() or errors:
        raise AssertionError("Controlled source callback failed or remained live")
    return not workers


def stop_during_start(path):
    entered, release = threading.Event(), threading.Event()
    client, workers, _, errors, open_callback, _ = classes(
        path, start_gate=lambda: pause(entered, release),
    )
    creator = threading.Thread(target=open_callback)
    creator.start()
    if not entered.wait(2):
        release.set()
        creator.join(2)
        raise AssertionError("Worker start did not enter its controlled window")

    def stop():
        try:
            client.stop_client()
        except Exception as error:
            errors.append(error)

    stopper = threading.Thread(target=stop)
    stopper.start()
    release.set()
    creator.join(2)
    stopper.join(2)
    if creator.is_alive() or stopper.is_alive():
        raise AssertionError("Publication regression did not release its owned threads")
    return not errors and all(worker.started and worker.stopped for worker in workers)


class JellyfinShutdownTests(unittest.TestCase):
    def test_original_reproduces_late_worker_during_startup_delay(self):
        self.assertFalse(stop_during(SOURCE / "ws_client-original.py", "delay"))

    def test_fixed_does_not_create_worker_after_stop_during_delay(self):
        self.assertTrue(stop_during(SOURCE / "ws_client.py", "delay"))

    def test_original_reproduces_late_worker_during_capability_request(self):
        self.assertFalse(stop_during(SOURCE / "ws_client-original.py", "capability"))

    def test_fixed_does_not_create_worker_after_stop_during_capability_request(self):
        self.assertTrue(stop_during(SOURCE / "ws_client.py", "capability"))

    def test_stop_during_worker_publication_joins_only_started_worker(self):
        for _ in range(20):
            self.assertTrue(stop_during_start(SOURCE / "ws_client.py"))

    def test_normal_startup_and_reconnect_replace_and_join_owned_workers(self):
        client, workers, posts, errors, callback, _ = classes(SOURCE / "ws_client.py")
        callback()
        callback()
        self.assertEqual(len(posts), 2)
        self.assertEqual(len(workers), 2)
        self.assertTrue(workers[0].stopped)
        self.assertFalse(workers[1].stopped)
        client.stop_client()
        self.assertTrue(all(worker.started and worker.stopped for worker in workers))
        self.assertEqual(errors, [])

    def test_real_keepalive_cancellation_signals_and_joins_its_thirty_second_wait(self):
        _, _, _, _, _, worker_type = classes(SOURCE / "ws_client.py")
        worker = worker_type(None, types.SimpleNamespace(send=Mock()))
        entered = threading.Event()
        event = threading.Event()

        class TracedEvent:
            def is_set(self):
                return event.is_set()

            def wait(self, seconds):
                if seconds != 30:
                    raise AssertionError("Keepalive interval was unexpectedly changed")
                entered.set()
                return event.wait(seconds)

            def set(self):
                event.set()

        worker.halt = TracedEvent()
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
        finally:
            worker.stop()
        self.assertFalse(worker.is_alive())
        worker.ws.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
