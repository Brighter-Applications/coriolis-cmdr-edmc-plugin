"""
Coriolis CMDR – Standalone EDMC Plugin.

Sends ship loadout, module, material, and stored-module data to the
Coriolis CMDR API (https://cmdr.coriolis.io) in real time as journal
events occur.

This is a standalone version of the CMDR sync functionality that has been
submitted as a PR to the core Coriolis plugin in EDMC. Once that PR is
accepted and released, this plugin will no longer be needed. Until then,
this plugin provides the same functionality as a separate install.

Copyright (c) EDCD, All Rights Reserved
Licensed under the GNU General Public License v3 or later.
See LICENSE file.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

import tkinter as tk
from tkinter import ttk

import requests
import myNotebook as nb  # noqa: N813
from config import appname, appversion, config
from monitor import monitor


# ---------------------------------------------------------------------------
# Logging – follows EDMC third-party plugin conventions
# ---------------------------------------------------------------------------
plugin_name = os.path.basename(os.path.dirname(__file__))
logger = logging.getLogger(f'{appname}.{plugin_name}')

if not logger.hasHandlers():
    level = logging.INFO
    logger.setLevel(level)
    logger_channel = logging.StreamHandler()
    logger_formatter = logging.Formatter(
        f'%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d:%(funcName)s: %(message)s'
    )
    logger_formatter.default_time_format = '%Y-%m-%d %H:%M:%S'
    logger_formatter.default_msec_format = '%s.%03d'
    logger_channel.setFormatter(logger_formatter)
    logger.addHandler(logger_channel)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PLUGIN_VERSION = '1.0.0'
DEFAULT_CMDR_API_URL = 'https://cmdr.coriolis.io/api/edmc/'
CMDR_API_TIMEOUT = 15  # seconds

# Padding constants (mirrored from EDMC core for consistent look-and-feel)
PADX = 10
BUTTONX = 12
PADY = 1
BOXY = 2

# Journal events we care about
SHIP_EVENTS = {
    'Loadout', 'ShipyardNew', 'ShipyardBuy', 'ShipyardSell',
    'SellShipOnRebuy', 'ShipyardSwap', 'ShipyardTransfer',
    'SetUserShipName', 'StartUp',
}
MODULE_EVENTS = {
    'ModuleBuy', 'ModuleSell', 'ModuleStore', 'ModuleRetrieve',
    'ModuleSwap', 'MassModuleStore',
}
ENGINEERING_EVENTS = {
    'EngineerCraft',
}
MATERIAL_EVENTS = {
    'Materials', 'MaterialCollected', 'MaterialDiscarded',
    'MaterialTrade', 'Synthesis', 'ScientificResearch',
    'TechnologyBroker', 'StartUp',
}
STORED_MODULE_EVENTS = {
    'StoredModules',
}
TRACKED_EVENTS = (
    SHIP_EVENTS | MODULE_EVENTS | ENGINEERING_EVENTS
    | MATERIAL_EVENTS | STORED_MODULE_EVENTS
)


# ---------------------------------------------------------------------------
# Plugin state
# ---------------------------------------------------------------------------
class _PluginState:
    """Mutable state for this plugin instance."""

    def __init__(self) -> None:
        self.cmdr_sync = tk.IntVar(value=0)
        self.cmdr: str | None = None
        self.apikey: nb.EntryMenu | None = None
        self.apikey_label: nb.Label | None = None
        self.last_materials: list[dict[str, Any]] | None = None
        self.status_label: tk.Label | None = None
        self.core_has_cmdr_sync: bool = False


_state = _PluginState()


# ---------------------------------------------------------------------------
# Core-plugin overlap detection
# ---------------------------------------------------------------------------
def _core_plugin_has_cmdr_sync() -> bool:
    """
    Check whether the core Coriolis plugin already provides CMDR sync.

    We look for the config key that the core plugin writes when its CMDR
    sync checkbox is toggled. If that key exists (regardless of value),
    the core plugin has the feature and this standalone plugin should
    stand down to avoid sending duplicate data.
    """
    try:
        # The core plugin stores its sync toggle under 'coriolis_cmdr_sync'.
        # If the key has never been written, get_int returns 0 by default,
        # but the key itself won't exist in the config store. We also check
        # for the presence of the CMDR API key list which is only written
        # by the core plugin's CMDR sync code.
        #
        # However, there's a subtlety: this standalone plugin also needs to
        # store its own settings. We use a different prefix
        # ('corioliscmdr_plugin_') to keep them separate.
        #
        # The most reliable detection is to inspect the loaded core plugin
        # module for the _send_to_cmdr_api function.
        import plug
        for plugin in getattr(plug, 'PLUGINS', []):
            module = getattr(plugin, 'module', None)
            if module is None:
                continue
            # The core coriolis plugin returns 'Coriolis' from plugin_start3
            mod_name = getattr(module, '__name__', '') or ''
            if 'coriolis' in mod_name.lower() and mod_name != __name__:
                if hasattr(module, '_send_to_cmdr_api'):
                    logger.info(
                        'Core Coriolis plugin already has CMDR sync. '
                        'This standalone plugin will disable itself.'
                    )
                    return True
        return False
    except Exception:
        return False



# ---------------------------------------------------------------------------
# Config helpers – use a unique prefix to avoid clashing with core plugin
# ---------------------------------------------------------------------------
_CFG_PREFIX = 'corioliscmdr_plugin_'


def _cfg_key(name: str) -> str:
    return f'{_CFG_PREFIX}{name}'


def _cmdr_api_key(cmdr: str | None) -> str | None:
    """Look up the stored API key for the given commander."""
    if not cmdr:
        return None

    cmdrs = config.get_list(_cfg_key('cmdrs'), default=[])
    apikeys = config.get_list(_cfg_key('apikeys'), default=[])

    if cmdr in cmdrs:
        idx = cmdrs.index(cmdr)
        if idx < len(apikeys) and apikeys[idx]:
            return apikeys[idx]

    return None


# ---------------------------------------------------------------------------
# EDMC plugin hooks
# ---------------------------------------------------------------------------
def plugin_start3(plugin_dir: str) -> str:
    """Called by EDMC on startup."""
    _state.cmdr_sync.set(config.get_int(_cfg_key('sync')))
    return 'Coriolis CMDR'


def plugin_app(parent: tk.Frame) -> tk.Frame:
    """Add a status line to the EDMC main window."""
    frame = tk.Frame(parent)
    _state.status_label = tk.Label(frame, text='')
    _state.status_label.grid(row=0, column=0, sticky=tk.W)
    return frame


def plugin_prefs(parent: ttk.Notebook, cmdr: str | None, is_beta: bool) -> nb.Frame:
    """Build the settings panel for this plugin."""
    conf_frame = nb.Frame(parent)
    conf_frame.columnconfigure(index=1, weight=1)
    cur_row = 0

    # --- Header / explanation ---
    nb.Label(
        conf_frame,
        text=(
            'Sends ship, module, and material data to Coriolis CMDR in real time.\n'
            'Get your API key from https://cmdr.coriolis.io'
        ),
    ).grid(sticky=tk.EW, row=cur_row, column=0, padx=PADX, pady=PADY, columnspan=3)
    cur_row += 1

    # --- Enable checkbox ---
    nb.Checkbutton(
        conf_frame,
        text='Send data to Coriolis CMDR',
        variable=_state.cmdr_sync,
        command=_prefs_sync_changed,
    ).grid(row=cur_row, columnspan=3, padx=BUTTONX, pady=PADY, sticky=tk.W)
    cur_row += 1

    # --- API key ---
    _state.apikey_label = nb.Label(conf_frame, text='API Key')
    _state.apikey_label.grid(row=cur_row, padx=PADX, pady=PADY, sticky=tk.W)
    _state.apikey = nb.EntryMenu(conf_frame, width=50)
    _state.apikey.grid(row=cur_row, column=1, padx=PADX, pady=BOXY, sticky=tk.EW)
    cur_row += 1

    # Populate the API key for the current CMDR
    prefs_cmdr_changed(cmdr, is_beta)

    return conf_frame


def _prefs_sync_changed() -> None:
    """Toggle API key field enabled/disabled based on the checkbox."""
    state = tk.NORMAL if _state.cmdr_sync.get() else tk.DISABLED
    if _state.apikey_label:
        _state.apikey_label['state'] = state
    if _state.apikey:
        _state.apikey['state'] = state


def prefs_cmdr_changed(cmdr: str | None, is_beta: bool) -> None:
    """Called when the commander changes in the settings dialog."""
    if _state.apikey is None:
        return

    _state.apikey['state'] = tk.NORMAL
    _state.apikey.delete(0, tk.END)
    if cmdr:
        cred = _cmdr_api_key(cmdr)
        if cred:
            _state.apikey.insert(0, cred)

    _prefs_sync_changed()


def prefs_changed(cmdr: str | None, is_beta: bool) -> None:
    """Called when the user closes the settings dialog."""
    config.set(_cfg_key('sync'), _state.cmdr_sync.get())

    if cmdr and _state.apikey is not None:
        cmdrs = config.get_list(_cfg_key('cmdrs'), default=[])
        apikeys = config.get_list(_cfg_key('apikeys'), default=[])
        new_key = _state.apikey.get().strip()

        if cmdr in cmdrs:
            idx = cmdrs.index(cmdr)
            apikeys.extend([''] * (1 + idx - len(apikeys)))
            apikeys[idx] = new_key
        else:
            cmdrs.append(cmdr)
            apikeys.append(new_key)

        config.set(_cfg_key('cmdrs'), cmdrs)
        config.set(_cfg_key('apikeys'), apikeys)


# ---------------------------------------------------------------------------
# Data builders
# ---------------------------------------------------------------------------
def _build_loadout(state: dict[str, Any]) -> dict[str, Any] | None:
    """Build a loadout dict from EDMC state."""
    if not state.get('Modules'):
        return None

    modules = []
    for m in state['Modules'].values():
        module: dict[str, Any] = {
            'slot': m['Slot'],
            'item': m['Item'],
            'on': m['On'],
            'priority': m['Priority'],
        }
        if m.get('Health') is not None:
            module['health'] = m['Health']
        if m.get('Value') is not None:
            module['value'] = m['Value']

        if 'Engineering' in m:
            eng = m['Engineering']
            engineering: dict[str, Any] = {
                'blueprintName': eng.get('BlueprintName', ''),
                'level': eng.get('Level', 0),
                'quality': eng.get('Quality', 0),
            }
            if 'ExperimentalEffect' in eng:
                engineering['experimentalEffect'] = eng['ExperimentalEffect']
            if 'Modifiers' in eng:
                mods = []
                for mod in eng['Modifiers']:
                    modifier: dict[str, Any] = {'label': mod['Label']}
                    if 'OriginalValue' in mod:
                        modifier['value'] = mod['Value']
                        modifier['originalValue'] = mod['OriginalValue']
                        modifier['lessIsGood'] = mod.get('LessIsGood', 0)
                    elif 'ValueStr' in mod:
                        modifier['valueStr'] = mod['ValueStr']
                    mods.append(modifier)
                engineering['modifiers'] = mods
            module['engineering'] = engineering

        modules.append(module)

    return {
        'shipType': state.get('ShipType', ''),
        'shipID': state.get('ShipID'),
        'shipName': state.get('ShipName', ''),
        'shipIdent': state.get('ShipIdent', ''),
        'modules': modules,
        'hullValue': state.get('HullValue'),
        'modulesValue': state.get('ModulesValue'),
        'rebuy': state.get('Rebuy'),
    }


def _build_materials(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a sorted material inventory from EDMC state."""
    materials = []
    for category in ('Raw', 'Manufactured', 'Encoded'):
        for name in sorted(state.get(category, {})):
            materials.append({
                'category': category.lower(),
                'name': name,
                'count': state[category][name],
            })
    return materials


def _build_stored_modules(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a stored-modules list from a StoredModules journal entry."""
    items = entry.get('Items', [])
    modules = []
    for item in items:
        mod: dict[str, Any] = {
            'storageSlot': item.get('StorageSlot', 0),
            'name': item.get('Name', ''),
            'nameLocalised': item.get('Name_Localised', ''),
            'buyPrice': item.get('BuyPrice', 0),
            'hot': item.get('Hot', False),
        }
        if 'StarSystem' in item:
            mod['starSystem'] = item['StarSystem']
        if 'MarketID' in item:
            mod['marketID'] = item['MarketID']
        if 'EngineerModifications' in item:
            mod['engineerModification'] = item['EngineerModifications']
        if 'Level' in item:
            mod['engineerLevel'] = item['Level']
        if 'Quality' in item:
            mod['engineerQuality'] = item['Quality']
        modules.append(mod)
    return modules


# ---------------------------------------------------------------------------
# HTTP sender
# ---------------------------------------------------------------------------
def _send_to_cmdr_api(cmdr: str, api_key: str, payload: dict[str, Any]) -> None:
    """POST a payload to the Coriolis CMDR API in a background thread."""
    def _do_send() -> None:
        try:
            masked = f'{api_key[:4]}...{api_key[-4:]}' if len(api_key) >= 8 else '***'
            logger.info(
                f'Coriolis CMDR API: POST {DEFAULT_CMDR_API_URL} '
                f'event={payload.get("event", "?")} key={masked}'
            )
            resp = requests.post(
                DEFAULT_CMDR_API_URL,
                json=payload,
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'X-Api-Key': api_key,
                    'User-Agent': f'{appname}/{appversion()}',
                    'Content-Type': 'application/json',
                },
                timeout=CMDR_API_TIMEOUT,
            )
            if not resp.ok:
                logger.warning(
                    f'Coriolis CMDR API returned {resp.status_code}: '
                    f'{resp.text[:2000]}'
                )
        except requests.RequestException as e:
            logger.warning(f'Coriolis CMDR API request failed: {e}')

    threading.Thread(
        target=_do_send, name='CoriolisCMDR-plugin sender', daemon=True
    ).start()


# ---------------------------------------------------------------------------
# Journal entry hook
# ---------------------------------------------------------------------------
def journal_entry(
    cmdr: str,
    is_beta: bool,
    system: str,
    station: str,
    entry: dict[str, Any],
    state: dict[str, Any],
) -> str | None:
    """Process journal events and send relevant data to Coriolis CMDR."""
    # --- Guard checks ---
    if not _state.cmdr_sync.get():
        return None

    if is_beta:
        return None

    if not monitor.is_live_galaxy():
        return None

    # Check once (lazily) whether the core plugin already handles this
    if not _state.core_has_cmdr_sync:
        _state.core_has_cmdr_sync = _core_plugin_has_cmdr_sync()

    if _state.core_has_cmdr_sync:
        # The core plugin already sends CMDR data – don't duplicate.
        if _state.status_label:
            _state.status_label['text'] = (
                'Coriolis CMDR: core plugin active, standalone disabled'
            )
        return None

    api_key = _cmdr_api_key(cmdr)
    if not api_key:
        return None

    event_name = entry.get('event', '')
    if event_name not in TRACKED_EVENTS:
        _check_material_changes(cmdr, api_key, entry, state)
        return None

    _state.cmdr = cmdr

    # --- Ship events ---
    if event_name in SHIP_EVENTS:
        loadout = _build_loadout(state)
        if loadout:
            payload: dict[str, Any] = {
                'event': event_name,
                'timestamp': entry.get('timestamp', ''),
                'commander': cmdr,
                'ship': loadout,
            }
            if event_name == 'ShipyardBuy':
                payload['storeShipID'] = entry.get('StoreShipID')
                payload['sellShipID'] = entry.get('SellShipID')
                payload['newShipType'] = entry.get('ShipType', '')
            elif event_name in ('ShipyardSell', 'SellShipOnRebuy'):
                payload['soldShipType'] = entry.get('ShipType', '')
                payload['soldShipID'] = (
                    entry.get('SellShipID') or entry.get('ShipID')
                )
            elif event_name == 'ShipyardSwap':
                payload['storeShipID'] = entry.get('StoreOldShip')
                payload['storeShipType'] = entry.get('ShipType', '')

            _send_to_cmdr_api(cmdr, api_key, payload)

    # --- Module events ---
    elif event_name in MODULE_EVENTS:
        loadout = _build_loadout(state)
        payload = {
            'event': event_name,
            'timestamp': entry.get('timestamp', ''),
            'commander': cmdr,
            'journalEntry': {
                k: v for k, v in entry.items()
                if k not in ('event', 'timestamp')
            },
        }
        if loadout:
            payload['ship'] = loadout
        _send_to_cmdr_api(cmdr, api_key, payload)

    # --- Engineering events ---
    elif event_name in ENGINEERING_EVENTS:
        loadout = _build_loadout(state)
        payload = {
            'event': event_name,
            'timestamp': entry.get('timestamp', ''),
            'commander': cmdr,
            'journalEntry': {
                k: v for k, v in entry.items()
                if k not in ('event', 'timestamp')
            },
        }
        if loadout:
            payload['ship'] = loadout
        _send_to_cmdr_api(cmdr, api_key, payload)

    # --- Material events ---
    if event_name in MATERIAL_EVENTS:
        materials = _build_materials(state)
        payload = {
            'event': event_name,
            'timestamp': entry.get('timestamp', ''),
            'commander': cmdr,
            'materials': materials,
        }
        _state.last_materials = materials
        _send_to_cmdr_api(cmdr, api_key, payload)

    # --- Stored modules ---
    elif event_name in STORED_MODULE_EVENTS:
        stored = _build_stored_modules(entry)
        payload = {
            'event': event_name,
            'timestamp': entry.get('timestamp', ''),
            'commander': cmdr,
            'storedModules': stored,
        }
        _send_to_cmdr_api(cmdr, api_key, payload)

    else:
        _check_material_changes(cmdr, api_key, entry, state)

    return None


def _check_material_changes(
    cmdr: str, api_key: str, entry: dict[str, Any], state: dict[str, Any],
) -> None:
    """Detect material inventory changes and send an update if they differ."""
    current = _build_materials(state)
    if (
        _state.last_materials is not None
        and current != _state.last_materials
    ):
        payload = {
            'event': 'MaterialsUpdated',
            'timestamp': entry.get('timestamp', ''),
            'commander': cmdr,
            'materials': current,
        }
        _send_to_cmdr_api(cmdr, api_key, payload)

    _state.last_materials = current
