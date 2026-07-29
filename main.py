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


def choose_sink(priority, connected):
    """Return the highest-priority sink name that is currently connected."""
    for name in priority:
        if name in connected:
            return name
    return None


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
        await self._set_default_sink(name)
        return await self._build_state()

    async def set_auto_switch(self, enabled: bool):
        self.settings["auto_switch"] = bool(enabled)
        save_settings(self.settings)
        await self._apply_policy(trigger_switch=True)
        return await self._build_state()

    async def move_priority(self, name: str, direction: str):
        priority = self.settings["priority"]
        if name in priority:
            idx = priority.index(name)
            new_idx = idx - 1 if direction == "up" else idx + 1
            if 0 <= new_idx < len(priority):
                priority[idx], priority[new_idx] = priority[new_idx], priority[idx]
                save_settings(self.settings)
                # A reorder is an explicit user action, apply it right away.
                await self._apply_policy(trigger_switch=True)
        return await self._build_state()

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
        sinks, default = await self._get_sinks()
        connected = {s["name"] for s in sinks}
        changed = False
        for sink in sinks:
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
        if trigger_switch and self.settings["auto_switch"]:
            target = choose_sink(self.settings["priority"], connected)
            if target and target != default:
                decky.logger.info("Auto-switching default sink to %s", target)
                await self._set_default_sink(target)

    async def _build_state(self):
        sinks, default = await self._get_sinks()
        connected = {s["name"]: s for s in sinks}
        entries = []
        for name in self.settings["priority"]:
            entries.append({
                "name": name,
                "description": connected.get(name, {}).get(
                    "description", self.settings["device_names"].get(name, name)),
                "connected": name in connected,
                "is_default": name == default,
            })
        # Sinks that somehow aren't in the priority list yet (first
        # enumeration after boot) are still shown.
        for name, sink in connected.items():
            if name not in self.settings["priority"]:
                entries.append({
                    "name": name,
                    "description": sink["description"],
                    "connected": True,
                    "is_default": name == default,
                })
        return {
            "sinks": entries,
            "priority": list(self.settings["priority"]),
            "auto_switch": self.settings["auto_switch"],
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
            elif "on server" in line:
                # Fires when the default sink changes (e.g. via Steam UI).
                pending_server = True
