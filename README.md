# intotheblue — banco di collaudo BLE (dongle nRF52 + blatann)

Piccola bench per **sviluppare e collaudare dispositivi Bluetooth LE** usando un
dongle Nordic **nRF52 con firmware Connectivity** pilotato via `blatann`.

Il dongle è un controller BLE completo: può fare da **central** (scanner / client
GATT) e da **peripheral** (advertising / GATT server), quindi copre entrambi i lati
di un test — cosa che le librerie basate sullo stack host (es. Bleak) non fanno.

## Requisiti hardware

Un dongle/board Nordic nRF52 flashato con il firmware **Connectivity** (SoftDevice
serializzata). Collegato, si presenta come `nRF52 Connectivity`
(VID `1915` / PID `c00a`) su una porta `/dev/ttyACMx`.

## Setup ambiente

> **Vincolo importante:** serve **Python 3.10**. `pc-ble-driver-py` (la libreria
> nativa Nordic sotto blatann) non pubblica wheel per Python ≥ 3.11, quindi con il
> Python di sistema (3.13/3.14) l'installazione fallisce.

```bash
# Crea un venv Python 3.10 (uv scarica l'interprete standalone)
uv venv --python 3.10 .venv310

# Installa le dipendenze
.venv310/bin/python -m pip install -r requirements.txt
```

## Uso

Tutti gli script autorilevano la porta del dongle da `/dev/serial/by-id`
(fallback `/dev/ttyACM0`); si può forzare con `--port`.

### Scansione / inventario
Rileva i device nel raggio, risolve il produttore dal Company Identifier ed esporta
in `csv/output_<epoch>.csv`.

```bash
.venv310/bin/python scan.py                 # autorileva il dongle
.venv310/bin/python scan.py --timeout 8
```

### Central / client GATT
Si connette a un target (per nome o indirizzo), scopre servizi e caratteristiche e
legge quelle leggibili. Generico: funziona con qualunque device.

```bash
.venv310/bin/python client.py --name "MyDevice"
.venv310/bin/python client.py --address AA:BB:CC:DD:EE:FF --subscribe
```

### Peripheral / emulazione device
Il dongle si annuncia e espone un servizio custom (RX write, TX notify con echo,
contatore). Utile per testare app o un secondo central.

```bash
.venv310/bin/python emulate.py --name "TestDevice"
```

Per verificarlo: connettiti dal telefono con l'app **nRF Connect**, oppure con
`client.py` da un secondo dongle.

## Struttura

```
scan.py         inventario BLE -> CSV
client.py       central: connessione + esplorazione GATT
emulate.py      peripheral: advertising + GATT server
bledev/         utility condivise
  device.py       apertura dongle + autorilevamento porta
  manufacturers.py risoluzione Company Identifier da manufact.yaml
manufact.yaml   database Company Identifiers (Bluetooth SIG)
```

## Strade complementari (quando NON usare questo dongle)

- **nRF Sniffer** — riflashando il dongle con il firmware *nRF Sniffer for
  Bluetooth LE* diventa uno sniffer passivo per **Wireshark**, ideale per debuggare
  il traffico reale tra due device. È un firmware diverso dal Connectivity: dopo
  averlo riflashato il dongle non funziona più con blatann finché non si ripristina
  il Connectivity.
- **Bleak** — libreria Python cross-platform che usa lo stack BLE dell'host (BlueZ
  su Linux). Solo ruolo **central**, ma non richiede hardware dedicato: comoda come
  fallback per test da lato host quando il dongle non è disponibile.
