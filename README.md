# Audio Output Switcher (Decky plugin)

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin for
Steam Deck, SteamOS, and Bazzite (game mode) that switches audio outputs
automatically based on a priority list, and also lets you switch manually from
the Quick Access menu.

## Features

- **Priority list**: every audio output (sink) the device has ever seen is kept
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

## Download

Grab the ready-to-install zip from the latest release:
[`audio-output-switcher.zip`](https://github.com/xenophobentx/decky-audio-switcher/releases/latest/download/audio-output-switcher.zip)
(all versions on the [releases page](https://github.com/xenophobentx/decky-audio-switcher/releases)).
Install it via Decky Loader's **Install plugin from ZIP** (requires developer
mode, see below).

## Compatibility

Supported platforms: Steam Deck, SteamOS, and Bazzite (game mode). The plugin
does not hardcode the `deck` user or uid 1000: it resolves the session user
through Decky's `DECKY_USER` and talks to that user's PipeWire instance via
`pactl`, which both distributions ship. On Bazzite, optional virtual sinks
(Game/Voice/Browser/Music) show up as always-connected devices; since newly
seen devices are appended to the bottom of the priority list, they never
steal audio unless you move them up on purpose.

### Game streaming

Game-streaming hosts are protected so the remote client never loses audio:

- **Sunshine**: while a stream runs, Sunshine creates a virtual sink
  (`sink-sunshine-stereo` / `sink-sunshine-surround51` /
  `sink-sunshine-surround71`) and makes it the default output. The plugin
  detects these sinks, keeps the default output locked to them for the
  duration of the stream, and never adds them to the priority list (they are
  ephemeral). When the stream ends, the priority list takes over again.
- **Steam Remote Play / Steam Link**: hosting creates no sink; Steam records
  the monitor of the current default output instead. While such a capture is
  active, the plugin pauses automatic switching entirely so audio is not
  pulled away from the remote client.

In both cases a streaming notice is shown in the plugin panel, and manual
switching from the dropdown remains available.

## Building

Requirements: Node.js 16.14+ and [pnpm](https://pnpm.io/).

```sh
pnpm install
pnpm run build
```

This produces `dist/index.js`, the bundled frontend.

## Installing

1. Build the plugin (see above), then create a zip with the following layout:

   ```sh
   mkdir -p out/audio-output-switcher/dist
   cp plugin.json package.json main.py LICENSE README.md out/audio-output-switcher/
   cp dist/index.js out/audio-output-switcher/dist/
   (cd out && zip -r audio-output-switcher.zip audio-output-switcher)
   ```

2. On the device, enable *Developer mode* in Decky Loader settings, then use
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
