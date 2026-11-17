#!/bin/bash
# Google emulator for the test rig (#33): AVD forge-test-<api> (1024x600, density 160 like the Avant 4),
# xhci controller and QMP socket, so rig.py can plug the DEV Forge's CP2102 in and out (QEMU usb-host).
#   emulator.sh start [30|35]     start (window on the desktop)
#   emulator.sh setup [30|35]     once per fresh AVD: USB host feature into the system image (writable system)
#   emulator.sh stop
# Only the SDK adb is used: two adb versions (SDK and /usr/bin) restart each other's server and leave the
# emulator "offline".
set -e
API=${2:-30}
SDK=$HOME/Android/Sdk
ADB=$SDK/platform-tools/adb
QMP=${QMP_SOCK:-/tmp/claude-1000/emu-qmp.sock}
SERIAL=emulator-5554
SHIM=$(mktemp -d)
ln -sf "$ADB" "$SHIM/adb"
export ANDROID_SDK_ROOT=$SDK ANDROID_HOME=$SDK PATH="$SHIM:$SDK/platform-tools:/usr/local/bin:/bin"

booted() { [ "$($ADB -s $SERIAL shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ]; }
wait_boot() {
  $ADB -s $SERIAL wait-for-device
  for _ in $(seq 1 120); do booted && return 0; sleep 2; done
  echo "no boot" >&2; return 1
}

case "$1" in
start)
  rm -f "$QMP"
  setsid nohup "$SDK/emulator/emulator" -avd "forge-test-$API" -no-snapshot -no-boot-anim -no-audio \
    -gpu swiftshader_indirect -writable-system \
    -qemu -device qemu-xhci,id=xhci -qmp "unix:$QMP,server=on,wait=off" \
    > "/tmp/claude-1000/emulator-$API.log" 2>&1 < /dev/null &
  wait_boot && echo "booted: $SERIAL"
  ;;
setup)
  wait_boot
  $ADB -s $SERIAL root; sleep 2
  if ! $ADB -s $SERIAL shell pm list features | grep -q usb.host; then
    $ADB -s $SERIAL remount || true            # first time: disables verity, asks for a reboot
    $ADB -s $SERIAL reboot; wait_boot
    $ADB -s $SERIAL root; sleep 2; $ADB -s $SERIAL remount
    f=$(mktemp)
    printf '<?xml version="1.0" encoding="utf-8"?>\n<permissions>\n    <feature name="android.hardware.usb.host" />\n</permissions>\n' > "$f"
    $ADB -s $SERIAL push "$f" /system/etc/permissions/android.hardware.usb.host.xml
    $ADB -s $SERIAL shell sync
    $ADB -s $SERIAL reboot; wait_boot
  fi
  $ADB -s $SERIAL shell pm list features | grep usb.host
  ;;
stop)
  $ADB -s $SERIAL reboot -p 2>/dev/null || $ADB -s $SERIAL emu kill
  ;;
*) echo "usage: $0 start|setup|stop [api]"; exit 1 ;;
esac
