import { mount } from "svelte";
import App from "./App.svelte";
import "./app.css";
import { androidDebuggable } from "./lib/android";
import * as appState from "./lib/app.svelte";
import * as device from "./lib/device";

// test rig (android/test-rig, #33): only in debug builds of the Android app, the GUI's own write paths
if (androidDebuggable())
  (window as unknown as { __forgeRig: unknown }).__forgeRig = { app: appState.app, write: appState.write,
    writeRaw: appState.writeRaw, sendModc: appState.sendModc, copyModc: device.copyModc, modcText: device.modcText };

export default mount(App, { target: document.getElementById("app")! });
