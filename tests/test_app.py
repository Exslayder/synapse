from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from tests._bootstrap import SRC  # noqa: F401
from synapse.app import acquire_lock, pid_alive, release_lock


class LockTests(unittest.TestCase):
    def test_current_process_is_alive(self) -> None:
        self.assertTrue(pid_alive(os.getpid()))

    def test_zero_and_negative_are_not_alive(self) -> None:
        self.assertFalse(pid_alive(0))
        self.assertFalse(pid_alive(-1))

    def test_acquire_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "app.lock"
            self.assertTrue(acquire_lock(lock))
            self.assertTrue(lock.exists())
            self.assertFalse(acquire_lock(lock))
            release_lock(lock)
            self.assertFalse(lock.exists())

    def test_stale_lock_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "app.lock"
            lock.write_text("0\n0\n", encoding="utf-8")
            self.assertTrue(acquire_lock(lock))
            self.assertEqual(lock.read_text(encoding="utf-8").splitlines()[0], str(os.getpid()))

    def test_broken_lock_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "app.lock"
            lock.write_text("not-a-pid", encoding="utf-8")
            self.assertTrue(acquire_lock(lock))


if __name__ == "__main__":
    unittest.main()
