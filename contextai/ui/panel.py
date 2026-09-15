"""The floating Panel (PRD FR-13 – FR-16): a non-activating `NSPanel` that renders `ui.state` states.

Display only. It never decides what to show — `app.py` hands it a state and a `frame_for(size)` callback
(placement lives in `ui.placement`) — and it reports user intent through three callbacks. The Target App
keeps keyboard focus throughout (NFR-2): the window is a non-activating panel, and Esc / click-outside
are observed with event monitors instead of by becoming key.
"""

from __future__ import annotations

from typing import Callable

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSEvent,
    NSEventMaskKeyDown,
    NSEventMaskLeftMouseDown,
    NSEventMaskRightMouseDown,
    NSFloatingWindowLevel,
    NSFont,
    NSMakeRect,
    NSMakeSize,
    NSPanel,
    NSPasteboard,
    NSPasteboardTypeString,
    NSProgressIndicator,
    NSScrollView,
    NSStringDrawingUsesLineFragmentOrigin,
    NSTextField,
    NSTextView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSAttributedString, NSObject
from Quartz import CGColorCreateGenericGray

from .placement import Rect
from .state import Actions, Error, Loading, PanelState, Result

WIDTH = 380.0
PAD = 12.0
TITLE_H = 18.0
BUTTON_H = 28.0
BODY_MIN_H = 24.0
BODY_MAX_H = 260.0
ESCAPE_KEYCODE = 53

ACTION_LABELS = {"summarize": "Summarize", "translate": "Translate"}


class _FloatingPanel(NSPanel):
    """A panel that never becomes key: focus stays in the Target App even when a button is clicked (NFR-2).

    Found live on 2026-09-14: a plain non-activating NSPanel still took key status on a button click,
    which made this process the frontmost application, so the *next* capture read our own panel.
    """

    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


class _Target(NSObject):
    """Objective-C target for button actions; forwards to the Panel's Python callbacks."""

    def initWithPanel_(self, panel):
        self = objc.super(_Target, self).init()
        self.panel = panel
        return self

    @objc.IBAction
    def summarize_(self, sender):
        self.panel.on_action("summarize")

    @objc.IBAction
    def translate_(self, sender):
        self.panel.on_action("translate")

    @objc.IBAction
    def retry_(self, sender):
        self.panel.on_retry()

    @objc.IBAction
    def copy_(self, sender):
        self.panel.copy_result()


class Panel:
    def __init__(
        self,
        on_action: Callable[[str], None],
        on_retry: Callable[[], None],
        on_dismiss: Callable[[], None],
    ) -> None:
        self.on_action = on_action
        self.on_retry = on_retry
        self.on_dismiss = on_dismiss
        self.state: PanelState | None = None
        self._frame_for: Callable[[tuple[float, float]], Rect] | None = None
        self._monitors: list = []
        self._target = _Target.alloc().initWithPanel_(self)
        self._build()

    # --- public -----------------------------------------------------------------------------------

    def present(self, state: PanelState, frame_for: Callable[[tuple[float, float]], Rect]) -> None:
        """Show (or move) the panel for `state`; `frame_for` maps the panel's size to its frame."""
        self._frame_for = frame_for
        self._render(state, reposition=True)
        if not self.window.isVisible():
            self._install_monitors()
        self.window.orderFrontRegardless()

    def update(self, state: PanelState) -> None:
        """Re-render in place (loading → result/error). Keeps the top edge where it is."""
        self._render(state, reposition=False)

    def dismiss(self) -> None:
        self._remove_monitors()
        self.window.orderOut_(None)
        self.state = None

    @property
    def visible(self) -> bool:
        return bool(self.window.isVisible())

    def contains(self, point: tuple[float, float]) -> bool:
        """Whether a screen point (NSScreen coordinates) is over the panel — gestures there are ours."""
        frame = self.window.frame()
        return (
            frame.origin.x <= point[0] < frame.origin.x + frame.size.width
            and frame.origin.y <= point[1] < frame.origin.y + frame.size.height
        )

    def copy_result(self) -> None:
        """FR-16: the only path by which ContextAI writes the clipboard, and only on the user's click."""
        if isinstance(self.state, Result) and self.state.display_text:
            pasteboard = NSPasteboard.generalPasteboard()
            pasteboard.clearContents()
            pasteboard.setString_forType_(self.state.display_text, NSPasteboardTypeString)
            self.copy_button.setTitle_("Copied")

    # --- construction -----------------------------------------------------------------------------

    def _build(self) -> None:
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        self.window = _FloatingPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, 120), style, NSBackingStoreBuffered, False
        )
        self.window.setLevel_(NSFloatingWindowLevel)
        self.window.setHidesOnDeactivate_(False)
        self.window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        self.window.setHasShadow_(True)
        self.window.setBackgroundColor_(NSColor.windowBackgroundColor())
        self.window.setReleasedWhenClosed_(False)
        content = self.window.contentView()
        content.setWantsLayer_(True)
        content.layer().setCornerRadius_(10.0)
        content.layer().setBorderWidth_(1.0)
        content.layer().setBorderColor_(CGColorCreateGenericGray(0.5, 0.35))  # a CGColorRef PyObjC can hold

        self.title = NSTextField.labelWithString_("")
        self.title.setFont_(NSFont.systemFontOfSize_(11.0))
        self.title.setTextColor_(NSColor.secondaryLabelColor())
        self.title.setLineBreakMode_(4)  # NSLineBreakByTruncatingTail
        content.addSubview_(self.title)

        self.scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, BODY_MIN_H))
        self.scroll.setHasVerticalScroller_(True)
        self.scroll.setBorderType_(0)
        self.scroll.setDrawsBackground_(False)
        self.body = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH - 2 * PAD, BODY_MIN_H))
        self.body.setEditable_(False)
        self.body.setSelectable_(False)  # selecting would need key status; Copy is the extraction path
        self.body.setDrawsBackground_(False)
        self.body.setFont_(self._body_font())
        self.body.setTextContainerInset_(NSMakeSize(0, 0))
        self.body.setVerticallyResizable_(True)
        self.body.setHorizontallyResizable_(False)
        self.body.textContainer().setWidthTracksTextView_(True)
        self.scroll.setDocumentView_(self.body)
        content.addSubview_(self.scroll)

        self.spinner = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(0, 0, 16, 16))
        self.spinner.setStyle_(1)  # NSProgressIndicatorStyleSpinning
        self.spinner.setControlSize_(1)  # small
        self.spinner.setDisplayedWhenStopped_(False)
        content.addSubview_(self.spinner)

        self.summarize_button = self._button("Summarize", "summarize:")
        self.translate_button = self._button("Translate", "translate:")
        self.retry_button = self._button("Retry", "retry:")
        self.copy_button = self._button("Copy", "copy:")
        for button in (self.summarize_button, self.translate_button, self.retry_button, self.copy_button):
            content.addSubview_(button)

    def _button(self, title: str, selector: str) -> NSButton:
        button = NSButton.buttonWithTitle_target_action_(title, self._target, selector)
        button.setBezelStyle_(1)  # rounded
        button.setControlSize_(1)  # small
        button.setFont_(NSFont.systemFontOfSize_(11.0))
        return button

    @staticmethod
    def _body_font() -> NSFont:
        return NSFont.systemFontOfSize_(13.0)

    # --- rendering --------------------------------------------------------------------------------

    def _render(self, state: PanelState, *, reposition: bool) -> None:
        self.state = state
        title, body, buttons, loading = self._describe(state)
        self.title.setStringValue_(title)
        self.body.setString_(body)
        self.copy_button.setTitle_("Copy")

        body_h = self._body_height(body) if body else 0.0
        height = (
            PAD + TITLE_H + (PAD / 2 + body_h if body else 0) + (PAD / 2 + BUTTON_H if buttons or loading else 0) + PAD
        )

        current = self.window.frame()
        if reposition or not self.window.isVisible():
            frame = (
                self._frame_for((WIDTH, height))
                if self._frame_for
                else Rect(current.origin.x, current.origin.y, WIDTH, height)
            )
        else:
            # Keep the top edge fixed so a result growing under the user's eyes does not jump.
            top = current.origin.y + current.size.height
            frame = Rect(current.origin.x, top - height, WIDTH, height)
        self.window.setFrame_display_(NSMakeRect(frame.x, frame.y, frame.w, frame.h), True)

        y = height - PAD - TITLE_H
        self.title.setFrame_(NSMakeRect(PAD, y, WIDTH - 2 * PAD, TITLE_H))
        if body:
            y -= PAD / 2 + body_h
            self.scroll.setFrame_(NSMakeRect(PAD, y, WIDTH - 2 * PAD, body_h))
            self.scroll.setHidden_(False)
        else:
            self.scroll.setHidden_(True)

        y -= PAD / 2 + BUTTON_H
        x = PAD
        all_buttons = {
            "summarize": self.summarize_button,
            "translate": self.translate_button,
            "retry": self.retry_button,
            "copy": self.copy_button,
        }
        for name, button in all_buttons.items():
            if name in buttons:
                button.sizeToFit()
                width = max(button.frame().size.width, 84.0)
                button.setFrame_(NSMakeRect(x, y, width, BUTTON_H))
                button.setHidden_(False)
                x += width + PAD / 2
            else:
                button.setHidden_(True)
        if loading:
            self.spinner.setFrame_(NSMakeRect(x, y + (BUTTON_H - 16) / 2, 16, 16))
            self.spinner.startAnimation_(None)
        else:
            self.spinner.stopAnimation_(None)

    def _describe(self, state: PanelState) -> tuple[str, str, tuple[str, ...], bool]:
        """(title, body text, visible buttons, loading?) for a state."""
        if isinstance(state, Actions):
            tier = f" · tier {state.tier}" if state.tier else ""
            return f"{len(state.selection):,} characters from {state.app}{tier}", "", ("summarize", "translate"), False
        if isinstance(state, Loading):
            return f"{ACTION_LABELS.get(state.action, state.action)}…", "", (), True
        if isinstance(state, Result):
            return ACTION_LABELS.get(state.action, state.action), state.display_text, ("copy",), False
        if isinstance(state, Error):
            title = "ContextAI"
            return title, state.message, ("retry",) if state.retryable else (), False
        raise TypeError(f"unknown panel state {state!r}")

    def _body_height(self, text: str) -> float:
        attributed = NSAttributedString.alloc().initWithString_attributes_(text, {"NSFont": self._body_font()})
        rect = attributed.boundingRectWithSize_options_(
            NSMakeSize(WIDTH - 2 * PAD - 4, 100_000), NSStringDrawingUsesLineFragmentOrigin
        )
        return max(BODY_MIN_H, min(BODY_MAX_H, rect.size.height + 6))

    # --- dismissal (FR-14) ------------------------------------------------------------------------

    def _install_monitors(self) -> None:
        def on_mouse(event):
            point = NSEvent.mouseLocation()
            if not self.contains((float(point.x), float(point.y))):
                self.on_dismiss()
            return event

        def on_key(event):
            if event.keyCode() == ESCAPE_KEYCODE:
                self.on_dismiss()
                return None if event.window() is self.window else event
            return event

        mouse_mask = NSEventMaskLeftMouseDown | NSEventMaskRightMouseDown
        self._monitors = [
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mouse_mask, on_mouse),
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mouse_mask, on_mouse),
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, on_key),
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, on_key),
        ]

    def _remove_monitors(self) -> None:
        for monitor in self._monitors:
            if monitor is not None:
                NSEvent.removeMonitor_(monitor)
        self._monitors = []
