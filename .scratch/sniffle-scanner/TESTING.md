# SniffleScanner — test rimasti (da fare in una nuova sessione)

Stato: Phase 1 (`sniff.py` + `sniffle/` + CLI activity `sniff`) **costruita e validata con uno
smoke non-curses su hardware reale** (dongle Sonoff su `/dev/ttyUSB0`, 10 device decodificati,
pause/resume, CSV, close→reopen pulito). **Non committata.** Mancano: validazione della UI
curses dal maintainer, e le Phase 2/3.

Contesto a freddo: leggere prima la sezione NOTES.md "Resume here — 2026-11-04, SniffleScanner
Phase 1" e `.scratch/sniffle-scanner/spec.md`. Il dongle Sonoff è un device seriale separato
(`/dev/ttyUSB*`, by-id `*ITead_Sonoff*`) **indipendente dall'nRF52** (`/dev/ttyACM*`): si può
sniffare mentre il CLI/webui tiene aperto l'nRF52, non c'è contesa.

## Pre-check (30 s)
```bash
cd /opt/repo/intotheblue
ls /dev/serial/by-id/ | grep -i itead          # deve esserci il link ITead_Sonoff -> ttyUSB*
.venv310/bin/python -c "from sniffle.sniffle_hw import SniffleHW; print('import ok')"
```
Se il dongle non c'è: VMware → Removable Devices → Silicon Labs CP210x → Connect.

## Test 1 — smoke non-curses (ripetibile, veloce) ✅ già passato 2026-11-04
Riconferma rapida che il backend apre/decodifica/chiude:
```bash
timeout 40 .venv310/bin/python .scratch/sniffle-scanner/smoke_phase1.py 8
```
Atteso: N device con Addr (pub/rnd-s/rnd-r/rnd-n), Ch=37, PHY=1M, Conn, Manufacturer/Type
valorizzati; riga `CSV -> csv/sniffle_*.csv`; `closed OK; is_scanning now: False`.

## Test 2 — UI curses via pty (IL test di accettazione Phase 1) ⬅️ DA FARE
L'harness pty **non è committato**: ricrearlo sotto `.scratch/sniffle-scanner/`. Deve lanciare
`sniff.run()` dentro uno pseudo-terminale, iniettare tasti e asserire su output/CSV. Note dure
(da CLAUDE.md): le frecce in application-cursor-mode sono `\x1bOB`/`\x1bOA` (non `\x1b[B`);
`Ctrl+S` arriva all'app perché `IXON` è disabilitato; `Ctrl+P`=pausa, `Q`=esci.

Scheletro harness (ricreare `pty_sniff.py`):
```python
import os, pty, time, select
pid, fd = pty.fork()
if pid == 0:
    os.execv("/opt/repo/intotheblue/.venv310/bin/python",
             ["python", "/opt/repo/intotheblue/sniff.py"])  # curses prende il pty
# genitore: leggere ~6-8s per popolare la tabella, poi:
def send(b): os.write(fd, b)
#  ... drena output con select() mentre aspetti ...
send(b"\x1bOB")      # freccia giù: la selezione si sposta (non deve saltare: ordine di scoperta)
send(b"\x10")        # Ctrl+P: lo status bar passa SCANNING -> PAUSED
send(b"\x10")        # Ctrl+P: torna SCANNING
send(b"\x13")        # Ctrl+S: salva CSV -> compare "saved ..." e nasce csv/sniffle_*.csv
send(b"\r")          # Enter su una riga: apre il menu per-device (Phase 1: "Show observed details")
send(b"q")           # esce dal menu / poi Q per uscire dalla tabella
send(b"Q")
```
Asserzioni:
- La tabella si popola e le **righe restano in ordine di scoperta** (nessun re-sort live).
- Le colonne **Ch** e **PHY** sono valorizzate (37 / 1M per legacy adv).
- `Ctrl+P` alterna SCANNING/PAUSED nello status bar; in PAUSED i conteggi non salgono.
- `Ctrl+S` crea un `csv/sniffle_*.csv` con le stesse righe a schermo (ordine di scoperta).
- `Enter` su una riga apre il menu per-device e "Show observed details" stampa i campi passivi.
- `Q` chiude pulito (nessun crash curses, nessun output sporco sullo schermo; i warning
  Sniffle devono finire in `sniff.log`, non a schermo).

In alternativa (manuale, senza pty): lanciare `.venv310/bin/python intotheblue.py --cli`,
scegliere l'attività **"Live BLE sniff — CC2652P/Sonoff (Sniffle)"**, e verificare a occhio gli
stessi punti. (`--webui` NON serve: la webui apre l'nRF52, non il Sonoff — è Phase 3.)

## Test 3 — parità con scan_live (stesso ambiente) ⬅️ DA FARE
Far girare per ~30 s `sniff.py` e, separatamente, `scan_live.py` (nRF52) nello stesso posto e
confrontare: il sniffer deve vedere **almeno** gli stessi advertiser legacy (può vederne di più;
vede traffico che il controller host non surfaccia). Differenze attese: il sniffer mostra Ch/PHY
e non fa l'enrichment GATT/servizio-nome (niente SIG lookup lato Sniffle in Phase 1).

## Test 4 — active scan (opzionale, trasmette) ⬅️ DA FARE
```bash
.venv310/bin/python sniff.py --active
```
Atteso: più **nomi** compaiono (gli scan-response arrivano perché il sniffer manda SCAN_REQ).
Nota boundary: `--active` **trasmette** (SCAN_REQ); il default passivo no.

## Dopo che il maintainer valida
- Aggiornare NOTES.md: promuovere Phase 1 a HARDWARE-VALIDATED.
- Commit (branch `devel`) solo su richiesta esplicita del maintainer.
- Poi scrivere/riprendere gli issue **03 (Phase 2 conn-follow)** e **04 (Phase 3 select/act +
  web/MCP + persistenza store nell'engine)** — vedi `spec.md` → Phasing. I file 03/04 non
  esistono ancora in `issues/`, vanno creati dallo spec.

## Cleanup di fine sessione
`smoke_phase1.py` e un eventuale `pty_sniff.py` sono harness di scratch (non committare). I
`csv/sniffle_*.csv` e `sniff.log` sono git-ignored.
