# Audio Output Switcher (Decky plugin)

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin for
the Steam Deck that switches audio outputs automatically based on a priority
list, and also lets you switch manually from the Quick Access menu.

## Features

- **Priority list**: every audio output (sink) the Deck has ever seen is kept
  in an ordered list. Reorder devices with the up/down buttons; newly seen
  devices are appended to the bottom so they never cause a surprise switch.
- **Automatic switching**: when a device appears or disappears, the plugin
  switches the default output to the highest-priority device that is
  currently connected. Can be toggled off.
- **Manual switching**: pick any connected output from a dropdown. A manual
  choice stays in effect until the device topology changes (a device is
  plugged in or removed), after which the priority list takes over again.
- Disconnected devices stay in the list (greyed out) and can be forgotten
  with the ✕ button.

Under the hood the plugin talks to PipeWire through `pactl`
(`pactl subscribe` for device events, `pactl set-default-sink` for switching,
plus moving live streams with `pactl move-sink-input`).

## Building

Requirements: Node.js 16.14+ and [pnpm](https://pnpm.io/).

```sh
pnpm install
pnpm run build
```

This produces `dist/index.js`, the bundled frontend.

## Installing on the Steam Deck

1. Build the plugin (see above), then create a zip with the following layout:

   ```sh
   mkdir -p out/audio-output-switcher/dist
   cp plugin.json package.json main.py LICENSE README.md out/audio-output-switcher/
   cp dist/index.js out/audio-output-switcher/dist/
   (cd out && zip -r audio-output-switcher.zip audio-output-switcher)
   ```

2. On the Deck, enable *Developer mode* in Decky Loader settings, then use
   **Install plugin from ZIP** and pick `audio-output-switcher.zip`.

   Alternatively copy the folder directly:

   ```sh
   scp -r out/audio-output-switcher deck@steamdeck:/home/deck/homebrew/plugins/
   ```

   then restart Decky Loader (or reboot).

## Notes

- Settings are stored in Decky's settings dir
  (`~/homebrew/settings/audio-output-switcher/settings.json`).
- The plugin runs unprivileged and talks to the user's PipeWire instance.
