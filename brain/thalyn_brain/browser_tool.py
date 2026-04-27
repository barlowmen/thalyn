"""Agent-facing browser tool spec.

Mirrors the shape :mod:`terminal_tool` settled on for v0.12: each
agent-callable surface gets a structured spec (``name``,
``description``, ``input_schema``) plus a Python entry that does the
work. SDK / MCP wiring is a separate concern that lands once the
tool-registration surface stabilises across providers.

We expose **five** tools rather than one. Combining them (e.g. a
single ``browser_action`` switch tool) was tempting, but the agent
SDK gets clearer plans and tighter input schemas with one tool per
verb, and the action log reads better.
"""

from __future__ import annotations

from typing import Any

from thalyn_brain.browser import BrowserManager

JsonValue = Any

NAVIGATE_TOOL = "browser_navigate"
GET_TEXT_TOOL = "browser_get_text"
CLICK_TOOL = "browser_click"
TYPE_TOOL = "browser_type"
SCREENSHOT_TOOL = "browser_screenshot"


def navigate_spec() -> dict[str, JsonValue]:
    return {
        "name": NAVIGATE_TOOL,
        "description": (
            "Navigate the user's browser to a URL. The browser is the "
            "real Chromium window the user can also click in directly. "
            "Returns when the navigation has been requested; the page "
            "may still be loading. Pair with browser_get_text or "
            "browser_screenshot to confirm content."
        ),
        "input_schema": {
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute URL to navigate to (http or https).",
                }
            },
            "additionalProperties": False,
        },
    }


def get_text_spec() -> dict[str, JsonValue]:
    return {
        "name": GET_TEXT_TOOL,
        "description": (
            "Read text content from the current page. With no "
            "selector, returns the full body innerText. With a CSS "
            "selector, returns the matched element's innerText (empty "
            "string if no match)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "Optional CSS selector to narrow the read.",
                }
            },
            "additionalProperties": False,
        },
    }


def click_spec() -> dict[str, JsonValue]:
    return {
        "name": CLICK_TOOL,
        "description": (
            "Click the element matching a CSS selector. Computes the "
            "element's centre via the browser's bounding rect and "
            "dispatches a left-button mouse press + release. Errors "
            "if the selector matches no element."
        ),
        "input_schema": {
            "type": "object",
            "required": ["selector"],
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector for the element to click.",
                }
            },
            "additionalProperties": False,
        },
    }


def type_spec() -> dict[str, JsonValue]:
    return {
        "name": TYPE_TOOL,
        "description": (
            "Focus an input/textarea matched by a CSS selector and "
            "type literal text into it. Does not press Enter; pair "
            "with browser_click on a submit control if needed."
        ),
        "input_schema": {
            "type": "object",
            "required": ["selector", "text"],
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector for the input element.",
                },
                "text": {
                    "type": "string",
                    "description": "Text to insert. May be empty.",
                },
            },
            "additionalProperties": False,
        },
    }


def screenshot_spec() -> dict[str, JsonValue]:
    return {
        "name": SCREENSHOT_TOOL,
        "description": (
            "Take a PNG screenshot of the current viewport. Returns base64-encoded image bytes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    }


def all_specs() -> list[dict[str, JsonValue]]:
    """All five browser tools' specs, in a stable order."""
    return [
        navigate_spec(),
        get_text_spec(),
        click_spec(),
        type_spec(),
        screenshot_spec(),
    ]


# Python entries — the SDK / MCP wiring layer wraps these. Each
# returns a wire-friendly dict the renderer / SDK can pass back to
# the model unmodified.


async def navigate(manager: BrowserManager, *, url: str) -> dict[str, JsonValue]:
    return (await manager.navigate(url)).to_wire()


async def get_text(manager: BrowserManager, *, selector: str | None = None) -> dict[str, JsonValue]:
    return (await manager.get_text(selector)).to_wire()


async def click(manager: BrowserManager, *, selector: str) -> dict[str, JsonValue]:
    return (await manager.click(selector)).to_wire()


async def type_text(manager: BrowserManager, *, selector: str, text: str) -> dict[str, JsonValue]:
    return (await manager.type_text(selector, text)).to_wire()


async def screenshot(manager: BrowserManager) -> dict[str, JsonValue]:
    return (await manager.screenshot()).to_wire()
