from __future__ import annotations

import random
from typing import Optional

from pet_context import PetContext

# Every frame line is exactly FRAME_W columns. The cat is drawn in Consolas, and a
# glyph Consolas lacks is drawn from a fallback font with a different advance width,
# which breaks the ASCII art. Only these non-ASCII glyphs were verified to exist in
# Consolas with the normal advance width (tests/test_classic2_face.py re-checks).
FRAME_W = 7
ALLOWED_NON_ASCII = frozenset("ω¬•♥˘°·")

FRAMES = {
    "CHILL": [
        " /\\_/\\\n( ^ω^ )\n > ^ <",
        " /\\_/\\\n( -ω- )\n > ^ <",
        " /\\_/\\\n( ^ω^ )\n > ^ <",
    ],
    "UNKNOWN": [
        " /\\_/\\\n( •ω• )\n > ^ <",
        " /\\_/\\\n( -ω- )\n > ^ <",
    ],
    "WORKING": [
        " /\\_/\\\n( -ω- )\n > ^ <",
        " /\\_/\\\n( ¬_¬ )\n > ^ <",
    ],
    "VIDEO": [
        # Not "▷": it is not in Consolas and shifts the paw line.
        " /\\_/\\\n( o.o )\n > ^ <",
        " /\\_/\\\n( -.- )\n > ^ <",
    ],
    "HEAVY_CPU": [
        " /\\_/\\\n( O_O )\n > ^ <",
        " /\\_/\\\n( O_o )\n > ^ <",
        " /\\_/\\\n( o_O )\n > ^ <",
    ],
    "GPU_BUSY": [
        " /\\_/\\\n( o_O )\n > ^ <",
        " /\\_/\\\n( O_o )\n > ^ <",
        " /\\_/\\\n( O_O )\n > ^ <",
    ],
    "HEAVY_LOAD": [
        " /\\_/\\\n( @_@ )\n > ! <",
        " /\\_/\\\n( O_O )\n > ! <",
    ],
    "THERMAL_PANIC": [
        " /\\_/\\\n( x_x )\n > ! <",
        " /\\_/\\\n( X_X )\n > ! <",
    ],
    "WARM": [
        " /\\_/\\\n( ~ω~ )\n > ^ <",
        " /\\_/\\\n( -~- )\n > ^ <",
    ],
    "HOT": [
        " /\\_/\\\n( >_< )\n > ! <",
        " /\\_/\\\n( >o< )\n > ! <",
    ],
    "COOLING": [
        " /\\_/\\\n( =ω= )\n > ^ <",
        " /\\_/\\\n( -ω- )\n > ^ <",
    ],
    "RELIEF": [
        " /\\_/\\\n( -ω- )\n > ^ <",
        " /\\_/\\\n( =ω= )\n > ^ <",
    ],
    "RECOVERING": [
        " /\\_/\\\n( =ω= )\n > ^ <",
        " /\\_/\\\n( -ω- )\n > ^ <",
    ],
    "CHARGING": [
        " /\\_/\\\n( ^ω^ )\n > ^ <",
        " /\\_/\\\n( -ω- )\n > ^ <",
    ],
    "LOW_BATTERY": [
        " /\\_/\\\n( ;_; )\n > _ <",
        " /\\_/\\\n( ;~; )\n > _ <",
    ],
    "SLEEP": [
        " /\\_/\\\n( -.- )\n > ^ <",
        " /\\_/\\\n( -.- )\n > ^ <",
    ],
    "PETTING": [
        # Not "‿": it is not in Consolas.
        " /\\_/\\\n( ˘ω˘ )\n > ♥ <",
        " /\\_/\\\n( ^ω^ )\n > ♥ <",
    ],
    "ANNOYED": [
        " /\\_/\\\n( -_- )\n > ^ <",
        " /\\_/\\\n( ¬_¬ )\n > ^ <",   # not "-.-": that is the sleeping face
    ],
    "THINKING": [
        " /\\_/\\\n( •ω• )\n > ? <",
        " /\\_/\\\n( -ω- )\n > ? <",
    ],
}

# Calm states get idle life (blinks, glances, tail, ears, yawns). Alarm states keep
# their own cycling frames so warnings stay unmistakable.
CALM = {"CHILL", "UNKNOWN", "WORKING", "VIDEO", "WARM", "COOLING", "RELIEF", "RECOVERING", "CHARGING"}

# Occasional cynical/sleepy faces per calm state (the 3 characters inside "( … )").
MOODS = {
    "CHILL": ("¬ω¬", "-ω-"),
    "UNKNOWN": ("¬ω¬",),
    "WORKING": ("¬_¬", "-_-"),
    "VIDEO": ("¬.¬",),
    "WARM": ("-ω-",),
    "COOLING": ("-ω-",),
    "RELIEF": ("˘ω˘",),
    "RECOVERING": ("-ω-",),
    "CHARGING": ("˘ω˘", "-ω-"),
}

EARS = " /\\_/\\ "
EARS_FLICK_R = " /\\_/| "
EARS_FLICK_L = " |\\_/\\ "
PAWS = " > ^ < "
PAWS_STRETCH = "<  ^  >"
TAIL_A = " > ^ <~"
TAIL_B = " > ^ <_"
EARS_UP = " |\\_/| "
# Picked up by the scruff: skin pinched up between the ears, feet and tail hanging.
EARS_SCRUFF = " /\\^/\\ "
PAWS_DANGLE = "  u|u  "
WALK_B = " < ^ > "   # alternates with PAWS while walking

# Short reactions to things that happen (events, being put down). Seconds.
REACTIONS = {"eat": 1.6, "startle": 1.0, "peek": 2.0, "groom": 1.6, "greet": 1.2,
             "stretch": 2.0, "yawn": 2.0}

TICK_S = 1.4  # cadence of the per-state frame cycle

# Large cat: the small 3-line cat (ears, face, paws) is set into a 5 x 11 body, so
# every expression, blink, glance and reaction works unchanged in both sizes.
LARGE_W = 11
LARGE_H = 5
POSES = ("sit", "loaf", "lie")
_BLANK_L = " " * LARGE_W


def _shift(line: str, k: int) -> str:
    """Move a line k columns right (negative: left), keeping its width."""
    if k > 0:
        return (" " * k + line)[:len(line)]
    if k < 0:
        return line[-k:] + " " * -k
    return line


def _mirror(line: str) -> str:
    swap = str.maketrans("/\\()<>`'", "\\/)(><'`")
    return line[::-1].translate(swap)


# 식빵: back dome (exhale / inhale), bread-knife cuts appear one by one while it bakes.
LOAF_BACK = (".-'     '-.", "_.-'   '-._")
LOAF_CUT_COLS = (4, 6, 2, 8)
LOAF_BAKE_S = 60.0          # one more cut per minute in the loaf pose
LOAF_BREATH_S = 4.0         # one breath; the back rises for the first 40 %


def enlarge(rows: list[str], pose: str, loaf: Optional[tuple[bool, bool, int]] = None) -> list[str]:
    """Set the small cat (ears, face, paws) into a LARGE_H x LARGE_W body.

    sit : round cat, front paws together in front of the belly (alarms, petting)
    loaf: 식빵, a round loaf with the tail wrapped along the side.
          loaf = (inhale, paws_out, cuts); None draws the resting loaf with paw tips.
    lie : lying on its side, head left, a two-row round body and tail to the right (default)
    walk_left / walk_right: side view with a round back rising from the ears; the legs
          step '/|' <-> '|\' (paws == WALK_B on odd steps); walking right mirrors the body
    """
    ears, face, paws = rows
    chest = paws[3] if len(paws) > 3 else " "
    if chest in "^ _":
        chest = " "
    tail = paws[6] if len(paws) > 6 and paws[6] in "~_" else ""
    if pose.startswith("walk_"):
        legs = "  |\\   |\\  " if paws == WALK_B else "  /|   /|  "
        back, belly = "  `.", " (       )" + (tail or "~")
        if pose == "walk_right":
            # Head leads to the right: mirror the body, keep the face readable.
            return [_BLANK_L, "  __" + ears, _mirror(back) + face, _mirror(belly), _mirror(legs)]
        return [_BLANK_L, ears + "__  ", face + back, belly, legs]
    if ears == EARS_SCRUFF:
        # Held by the scruff from above; the limp body swings like a pendulum
        # (small paws row shifted by `sway`, the feet swing twice as far as the belly).
        # No hand is drawn: the mouse pointer is the hand (a '|' there read as a rope).
        sway = paws.find("u") - PAWS_DANGLE.find("u")
        return [_BLANK_L, "  " + ears + "  ", "  " + face + "  ",
                _shift("   (   )   ", sway), _shift("    u|u    ", 2 * sway)]
    if paws == PAWS_STRETCH:
        return ["  " + ears + "  ", "  " + face + "  ", "<-/  ^  \\->", " (       ) ", "  `-u-u-'  "]
    if pose == "loaf":
        inhale, paws_out, cuts = loaf or (False, True, 0)
        back = LOAF_BACK[1 if inhale else 0]
        back = back[:5] + chest + back[6:]
        body = list("(         )")
        for col in LOAF_CUT_COLS[:max(0, min(cuts, len(LOAF_CUT_COLS)))]:
            body[col] = "/"
        base = (" `-u---u-'" if paws_out else " `-------'") + (tail or "~")
        return ["  " + ears + "  ", "  " + face + "  ", back, "".join(body), base]
    if pose == "lie":
        return [_BLANK_L, ears + "    ", face + "--. ", " (        )", "  `u-u---'" + (tail or "~")]
    front = paws[1:6] if len(paws) >= 6 else "> ^ <"
    return ["  " + ears + "  ", "  " + face + "  ", "  /" + front + "\\  ", " (       )" + (tail or "~"),
            "  `-u-u-'  "]


def _lines(frame: str) -> list[str]:
    rows = [r.ljust(FRAME_W) for r in frame.split("\n")]
    while len(rows) < 3:
        rows.append(" " * FRAME_W)
    return rows[:3]


def _face(inner: str, look: str = "center") -> str:
    if look == "left":
        return "(" + inner + "  )"
    if look == "right":
        return "(  " + inner + ")"
    return "( " + inner + " )"


class PetStateMachine:
    """Visual state only.

    Sentence selection lives in VoiceEngine. The face is derived from the same
    PetContext used by accessories and VoiceEngine; this class only adds motion.
    """

    def __init__(self, seed: Optional[int] = None) -> None:
        self.state = "CHILL"
        self.rng = random.Random(seed)
        self.night = False
        self._state_since = 0.0
        self._action: Optional[tuple[str, float, float, str]] = None  # name, start, end, arg
        self._next_action_at: Optional[float] = None
        self._next_blink_at: Optional[float] = None
        self._blink_until = 0.0
        self._talk_until = 0.0
        self._reaction: Optional[tuple[str, float, float]] = None
        self.held = False
        self._held_since: Optional[float] = None
        self._sway = 0              # -1 / +1: body trails the hand while carried
        self._sway_until = 0.0
        self._gaze: Optional[str] = None
        self._chase_until = 0.0
        self.affection = "NORMAL"  # LOW / NORMAL / HIGH from affection.Affection
        self.size = "small"        # "small" (3 x 7) or "large" (5 x 11)
        self.walking: Optional[str] = None  # "left" / "right" while strolling, None otherwise
        self.pose = "lie"          # the big cat mostly lies on its side
        self._pose_until = 0.0
        self._loaf_since = 0.0
        self._paw_rng = random.Random(None if seed is None else seed + 7)  # keeps pose draws unchanged
        self._paws_out_until = 0.0
        self._next_paws_at: Optional[float] = None

    def update_context(self, ctx: PetContext) -> str:
        new = ctx.face_state if ctx.face_state in FRAMES else "UNKNOWN"
        if new != self.state:
            self._action = None
            self._next_action_at = None
            self._state_since = ctx.now_mono
        self.state = new
        self.night = bool({"TIME_NIGHT", "TIME_DAWN"} & set(ctx.facts))
        return self.state

    def react(self, name: str, now: float) -> None:
        """Play a short reaction (see REACTIONS); a newer one replaces an older one."""
        if name in REACTIONS:
            self._reaction = (name, now, now + REACTIONS[name])

    def set_gaze(self, direction: Optional[str], now: float, chase: bool = False) -> None:
        """Where the pointer is ("left"/"right"/None). chase = pointer shaken fast nearby."""
        self._gaze = direction if direction in ("left", "right") else None
        if chase:
            self._chase_until = now + 1.0

    def carried(self, dx: float, now: float) -> None:
        """The hand moved dx px while holding the cat: the dangling body trails behind."""
        if abs(dx) >= 3:
            self._sway = -1 if dx > 0 else 1
            self._sway_until = now + 0.25

    def _held_frame(self, now: float) -> str:
        if self._held_since is None:
            self._held_since = now
        held_for = now - self._held_since
        # A moment of surprise, then the limp resignation of a scruffed cat.
        inner = "°ω°" if held_for < 0.6 else ("-_-" if held_for < 4.0 else "¬_¬")
        sway = self._sway if now < self._sway_until else 0
        return "\n".join((EARS_SCRUFF, _face(inner), _shift(PAWS_DANGLE, sway)))

    def talk(self, now: float, duration: float = 1.2) -> None:
        """A new speech line appeared: move the mouth for a moment."""
        self._talk_until = max(self._talk_until, now + duration)

    # ------------------------------------------------------------------ frames
    def frame(self, tick: int = 0, override: Optional[str] = None, now: Optional[float] = None) -> str:
        """Small: 3 lines x FRAME_W. Large: LARGE_H lines x LARGE_W.

        Without `now` the result is deterministic (used by layout tests).
        """
        small = self._small_frame(tick, override, now)
        if self.size != "large":
            return small
        pose = "sit" if now is None else self._choose_pose(override, now)
        if now is not None and self.walking and not override:
            pose = "walk_" + self.walking
        loaf = self._loaf_detail(now) if pose == "loaf" and now is not None else None
        return "\n".join(enlarge(small.split("\n"), pose, loaf))

    def _loaf_detail(self, now: float) -> tuple[bool, bool, int]:
        """(inhale, paws_out, cuts): slow breathing, paw tips that peek out now and then,
        and one bread cut per minute of baking."""
        inhale = (now % LOAF_BREATH_S) < LOAF_BREATH_S * 0.4
        if self._next_paws_at is None:
            self._next_paws_at = now + self._paw_rng.uniform(5, 20)
        elif now >= self._next_paws_at:
            self._paws_out_until = now + self._paw_rng.uniform(2.0, 4.0)
            self._next_paws_at = now + self._paw_rng.uniform(15, 40)
        cuts = int(max(0.0, now - self._loaf_since) // LOAF_BAKE_S)
        return inhale, now < self._paws_out_until, cuts

    def _choose_pose(self, override: Optional[str], now: float) -> str:
        busy = override or self.held or self.walking or (self._reaction and now < self._reaction[2])
        if self.state == "SLEEP" and not busy:
            return "lie"
        if busy or self.state not in CALM:
            return "sit"
        if now >= self._pose_until:
            calm_for = now - self._state_since
            if calm_for < 30 and self._state_since > 0:
                # Just calmed down after something: stay sitting up a moment.
                self.pose = "sit"
                self._pose_until = self._state_since + 30
            else:
                lie = 0.65 if self.night else 0.5
                loaf = 0.35
                previous = self.pose
                self.pose = self.rng.choices(POSES, weights=(1 - loaf - lie, loaf, lie), k=1)[0]
                self._pose_until = now + self.rng.uniform(90, 240)
                if self.pose == "loaf" and previous != "loaf":
                    self._loaf_since = now  # a fresh loaf goes into the oven
        return self.pose

    def _small_frame(self, tick: int, override: Optional[str], now: Optional[float]) -> str:
        state = override or self.state
        frames = FRAMES.get(state, FRAMES["UNKNOWN"])
        if now is None:
            return "\n".join(_lines(frames[tick % len(frames)] if state not in CALM or override else frames[0]))
        if not self.held:
            self._held_since = None
        if not override:
            if self.held:
                return self._held_frame(now)
            if self.walking:
                inner = "-ω-" if now < self._blink_until else "^ω^"
                return "\n".join((EARS, _face(inner, self.walking), (PAWS, WALK_B)[int(now / 0.25) % 2]))
            if self._reaction and now < self._reaction[2]:
                return "\n".join(self._reaction_frame(frames, now))
            self._reaction = None
        if override or state not in CALM:
            rows = _lines(frames[int(now / TICK_S) % len(frames)])
            if state != "SLEEP":
                rows[1] = self._talking(rows[1], now)
            return "\n".join(rows)
        return "\n".join(self._calm(state, frames, now))

    def _reaction_frame(self, frames: list[str], now: float) -> list[str]:
        name, start, _end = self._reaction
        t = now - start
        ears, face, paws = _lines(frames[0])
        inner = face[2:5]
        if name == "eat":
            inner = ("^o^", "^ω^")[int(t / 0.2) % 2]
        elif name == "startle":
            ears, inner = EARS_UP, "°o°"
        elif name == "peek":
            return [ears, _face("•ω•", "right"), paws]
        elif name == "groom":
            ears = (EARS_FLICK_L, EARS, EARS_FLICK_R, EARS)[int(t / 0.4) % 4]
            inner = "˘ω˘"
        elif name == "greet":
            ears, inner = (EARS_FLICK_R if int(t / 0.3) % 2 else EARS), "^ω^"
        elif name == "yawn":
            inner = "-o-" if t < 0.6 else (">O<" if t < 1.4 else "-ω-")
        elif name == "stretch":
            if t < 1.5:
                inner, paws = ">ω<", PAWS_STRETCH
            else:
                inner = "-ω-"
        return [ears, _face(inner), paws]

    def _talking(self, face: str, now: float) -> str:
        if now >= self._talk_until or len(face) != FRAME_W:
            return face
        mouth_at = face.find("ω")
        if mouth_at < 0:
            # "_" mouths may open too, unless the eyes are o/O/x/X/@ ("( OoO )", "( xox )" read as letters).
            mouth_at = face.find("_")
            if mouth_at < 1 or face[mouth_at - 1] in "oOxX@" or face[mouth_at + 1] in "oOxX@":
                return face
        if int(now / 0.18) % 2 == 0:
            return face[:mouth_at] + "o" + face[mouth_at + 1:]
        return face

    def _schedule(self, now: float) -> None:
        if self._next_blink_at is None:
            self._next_blink_at = now + self.rng.uniform(2.0, 5.0)
        if now >= self._next_blink_at:
            self._blink_until = now + 0.15
            self._next_blink_at = now + self.rng.uniform(3.0, 7.0)
        if self._action and now >= self._action[2]:
            self._action = None
        if self._next_action_at is None:
            self._next_action_at = now + self.rng.uniform(6.0, 14.0)
        if self._action is None and now >= self._next_action_at:
            calm_for = now - self._state_since
            mood_w = {"LOW": 4.0, "HIGH": 2.0}.get(self.affection, 2.0)
            choices = [("look", 2.0), ("tail", 2.0), ("ear", 1.5), ("mood", mood_w),
                       ("yawn", 2.0 if self.night else 0.5),
                       ("stretch", (1.0 if self.night else 0.3) if calm_for >= 120 else 0.0)]
            names, weights = zip(*[(n, w) for n, w in choices if w > 0])
            name = self.rng.choices(names, weights=weights, k=1)[0]
            length = {"look": 1.4, "tail": 1.4, "ear": 0.3, "mood": 2.6, "yawn": 2.0, "stretch": 2.0}[name]
            arg = ""
            if name == "look":
                arg = self.rng.choice(("left", "right"))
            elif name == "ear":
                arg = self.rng.choice(("left", "right"))
            elif name == "mood":
                arg = self.rng.choice(MOODS.get(self.state, ("-ω-",)))
                if self.affection == "HIGH" and self.rng.random() < 0.6:
                    arg = "˘ω˘"   # content
                elif self.affection == "LOW" and self.rng.random() < 0.6:
                    arg = "¬_¬" if self.state == "WORKING" else "¬ω¬"  # sulking
            self._action = (name, now, now + length, arg)
            self._next_action_at = now + length + self.rng.uniform(10.0, 25.0)

    def _calm(self, state: str, frames: list[str], now: float) -> list[str]:
        self._schedule(now)
        base = _lines(frames[0])
        ears, face, paws = base
        inner, look = face[2:5], "center"
        if self._action:
            name, start, _end, arg = self._action
            t = now - start
            if name == "look":
                look = arg
            elif name == "tail":
                paws = (TAIL_A, TAIL_B)[int(t / 0.35) % 2]
            elif name == "ear":
                ears = EARS_FLICK_R if arg == "right" else EARS_FLICK_L
            elif name == "mood":
                inner = arg
            elif name == "yawn":
                inner = "-o-" if t < 0.6 else (">O<" if t < 1.4 else "-ω-")
            elif name == "stretch":
                if t < 1.5:
                    inner, paws = ">ω<", PAWS_STRETCH
                else:
                    inner = "-ω-"
        if self._gaze and not (self._action and self._action[0] == "look"):
            look = self._gaze
        if now < self._chase_until:
            inner = "OωO"
        yawning = self._action is not None and self._action[0] == "yawn"
        if now < self._blink_until and inner[0] not in "->" and not yawning:
            inner = "-" + inner[1] + "-"
        face = _face(inner, look)
        if not yawning:
            face = self._talking(face, now)
        return [ears, face, paws]


def all_glyphs() -> set[str]:
    """Every character any frame or motion can draw (for glyph-coverage tests)."""
    chars = set()
    for frames in FRAMES.values():
        for f in frames:
            chars |= set(f.replace("\n", ""))
    for moods in MOODS.values():
        for m in moods:
            chars |= set(m)
    for s in (EARS, EARS_FLICK_R, EARS_FLICK_L, EARS_UP, EARS_SCRUFF, PAWS, PAWS_STRETCH, PAWS_DANGLE, WALK_B,
              TAIL_A, TAIL_B, "-o->O<>ω<o", "°ω°°o°•ω•˘ω˘^o^OωO-_-¬_¬"):
        chars |= set(s)
    for pose in POSES + ("walk_left", "walk_right"):
        for paws in (PAWS, PAWS_STRETCH, PAWS_DANGLE, WALK_B, TAIL_A, TAIL_B, " > ♥ < ", " > ! < ", " > ? < "):
            for line in enlarge([EARS, "( ^ω^ )", paws], pose):
                chars |= set(line)
    for inhale in (False, True):
        for paws_out in (False, True):
            for cuts in range(len(LOAF_CUT_COLS) + 1):
                for line in enlarge([EARS, "( ^ω^ )", " > ♥ <~"], "loaf", (inhale, paws_out, cuts)):
                    chars |= set(line)
    for sway in (-1, 0, 1):
        for line in enlarge([EARS_SCRUFF, "( -_- )", _shift(PAWS_DANGLE, sway)], "sit"):
            chars |= set(line)
    return chars
