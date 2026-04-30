"""
Preservation property tests for ship-change-detection bugfix.

These tests encode PRESERVED (unchanged) behavior — behaviors that must
continue to work correctly both before and after the fix is applied.

They are expected to PASS on UNFIXED code, confirming the baseline behavior
that the fix must not break.

Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_plugin_fresh():
    """Import a fresh copy of the plugin registered as 'load'."""
    plugin_dir = os.path.join(os.path.dirname(__file__), '..')
    load_py = os.path.abspath(os.path.join(plugin_dir, 'load.py'))
    sys.modules.pop('load', None)
    sys.modules.pop('corioliscmdr.load', None)
    spec = importlib.util.spec_from_file_location('load', load_py)
    plugin = importlib.util.module_from_spec(spec)
    sys.modules['load'] = plugin
    # Ensure the real sys module is available in the plugin's globals.
    # When loaded via importlib in a stubbed environment, 'import sys' inside
    # load.py may resolve to None from sys.modules; inject the real one.
    plugin.__dict__['sys'] = sys
    spec.loader.exec_module(plugin)
    return plugin


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


def _setup_plugin_for_send(plugin):
    """Enable sync and store a fake API key so journal_entry() will send."""
    plugin._state.cmdr_sync.set(1)
    import config as cfg_mod
    cfg_mod.config._store['corioliscmdr_plugin_cmdrs'] = ['TestCmdr']
    cfg_mod.config._store['corioliscmdr_plugin_apikeys'] = ['test-api-key-1234']


# ---------------------------------------------------------------------------
# TestGenuineCorePluginDetection
# Validates: Preservation Requirement 3.1
# ---------------------------------------------------------------------------

class TestGenuineCorePluginDetection:
    """
    Preservation Requirement 3.1:
    When a genuinely different Coriolis plugin module (not the standalone
    plugin itself) is loaded and provides _send_to_cmdr_api,
    _core_plugin_has_cmdr_sync() must continue to return True.

    These tests must PASS on unfixed code.
    """

    def test_genuine_different_module_detected_as_core(self):
        """
        A different module (not the plugin itself) with __name__ containing
        'coriolis' and _send_to_cmdr_api must be detected as the core plugin.

        Validates: Preservation Requirement 3.1
        """
        plugin = _load_plugin_fresh()

        # Create a DIFFERENT module — not the plugin itself.
        fake_core = types.ModuleType('coriolis_core.load')
        fake_core._send_to_cmdr_api = lambda *a, **kw: None  # type: ignore[attr-defined]

        # Verify it is a different object from the running plugin.
        assert fake_core is not sys.modules.get('load')

        fake_plugin_obj = MagicMock()
        fake_plugin_obj.module = fake_core

        import plug
        original_plugins = plug.PLUGINS
        plug.PLUGINS = [fake_plugin_obj]

        try:
            result = plugin._core_plugin_has_cmdr_sync()
            assert result is True, (
                "Genuine core plugin detection broken: _core_plugin_has_cmdr_sync() "
                "returned False for a different module with 'coriolis' in its name "
                "and _send_to_cmdr_api defined."
            )
        finally:
            plug.PLUGINS = original_plugins
            sys.modules.pop('load', None)

    @given(
        module_name=st.text(
            alphabet=st.characters(whitelist_categories=('Ll',)),
            min_size=1,
        ).map(lambda s: f'coriolis_{s}.load')
    )
    @settings(max_examples=50)
    def test_genuine_core_detection_property(self, module_name):
        """
        For any module name containing 'coriolis' that is NOT 'load'
        (not the running instance's name), a fake module with that name
        and _send_to_cmdr_api must be detected as the core plugin.

        **Validates: Requirements 3.1**

        Must PASS on unfixed code.
        """
        plugin = _load_plugin_fresh()

        # The generated name always starts with 'coriolis_' so it is never
        # equal to 'load' (the running instance's __name__).
        assert module_name != 'load'

        # Create a fake module that is a different object from the plugin.
        fake_core = types.ModuleType(module_name)
        fake_core._send_to_cmdr_api = lambda *a, **kw: None  # type: ignore[attr-defined]

        # Must be a different object from the running plugin module.
        assert fake_core is not sys.modules.get('load')

        fake_plugin_obj = MagicMock()
        fake_plugin_obj.module = fake_core

        import plug
        original_plugins = plug.PLUGINS
        plug.PLUGINS = [fake_plugin_obj]

        try:
            result = plugin._core_plugin_has_cmdr_sync()
            assert result is True, (
                f"Genuine core plugin detection broken for module_name={module_name!r}: "
                "_core_plugin_has_cmdr_sync() returned False for a different module "
                "with 'coriolis' in its name and _send_to_cmdr_api defined."
            )
        finally:
            plug.PLUGINS = original_plugins
            sys.modules.pop('load', None)


# ---------------------------------------------------------------------------
# TestNonSwapShipEvents
# Validates: Preservation Requirements 3.2, 3.3
# ---------------------------------------------------------------------------

class TestNonSwapShipEvents:
    """
    Preservation Requirements 3.2, 3.3:
    Non-ShipyardSwap ship events with populated state['Modules'] and no core
    plugin loaded must continue to call _send_to_cmdr_api exactly once.

    These tests must PASS on unfixed code.
    """

    def test_loadout_on_startup_sends(self):
        """
        A Loadout event on startup with populated modules must call
        _send_to_cmdr_api exactly once.

        Validates: Preservation Requirement 3.2
        """
        plugin = _load_plugin_fresh()
        _setup_plugin_for_send(plugin)

        import plug
        original_plugins = plug.PLUGINS
        plug.PLUGINS = []  # No core plugin present.

        send_calls = []

        try:
            with patch.object(plugin, '_send_to_cmdr_api',
                              side_effect=lambda *a, **kw: send_calls.append(a)):
                plugin.journal_entry(
                    cmdr='TestCmdr',
                    is_beta=False,
                    system='Sol',
                    station='Jameson Memorial',
                    entry={
                        'event': 'Loadout',
                        'timestamp': '2024-01-01T00:00:00Z',
                    },
                    state=_minimal_state(modules=_populated_modules()),
                )

            assert len(send_calls) == 1, (
                f"Expected _send_to_cmdr_api to be called exactly once for Loadout, "
                f"got {len(send_calls)} call(s)."
            )
        finally:
            plug.PLUGINS = original_plugins
            sys.modules.pop('load', None)

    def test_shipyard_buy_sends(self):
        """
        A ShipyardBuy event with populated modules must call
        _send_to_cmdr_api exactly once.

        Validates: Preservation Requirement 3.3
        """
        plugin = _load_plugin_fresh()
        _setup_plugin_for_send(plugin)

        import plug
        original_plugins = plug.PLUGINS
        plug.PLUGINS = []  # No core plugin present.

        send_calls = []

        try:
            with patch.object(plugin, '_send_to_cmdr_api',
                              side_effect=lambda *a, **kw: send_calls.append(a)):
                plugin.journal_entry(
                    cmdr='TestCmdr',
                    is_beta=False,
                    system='Sol',
                    station='Jameson Memorial',
                    entry={
                        'event': 'ShipyardBuy',
                        'timestamp': '2024-01-01T00:00:00Z',
                        'ShipType': 'sidewinder',
                    },
                    state=_minimal_state(modules=_populated_modules()),
                )

            assert len(send_calls) == 1, (
                f"Expected _send_to_cmdr_api to be called exactly once for ShipyardBuy, "
                f"got {len(send_calls)} call(s)."
            )
        finally:
            plug.PLUGINS = original_plugins
            sys.modules.pop('load', None)

    @given(
        event_name=st.sampled_from(
            [e for e in ['Loadout', 'ShipyardBuy', 'ShipyardNew', 'SetUserShipName']]
        )
    )
    @settings(max_examples=20)
    def test_non_swap_ship_events_property(self, event_name):
        """
        For any non-ShipyardSwap ship event with populated state['Modules']
        and no core plugin loaded, _send_to_cmdr_api must be called exactly once.

        **Validates: Requirements 3.2, 3.3**

        Must PASS on unfixed code.
        """
        plugin = _load_plugin_fresh()
        _setup_plugin_for_send(plugin)

        import plug
        original_plugins = plug.PLUGINS
        plug.PLUGINS = []  # No core plugin present.

        send_calls = []

        try:
            with patch.object(plugin, '_send_to_cmdr_api',
                              side_effect=lambda *a, **kw: send_calls.append(a)):
                plugin.journal_entry(
                    cmdr='TestCmdr',
                    is_beta=False,
                    system='Sol',
                    station='Jameson Memorial',
                    entry={
                        'event': event_name,
                        'timestamp': '2024-01-01T00:00:00Z',
                    },
                    state=_minimal_state(modules=_populated_modules()),
                )

            assert len(send_calls) == 1, (
                f"Expected _send_to_cmdr_api to be called exactly once for "
                f"event={event_name!r}, got {len(send_calls)} call(s)."
            )
        finally:
            plug.PLUGINS = original_plugins
            sys.modules.pop('load', None)


# ---------------------------------------------------------------------------
# TestSuppressionGuards
# Validates: Preservation Requirements 3.4, 3.5
# ---------------------------------------------------------------------------

class TestSuppressionGuards:
    """
    Preservation Requirements 3.4, 3.5:
    When sync is disabled, no API key is configured, the game is in beta,
    or the galaxy is not live, _send_to_cmdr_api must never be called.

    These tests must PASS on unfixed code.
    """

    def test_beta_suppresses_send(self):
        """
        When is_beta=True, _send_to_cmdr_api must never be called.

        Validates: Preservation Requirement 3.5
        """
        plugin = _load_plugin_fresh()
        _setup_plugin_for_send(plugin)

        import plug
        original_plugins = plug.PLUGINS
        plug.PLUGINS = []

        send_calls = []

        try:
            with patch.object(plugin, '_send_to_cmdr_api',
                              side_effect=lambda *a, **kw: send_calls.append(a)):
                plugin.journal_entry(
                    cmdr='TestCmdr',
                    is_beta=True,  # Beta mode — must suppress.
                    system='Sol',
                    station='Jameson Memorial',
                    entry={
                        'event': 'Loadout',
                        'timestamp': '2024-01-01T00:00:00Z',
                    },
                    state=_minimal_state(modules=_populated_modules()),
                )

            assert len(send_calls) == 0, (
                f"Expected _send_to_cmdr_api to never be called in beta mode, "
                f"got {len(send_calls)} call(s)."
            )
        finally:
            plug.PLUGINS = original_plugins
            sys.modules.pop('load', None)

    def test_sync_disabled_suppresses_send(self):
        """
        When cmdr_sync=0, _send_to_cmdr_api must never be called.

        Validates: Preservation Requirement 3.4
        """
        plugin = _load_plugin_fresh()
        # Deliberately do NOT enable sync.
        plugin._state.cmdr_sync.set(0)
        import config as cfg_mod
        cfg_mod.config._store['corioliscmdr_plugin_cmdrs'] = ['TestCmdr']
        cfg_mod.config._store['corioliscmdr_plugin_apikeys'] = ['test-api-key-1234']

        import plug
        original_plugins = plug.PLUGINS
        plug.PLUGINS = []

        send_calls = []

        try:
            with patch.object(plugin, '_send_to_cmdr_api',
                              side_effect=lambda *a, **kw: send_calls.append(a)):
                plugin.journal_entry(
                    cmdr='TestCmdr',
                    is_beta=False,
                    system='Sol',
                    station='Jameson Memorial',
                    entry={
                        'event': 'Loadout',
                        'timestamp': '2024-01-01T00:00:00Z',
                    },
                    state=_minimal_state(modules=_populated_modules()),
                )

            assert len(send_calls) == 0, (
                f"Expected _send_to_cmdr_api to never be called when sync is disabled, "
                f"got {len(send_calls)} call(s)."
            )
        finally:
            plug.PLUGINS = original_plugins
            sys.modules.pop('load', None)

    def test_no_api_key_suppresses_send(self):
        """
        When no API key is stored for the commander, _send_to_cmdr_api must
        never be called.

        Validates: Preservation Requirement 3.4
        """
        plugin = _load_plugin_fresh()
        plugin._state.cmdr_sync.set(1)
        # Deliberately do NOT store an API key.
        import config as cfg_mod
        cfg_mod.config._store.pop('corioliscmdr_plugin_cmdrs', None)
        cfg_mod.config._store.pop('corioliscmdr_plugin_apikeys', None)

        import plug
        original_plugins = plug.PLUGINS
        plug.PLUGINS = []

        send_calls = []

        try:
            with patch.object(plugin, '_send_to_cmdr_api',
                              side_effect=lambda *a, **kw: send_calls.append(a)):
                plugin.journal_entry(
                    cmdr='TestCmdr',
                    is_beta=False,
                    system='Sol',
                    station='Jameson Memorial',
                    entry={
                        'event': 'Loadout',
                        'timestamp': '2024-01-01T00:00:00Z',
                    },
                    state=_minimal_state(modules=_populated_modules()),
                )

            assert len(send_calls) == 0, (
                f"Expected _send_to_cmdr_api to never be called when no API key is set, "
                f"got {len(send_calls)} call(s)."
            )
        finally:
            plug.PLUGINS = original_plugins
            sys.modules.pop('load', None)
