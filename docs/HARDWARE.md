# Hardware

Inventario vivo dell'hardware che incontriamo sul bench e dei candidati valutati.
**Aggiornalo nella stessa modifica in cui aggiungi/scopri hardware** (come NOTES.md).

Scopo: separare ciò che è **verificato** (con fonte) da ciò che è **inferito**, così da non
ri-litigare e non prendere decisioni d'acquisto/flashing su assunzioni non controllate.

## Legenda

- ✅ **Confermato** — con fonte esplicita. Le fonti possibili:
  - *osservato* — output di comando visto in sessione (con data);
  - *repo* — file versionato in questo repository;
  - *web* — link esterno (vedi §Fonti web).
- 🔶 **Ipotesi / inferenza** — ragionamento plausibile ma **non** verificato sull'hardware.

> Le osservazioni "osservato 2026-11-03/04" vengono dalla sessione di test Fast Pair handshake;
> i dettagli di comportamento del target sono in `NOTES.md` (sezioni datate 2026-11-04).

---

## 1. Radio BLE primaria — Nordic nRF52 *Connectivity* dongle (in uso)

Il backend BLE del bench (`scan.py`, `scan_live.py`, `client.py`, `emulate.py`), pilotato da
`blatann` → `pc-ble-driver-py` su seriale. **Non** è un adattatore HCI: non compare in `hciconfig`.

- ✅ Presente come `/dev/serial/by-id/usb-Nordic_Semiconductor_nRF52_Connectivity_CEC11B51DD23-if01 → ../../ttyACM0` — *osservato 2026-11-03* (`ls /dev/serial/by-id`).
- ✅ Firmware = **Connectivity** (per pc-ble-driver); è il descrittore USB stesso a dichiararlo, e l'autodetect di `bledev.open_device` cerca `*nRF52*` in by-id — *repo* (`CLAUDE.md`, `bledev/device.py`).
- ✅ Vincolo tooling: `pc-ble-driver-py` → **Python 3.10** (niente wheel per ≥3.11); interprete in `.venv310/` — *repo* (`CLAUDE.md`).
- 🔶 Modello fisico esatto (PCA10059 dongle vs PCA10040/PCA10056 DK): **non** determinabile dalla stringa by-id (`CEC11B51DD23` è un seriale). Inferenza: dongle USB in stile PCA10059, dato che enumera via USB-CDC senza debugger esterno.
- 🔶 È **l'unica** radio nRF disponibile ora → CLI e webui sono mutuamente esclusivi (una seconda `open()` fallisce con `rpc_h5_transport_state`) e il *positive control* Fast Pair resta bloccato finché non c'è un **2° nRF52** (vedi §7) — *repo* (`CLAUDE.md`, `NOTES.md`).

## 2. Adattatore Bluetooth host — BlueZ / `hci0` (Classic + lato BlueZ del fused)

Usato da `scan_classic.py`, dal lato BlueZ di `scan_fused.py` e da `gatt_bluez.py`
(`bluetoothctl`). Radio **distinta** dal dongle nRF.

- ✅ `hci0` — BD Address **`E8:48:B8:C8:20:00`**, `Type: Primary  Bus: USB`, stato `UP RUNNING PSCAN` — *osservato 2026-11-04* (`hciconfig hci0`).
- ✅ `Powered: yes`, e stack **BlueZ `bluetoothctl` 5.87** — *osservato 2026-11-04* (`bluetoothctl show` / `--version`).
- ✅ Sul percorso GATT via BlueZ, su un char FE2C del target: **MTU negoziato 256**, e `gatt.write` fallisce con `org.bluez.Error.NotSupported` su ogni tipo (`command`/`request`/`reliable`) — *osservato 2026-11-04* (vedi §6 e `NOTES.md`).
- 🔶 `Bus: USB` ⇒ probabile **dongle BT USB** (non la radio interna del portatile), ma il modello non è stato identificato.

## 3. Sniffer BLE (in allestimento) — Sonoff ZBDongle-P (CC2652P) + Sniffle

Previsto come sniffer *connection-following* (roadmap multi-radio). **Non** integrato nel bench.

- ✅ Presente come `/dev/serial/by-id/usb-ITead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_b2a1ac84e3a0ef11b6cba1a361ce3355-if00-port0 → ../../ttyUSB0` — *osservato 2026-11-03* (`ls /dev/serial/by-id`).
- ✅ È un **Sonoff ZBDongle-P** = ITead Zigbee 3.0 USB Dongle Plus, radio **CC2652P**, bridge **CP210x** cablato all'UART del CC2652P con ingresso BSL automatico via DTR/RTS (niente saldature/FTDI esterno) — *repo* (`.scratch/sonoff-sniffle-flash/README.md`).
- ✅ Target firmware = **Sniffle** (JelmerT/cc2538-bsl per il flash); hex staged `sniffle_cc1352p1_cc2652p1.hex` / `_1M.hex` — *repo* (stesso README).
- 🔶 Firmware **attualmente** caricato sul ZBDongle-P: incerto. La stringa by-id dice ancora "Zigbee 3.0" ma è solo il descrittore USB del bridge (non cambia col firmware del CC2652P). Lo script di flash è *staged*, non risulta un esito registrato.

## 4. ZigStar LAN GW (CC2652P + ESP32 WT32-ETH01) — coordinator Zigbee (fuori scope BLE)

Presente nel workspace ma **non** parte del bench BLE; lavoro parallelo di ripristino.

- ✅ Gateway LAN con **CC2652P** (modulo **RF-BM-2652P2**) + **ESP32 (WT32-ETH01)**; il CC2652P **attualmente esegue Sniffle** (lo Z-Stack coordinator fu cancellato nel flash Sniffle del 2026-09-15); l'ESP32 è intatto e **non va toccato** — *repo* (`.scratch/zigstar-coordinator-restore/RESUME.md`).
- ✅ Ripristino **BLOCCATO** sulla linea TX host→chip (adapter TXD → modulo DIO12), in pausa dal 2026-10-31; firmware coordinator staged `CC1352P2_CC2652P_launchpad_coordinator_20250321.hex` (sha256 `c2d3a05f…a1ff`, variante **launchpad**) — *repo* (stesso RESUME).
- 🔶 Irrilevante per il Fast Pair/BLE: annotato solo per completezza dell'inventario.

## 5. Adattatore USB-UART — DSD TECH SH-U09C5 (FTDI)

Adattatore USB↔TTL seriale; sul bench serve per il flashing BSL via UART esterno (es. percorso ZigStar).

- ✅ Presente come immagine di riferimento `FTDI/DSD TECH SH-U09C5.png` — *repo*.
- ✅ È un **USB-to-TTL FTDI** (famiglia FT232R), livelli TTL selezionabili tra cui **3.3V e 5V**, ed espone anche **DTR/RTS/RESET** (utili per l'auto-reset/ingresso BSL) — *web* (listing/recensioni, vedi §Fonti).
- 🔶 Variante chip esatta: i listing discordano (**FT232RNL** vs **FT232RL**); da verificare sull'etichetta fisica. Da non confondere con il modello **SH-U09C** (senza "5"), prodotto diverso/più vecchio.
- 🔶 Ruolo sul bench = ponte UART per `cc2538-bsl` (ZigStar): inferito dal contesto, non da una nota esplicita.

## 6. SDR — HackRF One (posseduto)

Software-Defined Radio a livello **RF/PHY**, non un controller BLE. Profilo complementare ai
dongle nRF: serve quando vogliamo scendere **sotto** il controller — osservare/trasmettere sul
canale fisico, non parlare il protocollo GATT.

- ✅ **Posseduto** — dichiarato dal maintainer (2026-11-04).
- ✅ SDR half-duplex, copertura **1 MHz–6 GHz** (quindi include i 2.4 GHz di BT/BLE), fino a ~**20 Msps**; **banda istantanea ~20 MHz** — *web* (specifiche Great Scott Gadgets; vedi §Fonti).
- ✅ Implicazione per BLE: i 20 MHz coprono **~un quarto** della banda BT (2402–2480 = 80 MHz) → vede **un canale advertising alla volta** (37/38/39 distano 24–48 MHz, non entrano insieme); seguire il frequency-hopping di una connessione con un **solo** HackRF è difficile (si suggerisce bladeRF in oversampling o Ubertooth) — *web* (Lexfo, StackOverflow, issue HackRF).
- ✅ Tooling esistente: **`ble_dump`** (GNU Radio) cattura i canali adv 37/38/39 in PCAP; **`ice9-bluetooth-sniffer`** canalizza un flusso I/Q largo (HackRF/bladeRF/USRP) in decoder per-canale e può agganciare connessioni **già in corso** — *web*.
- 🔶 **Ruolo utile sul bench** (inferenza progettuale, da validare quando implementeremo):
  - *Ground-truth on-air*: registrare l'I/Q grezzo di un canale adv per confermare cosa un target trasmette davvero (es. il buco "la Redmi risponde o no?" — anche se per il *connection-following* comodo resta meglio Sniffle/nRF).
  - *Analisi di spettro* 2.4 GHz (chi advertisa, interferenze).
  - *TX/attivo a livello PHY*: trasmettere waveform custom, replay/inject sui canali adv, fuzzing di PHY, esperimenti RF — area "active" della roadmap che i controller nRF **non** possono fare (loro parlano solo link-layer in su).
- 🔶 **NON** è il tool per: sniffing BLE comodo *connection-following* né per fare da controller BLE — lì vincono nRF/Sniffle/Ubertooth. HackRF = livello RF e trasmissione, da tenere per quando un test richiede **il PHY**, non il GATT.

> ⚠️ La trasmissione RF è potente e **regolamentata**: usarla solo nell'ambiente di qualità
> autorizzato/schermato previsto dal progetto.

## 7. Dispositivo target — Redmi Buds 6 Play (Fast Pair)

Accessorio Fast Pair usato come target autorizzato per l'handshake Key-based Pairing (item 2).

- ✅ Indirizzo **pubblico stabile `00:13:D6:99:85:66`** (`addr_type=pub`), nome "Redmi Buds 6 Play", protocollo **Google Fast Pair**, **Model ID `FB9B72`**, OUI vendor **"TII NETWORK TECHNOLOGIES, INC."**, `tx_power=-8` — *osservato 2026-11-03/04* (`list_devices`/`scan_snapshot` via MCP).
- ✅ In **pairing mode** advertisa un **RPA risolvibile (`rnd-r`) che RUOTA a ogni sessione**; RPA osservati: `6B:CF:9D:7F:E3:D7`, `62:18:14:11:6F:29`, `4B:B4:AC:04:9F:5D`, `42:6E:1B:13:CA:23`, `6D:70:9F:DC:EA:2D` — *osservato 2026-11-04*.
- ✅ Il **dongle** vede il frame discoverable solo in modo **intermittente** e solo a corto raggio (agganciato a RSSI ≈ −37); il lato **BlueZ** lo vede più spesso. Il fused `snapshot()` è BlueZ-keyed → un RPA visto solo da BlueZ dà "not currently seen by the dongle" — *osservato 2026-11-04* (`NOTES.md`).
- ✅ GATT Fast Pair (FE2C) completo: `…1233`, **`…1234` Key-based Pairing**, `…1235` Passkey, `…1236` Account Key, `…1237` Additional Data; **`…1234` flags = `write` + `notify`** — *osservato 2026-11-04* (`gatt_bluez`/`fast_pair_profile`).
- ✅ Comportamento handshake: **write 80 B riuscita via dongle** (sia su pubblico sia su RPA discoverable) ma **nessuna KBP notify** (finestra fino a 30 s), idem a payload garbage; **link mai caduto** — *osservato 2026-11-04* (`NOTES.md`). Chiave anti-spoofing `FB9B72` risolta **offline** da `fastpair_keys.yaml` — *repo*.
- 🔶 Causa del silenzio **non ancora distinta** (vedi `NOTES.md`): (a) `provider_addr` incapsulato public-vs-RPA; (b) chiave non valida per il firmware (improbabile: è per-modello, da Google metadata); (c) firmware **hardened** che ignora KBP Case-1 da un central non-Android. Coerente col precedente test identity-disclosure (bond Just Works **rifiutato**) — *repo* (`docs/test-plan-redmi-buds-6.md`).

## 8. Candidati 2° radio nRF52 (ricerca — non posseduti)

Per il *positive control* (`emulate.py --fast-pair-gatt`) serve una **seconda** radio nRF52 col
firmware **Connectivity**. Requisiti: SoC nRF52 supportato da pc-ble-driver **e** un modo per
flashare il firmware **e** enumerazione seriale `/dev/ttyACM*`.

| Candidato | SoC | Bootloader / flashing | Verdetto |
|---|---|---|---|
| **Nordic nRF52840 Dongle (PCA10059)** | nRF52840 | **DFU USB Nordic** integrato → flash diretto con nRF Connect Programmer/`nrfutil`, **no debugger** | ✅ **Consigliato** — board di riferimento, plug-and-play |
| GeeekPi nRF52840 Micro Dev Kit USB Dongle (= **Makerdiary MDK**) | nRF52840 | **UF2** (Adafruit-style), **no** debugger onboard; SWD solo sui pad | 🔶 Scomodo: connectivity hex via UF2 è fragile/rischio brick, o serve SWD |
| **Seeed XIAO BLE nRF52840** | nRF52840 | **UF2** (Adafruit), **no** debugger onboard; SWD da **saldare** sul retro | 🔶 Funziona *in teoria* (USB-CDC), ma flashing non plug-and-play (vedi sotto) |
| Raspberry Pi Pico come **picoprobe/CMSIS-DAP** | RP2040 | n/a (è il *programmatore*) | ✅ **Abilitante** — SWD economico (~5 €) per flashare XIAO/MDK in modo affidabile |

Dettaglio fonti/assunzioni:

- ✅ **PCA10059**: niente debugger onboard, si flasha mettendolo in bootloader Nordic e inviando il pacchetto via `nrfutil`/nRF Connect Programmer — *web* (Zephyr, Nordic DevZone).
- ✅ **GeeekPi = Makerdiary MDK**: GeeekPi è rivenditore, produttore Makerdiary; **bootloader UF2**, no debugger onboard, SWD sui pad di bordo — *web* (wiki Makerdiary, Tindie).
- ✅ **XIAO BLE**: **bootloader Adafruit nRF52 (UF2)**, double-tap reset → drive `XIAO-BOOT`/`XIAO-SENSE`, **no** debug; per SWD servono header saldati o la Expansion Board — *web* (Zephyr).
- 🔶 **XIAO/MDK come connectivity dongle**: inferenza ragionata — l'nRF52840 ha USB nativo, quindi col connectivity firmware **USB-CDC** (quello del PCA10059) enumererebbe come il dongle che serve. **Non validato** su questi board.
- 🔶 **Flashing via UF2** del connectivity hex (conversione `uf2conv`, family `0xADA52840`): *potrebbe* funzionare ma il bootloader Adafruit assume un suo layout di SoftDevice → **rischio brick** (recupero via SWD). Percorso **affidabile = SWD** (J-Link o Pi Pico/CMSIS-DAP) + `nrfjprog`/`openocd` — *web*.
- 🔶 Versione/variante esatta del **connectivity firmware + SoftDevice** da usare su un 52840 non-Nordic: **da determinare** (dal README/release di pc-ble-driver) prima di tentare.

### Alternativa senza hardware nuovo

- 🔶 Ospitare il **Provider Fast Pair su BlueZ/`hci0`** (GATT server FE2C) invece che su un 2° dongle: il Seeker gira sul dongle esistente, il Provider su hci0 → due radio già disponibili. È **codice nuovo** (`emulate.py` oggi è solo-blatann; `fastpair.py` ha però la cripto riusabile senza blatann). Fattibilità plausibile, non implementata.

---

## Fonti web

- Nordic nRF52840 Dongle (PCA10059), flashing senza debugger: <https://docs.zephyrproject.org/2.1.0/boards/arm/nrf52840_pca10059/doc/index.html>, <https://devzone.nordicsemi.com/f/nordic-q-a/90956/flashing-example-hex-file-to-nrf52840-dongle-pca10059-to-achieve-uart-over-ble>
- Makerdiary nRF52840-MDK USB Dongle (bootloader UF2, SWD): <https://wiki.makerdiary.com/nrf52840-mdk-usb-dongle/programming/uf2boot/>, <https://www.tindie.com/products/makerdiary/nrf52840-micro-dev-kit-usb-dongle/>
- Seeed XIAO BLE (bootloader Adafruit UF2, no debug, SWD da saldare): <https://docs.zephyrproject.org/boards/seeed/xiao_ble/doc/index.html>
- Conversione UF2 + SoftDevice (family `0xADA52840`, note di layout): <https://github.com/adafruit/Adafruit_nRF52_Bootloader/releases/tag/softdevice-uf2>
- DSD TECH SH-U09C5 (USB-TTL FTDI, livelli TTL, pin DTR/RTS/RESET; variante chip discordante): <https://www.desertcart.ae/products/157344980-model-sh-u09c5.md>
- HackRF One — banda istantanea ~20 MHz vs 80 MHz della banda BT; sniffing connessioni stabilite (impegnativo) e tooling: <https://blog.lexfo.fr/sniffing-ble-sdr.html>, <https://github.com/drtyhlpr/ble_dump>
