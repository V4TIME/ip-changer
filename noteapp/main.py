"""Pixel Note - a small, offline, blocky note app.

Design tokens are declared once at the top. The layout is a strict vertical
grid of flat panels with hairline borders: no gradients, no glows, no rounded
"card" clichés. Typography is a bundled monospace face, not the platform
default, so the character grid stays even on every device.
"""

from __future__ import annotations

import time
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Line, Rectangle
from kivy.metrics import dp
from kivy.properties import ListProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.utils import get_color_from_hex

from store import MAX_TEXT, Note, NoteStore

# --- Design tokens -----------------------------------------------------------

BG = get_color_from_hex("#0D0F12")
PANEL = get_color_from_hex("#15181D")
BORDER = get_color_from_hex("#2B3038")
TEXT = get_color_from_hex("#E7E7E1")
MUTED = get_color_from_hex("#8A9099")
ACCENT = get_color_from_hex("#C9A96A")
OK = get_color_from_hex("#7FA37F")
DANGER = get_color_from_hex("#C8796F")

HERE = Path(__file__).resolve().parent
FONT_PATH = HERE / "assets" / "DejaVuSansMono.ttf"
FONT = str(FONT_PATH) if FONT_PATH.exists() else ""

Window.clearcolor = BG
Window.softinput_mode = "below_target"


# --- Widgets -----------------------------------------------------------------


class Block(BoxLayout):
    """Flat panel: solid fill plus a one-pixel hairline border."""

    fill_color = ListProperty(BG)
    line_color = ListProperty(BORDER)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            self._fill = Color(*self.fill_color)
            self._rect = Rectangle(pos=self.pos, size=self.size)
            self._stroke = Color(*self.line_color)
            self._border = Line(rectangle=(*self.pos, *self.size), width=dp(1))
        self.bind(
            pos=self._sync_geometry,
            size=self._sync_geometry,
            fill_color=self._sync_fill,
            line_color=self._sync_line,
        )

    def _sync_geometry(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._border.rectangle = (self.x, self.y, self.width, self.height)

    def _sync_fill(self, *_):
        self._fill.rgba = self.fill_color

    def _sync_line(self, *_):
        self._stroke.rgba = self.line_color


class WrapLabel(Label):
    """Label that wraps to the panel width and reports its real height."""

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("halign", "left")
        kwargs.setdefault("valign", "top")
        kwargs.setdefault("color", TEXT)
        super().__init__(**kwargs)
        if FONT:
            self.font_name = FONT
        self.bind(width=self._rewrap, texture_size=self._resize)
        self._rewrap()

    def _rewrap(self, *_):
        self.text_size = (self.width, None)

    def _resize(self, *_):
        self.height = self.texture_size[1]


def flat_button(text: str, color, text_color=TEXT, **kwargs) -> Button:
    """Solid, square button with no platform shading."""

    return Button(
        text=text,
        font_name=FONT or None,
        background_normal="",
        background_down="",
        background_color=color,
        color=text_color,
        **kwargs,
    )


class NoteRow(Block):
    """One saved note: timestamp, text, and a delete action."""

    def __init__(self, note: Note, on_delete, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("padding", (dp(10), dp(8)))
        kwargs.setdefault("spacing", dp(4))
        kwargs.setdefault("fill_color", PANEL)
        super().__init__(**kwargs)

        header = BoxLayout(size_hint_y=None, height=dp(20), spacing=dp(8))
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(note.created)) if note.created else "IMPORTED"
        meta = Label(
            text=stamp,
            font_name=FONT or None,
            font_size=dp(10),
            color=MUTED,
            halign="left",
            valign="middle",
            size_hint_x=1,
        )
        meta.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        remove = flat_button(
            "DELETE", DANGER, text_color=BG,
            size_hint=(None, None), size=(dp(72), dp(22)), font_size=dp(9),
        )
        remove.bind(on_release=lambda *_: on_delete(note.id))
        header.add_widget(meta)
        header.add_widget(remove)

        body = WrapLabel(text=note.text, font_size=dp(14), padding=(0, dp(4)))
        body.bind(width=lambda w, *_: setattr(w, "text_size", (w.width, None)))

        self.add_widget(header)
        self.add_widget(body)
        self.bind(minimum_height=self.setter("height"))


class PixelNoteApp(App):
    title = "Pixel Note"

    def build(self):
        self.store = NoteStore(self.user_data_dir)
        self.notes: list[Note] = self.store.load()
        self._status_event = None

        root = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        with root.canvas.before:
            Color(*BG)
            self._root_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=self._resize_bg, size=self._resize_bg)

        root.add_widget(self._build_header())
        root.add_widget(self._build_composer())
        root.add_widget(self._build_list())
        root.add_widget(self._build_footer())
        self._refresh()
        return root

    # -- layout ----------------------------------------------------------

    def _resize_bg(self, widget, *_):
        self._root_rect.pos = widget.pos
        self._root_rect.size = widget.size

    def _build_header(self) -> Block:
        header = Block(size_hint_y=None, height=dp(58), padding=(dp(14), dp(10)))
        title = BoxLayout(orientation="vertical", spacing=dp(1))
        title.add_widget(Label(
            text="PIXEL NOTE", font_name=FONT or None, font_size=dp(17), bold=True,
            color=TEXT, halign="left", valign="bottom",
        ))
        title.add_widget(Label(
            text="LOCAL  ·  OFFLINE  ·  NO ACCOUNT", font_name=FONT or None,
            font_size=dp(9), color=MUTED, halign="left", valign="top",
        ))
        for child in title.children:
            child.bind(size=lambda w, *_: setattr(w, "text_size", w.size))

        self.count_label = Label(
            text="00", font_name=FONT or None, font_size=dp(20), bold=True,
            color=ACCENT, size_hint_x=None, width=dp(52), halign="right", valign="middle",
        )
        self.count_label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        header.add_widget(title)
        header.add_widget(self.count_label)
        return header

    def _build_composer(self) -> Block:
        composer = Block(orientation="vertical", size_hint_y=None, height=dp(196),
                         padding=(dp(12), dp(10)), spacing=dp(6))
        composer.add_widget(self._section_label("NEW ENTRY"))

        self.input = TextInput(
            multiline=True,
            hint_text="Type a note. It stays on this device.",
            font_name=FONT or None,
            font_size=dp(14),
            background_color=PANEL,
            foreground_color=TEXT,
            cursor_color=ACCENT,
            hint_text_color=(*MUTED[:3], 0.8),
            padding=(dp(10), dp(8)),
        )
        self.input.bind(text=self._on_typed)
        composer.add_widget(self.input)

        actions = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        self.counter = Label(
            text=f"0 / {MAX_TEXT}", font_name=FONT or None, font_size=dp(10),
            color=MUTED, halign="left", valign="middle",
        )
        self.counter.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        save = flat_button("SAVE ENTRY", ACCENT, size_hint=(None, 1), width=dp(128), font_size=dp(12))
        save.bind(on_release=self._save_note)
        actions.add_widget(self.counter)
        actions.add_widget(save)
        composer.add_widget(actions)
        return composer

    def _build_list(self) -> Block:
        panel = Block(orientation="vertical", padding=(dp(0), dp(0)), spacing=dp(6))
        header = BoxLayout(size_hint_y=None, height=dp(30), padding=(dp(12), 0))
        header.add_widget(self._section_label("SAVED"))
        clear = flat_button("CLEAR ALL", BORDER, size_hint=(None, 1), width=dp(96), font_size=dp(10))
        clear.bind(on_release=self._confirm_clear)
        header.add_widget(clear)
        panel.add_widget(header)
        panel.add_widget(Block(size_hint_y=None, height=dp(1), fill_color=BORDER, line_color=BORDER))

        self.scroller = ScrollView(bar_width=dp(3), bar_color=(*MUTED[:3], 0.5),
                                   bar_inactive_color=(*BORDER[:3], 0.5))
        self.grid = GridLayout(cols=1, spacing=dp(6), size_hint_y=None, padding=(dp(10), dp(6)))
        self.grid.bind(minimum_height=self.grid.setter("height"))
        self.scroller.add_widget(self.grid)
        panel.add_widget(self.scroller)
        return panel

    def _build_footer(self) -> Label:
        self.status = Label(
            text="", font_name=FONT or None, font_size=dp(10), color=MUTED,
            size_hint_y=None, height=dp(20), halign="left", valign="middle",
        )
        self.status.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        return self.status

    @staticmethod
    def _section_label(text: str) -> Label:
        label = Label(
            text=text, font_name=FONT or None, font_size=dp(10), bold=True,
            color=MUTED, size_hint_y=None, height=dp(18), halign="left", valign="middle",
        )
        label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        return label

    # -- behaviour -------------------------------------------------------

    def _on_typed(self, _widget, value: str):
        self.counter.text = f"{min(len(value), MAX_TEXT)} / {MAX_TEXT}"

    def _set_status(self, text: str, color=MUTED):
        self.status.text = text
        self.status.color = color
        if self._status_event is not None:
            self._status_event.cancel()
        self._status_event = Clock.schedule_once(lambda *_: self._clear_status(), 4)

    def _clear_status(self):
        self.status.text = ""
        self._status_event = None

    def _save_note(self, *_):
        try:
            self.notes = self.store.add(self.input.text)
        except ValueError as exc:
            self._set_status(str(exc).upper(), DANGER)
            return
        self.input.text = ""
        self._set_status("SAVED", OK)
        self._refresh()

    def _delete_note(self, note_id: str):
        self.notes = self.store.delete(note_id)
        self._set_status("DELETED", OK)
        self._refresh()

    def _confirm_clear(self, *_):
        if not self.notes:
            self._set_status("NOTHING TO CLEAR", MUTED)
            return
        content = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
        content.add_widget(Label(
            text=f"Delete all {len(self.notes)} notes?\nThis cannot be undone.",
            font_name=FONT or None, font_size=dp(13), color=TEXT,
        ))
        actions = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        cancel = flat_button("CANCEL", BORDER)
        confirm = flat_button("DELETE ALL", DANGER)
        actions.add_widget(cancel)
        actions.add_widget(confirm)
        content.add_widget(actions)

        popup = Popup(title="CLEAR ALL", title_font=FONT or "Roboto", content=content,
                      size_hint=(0.85, None), height=dp(190), background_color=PANEL,
                      separator_color=ACCENT)

        def do_clear(*_):
            self.notes = self.store.clear()
            popup.dismiss()
            self._set_status("ALL NOTES DELETED", OK)
            self._refresh()

        cancel.bind(on_release=lambda *_: popup.dismiss())
        confirm.bind(on_release=do_clear)
        popup.open()

    def _refresh(self):
        self.grid.clear_widgets()
        self.count_label.text = f"{len(self.notes):02d}"
        if not self.notes:
            empty = Label(
                text="NO NOTES YET\nSAVE ONE ABOVE",
                font_name=FONT or None, font_size=dp(12), color=MUTED,
                halign="center", valign="middle", size_hint_y=None, height=dp(120),
            )
            empty.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
            self.grid.add_widget(empty)
            return
        for note in self.notes:
            row = NoteRow(note, self._delete_note)
            self.grid.add_widget(row)

    def on_pause(self):
        # Every mutation is already persisted; just make Android's pause safe.
        return True


if __name__ == "__main__":
    PixelNoteApp().run()
