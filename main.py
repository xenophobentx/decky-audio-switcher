import asyncio
import json
import os
import pwd
import shutil

import decky

SETTINGS_PATH = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")

DEFAULT_SETTINGS = {
    "auto_switch": True,
    # Sink names ordered from highest to lowest priority. Disconnected but
    # previously seen devices stay in the list so their slot is remembered.
    "priority": [],
    # sink name -> last known human readable description
    "device_names": {},
}


# Virtual sinks created by game-streaming hosts (Sunshine creates
# sink-sunshine-stereo/-surround51/-surround71 and sets one of them default
# while a stream runs). These sinks are ephemeral and must stay the default
# output for the duration of the stream.
STREAMING_SINK_PATTERNS = ("sink-sunshine", "sunshine", "steam_streaming",
                           "steam streaming", "streaming speaker", "remote play")

# application.name values of clients that record a sink monitor while game
# streaming (Steam Remote Play hosting creates no sink; Steam records the
# monitor of the current default sink instead).
STREAMING_CAPTURE_APPS = ("steam", "sunshine", "remote play", "remoteplay", "moonlight")


def choose_sink(priority, connected):
    """Return the highest-priority sink name that is currently connected."""
    for name in priority:
        if name in connected:
            return name
    return None


def is_streaming_sink(name, description):
    """True if a sink looks like a game-streaming virtual device."""
    haystack = "{} {}".format(name or "", description or "").lower()
    return any(pattern in haystack for pattern in STREAMING_SINK_PATTERNS)


def find_streaming_capture(source_outputs, sources):
    """True if a streaming client records a sink monitor (Remote Play hosting).

    Only monitor captures by known streaming apps count: SteamOS keeps a
    permanent echo-cancel monitor capture, so "any monitor capture" would be
    far too broad.
    """
    monitor_indices = set()
    for src in sources or []:
        if str(src.get("name", "")).endswith(".monitor"):
            monitor_indices.add(str(src.get("index")))
    for out in source_outputs or []:
        if str(out.get("source")) not in monitor_indices:
            continue
        props = out.get("properties") or {}
        app = str(props.get("application.name", "")).lower()
        if any(pattern in app for pattern in STREAMING_CAPTURE_APPS):
            return True
    return False


def load_settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    settings = dict(DEFAULT_SETTINGS)
    for key in settings:
        if key in data:
            settings[key] = data[key]
    return settings


def save_settings(settings):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    tmp_path = SETTINGS_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    os.replace(tmp_path, SETTINGS_PATH)


class Plugin:
    async def _main(self):
        self.settings = load_settings()
        self.monitor_task = None
        self.subscribe_proc = None
        self.pactl_env = self._build_env()
        await self._apply_policy(trigger_switch=True)
        self.monitor_task = asyncio.get_event_loop().create_task(self._monitor())
        decky.logger.info("Audio Output Switcher loaded")

    async def _unload(self):
        if self.monitor_task is not None:
            self.monitor_task.cancel()
            self.monitor_task = None
        if self.subscribe_proc is not None:
            try:
                self.subscribe_proc.terminate()
            except ProcessLookupError:
                pass
            self.subscribe_proc = None
        decky.logger.info("Audio Output Switcher unloaded")

    # ---------- frontend callables ----------

    async def get_state(self):
        return await self._build_state()

    async def set_default_sink(self, name: str):
        """Manual switch. Stays in effect until the device topology changes."""
        ok = await self._set_default_sink(name)
        return await self._build_state(assume_default=name if ok else None)

    async def set_auto_switch(self, enabled: bool):
        self.settings["auto_switch"] = bool(enabled)
        save_settings(self.settings)
        switched = await self._apply_policy(trigger_switch=True)
        return await self._build_state(assume_default=switched)

    async def move_priority(self, name: str, direction: str):
        priority = self.settings["priority"]
        switched = None
        if name in priority:
            idx = priority.index(name)
            new_idx = idx - 1 if direction == "up" else idx + 1
            if 0 <= new_idx < len(priority):
                priority[idx], priority[new_idx] = priority[new_idx], priority[idx]
                save_settings(self.settings)
                # A reorder is an explicit user action, apply it right away.
                switched = await self._apply_policy(trigger_switch=True)
        return await self._build_state(assume_default=switched)

    async def forget_device(self, name: str):
        self.settings["priority"] = [n for n in self.settings["priority"] if n != name]
        self.settings["device_names"].pop(name, None)
        save_settings(self.settings)
        return await self._build_state()

    # ---------- audio backend (pactl) ----------

    def _build_env(self):
        env = dict(os.environ)
        # Always point at the session user's runtime dir. The backend may
        # inherit a root environment (XDG_RUNTIME_DIR=/run/user/0), in which
        # case pactl would not find the user's PipeWire instance. DECKY_USER
        # is the real session user on both SteamOS ("deck") and Bazzite.
        try:
            uid = pwd.getpwnam(decky.DECKY_USER).pw_uid
        except KeyError:
            uid = 1000
        env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
        env.setdefault("HOME", decky.DECKY_USER_HOME)
        return env

    async def _pactl(self, *args):
        pactl = shutil.which("pactl") or "/usr/bin/pactl"
        proc = await asyncio.create_subprocess_exec(
            pactl, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.pactl_env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            decky.logger.warning(
                "pactl %s failed (%s): %s", " ".join(args), proc.returncode,
                stderr.decode(errors="replace").strip(),
            )
            return None
        return stdout.decode(errors="replace")

    async def _get_sinks(self):
        """Return ([{name, description}], default_sink_name)."""
        sinks = []
        out = await self._pactl("-f", "json", "list", "sinks")
        if out is not None:
            try:
                for entry in json.loads(out):
                    sinks.append({
                        "name": entry.get("name", ""),
                        "description": entry.get("description", entry.get("name", "")),
                    })
            except ValueError:
                out = None
        if out is None:
            # Fallback for pactl builds without JSON support.
            short = await self._pactl("list", "short", "sinks")
            if short:
                for line in short.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        sinks.append({"name": parts[1], "description": parts[1]})
        default = (await self._pactl("get-default-sink") or "").strip() or None
        return sinks, default

    async def _get_streaming_capture(self):
        """Best effort: True if a streaming client is recording a sink monitor.

        If pactl has no JSON support or the output cannot be parsed, report no
        capture — the sink-based streaming protection still works.
        """
        out_outputs = await self._pactl("-f", "json", "list", "source-outputs")
        out_sources = await self._pactl("-f", "json", "list", "sources")
        if out_outputs is None or out_sources is None:
            return False
        try:
            return find_streaming_capture(json.loads(out_outputs),
                                          json.loads(out_sources))
        except ValueError:
            return False

    async def _set_default_sink(self, name: str):
        result = await self._pactl("set-default-sink", name)
        if result is None:
            return False
        # Move already playing streams over as well; streams pinned to a
        # specific device by the user keep working if the move fails.
        short = await self._pactl("list", "short", "sink-inputs")
        if short:
            for line in short.splitlines():
                parts = line.split("\t")
                if parts and parts[0].isdigit():
                    await self._pactl("move-sink-input", parts[0], name)
        return True

    # ---------- policy ----------

    async def _apply_policy(self, trigger_switch: bool):
        """Refresh device bookkeeping and switch the default sink if needed.

        Returns the sink name that was switched to, or None. WirePlumber
        applies a default change asynchronously, so right after a switch
        `pactl get-default-sink` may still report the old sink — callers
        pass the returned name to _build_state(assume_default=...) so the
        UI reflects the switch immediately.
        """
        sinks, default = await self._get_sinks()
        connected = set()
        streaming_sink = None
        changed = False
        for sink in sinks:
            if is_streaming_sink(sink["name"], sink["description"]):
                # Streaming sinks are ephemeral: never remember them in the
                # priority list (and drop them if an older version did).
                if streaming_sink is None:
                    streaming_sink = sink["name"]
                if sink["name"] in self.settings["priority"]:
                    self.settings["priority"].remove(sink["name"])
                    changed = True
                if self.settings["device_names"].pop(sink["name"], None) is not None:
                    changed = True
                continue
            connected.add(sink["name"])
            if sink["name"] not in self.settings["priority"]:
                # Unknown devices go to the bottom so they never cause a
                # surprise switch; the user can move them up afterwards.
                self.settings["priority"].append(sink["name"])
                changed = True
            if self.settings["device_names"].get(sink["name"]) != sink["description"]:
                self.settings["device_names"][sink["name"]] = sink["description"]
                changed = True
        if changed:
            save_settings(self.settings)
        if not trigger_switch:
            return None
        if streaming_sink is not None:
            # A game stream is being hosted through a virtual sink (Sunshine):
            # keep it the default so the remote client does not lose audio.
            if streaming_sink != default:
                decky.logger.info(
                    "Streaming sink %s active, locking default output to it",
                    streaming_sink)
                if await self._set_default_sink(streaming_sink):
                    return streaming_sink
            return None
        if not self.settings["auto_switch"]:
            return None
        if await self._get_streaming_capture():
            # Steam Remote Play hosting records the monitor of the current
            # default sink; switching away would cut the remote client's
            # audio, so suspend auto-switching while the capture is active.
            decky.logger.info(
                "Streaming capture active, auto-switching suspended")
            return None
        target = choose_sink(self.settings["priority"], connected)
        if target and target != default:
            decky.logger.info("Auto-switching default sink to %s", target)
            if await self._set_default_sink(target):
                return target
        return None

    async def _build_state(self, assume_default=None):
        sinks, default = await self._get_sinks()
        if assume_default is not None:
            # Right after a switch get-default-sink may still be stale.
            default = assume_default
        connected = {s["name"]: s for s in sinks}
        entries = []
        for name in self.settings["priority"]:
            description = connected.get(name, {}).get(
                "description", self.settings["device_names"].get(name, name))
            entries.append({
                "name": name,
                "description": description,
                "connected": name in connected,
                "is_default": name == default,
                "is_streaming": is_streaming_sink(name, description),
            })
        # Sinks that aren't in the priority list (first enumeration after
        # boot, or ephemeral streaming sinks) are still shown.
        for name, sink in connected.items():
            if name not in self.settings["priority"]:
                entries.append({
                    "name": name,
                    "description": sink["description"],
                    "connected": True,
                    "is_default": name == default,
                    "is_streaming": is_streaming_sink(name, sink["description"]),
                })
        streaming_sink = next(
            (s for s in sinks
             if is_streaming_sink(s["name"], s["description"])), None)
        if streaming_sink is not None:
            streaming = {"active": True, "mode": "sink",
                         "sink": streaming_sink["name"]}
        elif await self._get_streaming_capture():
            streaming = {"active": True, "mode": "capture", "sink": None}
        else:
            streaming = {"active": False, "mode": None, "sink": None}
        return {
            "sinks": entries,
            "priority": list(self.settings["priority"]),
            "auto_switch": self.settings["auto_switch"],
            "streaming": streaming,
        }

    # ---------- device monitoring ----------

    async def _monitor(self):
        backoff = 1
        while True:
            try:
                self.subscribe_proc = await asyncio.create_subprocess_exec(
                    "pactl", "subscribe",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=self.pactl_env,
                )
                backoff = 1
                await self._read_events(self.subscribe_proc)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                decky.logger.warning("pactl subscribe failed: %s", e)
            # pactl subscribe exited (e.g. audio server restart), retry.
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _read_events(self, proc):
        pending_topology = False
        pending_server = False
        while True:
            try:
                line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=0.5)
            except asyncio.TimeoutError:
                # Debounce window elapsed with no further events: act now.
                if pending_topology:
                    await self._apply_policy(trigger_switch=True)
                if pending_topology or pending_server:
                    await decky.emit("audio_state_changed", await self._build_state())
                pending_topology = pending_server = False
                continue
            if not line_bytes:
                return
            line = line_bytes.decode(errors="replace")
            if "on sink" in line and ("'new'" in line or "'remove'" in line):
                pending_topology = True
            elif "on source-output" in line and ("'new'" in line or "'remove'" in line):
                # Remote Play hosting starts/stops a monitor capture without
                # any sink change; re-evaluate so the pause takes effect.
                pending_topology = True
            elif "on server" in line:
                # Fires when the default sink changes (e.g. via Steam UI).
                pending_server = True
