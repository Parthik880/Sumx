"""How many transcription requests may run now: per job (adaptive) and across all workers (budget)."""

import asyncio
import logging
import math
import random
import time
import uuid
from collections import deque
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from ..config import settings
from ..models import DB, SttLease, SttState

log = logging.getLogger("sumx.stt")
PROBLEMS_BEFORE_DECREASE = 3
PROBLEM_WINDOW_SECONDS = 60
LATENCY_WINDOW = 12
LATENCY_DECREASE = 2.0
LATENCY_INCREASE = 1.5
CEILING_COOLDOWNS = 5
PREFERENCE_SECONDS = 86400
LEASE_SECONDS = 300


def p95(values):
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * 0.95) - 1] if ordered else 0.0


class AdaptiveLimit:
    """Steps down at once on throttling, and up slowly once a level has proven itself.

    A reduction applies to future scheduling only: requests in flight finish. Requests that began
    before a reduction cannot trigger another one, so a single burst costs a single step.
    """

    def __init__(self, levels, start, adaptive=True, clock=time.monotonic):
        self.levels = sorted(set(levels) | {start}, reverse=True)
        self.position = self.levels.index(start)
        self.adaptive = adaptive
        self.clock = clock
        self.active = 0
        self.epoch = 0
        self.paused_until = 0.0
        self.successes = 0
        self.changed_at = clock()
        self.latency = deque(maxlen=LATENCY_WINDOW)
        self.baseline = None
        self.problems = deque()
        self.probing = False
        self.ceiling = 0
        self.ceiling_until = 0.0
        self.cooldown = settings.stt_increase_cooldown_seconds
        self.changes = []
        self.peak = 0
        self.wake = asyncio.Event()

    @property
    def limit(self):
        return self.levels[self.position]

    @property
    def stable_level(self):
        """A level under probation has not earned being remembered."""
        return self.levels[self.position + 1] if self.probing else self.limit

    async def acquire(self):
        while True:
            wait = self.paused_until - self.clock()
            if wait > 0:
                await asyncio.sleep(wait)
            elif self.active < self.limit:
                self.active += 1
                self.peak = max(self.peak, self.active)
                return self.epoch
            else:
                self.wake.clear()
                await self.wake.wait()

    def release(self):
        self.active -= 1
        self.wake.set()

    def pause(self, seconds):
        self.paused_until = max(self.paused_until, self.clock() + seconds)

    def throttled(self, epoch, wait):
        self.pause(wait)
        self.decrease(epoch, "throttled")

    def problem(self, epoch):
        """5xx responses and timeouts count together; one alone may just be one bad request."""
        if epoch != self.epoch:
            return
        now = self.clock()
        self.problems.append(now)
        while self.problems and self.problems[0] < now - PROBLEM_WINDOW_SECONDS:
            self.problems.popleft()
        self.successes = 0
        if len(self.problems) >= PROBLEMS_BEFORE_DECREASE:
            self.decrease(epoch, "provider errors")

    def success(self, epoch, seconds):
        if epoch != self.epoch:
            return
        self.latency.append(seconds)
        self.successes += 1
        if len(self.latency) == LATENCY_WINDOW:
            current = p95(self.latency)
            if self.baseline is None or current < self.baseline:
                self.baseline = current
            elif (
                current > self.baseline * LATENCY_DECREASE
                and sum(n > self.baseline * LATENCY_DECREASE for n in self.latency) >= LATENCY_WINDOW * 0.66
            ):
                self.decrease(epoch, "rising latency")
                return
        self.increase()

    def decrease(self, epoch, reason):
        if not self.adaptive or epoch != self.epoch:
            return
        if self.position == len(self.levels) - 1:
            self.successes, self.changed_at = 0, self.clock()
            self.latency.clear()
            return
        if self.probing:
            # This level proved worse than the one below it: stay below it for a while.
            self.ceiling = self.position + 1
            self.ceiling_until = self.clock() + self.cooldown * CEILING_COOLDOWNS
            self.cooldown = min(900, self.cooldown * 2)
            self.probing = False
        self.move(self.position + 1, reason)

    def increase(self):
        while self.problems and self.problems[0] < self.clock() - PROBLEM_WINDOW_SECONDS:
            self.problems.popleft()
        if not self.adaptive or self.successes < settings.stt_increase_successes:
            return
        if self.probing:
            self.probing = False
            self.successes = 0
            return
        now = self.clock()
        if (
            self.position == 0
            or now - self.changed_at < self.cooldown
            or self.problems
            or len(self.latency) < LATENCY_WINDOW
            or (self.baseline and p95(self.latency) > self.baseline * LATENCY_INCREASE)
            or (self.position <= self.ceiling and now < self.ceiling_until)
        ):
            return
        self.probing = True
        self.move(self.position - 1, "stable")

    def move(self, position, reason):
        before = self.limit
        self.position = position
        self.epoch += 1
        self.successes = 0
        self.latency.clear()
        self.problems.clear()
        self.changed_at = self.clock()
        self.changes.append({"from": before, "to": self.limit, "reason": reason})
        log.info("STT concurrency %s -> %s (%s)", before, self.limit, reason)
        self.wake.set()


class NoBudget:
    async def take(self):
        return None

    async def give(self, slot):
        pass

    async def result(self, slot, error=None, seconds=None):
        pass

    async def take_extract(self):
        return None


class GlobalBudget:
    """Numbered slots in the database, so simultaneous meetings in any worker share one budget.

    Each slot is a row with a unique number. A crashed worker's leases simply expire.
    """

    def __init__(self, owner):
        self.owner = uuid.uuid4().hex
        self.epochs = {}

    def locked_controller(self, db):
        # UPDATE takes SQLite's writer lock before reading; FOR UPDATE also covers PostgreSQL.
        db.execute(update(SttState).where(SttState.id == 1).values(level=SttState.level))
        state = db.scalar(select(SttState).where(SttState.id == 1).with_for_update())
        if state is None:
            try:
                with db.begin_nested():
                    state = SttState(id=1, level=settings.stt_initial_concurrency, updated=0, control={})
                    db.add(state)
                    db.flush()
            except IntegrityError:
                state = db.scalar(select(SttState).where(SttState.id == 1).with_for_update())
        levels = [
            n
            for n in settings.stt_concurrency_levels
            if settings.stt_min_concurrency <= n <= settings.stt_max_concurrency
        ]
        controller = AdaptiveLimit(
            levels, settings.stt_initial_concurrency, settings.stt_adaptive, clock=time.time
        )
        saved = state.control or {}
        if (
            settings.stt_adaptive
            and state.updated > time.time() - PREFERENCE_SECONDS
            and saved.get("levels") == controller.levels
        ):
            for key in (
                "position",
                "epoch",
                "paused_until",
                "successes",
                "changed_at",
                "baseline",
                "probing",
                "ceiling",
                "ceiling_until",
                "cooldown",
            ):
                if key in saved:
                    setattr(controller, key, saved[key])
            controller.latency.extend(saved.get("latency", []))
            controller.problems.extend(saved.get("problems", []))
        return state, controller

    @staticmethod
    def persist(db, state, controller):
        state.level, state.updated = controller.limit, time.time()
        state.control = {
            key: getattr(controller, key)
            for key in (
                "levels",
                "position",
                "epoch",
                "paused_until",
                "successes",
                "changed_at",
                "baseline",
                "probing",
                "ceiling",
                "ceiling_until",
                "cooldown",
            )
        }
        state.control.update(latency=list(controller.latency), problems=list(controller.problems))
        db.commit()

    def try_take(self, extraction=False):
        now = time.time()
        with DB() as db:
            state, controller = self.locked_controller(db)
            db.execute(delete(SttLease).where(SttLease.expires < now))
            used = set(db.scalars(select(SttLease.slot)))
            if controller.paused_until > now:
                db.commit()
                return None
            slots = (
                range(-settings.stt_extract_concurrency, 0)
                if extraction
                else range(min(settings.stt_global_concurrency, controller.limit))
            )
            active = sum(slot < 0 if extraction else slot >= 0 for slot in used)
            if active >= len(slots):
                db.commit()
                return None
            for slot in slots:
                if slot in used:
                    continue
                db.add(SttLease(slot=slot, owner=self.owner, expires=now + LEASE_SECONDS))
                try:
                    db.commit()
                    self.epochs[slot] = controller.epoch
                    return slot
                except IntegrityError:
                    db.rollback()
                    return None
            db.commit()
        return None

    def feedback(self, slot, error=None, seconds=None):
        with DB() as db:
            state, controller = self.locked_controller(db)
            epoch = self.epochs.get(slot, controller.epoch)
            if error:
                if error.kind in ("rate_limit", "in_flight"):
                    controller.throttled(
                        epoch, getattr(error, "backoff_seconds", None) or error.retry_after or 1
                    )
                elif error.kind in ("server", "ambiguous", "network"):
                    controller.problem(epoch)
            else:
                controller.success(epoch, seconds)
            self.persist(db, state, controller)

    async def result(self, slot, error=None, seconds=None):
        await asyncio.to_thread(self.feedback, slot, error, seconds)

    async def take(self):
        while True:
            slot = await asyncio.to_thread(self.try_take)
            if slot is not None:
                return slot
            await asyncio.sleep(0.25 + random.uniform(0, 0.25))

    async def take_extract(self):
        while True:
            slot = await asyncio.to_thread(self.try_take, True)
            if slot is not None:
                return slot
            await asyncio.sleep(0.1)

    def release(self, slot):
        with DB() as db:
            db.execute(delete(SttLease).where(SttLease.slot == slot, SttLease.owner == self.owner))
            db.commit()

    async def give(self, slot):
        await asyncio.to_thread(self.release, slot)
        self.epochs.pop(slot, None)


def preferred_level():
    """Start where recent work settled, so a bad level is not rediscovered by every meeting."""
    start = settings.stt_initial_concurrency
    if not settings.stt_adaptive:
        return start
    with DB() as db:
        state = db.get(SttState, 1)
        if state and state.updated > time.time() - PREFERENCE_SECONDS:
            stable = state.level
            control = state.control or {}
            levels, position = control.get("levels", []), control.get("position", -1)
            if control.get("probing") and 0 <= position + 1 < len(levels):
                stable = levels[position + 1]
            return min(start, stable)
    return start


def remember_level(level):
    with DB() as db:
        state = db.get(SttState, 1)
        if state:
            state.level, state.updated = level, time.time()
            state.control = {}
        else:
            db.add(SttState(id=1, level=level, updated=time.time()))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
