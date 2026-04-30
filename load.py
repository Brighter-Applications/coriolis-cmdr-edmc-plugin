"""
Coriolis CMDR - Standalone EDMC Plugin.

Sends ship loadout, module, material, and stored-module data to the
Coriolis CMDR API (https://cmdr.coriolis.io) in real time as journal
events occur.

Ship-change detection
---------------------
The game writes a Loadout journal event after ShipyardSwap, but EDMC may
consume it during catch-up replay and not forward it to plugins.  The
reliable path for ship-swap detection is the CAPI hook (cmdr_data) which
fires on every dock and manual sync with the full current ship loadout
from Frontier's servers.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime, timezone
from typing import Any

import tkinter as tk
from tkinter import ttk

import requests
import myNotebook as nb  # noqa: N813
from config import appname, appversion, config
from monitor import monitor


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
plugin_name = os.path.basename(os.path.dirname(__file__))
logger = logging.getLogger(f'{appname}.{plugin_name}')

if not logger.hasHandlers():
    logger.setLevel(logging.DEBUG)
    _ch = logging.StreamHandler()
    _ch.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d:%(funcName)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    logger.addHandler(_ch)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PLUGIN_VERSION = '1.6.0'
DEFAULT_CMDR_API_URL = 'https://cmdr.coriolis.io/api/edmc/'
CMDR_API_TIMEOUT = 15

PADX = 10
BUTTONX = 12
PADY = 1
BOXY = 2

SHIP_EVENTS = {
    'Loadout', 'ShipyardNew', 'ShipyardBuy', 'ShipyardSell',
    'SellShipOnRebuy', 'ShipyardSwap', 'ShipyardTransfer',
    'SetUserShipName', 'StartUp',
}
MODULE_EVENTS = {
    'ModuleBuy', 'ModuleSell', 'ModuleStore', 'ModuleRetrieve',
    'ModuleSwap', 'MassModuleStore',
}
ENGINEERING_EVENTS = {'EngineerCraft'}
MATERIAL_EVENTS = {
    'Materials', 'MaterialCollected', 'MaterialDiscarded',
    'MaterialTrade', 'Synthesis', 'ScientificResearch',
    'TechnologyBroker', 'StartUp',
}
STORED_MODULE_EVENTS = {'StoredModules'}
TRACKED_EVENTS = (
    SHIP_EVENTS | MODULE_EVENTS | ENGINEERING_EVENTS
    | MATERIAL_EVENTS | STORED_MODULE_EVENTS
)


# ---------------------------------------------------------------------------
# Plugin state
# ---------------------------------------------------------------------------
class _PluginState:
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
    """Return True if a different loaded plugin already provides CMDR sync."""
    try:
        import plug
        own_modules = set(sys.modules.values())
        for plugin in getattr(plug, 'PLUGINS', []):
            module = getattr(plugin, 'module', None)
            if module is None:
                continue
            mod_name = getattr(module, '__name__', '') or ''
            if 'coriolis' in mod_name.lower() and module not in own_modules:
                if hasattr(module, '_send_to_cmdr_api'):
                    logger.info('Core Coriolis plugin with CMDR sync detected -- standalone disabled.')
                    return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Guard helper
# ---------------------------------------------------------------------------
def _check_guards(cmdr: str, is_beta: bool) -> str | None:
    """Run all pre-send guards. Returns API key if all pass, else None."""
    if not _state.cmdr_sync.get():
        return None
    if is_beta:
        return None
    if not monitor.is_live_galaxy():
        return None
    if not _state.core_has_cmdr_sync:
        _state.core_has_cmdr_sync = _core_plugin_has_cmdr_sync()
    if _state.core_has_cmdr_sync:
        if _state.status_label:
            _state.status_label['text'] = 'Coriolis CMDR: core plugin active, standalone disabled'
        return None
    api_key = _cmdr_api_key(cmdr)
    if not api_key:
        return None
    return api_key


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
_CFG_PREFIX = 'corioliscmdr_plugin_'


def _cfg_key(name: str) -> str:
    return f'{_CFG_PREFIX}{name}'


def _cmdr_api_key(cmdr: str | None) -> str | None:
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
    _state.cmdr_sync.set(config.get_int(_cfg_key('sync')))
    return 'Coriolis CMDR'


def plugin_app(parent: tk.Frame) -> tk.Frame:
    frame = tk.Frame(parent)
    _state.status_label = tk.Label(frame, text='')
    _state.status_label.grid(row=0, column=0, sticky=tk.W)
    return frame


def plugin_prefs(parent: ttk.Notebook, cmdr: str | None, is_beta: bool) -> nb.Frame:
    conf_frame = nb.Frame(parent)
    conf_frame.columnconfigure(index=1, weight=1)
    cur_row = 0
    nb.Label(conf_frame, text=(
        'Sends ship, module, and material data to Coriolis CMDR in real time.\n'
        'Get your API key from https://cmdr.coriolis.io'
    )).grid(sticky=tk.EW, row=cur_row, column=0, padx=PADX, pady=PADY, columnspan=3)
    cur_row += 1
    nb.Checkbutton(conf_frame, text='Send data to Coriolis CMDR',
                   variable=_state.cmdr_sync, command=_prefs_sync_changed,
                   ).grid(row=cur_row, columnspan=3, padx=BUTTONX, pady=PADY, sticky=tk.W)
    cur_row += 1
    _state.apikey_label = nb.Label(conf_frame, text='API Key')
    _state.apikey_label.grid(row=cur_row, padx=PADX, pady=PADY, sticky=tk.W)
    _state.apikey = nb.EntryMenu(conf_frame, width=50)
    _state.apikey.grid(row=cur_row, column=1, padx=PADX, pady=BOXY, sticky=tk.EW)
    cur_row += 1
    prefs_cmdr_changed(cmdr, is_beta)
    return conf_frame


def _prefs_sync_changed() -> None:
    state = tk.NORMAL if _state.cmdr_sync.get() else tk.DISABLED
    if _state.apikey_label:
        _state.apikey_label['state'] = state
    if _state.apikey:
        _state.apikey['state'] = state


def prefs_cmdr_changed(cmdr: str | None, is_beta: bool) -> None:
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
    """Build a loadout dict from EDMC journal state."""
    if not state.get('Modules'):
        return None
    modules = []
    for m in state['Modules'].values():
        module: dict[str, Any] = {
            'slot': m['Slot'], 'item': m['Item'],
            'on': m['On'], 'priority': m['Priority'],
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
        'shipName': state.get('ShipName') or '',
        'shipIdent': state.get('ShipIdent') or '',
        'modules': modules,
        'hullValue': state.get('HullValue'),
        'modulesValue': state.get('ModulesValue'),
        'rebuy': state.get('Rebuy'),
    }


def _build_loadout_from_capi(ship: dict[str, Any]) -> dict[str, Any] | None:
    """Build a loadout dict from CAPI data['ship']."""
    capi_modules = ship.get('modules')
    if not capi_modules:
        return None
    modules = []
    for slot, m in capi_modules.items():
        if not isinstance(m, dict):
            continue
        mod_info = m.get('module', {})
        if not isinstance(mod_info, dict):
            continue
        item_name = mod_info.get('name', '')
        if not item_name:
            continue
        module: dict[str, Any] = {
            'slot': slot, 'item': item_name,
            'on': m.get('on', True), 'priority': m.get('priority', 1),
        }
        health = m.get('health')
        if health is not None:
            module['health'] = health / 10000.0
        value = m.get('value', {})
        if isinstance(value, dict) and value.get('base'):
            module['value'] = value['base']
        mods_raw = m.get('modifications') or m.get('WorkInProgress_modifications') or {}
        if mods_raw and isinstance(mods_raw, dict):
            engineering: dict[str, Any] = {
                'blueprintName': mod_info.get('engineering', {}).get('recipeName', ''),
                'level': mod_info.get('engineering', {}).get('recipeLevel', 0),
                'quality': mod_info.get('engineering', {}).get('recipeQuality', 0),
            }
            mods = []
            for label, mod in mods_raw.items():
                if not isinstance(mod, dict):
                    continue
                modifier: dict[str, Any] = {'label': label}
                if 'value' in mod and 'originalValue' in mod:
                    modifier['value'] = mod['value']
                    modifier['originalValue'] = mod['originalValue']
                    modifier['lessIsGood'] = mod.get('lessIsGood', 0)
                elif 'valueStr' in mod:
                    modifier['valueStr'] = mod['valueStr']
                mods.append(modifier)
            if mods:
                engineering['modifiers'] = mods
            module['engineering'] = engineering
        modules.append(module)
    if not modules:
        return None
    return {
        'shipType': ship.get('name', ''),
        'shipID': ship.get('id'),
        'shipName': ship.get('shipName') or '',
        'shipIdent': ship.get('shipIdent') or '',
        'modules': modules,
        'hullValue': ship.get('hullValue'),
        'modulesValue': ship.get('modulesValue'),
        'rebuy': ship.get('rebuy'),
    }


def _build_materials(state: dict[str, Any]) -> list[dict[str, Any]]:
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
    modules = []
    for item in entry.get('Items', []):
        mod: dict[str, Any] = {
            'storageSlot': item.get('StorageSlot', 0),
            'name': item.get('Name', ''),
            'nameLocalised': item.get('Name_Localised', ''),
            'buyPrice': item.get('BuyPrice', 0),
            'hot': item.get('Hot', False),
        }
        for k, v in (('StarSystem', 'starSystem'), ('MarketID', 'marketID')):
            if k in item:
                mod[v] = item[k]
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
                logger.warning(f'Coriolis CMDR API returned {resp.status_code}: {resp.text[:2000]}')
        except requests.RequestException as e:
            logger.warning(f'Coriolis CMDR API request failed: {e}')
    threading.Thread(target=_do_send, name='CoriolisCMDR-plugin sender', daemon=True).start()


# ---------------------------------------------------------------------------
# CAPI data hook
# ---------------------------------------------------------------------------
def cmdr_data(data: Any, is_beta: bool) -> str | None:
    """
    Called by EDMC after a successful CAPI query (on docking and manual sync).

    This is the primary mechanism for detecting ship changes after a
    ShipyardSwap, because the Loadout journal event may not be forwarded
    to plugins by EDMC after a catch-up replay.
    """
    cmdr = getattr(monitor, 'cmdr', None)
    if not cmdr:
        return None
    api_key = _check_guards(cmdr, is_beta)
    if not api_key:
        return None
    ship = data.get('ship') if hasattr(data, 'get') else None
    if not ship:
        return None
    loadout = _build_loadout_from_capi(ship)
    if not loadout:
        return None
    logger.info(f'cmdr_data: sending loadout from CAPI for {cmdr!r}')
    payload: dict[str, Any] = {
        'event': 'Loadout',
        'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'commander': cmdr,
        'ship': loadout,
    }
    _send_to_cmdr_api(cmdr, api_key, payload)
    return None


# ---------------------------------------------------------------------------
# Journal entry hook
# ---------------------------------------------------------------------------
def journal_entry(
    cmdr: str, is_beta: bool, system: str, station: str,
    entry: dict[str, Any], state: dict[str, Any],
) -> str | None:
    """Process journal events and send relevant data to Coriolis CMDR."""
    api_key = _check_guards(cmdr, is_beta)
    if not api_key:
        return None

    event_name = entry.get('event', '')
    if event_name not in TRACKED_EVENTS:
        _check_material_changes(cmdr, api_key, entry, state)
        return None

    _state.cmdr = cmdr

    # Ship events
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
                payload['soldShipID'] = entry.get('SellShipID') or entry.get('ShipID')
            elif event_name == 'ShipyardSwap':
                payload['storeShipID'] = entry.get('StoreOldShip')
                payload['storeShipType'] = entry.get('ShipType', '')
            _send_to_cmdr_api(cmdr, api_key, payload)
        return None

    # Module / Engineering events
    if event_name in MODULE_EVENTS or event_name in ENGINEERING_EVENTS:
        loadout = _build_loadout(state)
        payload = {
            'event': event_name,
            'timestamp': entry.get('timestamp', ''),
            'commander': cmdr,
            'journalEntry': {k: v for k, v in entry.items() if k not in ('event', 'timestamp')},
        }
        if loadout:
            payload['ship'] = loadout
        _send_to_cmdr_api(cmdr, api_key, payload)
        return None

    # Material events
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
        return None

    # Stored modules
    if event_name in STORED_MODULE_EVENTS:
        stored = _build_stored_modules(entry)
        payload = {
            'event': event_name,
            'timestamp': entry.get('timestamp', ''),
            'commander': cmdr,
            'storedModules': stored,
        }
        _send_to_cmdr_api(cmdr, api_key, payload)
        return None

    _check_material_changes(cmdr, api_key, entry, state)
    return None


def _check_material_changes(
    cmdr: str, api_key: str, entry: dict[str, Any], state: dict[str, Any],
) -> None:
    current = _build_materials(state)
    if _state.last_materials is not None and current != _state.last_materials:
        payload = {
            'event': 'MaterialsUpdated',
            'timestamp': entry.get('timestamp', ''),
            'commander': cmdr,
            'materials': current,
        }
        _send_to_cmdr_api(cmdr, api_key, payload)
    _state.last_materials = current
