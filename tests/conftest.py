"""
Pytest configuration: inject stub modules for EDMC dependencies so that
load.py can be imported in a plain Python environment without EDMC or Tk
installed.
"""
from __future__ import annotations

import sys
import types


def _make_stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# ---------------------------------------------------------------------------
# tkinter – stub out the entire tkinter hierarchy so load.py can be imported
# without a display or Tk libraries.
# ---------------------------------------------------------------------------

class _TkVar:
    """Minimal stand-in for tk.IntVar / tk.StringVar."""
    def __init__(self, value=0):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _Widget:
    """Minimal stand-in for any Tk widget."""
    def __init__(self, *args, **kwargs):
        self._state = 'normal'
        self._text = kwargs.get('text', '')

    def __setitem__(self, key, value):
        if key == 'state':
            self._state = value
        elif key == 'text':
            self._text = value

    def __getitem__(self, key):
        if key == 'state':
            return self._state
        if key == 'text':
            return self._text
        return None

    def grid(self, **kwargs):
        pass

    def columnconfigure(self, *args, **kwargs):
        pass

    def delete(self, *args):
        pass

    def insert(self, *args):
        pass

    def get(self):
        return ''


# Build a minimal tkinter stub
tk_mod = _make_stub('tkinter')
tk_mod.IntVar = _TkVar
tk_mod.StringVar = _TkVar
tk_mod.Frame = _Widget
tk_mod.Label = _Widget
tk_mod.Entry = _Widget
tk_mod.Checkbutton = _Widget
tk_mod.NORMAL = 'normal'
tk_mod.DISABLED = 'disabled'
tk_mod.W = 'w'
tk_mod.E = 'e'
tk_mod.EW = 'ew'
tk_mod.END = 'end'

ttk_mod = _make_stub('tkinter.ttk')
ttk_mod.Notebook = _Widget

sys.modules.setdefault('tkinter', tk_mod)
sys.modules.setdefault('tkinter.ttk', ttk_mod)

# ---------------------------------------------------------------------------
# myNotebook – minimal stubs for the widgets used in load.py
# ---------------------------------------------------------------------------
nb_mod = _make_stub(
    'myNotebook',
    EntryMenu=_Widget,
    Label=_Widget,
    Frame=_Widget,
    Checkbutton=_Widget,
)
sys.modules.setdefault('myNotebook', nb_mod)

# ---------------------------------------------------------------------------
# config – minimal stub
# ---------------------------------------------------------------------------
class _Config:
    def __init__(self):
        self._store: dict = {}

    def get_int(self, key: str, default: int = 0) -> int:
        return self._store.get(key, default)

    def get_list(self, key: str, default=None):
        return self._store.get(key, default if default is not None else [])

    def set(self, key: str, value) -> None:
        self._store[key] = value


_config_instance = _Config()

config_mod = _make_stub(
    'config',
    appname='EDMarketConnector',
    appversion=lambda: '5.0.0',
    config=_config_instance,
)
sys.modules.setdefault('config', config_mod)

# ---------------------------------------------------------------------------
# monitor – minimal stub
# ---------------------------------------------------------------------------
class _Monitor:
    def is_live_galaxy(self) -> bool:
        return True


monitor_mod = _make_stub('monitor', monitor=_Monitor())
sys.modules.setdefault('monitor', monitor_mod)

# ---------------------------------------------------------------------------
# plug – minimal stub (PLUGINS list populated per-test)
# ---------------------------------------------------------------------------
plug_mod = _make_stub('plug', PLUGINS=[])
sys.modules.setdefault('plug', plug_mod)

# ---------------------------------------------------------------------------
# requests – minimal stub (real network calls must not happen in tests)
# ---------------------------------------------------------------------------
class _Response:
    ok = True
    status_code = 200
    text = ''


requests_mod = _make_stub('requests', RequestException=Exception)

def _fake_post(*args, **kwargs):
    return _Response()

requests_mod.post = _fake_post  # type: ignore[attr-defined]
sys.modules.setdefault('requests', requests_mod)
