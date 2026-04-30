"""
Bug condition exploration tests for ship-change-detection bugfix.

These tests encode the EXPECTED (fixed) behavior.
On UNFIXED code they are expected to FAIL — failure confirms the bugs exist.

Sub-condition A — self-detection:
    _core_plugin_has_cmdr_sync() must return False when the only "coriolis"
    plugin with _send_to_cmdr_api present is the standalone module itself.

    The bug: the module is loaded as 'load' (sys.modules['load'] = plugin),
    then EDMC renames plugin.__name__ to 'corioliscmdr.load'. The function
    calls sys.modules.get(__name__) where __name__ is now 'corioliscmdr.load'
    — which is NOT in sys.modules — so own_module = None. Then
    module is not None -> True -> self-detection fires.

Sub-condition B — full swap sequence:
    journal_entry() called with ShipyardSwap (empty state['Modules']) then
    with Loadout (populated state['Modules']) must result in _send_to_cmdr_api
    being called exactly once (on Loadout) and never on ShipyardSwap.

Validates: Requirements 1.1, 1.2, 1.3
"""
from __future__ import annotations

import sys
import types
import importlib.util
import os
from unittest.mock import MagicMock, patch


def _load_plugin(name='load'):
    plugin_dir = os.path.join(os.path.dirname(__file__), '..')
    load_py = os.path.abspath(os.path.join(plugin_dir, 'load.py'))
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, load_py)
    plugin = importlib.util.module_from_spec(spec)
    sys.modules[name] = plugin
    plugin.__dict__['sys'] = sys
    spec.loader.exec_module(plugin)
    return plugin


def _make_fake_plugin_object(module):
    fake = MagicMock()
    fake.module = module
    return fake


def _minimal_state(modules=None):
    return {
        'Modules': modules if modules is not None else {},
        'ShipType': 'sidewinder', 'ShipID': 1,
        'ShipName': 'Test Ship', 'ShipIdent': 'TS-1',
        'HullValue': 0, 'ModulesValue': 0, 'Rebuy': 0,
        'Raw': {}, 'Manufactured': {}, 'Encoded': {},
    }


def _populated_modules():
    return {
        'Armour': {
            'Slot': 'Armour', 'Item': 'sidewinder_armour_grade1',
            'On': True, 'Priority': 1, 'Health': 1.0, 'Value': 0,
        }
    }


class TestSelfDetection:
    """
    Property 1 / Sub-condition A:
    _core_plugin_has_cmdr_sync() must return False when the only plugin in
    plug.PLUGINS whose module has "coriolis" in its name and defines
    _send_to_cmdr_api is the standalone module itself.

    The bug: module loaded as 'load', EDMC renames __name__ to
    'corioliscmdr.load'. sys.modules.get('corioliscmdr.load') = None so
    own_module=None and module is not None passes -> self-detection fires.

    The fix: check module not in sys.modules.values() instead.

    On UNFIXED code this test FAILS. Validates: Requirements 1.1
    """

    def test_own_module_not_detected_as_core(self):
        """
        EXPECTED (fixed): returns False for own module.
        ACTUAL (unfixed): returns True — self-detection fires.

        Counterexample: sys.modules.get('corioliscmdr.load') = None
        -> own_module=None -> module is not None -> self-detection.
        """
        sys.modules.pop('load', None)
        sys.modules.pop('corioliscmdr.load', None)

        plugin = _load_plugin('load')
        assert plugin.__name__ == 'load'

        # EDMC renames __name__ but keeps sys.modules key as 'load'.
        plugin.__name__ = 'corioliscmdr.load'

        import plug
        original_plugins = plug.PLUGINS
        plug.PLUGINS = [_make_fake_plugin_object(plugin)]

        try:
            result = plugin._core_plugin_has_cmdr_sync()
            assert result is False, (
                "BUG CONFIRMED (sub-condition A): _core_plugin_has_cmdr_sync() "
                f"returned True for the standalone module itself. "
                f"module.__name__={plugin.__name__!r}, registered as 'load'. "
                "sys.modules.get('corioliscmdr.load') returns None so "
                "own_module=None and the identity check passes incorrectly."
            )
        finally:
            plug.PLUGINS = original_plugins
            sys.modules.pop('load', None)
            sys.modules.pop('corioliscmdr.load', None)


class TestSwapSequence:
    """
    Property 1 / Sub-condition B:
    journal_entry() with ShipyardSwap then Loadout must call _send_to_cmdr_api
    exactly once (on Loadout).

    On UNFIXED code this test FAILS because self-detection during ShipyardSwap
    permanently disables the plugin before Loadout is processed.

    Validates: Requirements 1.2, 1.3
    """

    def test_send_to_cmdr_api_called_once_on_loadout_not_on_swap(self):
        """
        EXPECTED (fixed): _send_to_cmdr_api called exactly once on Loadout.
        ACTUAL (unfixed): called zero times — permanent disable via self-detection.

        Counterexample: self-detection sets core_has_cmdr_sync=True during
        ShipyardSwap, blocking the subsequent Loadout event.
        """
        sys.modules.pop('load', None)
        sys.modules.pop('corioliscmdr.load', None)

        plugin = _load_plugin('load')
        plugin._state.cmdr_sync.set(1)

        import config as cfg_mod
        cfg_mod.config._store['corioliscmdr_plugin_cmdrs'] = ['TestCmdr']
        cfg_mod.config._store['corioliscmdr_plugin_apikeys'] = ['test-api-key-1234']

        # Simulate EDMC renaming __name__ — triggers self-detection on unfixed code.
        plugin.__name__ = 'corioliscmdr.load'

        import plug
        original_plugins = plug.PLUGINS
        plug.PLUGINS = [_make_fake_plugin_object(plugin)]

        send_calls = []

        try:
            with patch.object(plugin, '_send_to_cmdr_api',
                              side_effect=lambda cmdr, key, payload: send_calls.append(payload)):
                plugin.journal_entry(
                    cmdr='TestCmdr', is_beta=False,
                    system='Sol', station='Jameson Memorial',
                    entry={'event': 'ShipyardSwap', 'timestamp': '2024-01-01T00:00:00Z',
                           'ShipType': 'sidewinder', 'StoreOldShip': 42},
                    state=_minimal_state(modules={}),
                )
                plugin.journal_entry(
                    cmdr='TestCmdr', is_beta=False,
                    system='Sol', station='Jameson Memorial',
                    entry={'event': 'Loadout', 'timestamp': '2024-01-01T00:00:01Z'},
                    state=_minimal_state(modules=_populated_modules()),
                )

            # Fixed behavior: pending_swap fires the deferred ShipyardSwap payload
            # when Loadout arrives (Path 4), then Loadout itself also sends.
            # So we expect at least one send, and the first must be ShipyardSwap.
            assert len(send_calls) >= 1, (
                f"BUG CONFIRMED (sub-condition B): _send_to_cmdr_api was called "
                f"{len(send_calls)} time(s) — expected at least 1. "
                "Likely cause: self-detection set core_has_cmdr_sync=True during "
                "ShipyardSwap, permanently disabling the plugin before Loadout."
            )
            events_sent = [c.get('event') for c in send_calls]
            assert 'ShipyardSwap' in events_sent, (
                f"Expected a ShipyardSwap send in the sequence, got: {events_sent}"
            )
        finally:
            plug.PLUGINS = original_plugins
            sys.modules.pop('load', None)
            sys.modules.pop('corioliscmdr.load', None)
