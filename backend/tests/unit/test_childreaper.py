"""Binding children to the parent lifetime.

Killing a parent does not kill its children on Windows, and on POSIX only for
terminal signals. A supervisor killed abruptly therefore leaves the engine
holding its single-instance lock and the web app holding its port, and every
later start fails against resources owned by a process nobody can see.

The graceful shutdown path remains preferred, because it lets the engine
release the lock and close its streams. This is what happens when that path
never runs.
"""

import subprocess
import sys

from scalper.childreaper import ChildReaper


class TestConstruction:
    def test_it_constructs_on_any_platform(self):
        # Job objects are unavailable in some sandboxes. Losing the backstop is
        # survivable; refusing to start the application is not.
        ChildReaper()

    def test_it_reports_whether_the_backstop_is_available(self):
        assert isinstance(ChildReaper().active, bool)


class TestAdoption:
    def test_adopting_a_live_child_does_not_raise(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            ChildReaper().adopt(child)
        finally:
            child.kill()
            child.wait(timeout=10)

    def test_adopting_an_already_dead_child_is_survivable(self):
        # The child can exit between spawning and adoption. That race must not
        # take down the supervisor.
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=10)
        ChildReaper().adopt(child)

    def test_adoption_is_a_no_op_when_unavailable(self):
        reaper = ChildReaper()
        reaper._job = None
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            reaper.adopt(child)
            assert child.poll() is None
        finally:
            child.kill()
            child.wait(timeout=10)
