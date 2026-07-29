import {
  DialogButton,
  DropdownItem,
  Field,
  PanelSection,
  PanelSectionRow,
  ToggleField,
} from "@decky/ui";
import {
  addEventListener,
  removeEventListener,
  callable,
  definePlugin,
} from "@decky/api";
import { useEffect, useState } from "react";
import {
  FaArrowDown,
  FaArrowUp,
  FaBroadcastTower,
  FaTimes,
  FaVolumeUp,
} from "react-icons/fa";

interface SinkEntry {
  name: string;
  description: string;
  connected: boolean;
  is_default: boolean;
  is_streaming?: boolean;
}

interface StreamingState {
  active: boolean;
  mode: "sink" | "capture" | null;
  sink: string | null;
}

interface AudioState {
  sinks: SinkEntry[];
  priority: string[];
  auto_switch: boolean;
  streaming?: StreamingState;
}

const getState = callable<[], AudioState>("get_state");
const setDefaultSink = callable<[name: string], AudioState>("set_default_sink");
const setAutoSwitch = callable<[enabled: boolean], AudioState>("set_auto_switch");
const movePriority = callable<[name: string, direction: string], AudioState>("move_priority");
const forgetDevice = callable<[name: string], AudioState>("forget_device");

const iconButtonStyle: React.CSSProperties = {
  minWidth: "40px",
  width: "40px",
  height: "32px",
  padding: "0",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};

function Content() {
  const [state, setState] = useState<AudioState | null>(null);

  useEffect(() => {
    let mounted = true;
    const update = (newState: AudioState) => {
      if (mounted) setState(newState);
    };
    getState().then(update);
    const listener = addEventListener<[state: AudioState]>(
      "audio_state_changed",
      update,
    );
    return () => {
      mounted = false;
      removeEventListener("audio_state_changed", listener);
    };
  }, []);

  if (!state) {
    return (
      <PanelSection>
        <PanelSectionRow>
          <Field label="Loading audio devices..." />
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const connectedSinks = state.sinks.filter((s) => s.connected);
  const currentDefault = state.sinks.find((s) => s.is_default)?.name;
  const orderedKnown = state.priority
    .map((name) => state.sinks.find((s) => s.name === name))
    .filter((s): s is SinkEntry => s !== undefined);
  const streaming = state.streaming;
  const streamingSinkDescription =
    streaming?.sink != null
      ? state.sinks.find((s) => s.name === streaming.sink)?.description ??
        streaming.sink
      : undefined;

  return (
    <>
      <PanelSection title="Output">
        {streaming?.active && (
          <PanelSectionRow>
            <Field
              icon={<FaBroadcastTower />}
              label={
                streaming.mode === "sink"
                  ? `Streaming active — output locked to ${streamingSinkDescription}`
                  : "Streaming active — automatic switching paused"
              }
            />
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <DropdownItem
            label="Current output"
            rgOptions={connectedSinks.map((s) => ({
              data: s.name,
              label: s.description,
            }))}
            selectedOption={currentDefault}
            onChange={(option) => {
              setDefaultSink(option.data as string).then(setState);
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Automatic switching"
            description="Switch to the highest-priority connected device when devices appear or disappear. A manual choice stays until the next device change."
            checked={state.auto_switch}
            onChange={(enabled) => {
              setAutoSwitch(enabled).then(setState);
            }}
          />
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title="Priority (highest first)">
        {orderedKnown.map((sink, idx) => (
          <PanelSectionRow key={sink.name}>
            <Field
              label={sink.description}
              description={sink.connected ? undefined : "Disconnected"}
              bottomSeparator="standard"
            >
              <div style={{ display: "flex", gap: "4px" }}>
                <DialogButton
                  style={iconButtonStyle}
                  disabled={idx === 0}
                  onClick={() => {
                    movePriority(sink.name, "up").then(setState);
                  }}
                >
                  <FaArrowUp />
                </DialogButton>
                <DialogButton
                  style={iconButtonStyle}
                  disabled={idx === orderedKnown.length - 1}
                  onClick={() => {
                    movePriority(sink.name, "down").then(setState);
                  }}
                >
                  <FaArrowDown />
                </DialogButton>
                {!sink.connected && (
                  <DialogButton
                    style={iconButtonStyle}
                    onClick={() => {
                      forgetDevice(sink.name).then(setState);
                    }}
                  >
                    <FaTimes />
                  </DialogButton>
                )}
              </div>
            </Field>
          </PanelSectionRow>
        ))}
      </PanelSection>
    </>
  );
}

export default definePlugin(() => {
  return {
    name: "Audio Output Switcher",
    content: <Content />,
    icon: <FaVolumeUp />,
  };
});
