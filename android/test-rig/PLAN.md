# Test- und Stressplan: Android-App mit dem DEV-Forge (#33)

Ziel: die App so belasten, wie sie im Auto arbeitet, mit dem echten Forge, aber reproduzierbar und automatisch.
Die Headunit ist nie Testziel. Alles läuft gegen die VM.

## Aufbau

```
PC (Workstation)
 ├─ Cuttlefish-VM avant4_virt (unser LineageOS 24, Mainline-Kernel 7.4, arm64 unter QEMU)
 │    ├─ Forge-App (Debug-Build aus der CI, Artefakt android-debug)
 │    └─ USB: CP2102 des DEV-Forge, per QMP device_add usb-host durchgereicht (an-/absteckbar)
 ├─ Bildschirm: scrcpy-Fenster (1024×600, Dichte 160 wie die Avant 4) oder https://localhost:8443
 ├─ Forge-USB-Audio (XMOS) bleibt am PC: Testtöne und Rauschen per pw-play
 └─ Rig (android/test-rig, Python): adb, QMP, uiautomator, DevTools der WebView, logcat
```

Kanäle, über die der Rig prüft:
- **Dienst:** `am broadcast …action.SEND` an den `CommandReceiver`. Debug-Builds schreiben die Antwort ins Log
  (`ForgeCmd`), so kann der Rig jede Zeile über denselben Weg lesen, den Fremd-Apps nutzen.
- **GUI:** Chrome DevTools der WebView (Debug-Build). JavaScript in der Seite: `window.ForgeAndroid`
  (Einstellungen, Status) und Bedienung der Seiten wie ein Mensch.
- **System:** `uiautomator` (USB-Dialoge), `input keyevent` (Lautstärketasten), `dumpsys` (Speicher, Dienste,
  Audio), logcat (Abstürze, ANR).
- **Signal:** Ton vom PC über USB-Audio in den Forge, Pegel je Ausgang mit `get.1 system.vu.out` über die App.

## Testkatalog

| # | Test | Prüft | Bestanden wenn |
|---|---|---|---|
| T01 | Erstes Anstecken | USB-Dialog, Dienst startet, Handshake | Dienst ONLINE in < 20 s, Master gelesen |
| T02 | Hotplug-Zyklen | Abziehen/Anstecken in zufälligen Abständen (0,3–10 s), auch während Anfragen | jedes Mal wieder ONLINE, kein Absturz, kein zweiter Dienst, keine hängende Anfrage |
| T03 | Befehlsdurchsatz | 1000 gemischte get/set über den CommandReceiver, jede Antwort gegen die Anfrage | 0 vertauschte Antworten, 0 verlorene, Laufzeiten (Median/99 %) |
| T04 | Lautstärke | Modi MIRROR, MIRROR_FIXED, REMOTE: Tastenstöße (`VOLUME_UP/DOWN`, 20 je s), `media volume` | Forge-Master folgt, Endwert stimmt, Pegel im DSP ändert sich wie gerechnet |
| T05 | Gleichzeitigkeit | GUI schreibt (DevTools), CommandReceiver fragt, Tasten laufen, Keepalive läuft – alles zugleich | Endzustand = Modell, keine vertauschten Antworten, keine Fehler im Forge |
| T06 | Speichern über die GUI | MODC- und Filteränderungen in der GUI, dann Forge-Neustart und App-Neustart | alle Werte nach dem Neustart wie geschrieben (offene Frage aus dem Fahrzeug) |
| T07 | Lebenszyklus | App beenden (`force-stop`), Prozess töten, Dienst neu, Bildschirm aus, Doze | Dienst kommt wieder, verbindet ohne Dialog, Lautstärke weiter |
| T08 | Forge-Neustart | Forge startet neu, während die App verbunden ist (CP2102 kurz an den PC, `c45b3 start`) | App merkt es, verbindet neu |
| T09 | Firmware über die App | Update auf 2.6.1 über die Firmware-Seite der App | Version danach 2.6.1, Abstimmung erhalten |
| T10 | Dauerlauf | Mischung aus T02–T05 über Stunden | Speicher und Threads stabil, keine Fehler im Log, Forge ohne Fehlerzähler |

Ergebnisse schreibt der Rig nach `test-rig/results/<Datum>/` (Bericht, logcat-Auszug, Messwerte).
