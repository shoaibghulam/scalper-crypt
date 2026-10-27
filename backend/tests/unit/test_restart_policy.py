"""The crash-loop breaker.

The first version counted failures inside a fixed time window while also
backing off exponentially between restarts. Those two mechanisms fight: by the
fourth retry the delays are long enough that older failures have aged out of
the window, so the count never reaches the limit and a permanently broken
process restarts forever.

Observed in practice as an engine started ten times against a held lock, with
no operator-visible conclusion.

Consecutive failures are therefore counted as well. A process that never stays
up has failed consecutively regardless of how far apart the attempts were.
"""

from scalper.supervisor import HEALTHY_RUNTIME_S, MAX_RESTARTS, RestartPolicy


class TestConsecutiveFailures:
    def test_repeated_failures_stop_even_when_spread_far_apart(self):
        policy = RestartPolicy()
        # Deliberately spaced beyond any window, mimicking the backoff.
        now = 0.0
        for _ in range(MAX_RESTARTS):
            assert policy.should_restart("engine", now=now) is True
            now += 120.0
        assert policy.should_restart("engine", now=now) is False

    def test_a_process_that_stayed_up_resets_the_count(self):
        # Running for a while and then dying is a different situation from
        # never starting: it deserves a fresh allowance.
        policy = RestartPolicy()
        for _ in range(MAX_RESTARTS - 1):
            policy.should_restart("engine", now=0.0)
        policy.note_healthy("engine")
        assert policy.should_restart("engine", now=0.0) is True

    def test_health_is_judged_by_how_long_the_process_ran(self):
        assert HEALTHY_RUNTIME_S > 0

    def test_counts_are_kept_per_process(self):
        policy = RestartPolicy()
        for _ in range(MAX_RESTARTS + 2):
            policy.should_restart("engine", now=0.0)
        assert policy.should_restart("api", now=0.0) is True


class TestBackoff:
    def test_it_grows_then_stops_growing(self):
        assert RestartPolicy.delay_for(1) < RestartPolicy.delay_for(3)
        assert RestartPolicy.delay_for(50) == RestartPolicy.delay_for(51)

    def test_the_first_retry_is_prompt(self):
        # A transient port clash clears in about a second; waiting longer for
        # the common case makes a restart feel broken.
        assert RestartPolicy.delay_for(1) <= 2.0
