# Coriolis CMDR – EDMC Plugin

A standalone [EDMC](https://github.com/EDCD/EDMarketConnector) plugin that sends ship loadout, module, material, and stored-module data to [Coriolis CMDR](https://cmdr.coriolis.io) in real time.

## Why does this exist?

This functionality has been submitted as a PR to the core Coriolis plugin inside EDMC itself. The EDMC maintainers are volunteers with lives outside of open-source work, and the PR review process takes time. In the meantime, this standalone plugin lets commanders use Coriolis CMDR sync right away without waiting for a core EDMC release.

**This plugin is intended to be temporary.** Once the CMDR sync feature is merged into the core Coriolis plugin and shipped in an EDMC release, this standalone plugin will no longer be needed and can be uninstalled.

## Duplicate-send protection

The plugin checks at runtime whether the core Coriolis plugin already includes CMDR sync support. If it detects that the core plugin has the feature, this standalone plugin automatically disables its own data sending to avoid duplicates. You don't need to rush to uninstall it the moment a new EDMC version ships — it will gracefully stand down on its own.

## Installation

1. Download or clone this repository.
2. Copy the entire `coriolis-cmdr-edmc-plugin` folder into your EDMC plugins directory:
   - **Windows:** `%LOCALAPPDATA%\EDMarketConnector\plugins`
   - **Mac:** `~/Library/Application Support/EDMarketConnector/plugins`
   - **Linux:** `~/.local/share/EDMarketConnector/plugins`
3. Restart EDMC.

## Configuration

1. Open EDMC and go to **File → Settings**.
2. Click the **Coriolis CMDR** tab.
3. Tick **Send data to Coriolis CMDR**.
4. Enter your API key from [cmdr.coriolis.io](https://cmdr.coriolis.io).
5. Click **OK**.

## What data is sent?

The plugin listens for journal events and sends the following to the Coriolis CMDR API:

- **Ship events** – full loadout on dock, ship purchase/sale/swap, name changes, and startup.
- **Module events** – buy, sell, store, retrieve, swap, and mass-store, along with the current loadout.
- **Engineering events** – craft events with the updated loadout.
- **Material events** – full material inventory whenever it changes.
- **Stored modules** – the complete list when the game reports it.

Data is only sent from the live galaxy (not beta or legacy) and only when the sync checkbox is enabled and an API key is configured.

## Uninstalling

Once the core EDMC Coriolis plugin includes CMDR sync:

1. Delete the `coriolis-cmdr-edmc-plugin` folder from your EDMC plugins directory.
2. Restart EDMC.
3. Configure the CMDR sync settings in the core Coriolis plugin tab instead.

## License

[GNU General Public License v3.0](LICENSE)

## Credits

Developed by the [EDCD](https://github.com/EDCD) community for use with [Coriolis](https://coriolis.io) and [Coriolis CMDR](https://cmdr.coriolis.io).
