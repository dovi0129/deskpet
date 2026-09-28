"""One-canvas renderer: speech bubble, the ASCII cat, and the card it sits on.

The card's top edge sits right under the baseline of the cat's paw line, so the
paws (and bowl/terminal accessories) touch the edge without any glyph crossing it.
Colour is used only for state: neutral by default, amber for warning, red for critical. The renderer knows nothing about sensors; DeskPet passes a
CardModel and the canvas is redrawn only when that model changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import tkinter as tk
import tkinter.font as tkfont
from typing import Optional

TRANSPARENT = "#010203"
WINDOW_BG = "#17171c"
CARD_BG = "#1b1b21"
CARD_EDGE = "#30303a"
TEXT = "#ececf1"
MUTED = "#9b9ba7"
TRACK = "#2d2d35"
FILL = "#c9c9d3"
WARN = "#e8a33d"
CRIT = "#ef5b52"
BUBBLE_BG = "#f1f1f4"
BUBBLE_FG = "#1b1b21"

LEVEL_COLOR = {"normal": TEXT, "muted": MUTED, "warn": WARN, "crit": CRIT}
BAR_COLOR = {"normal": FILL, "muted": FILL, "warn": WARN, "crit": CRIT}

Segment = tuple[str, str]  # (text, level)


@dataclass(frozen=True)
class Row:
    label: str
    segments: tuple[Segment, ...]
    fraction: Optional[float] = None  # 0..1 draws a bar; None means text-only row
    level: str = "normal"


@dataclass(frozen=True)
class CardModel:
    cat_text: str
    bubble: str = ""
    bubble_level: str = "normal"
    sleep_fx: str = ""
    expanded: bool = False
    summary: tuple[Segment, ...] = ()
    rows: tuple[Row, ...] = field(default_factory=tuple)


def cat_line_px(card: "CatCard") -> int:
    return card.fonts["cat"].metrics("linespace")


class CatCard:
    CAT_FONT = ("Consolas", 12, "bold")
    BUBBLE_FONT = ("Segoe UI", 9)
    LABEL_FONT = ("Segoe UI", 9)
    VALUE_FONT = ("Consolas", 9)
    SUMMARY_FONT = ("Segoe UI", 9)

    def __init__(self, parent: tk.Misc, *, width: int, scale: float, transparent: bool) -> None:
        self.scale = scale
        self.width = width
        self.transparent = transparent
        self.canvas = tk.Canvas(parent, width=width, height=10, highlightthickness=0, bd=0,
                                bg=TRANSPARENT if transparent else WINDOW_BG)
        self.fonts = {name: tkfont.Font(root=parent, font=spec) for name, spec in (
            ("cat", self.CAT_FONT), ("bubble", self.BUBBLE_FONT), ("label", self.LABEL_FONT),
            ("value", self.VALUE_FONT), ("summary", self.SUMMARY_FONT))}
        self._model: Optional[CardModel] = None
        self.cat_lines = 3
        self.height = 10
        self.cat_bbox = (0, 0, 0, 0)
        self.bubble_bbox: Optional[tuple[int, int, int, int]] = None
        self.card_bbox = (0, 0, 0, 0)

    # ---------------------------------------------------------------- geometry
    def px(self, v: float) -> int:
        return round(v * self.scale)

    def line(self, name: str) -> int:
        return self.fonts[name].metrics("linespace")

    def _bubble_height(self) -> int:
        return self.line("bubble") + self.px(10)

    def layout_height(self, expanded: bool, rows: int) -> int:
        """Pure function of mode and row count, so the window never 'breathes'."""
        pad, tail = self.px(6), self.px(6)
        y = pad + self._bubble_height() + tail + self.px(2)
        _, content = self._card_geometry(y)
        if expanded:
            content += rows * self._row_height() + self.px(4)
        else:
            content += self.line("summary") + self.px(4)
        return content + self.px(6) + self.px(2)

    def _card_geometry(self, cat_y: int) -> tuple[int, int]:
        """(card_top, content_y). Top = baseline of the 3rd cat line + 1 px."""
        cat_line = self.line("cat")
        card_top = cat_y + (self.cat_lines - 1) * cat_line + self.fonts["cat"].metrics("ascent") + self.px(1)
        return card_top, card_top + self.px(9)

    def _row_height(self) -> int:
        return max(self.line("label"), self.line("value")) + self.px(5)

    # ---------------------------------------------------------------- drawing
    def set_width(self, width: int, cat_lines: int) -> None:
        self.width = width
        self.cat_lines = cat_lines
        self.canvas.configure(width=width)
        self._model = None

    def set_transparent(self, transparent: bool) -> None:
        self.transparent = transparent
        self.canvas.configure(bg=TRANSPARENT if transparent else WINDOW_BG)
        self._model = None

    def render(self, model: CardModel) -> bool:
        """Redraw if the model changed. Returns True when the height changed."""
        if model == self._model:
            return False
        self._model = model
        c = self.canvas
        c.delete("all")
        w = self.width
        pad, tail = self.px(6), self.px(6)
        inset = self.px(8)

        # Bubble slot is always reserved; an empty bubble just draws nothing.
        y = pad
        bh = self._bubble_height()
        self.bubble_bbox = None
        if model.bubble:
            text = self._fit(model.bubble, "bubble", w - 2 * inset - self.px(24))
            tw = self.fonts["bubble"].measure(text)
            bw = tw + self.px(24)
            x0 = (w - bw) // 2
            outline = {"warn": WARN, "crit": CRIT}.get(model.bubble_level, BUBBLE_BG)
            self._round_rect(x0, y, x0 + bw, y + bh, self.px(8), fill=BUBBLE_BG, outline=outline,
                             width=max(1, self.px(1.5)), tags=("bubble",))
            cx = w // 2
            c.create_polygon(cx - self.px(5), y + bh - 1, cx + self.px(5), y + bh - 1, cx, y + bh + tail,
                             fill=BUBBLE_BG, outline=BUBBLE_BG, tags=("bubble",))
            c.create_text(w // 2, y + bh // 2, text=text, font=self.fonts["bubble"], fill=BUBBLE_FG,
                          tags=("bubble",))
            self.bubble_bbox = (x0, y, x0 + bw, y + bh + tail)
        y += bh + tail + self.px(2)

        cat_y = y
        card_top, content_y = self._card_geometry(cat_y)
        self.paw_baseline = card_top - self.px(1)
        rows = model.rows if model.expanded else ()
        if model.expanded:
            bottom = content_y + len(rows) * self._row_height() + self.px(4)
        else:
            bottom = content_y + self.line("summary") + self.px(4)
        bottom += self.px(6)
        self.card_bbox = (inset, card_top, w - inset, bottom)
        self._round_rect(inset, card_top, w - inset, bottom, self.px(10), fill=CARD_BG, outline=CARD_EDGE,
                         width=1, tags=("card",))

        # Cat drawn last; its paw line ends exactly on the card's top edge.
        c.create_text(w // 2, cat_y, text=model.cat_text, font=self.fonts["cat"], fill=TEXT, anchor="n",
                      justify="left", tags=("cat",))
        self.cat_bbox = c.bbox("cat") or (0, 0, 0, 0)
        if model.sleep_fx:
            c.create_text(w // 2 + self.px(38), cat_y - self.px(2), text=model.sleep_fx,
                          font=self.fonts["value"], fill=MUTED, anchor="s", tags=("sleep",))

        if model.expanded:
            self._draw_rows(rows, inset, w - inset, content_y)
        else:
            self._draw_segments(model.summary, w // 2, content_y, anchor="center")

        new_height = bottom + self.px(2)
        changed = new_height != self.height
        self.height = new_height
        c.configure(height=new_height)
        return changed

    def _draw_rows(self, rows, x0, x1, y) -> None:
        c = self.canvas
        rh = self._row_height()
        label_x = x0 + self.px(12)
        bar_x0 = x0 + self.px(50)
        value_x = x1 - self.px(12)
        bar_x1 = value_x - self.px(44)
        bar_h = max(3, self.px(5))
        for row in rows:
            mid = y + rh // 2
            c.create_text(label_x, mid, text=row.label, font=self.fonts["label"], fill=MUTED, anchor="w",
                          tags=("row",))
            if row.fraction is not None:
                f = max(0.0, min(1.0, row.fraction))
                c.create_rectangle(bar_x0, mid - bar_h // 2, bar_x1, mid + bar_h - bar_h // 2,
                                   fill=TRACK, outline="", tags=("row",))
                if f > 0:
                    fill_x = bar_x0 + max(2, round((bar_x1 - bar_x0) * f))
                    c.create_rectangle(bar_x0, mid - bar_h // 2, fill_x, mid + bar_h - bar_h // 2,
                                       fill=BAR_COLOR.get(row.level, FILL), outline="", tags=("row",))
                self._draw_segments(row.segments, value_x, mid - rh // 2 + self.px(2), anchor="e")
            else:
                self._draw_segments(row.segments, bar_x0, mid - rh // 2 + self.px(2), anchor="w")
            y += rh

    def _draw_segments(self, segments, x, y, *, anchor: str) -> None:
        """Draw coloured text runs on one line, aligned left/right/centre at x."""
        if not segments:
            return
        font = self.fonts["value"] if anchor != "center" else self.fonts["summary"]
        widths = [font.measure(t) for t, _ in segments]
        total = sum(widths)
        start = {"w": x, "e": x - total, "center": x - total / 2}[anchor]
        for (text, level), wd in zip(segments, widths):
            self.canvas.create_text(start, y, text=text, font=font, fill=LEVEL_COLOR.get(level, TEXT),
                                    anchor="nw", tags=("row",))
            start += wd

    def _fit(self, text: str, font: str, max_px: int) -> str:
        f = self.fonts[font]
        if f.measure(text) <= max_px:
            return text
        while text and f.measure(text + "…") > max_px:
            text = text[:-1]
        return text.rstrip() + "…"

    def _round_rect(self, x0, y0, x1, y1, r, **kw):
        r = max(1, min(r, (x1 - x0) // 2, (y1 - y0) // 2))
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
               x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return self.canvas.create_polygon(pts, smooth=True, **kw)
