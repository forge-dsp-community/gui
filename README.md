# Forge DSP GUI NG

Neue Bedienoberfläche für **Forge DSP** und **ETON CORE D8** als Nachfolger der Java-GUI. Eine Web-App, die auf drei Wegen mit dem Gerät spricht:

| Weg | Wie | Voraussetzung |
|---|---|---|
| **Browser, USB** | https://psych0d0g.github.io/forge-dsp-gui-ng/ → Web Serial → USB-COM des Forge (CP2102, 38400 8N1) | Chrome, Edge oder ein Firefox mit Web Serial; nichts zu installieren |
| **Desktop-App, USB** | Dieselbe Oberfläche als Programm: [Releases](https://github.com/psych0d0g/forge-dsp-gui-ng/releases) (Windows msi oder portable exe, Linux deb, rpm, AppImage, Flatpak, macOS dmg). Dateien mit „Speichern unter“, serielle Anschlüsse direkt | nichts weiter; macOS beim ersten Start per Rechtsklick › Öffnen (unsigniert) |
| **Android-App, USB** (#29, in Arbeit) | Für Android-Headunits (7", 1024×600): dieselbe Oberfläche, Startseite *Fahrzeug* mit großen Tasten (Preset, Lautstärke, Mute, Quelle, GALA, Status), Touch-Layout. Der Forge per USB am Headunit, die App startet beim Einstecken. APK aus den [Releases](https://github.com/psych0d0g/forge-dsp-gui-ng/releases) bzw. als Artefakt des Workflows `android` | Android 7 oder neuer mit USB-Host und aktueller System-WebView (Chrome 94+); Installation aus unbekannten Quellen erlauben |
| **ESP32** | Der ESP32 steckt am RS485-Master/Slave-Port, öffnet ein WLAN und liefert die App selbst aus. Die App spricht per WebSocket, der ESP reicht die Zeilen zur UART durch | ESP32 mit 3,3-V-Full-Duplex-Transceiver, siehe [docs/hardware-esp32.md](docs/hardware-esp32.md) |

Die Firmware des Forge (Original BASCOM 1.11.6000 oder der C-Port [forge-dsp-firmware](https://github.com/psych0d0g/forge-dsp-firmware)) bleibt unverändert, die App nutzt das Textprotokoll.

**Stand:** Release 1.4.0, Pre-Release 1.5.0-beta.1 und Hauptzweig. Seiten: Übersicht mit Pegeln aller Ein- und Ausgänge, Startzeiten und Laufzeit (Port-Firmware ab 2.4.1), Mixer als Mischpult (Kanalzüge, Routing,
Ausgangsbank, Profile, „All Over“), Filter & EQ (Frequenz- und Phasengang, Weichenansicht mit elektrischer Summe,
Kanal kopieren, Filter aus REW übernehmen, Weichen-Assistent), Laufzeit, Steuerblöcke (Loudness nennt die nötigen freien Filter und lässt sich nur einschalten, wenn sie frei sind, die Filterseite hält sie frei; darin GALA: geschwindigkeitsabhängige Lautstärke mit Kennlinie, Live-Anzeige und Testgeschwindigkeit, ab Port-Firmware mit #71), Geräteeinstellungen (Ein-/Ausschalten als Ablauf, Clickless, Haupttakt, S/PDIF, ES9038PRO in Worten; nur, was das Gerät hat; Werks-Reset ab Port-Firmware 2.6.2, zeigt und sichert vorher die Kalibrierung, #40), Presets mit Abstimmungsdateien im `.cfg`-Format der Original-GUI und Vergleich
(zwei Presets oder Gerät gegen Datei, Unterschiede in Worten), Biquad-Verteilung, Diagnose mit Log der Speicherkarte, Speicherkarte, Firmware-Update im Browser (auch direkt aus den
Firmware-Releases; in der Android-App aus einer Datei, App dabei im Vordergrund lassen, #41), Einstellungen, Konsole. Abziehen, Ausschalten oder ein Neustart der ESP32-Bridge werden erkannt: die App geht auf offline und verbindet neu (#34). In der Kopfzeile Ausgangspegel, Master-Lautstärke und Rückgängig/Wiederholen; Tastenkürzel nach dem ETON Control
Center (Übersicht mit `?`). Viele Funktionen
nutzen die Erweiterungen der Port-Firmware ab 2.1.0; mit der Original-Firmware 1.11.6000 funktioniert alles, was diese
kann. Zum Ansehen ohne Gerät: `?demo`. Am DEV-Gerät (Forge 96ELN) im Browser geprüft: Verbinden, Pegel, Speicherkarte
und Log, Firmware-Update aus dem Release.

## Aufbau

```
src/lib/protocol/   Textprotokoll: Zeilen bauen (Ziel, Prüfsumme), Antworten parsen, Client mit Warteschlange
src/lib/transport/  Web Serial (USB) und WebSocket (ESP32) hinter einer Schnittstelle
src/lib/device/     Gerätemodell: Zustand, Einlesen, Übernahme von Antworten und Ereignissen, Demo-Gerät
src/lib/app.svelte.ts  Zustand der App (Verbindung, Gerät, Schreiben mit Drosselung)
src/App.svelte      App-Rahmen; src/pages/ Seiten, src/components/ Bedienelemente (Svelte 5)
tests/              Vitest, Antworten wie sie die Firmware sendet; tests/e2e/ Playwright gegen den Simulator
esp32/              ESP-IDF-Firmware der Brücke (PlatformIO), bettet die gebaute App ein
```

Simulator: Mit `?sim` (oder dem Knopf „Simulator“ auf der Startseite) läuft die echte Firmware als WebAssembly im
Browser, ohne Gerät (`src/lib/transport/sim.ts`, gebaut aus `tools/sim` der Firmware). EEPROM und Presets bleiben je
Modell im Browser. Pegelanzeige und Bootloader gibt es dort nicht. Die Ende-zu-Ende-Tests (`tests/e2e/`) bedienen die
gebaute Seite damit Klick für Klick, auch in Handybreite.

Firmware-Update (Seite *Firmware*, nur über USB):
- **Port-Firmware:** Der Forge geht per `system.boot` selbst in den Bootloader. Mit Karte im Forge und Firmware ab #61
  sichert die Firmware vorher Flash, MCU-EEPROM, Preset-EEPROM und DSP-EEPROMs auf die Karte (Platz 1–9, rund 40 s).
  Ohne Karte liest der Bootloader Flash und EEPROM als Dateien aus (rund 1,5 min).
- **Original-Firmware (erstes Update):** Sie kann den Bootloader nicht starten. Die Seite führt durch (ausschalten, Taster
  neben dem USB-Anschluss halten, einschalten), wartet auf den USB-Anschluss und verbindet sich mit dem Bootloader.
- **Forge im Bootloader:** Die App erkennt ihn beim Verbinden, sperrt alle anderen Seiten und liest Flash und EEPROM
  (Sicherung). Aus dem Image im Flash bestimmt sie, was läuft (Original/Port, Modell, PCB 3.0 oder 4.x), und lädt die
  passende Datei des neuesten Releases vor. Hinter einem kleineren Image bleibt der Rest einer älteren Firmware im Flash
  stehen (der Bootloader schreibt nur die Seiten des Images); erkannt wird das Image, das den Flash beginnt.
  Der Bootloader liest das letzte EEPROM-Byte (0xFFF) nicht, die Sicherung hat 4095 Byte; das Original nutzt das Byte nicht.

Die Protokoll-Logik steckt komplett in der Web-App. Der ESP32 ist nur Webserver und Brücke WebSocket ↔ UART, damit es die Logik nur einmal gibt.

## Bauen

```sh
npm ci
npm test            # Protokoll-Tests
npm run check       # Typprüfung
npm run dev         # Entwicklungsserver (USB per Web Serial)
npm run build       # dist/index.html (eine Datei) und dist/index.html.gz für den ESP32

scripts/build-sim.sh tests/sim V40-96ELN   # Simulator: die Firmware als WebAssembly (emsdk, holt forge-dsp-firmware)
npm run e2e         # Playwright: dist/ im Browser gegen den Simulator (vorher build; PW_CHROMIUM=/usr/bin/chromium
                    # nimmt das Chromium des Systems)

cd esp32
pio run             # Firmware, bettet ../dist/index.html.gz ein (ohne Build: Platzhalterseite)
pio run -t upload   # flashen
pio run -t menuconfig   # WLAN-Name/-Passwort, UART-Pins, Baudrate ("Forge bridge")
```

Desktop-App (Tauri, `src-tauri/`): die Web-App im Webview des Systems, dazu in Rust die serielle Schnittstelle
(WebKit hat kein Web Serial), „Speichern unter“ und der Abruf der Firmware-Releases. Bauen: `npx tauri build` (braucht
Rust; unter Linux `libwebkit2gtk-4.1-dev`, `libudev-dev`). Flatpak: `flatpak/`. Die Builds für alle drei Systeme macht
`.github/workflows/desktop.yml` (Tag `v*`:
an das Release, „Run workflow“: nur Artefakte).

Android-App (`android/`, Kotlin): die gebaute Web-App (`dist/`) als Assets in einer WebView
(`https://appassets.androidplatform.net/assets/index.html`, offline). Die USB-Verbindung zum CP2102 läuft über
usb-serial-for-android, in die Seite gereicht als `window.ForgeAndroid` (`src/lib/android.ts`: Zeilen, rohe Bytes für den
Bootloader, Baudrate, „Speichern unter“). Dateiauswahl über die Dateien-App des Systems, Firmware-Releases von der
Pages-Seite. Die Android-Statusleiste ist ausgeblendet (Wischen vom oberen Rand zeigt sie kurz). Bauen: `npm run build`, dann `gradle -p android assembleRelease` (JDK 17, Android-SDK 35, Gradle 8.10.2).
Die Verbindung hält ein Hintergrunddienst (`android/…/service/`), auch ohne geöffnete Oberfläche; er startet beim
Einstecken des Forge (ohne die Oberfläche zu öffnen) und mit dem Headunit. Er lässt auf Wunsch die Android-Lautstärke
auf den Forge-Master (Steuerblock „Lautstärke“) wirken: spiegeln, spiegeln mit festem Android-Pegel oder als
Remote-Lautstärke (Einstellungen › Android-App). Im eigenen ROM, mit der App als privilegierter System-App, gibt es
zusätzlich „Absolut“ (#31): absolute Lautstärke auf dem USB-Audiogerät, Android gibt 0 dB aus und der Index stellt den
Master, nur solange der Forge antwortet (sonst sofort zurück auf normale Lautstärke). Das ROM legt dafür
`android/rom/privapp-permissions-forgedsp.xml` (Berechtigungen) und `android/rom/sysconfig-forgedsp.xml` (Freigabe der
System-API) neben die APK. Andere Apps senden mit der Berechtigung
`io.github.psych0d0g.forgedsp.permission.CONTROL` Zeilen per Broadcast `io.github.psych0d0g.forgedsp.action.SEND`
(Extra `line`, optional `id`, Paket `io.github.psych0d0g.forgedsp`) und bekommen `…action.ANSWER` (Extras `id`,
`lines`, ggf. `error`) und die Ereignisse des Forge als `…action.EVENT` (Extra `line`).
Die Seite *Fahrzeug* und das Touch-Layout gehen auch im Browser: `?skin=touch&page=car`. Die APK baut
`.github/workflows/android.yml` (jeder Push: Artefakt, Tag `v*`: an das Release), signiert mit dem Release-Schlüssel aus
den Secrets `ANDROID_KEYSTORE` (base64), `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`. Ohne Schlüssel (Forks) nimmt
er den Debug-Schlüssel, ein Release bricht dann ab. Version aus dem letzten Tag (`1.2.0`, Zwischenstände
`1.2.0-5-gabc1234`), Versionscode `((x·100+y)·100+z)·1000 + Commits seit dem Tag`. Die App aktualisiert sich selbst aus
dem neuesten GitHub-Release (Einstellungen › App-Update, beim Start höchstens alle 6 Stunden geprüft, Hinweis auf der
Seite *Fahrzeug*): Download der APK, Abgleich mit `SHA256SUMS` des Releases (beide Workflows führen sie zusammen), Installation über den PackageInstaller. Beim ersten Mal
fragt Android („Unbekannte Apps installieren“), ab Android 12 danach ohne Rückfrage.

Der ESP32 öffnet standardmäßig das WLAN `forge-dsp` (Passwort `forge-dsp-setup`, bitte in menuconfig ändern), die App liegt unter http://192.168.4.1/. Optional tritt er zusätzlich einem bestehenden WLAN bei.

## Protokoll in Kürze

```
get.1 system.ping              ->  <.1 system.ping="pong"
set.1 gain.out.1=200;53        ->  *.1 gain.out.1 ok        (;53 = Prüfsumme, optional)
set.1 system.startup.9="x"     ->  !.1 system.startup.9 command out of range
                                   #.1 preset.loaded=2      (Ereignis, ohne Anfrage)
```

Ziel nach dem Punkt: `1`–`6` Gerät in der Kette, `a` alle, `m`/`s` Master/Slave-Port, `t` dieses Gerät. Prüfsumme: Bytesumme mod 256 ab dem Registernamen bis vor das `;`, zwei Hex-Ziffern.
