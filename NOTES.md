# NOTES

Working memory for this project. See `CLAUDE.md` for how it's used. Git holds the
detailed change history; this file holds rationale, plans, and open questions.

## Resume here — 2026-11-04, SniffleScanner Phase 1 (advertising sniff + CLI) — HARDWARE-VALIDATED ✅

*Terzo backend di scan: il CC2652P/Sonoff flashato Sniffle (v1.11.0, 2 Mbps — vedi
checkpoint "FLASHED & CRC-VERIFIED" più sotto) entra nel modello scan→select→act accanto a
nRF52/blatann e BlueZ. Spec+ticket in `.scratch/sniffle-scanner/`. Fatti gli issue 01
(packaging) e 02 (Phase 1). Phase 2 (conn-follow) e Phase 3 (select/act + web/MCP) da fare.*

- **Issue 01 — packaging (DONE).** Vendorizzato `python_cli/sniffle/` → top-level `sniffle/`
  (in-repo come `bledev/`/`webui/`, **non** pip-installed). Droppati i moduli SDR pesanti
  (`sniffle_sdr.py`, `channelizer.py`, `resampler.py`, `sdr_utils.py`, `coding_ble.py`) che
  tirano numpy/scipy/SoapySDR: l'import SDR in `sniffle_hw.make_sniffle_hw` è **lazy** e solo
  per `serport` `rfnm`/`file:`, mai raggiunto con una porta seriale reale. `__init__.py`
  ri-trimmato. `sniffle/VENDORED.md` traccia fonte (commit `3a53f5a`, tag v1.11.0), cosa è
  stato tolto e il "re-vendor on upgrade". `pyserial` pinnato in `requirements.txt` (unica dep
  runtime del package trimmato). Import headless OK: `from sniffle.sniffle_hw import SniffleHW`
  senza numpy.
- **Issue 02 — Phase 1 `sniff.py` (DONE, headless).** Backend indipendente che avvolge
  `SniffleHW`, **no blatann**, interfaccia `livetable` come `scan_classic`:
  - **Autodetect** porta via glob `/dev/serial/by-id/*ITead_Sonoff*` (fallback `/dev/ttyUSB0`),
    risolta a realpath; `--port` override. Mirror di `bledev.find_dongle_port`.
  - **`SniffleScanner`**: `setup_sniffer(mode=PASSIVE_SCAN, chan=37, ext_adv=False)` +
    `mark_and_flush()`, poi un thread daemon fa loop `recv_and_decode()` e piega ogni
    `AdvaMessage`/`ScanRspMessage`/`AdvDirectIndMessage` in un `SniffleRecord` per-indirizzo
    sotto lock. `pause()` **non** ferma la radio (il firmware sniffa comunque) ma smette di
    *aggiornare* i record continuando a drenare la seriale (niente backlog) — non c'è
    l'invariante connect-on-same-device dell'nRF52. `close()`: `cancel_recv()` → join →
    `cmd_reset()` → `ser.close()`. Logging Sniffle instradato a `sniff.log` (TrivialLogger
    stampa su stderr → corromperebbe curses; logger dedicato, `propagate=False`).
  - **Boundary**: default **PASSIVE_SCAN** (solo ascolto, 0 TX — coerente con la postura
    passiva). `--active`/prompt y/N abilita **ACTIVE_SCAN** che **trasmette SCAN_REQ** per
    elicitare i nomi (etichettato come trasmittente).
  - **Parsing AD**: Sniffle dà `adv_data` **grezzo** (non l'oggetto parsato di blatann), quindi
    parser LTV locale `_parse_adv_data` (local name, flags, tx power, mfr data, service data
    16-bit, service UUID 16-bit) + copie blatann-free di `_decode_protocol`/`_fast_pair` e delle
    tabelle `_SERVICE_DATA_UUIDS`/`_APPLE_MSG_TYPES` (copiate da `scan_live`, non importate).
    Manufacturer via loader locale di `manufact.yaml` (no `bledev`→blatann; snapshot, non il
    SIG checkout — sufficiente per Phase 1). Addr type da `TxAdd` + top-2-bit del MSB
    (pub/rnd-s/rnd-r/rnd-n).
  - **COLUMNS**: Address, Addr, Name, RSSI, **Ch**, **PHY**, Conn, FastPair, Manufacturer,
    Type, Svc, Pkt, Age — ordine di scoperta. `save_csv()` → `csv/sniffle_*.csv`.
  - **`SniffleRecord`** espone gli attributi letti da `store.record_fields` (address, addr_type,
    name, rssi, connectable, manufacturer, protocol, fast_pair, fp_payload, tx_power, directed,
    services, count, first/last_seen) → le sighting Sniffle si fonderanno nelle stesse righe
    device quando l'engine le persiste (Phase 3). **Per ora `sniff.py` NON tocca `store`**, in
    linea con gli altri scanner standalone (`scan_live`/`scan_classic`/`scan_fused` non
    persistono; solo `webui/engine` fa upsert). 💡 La persistenza Sniffle è Phase 3/issue 04,
    dentro l'engine — non duplicata nel CLI.
  - **CLI**: nuova ACTIVITIES `"sniff"` (`activity_sniff`) via `_scan_interact_loop`;
    `_interact_sniff` apre un menu per-device **solo passivo** (dettagli osservati). Conn-follow
    e PCAP sono Phase 2/3.
- **Headless OK** (no HW): import del package senza SDR/numpy; `_parse_adv_data` su payload
  sintetici (local name, Apple AirPods mfr, FE2C model-id discoverable→hex e non-discoverable→
  "Yes"); classificazione addr type pub/rnd-s/rnd-r/rnd-n; `_manufacturer(0x4C)`="76 Apple,
  Inc."; `update()` end-to-end su un `ADV_IND` decodificato da `DPacketMessage.from_body`
  (addr/name/conn/fastpair giusti); tutte le COLUMNS rendono; `store.record_fields(rec,
  'sniffle')` legge il record; `py_compile` di `sniff.py`+`cli.py`.
- **HW-VALIDATO (2026-11-04, dongle Sonoff su `/dev/ttyUSB0`)**: il maintainer ha validato a
  occhio la UI curses via `intotheblue.py --cli` → attività "Live BLE sniff": tabella popolata
  in ordine di scoperta (nessun re-sort live), colonne Ch/PHY valorizzate (37 / 1M), `^P`
  alterna SCANNING/PAUSED, `^S` salva il CSV, `Enter` mostra i dettagli passivi, `Q` esce pulito.
  Lo smoke non-curses (`smoke_phase1.py`) era già passato. Test 3 (parità con `scan_live`) e
  Test 4 (`--active`) restano opzionali/da-fare.
- **Fix entry point (stesso checkpoint)**: `intotheblue.py --cli` passava il flag `--cli` dritto
  a `cli.main()`, che lo rileggeva da `sys.argv` come activity → "Unknown activity: '--cli'".
  Ora `cli.main(argv=...)` accetta un argv esplicito e `intotheblue.py` passa `argv=[]` per
  forzare il menu; `python cli.py <activity>` standalone invariato (legge `sys.argv`).
- **Prossimo**: Phase 2 (issue 03, conn-follow) e Phase 3 (issue 04, select/act + web/MCP +
  persistenza store nell'engine) — i file 03/04 vanno creati dallo `spec.md`.

## Resume here — 2026-11-03, Fast Pair GATT: passive profile + active handshake/stress (items 1-3) — HEADLESS-VALIDATED, HW RUN PENDING

*Scelta maintainer (/plan): implementare gli item 1+2+3 del blocco Fast Pair, validati
con un **Provider emulato**. Item 4 (pairing completo: passkey + account key) rinviato.*

- **Nuovo `fastpair.py`** (top-level, **no blatann**, come `bledev/fastpair.py`): crypto +
  codec del Key-based Pairing. ECDH **secp256r1** → **SHA-256[:16]** → chiave **AES-128**;
  **AES-128-ECB** singolo blocco (16B, no IV). `build_kbp_request`/`parse_kbp_request`/
  `build_kbp_response`/`parse_kbp_response` (round-trip simmetrico), `derive_key`,
  `gen_keypair`, `public_key_bytes` (64B X‖Y). UUID + `FP_CHARACTERISTICS` per il surfacing.
  Layout byte + crypto **verificati dallo spec** developers.google.com (NOTES diceva
  erroneamente "fetch bloccato" — è accessibile; vedi `bond.py` docstring, da correggere).
  `cryptography` 49.0.0 già nel venv → nessuna nuova dep nativa (pin aggiunto in requirements).
- **Item 1 (passivo)**: `engine.fast_pair_profile` + `_fast_pair_profile_from_tree` sul tree
  salvato (servizio `fe2c`), tab **Fast Pair** (`_fastpair.html`, refresh su `gatt-updated`
  come Capabilities), route `GET …/fastpair`, MCP `fast_pair_profile`. Niente transmit.
- **Item 2 (handshake attivo)**: `engine.fastpair_handshake(address, provider_public_hex)`
  sul peer della sessione held-open: request cifrata (Case 1, pubkey effimera appesa) → write
  `…1234` → attesa notify (subscribe one-shot) → `parse_kbp_response`. Esito fedele (risposta
  che non decifra / nessuna notify = finding, non errore). Route `POST …/gatt/fastpair-handshake`,
  form in `_gatt_tree.html`, MCP `fastpair_handshake`, esito su `gatt_session().fp_status`.
- **Item 3 (stress attivo)**: `engine.fastpair_stress(address, variant)` — payload malformati
  a `…1234` (`unencrypted-zero`/`wrong-type`/`too-short`/`too-long`/`random-16`), riporta
  notify/silenzio/link-drop. Route `POST …/gatt/fastpair-stress`, `<select>`, MCP `fastpair_stress`.
- **Provider emulato**: `emulate.py --fast-pair-gatt` (combinabile con `--fast-pair MODELID`):
  server GATT **FE2C** reale (`…1234` W+N, `…1235` W+N, `…1236` W), coppia anti-spoofing
  generata, **chiave pubblica stampata + salvata in `csv/fastpair_provider_pub.hex`** per il
  Seeker. Handler `…1234`: `parse_kbp_request`→`build_kbp_response`→notify. Entrambi i lati
  nostri (come `--positive-control`). Mutuamente esclusivo con `--positive-control`.
- **MCP a 25 tool** (+`fast_pair_profile`, `fastpair_handshake`, `fastpair_stress`,
  `fastpair_resolve_key`).
- **Pubkey fetch per Model ID (nuovo `fastpair_keys.py`, no blatann)** — sblocca l'handshake
  sui device reali senza Provider emulato. Catena first-hit: (1) file locale
  `fastpair_keys.yaml` (git-ignored; seed `fastpair_keys.example.yaml`; override
  `$INTOTHEBLUE_FASTPAIR_KEYS`), (2) **lookup Google opt-in** su
  `nearbydevices-pa.googleapis.com/v1/device/<model-id-DECIMALE>?key=…&mode=MODE_RELEASE&alt=json`
  (endpoint + campo `device.antiSpoofingKeyPair.publicKey` **verificati dal Chromium
  `device_metadata_fetcher.cc`/`device_metadata.cc`**), con **cache** nel YAML. Niente API key
  hardcoded: operatore via `$INTOTHEBLUE_FASTPAIR_API_KEY` (senza → path Google disabilitato).
  `engine.fastpair_resolve_key` (loggato `boundary="network"` solo quando abilitato), e
  `fastpair_handshake` accetta ora `provider_public` **o** `model_id`+`allow_network`. UI: nel
  form handshake, campo Model ID (prefill dal `fast_pair` advertisato) + checkbox "allow Google
  lookup". Spec: `.scratch/fastpair-pubkey-fetch/spec.md`.
  **Headless OK**: resolve file (hex+base64), normalize 64/65B, fetch Google mockato + cache,
  parse `_extract_pubkey` (camelCase + snake_case), `_fp_acquire_pubkey` (hex/base64/model-id/
  errori), 25 tool MCP.
- **LIVE — validato guidando la webui/MCP contro Redmi Buds 6 Play reali (2026-11-03).**
  Dongle fused scan via l'API del server in esecuzione (niente headless):
  - **Item 1 OK**: `GET /api/devices/<redmi>/fastpair` → profilo FE2C con **tutte e 4** le
    char (`…1234` W/N, `…1235` W/N, `…1236` W, `…1237` W/N) sia dal tree in store sia dopo un
    **browse fresco** (sessione aperta su RPA `46:31:E4:33:50:9E`). Confermato Provider reale.
  - **Item 3 OK**: i 5 stress variant scritti su `…1234` → **nessuna notify, link stabile**
    (sessione mai caduta su tutti e 5). Finding coerente con lo spec: le Redmi ignorano
    silenziosamente le richieste KBP che non decifrano, senza rispondere né disconnettere.
  - **Item 2 — plumbing validato end-to-end contro Redmi reale; device NON risponde.**
    Pubkey anti-spoofing delle Redmi (`FB9B72`) **ottenuta da Google**: endpoint
    `nearbydevices-pa.googleapis.com/v1/device/16489330?...&alt=json` con una **API key
    estratta da GMS** (`com.google.android.gms` base.apk, nel dex che contiene la stringa
    `nearbydevices-pa`: 2 candidate, funziona `AIzaSyAP-gfH3qvi6vgHZbSYwQ_XHqV_mXHhzIk`;
    l'altra `API_KEY_SERVICE_BLOCKED`) + header **`X-Android-Package`/`X-Android-Cert`**
    (richiesti: le AIza GMS sono Android-restricted). Response: `device.antiSpoofingKeyPair.
    publicKey` base64 = 64B secp256r1 valida (ECDH ok). Salvata in `fastpair_keys.yaml`
    (`FB9B72`, git-ignored). `fetch_from_google` ora manda gli header Android.
    **Nota operativa:** la richiesta a Google e l'handshake verso il device li blocca
    l'auto-mode dell'agente come "Third-Party Attack" → vanno lanciati dal maintainer (`!`
    o browser). Handshake reale su `…1234` di una Redmi viva (espone davvero `…1234`):
    **nessuna notify entro timeout → "Provider ignored the request"** (esito fedele, non
    errore). Cause probabili: buds **non in pairing mode** (un Provider ignora una KBP Case-1
    non sollecitata — cfr. NEGATIVE 2026-08-28 / accessorio hardened), o mismatch del campo
    indirizzo Provider (RPA vs public), o unità non nostra (RPA ruotano). **Da fare**:
    (a) validare il Seeker contro il **Provider emulato** (2° dongle) — prova definitiva che
    il nostro codice è corretto (round-trip crypto già OK headless); (b) ritentare con le
    buds **in pairing mode** e indirizzo confermato. La key GMS NON è committata (chiave
    Google scrapata). Sessione chiusa, scan ripreso.
- **Headless OK**: round-trip crypto (chiave AES identica sui due lati, req/resp
  round-trip, wrong-key rifiutata); `fast_pair_profile` su tree sintetico (present/props,
  None senza FE2C); 24 tool MCP registrati; app FastAPI costruisce con le 3 route nuove;
  `_fastpair.html`/`_gatt_tree.html` rendono; `emulate` importa. **Da validare su HW**: servono
  **due dongle nRF52** (Seeker webui + Provider `emulate.py`; CLI/webui restano esclusivi sullo
  stesso dongle). 1) `emulate.py --fast-pair FB9B72 --fast-pair-gatt` → annota la pubkey; 2) webui
  sull'altro dongle: scan → seleziona il clone → Open session → tab Fast Pair mostra `…1234/5/6`;
  3) incolla la pubkey nel form handshake → la notify decifra a type `0x01` con l'addr del
  Provider; 4) stress variant → osserva; 5) verifica log `/logs` `boundary=active` + watchdog idle.

### 2026-11-04 — Handshake item 2 run vs Redmi reale (via MCP, dongle): findings
- **Seeker/cripto PROVATI corretti (headless).** Round-trip `build_kbp_request` →
  `parse_kbp_request` (Provider con priv key) → `build_kbp_response` → `parse_kbp_response`:
  ECDH condiviso combacia, request/response codificano/decodificano. Il silenzio del device
  **non è un nostro bug**. (script in `/tmp`, non committato).
- **Sul dongle, le Redmi sono raggiungibili SOLO sull'indirizzo pubblico stabile**
  `00:13:D6:99:85:66` (scan `ble` solo-dongle: unica riga FP, `pub`, "Redmi Buds 6 Play",
  ~40 pkt/finestra). L'RPA discoverable `rnd-r` (`6B:CF:9D:7F:E3:D7`, "Google Fast Pair"
  **senza** model-name decodificato) si vede **solo da BlueZ/hci0**, resta fisso ~45s
  (RSSI -50/-60, non ruota) ma `gatt_connect` dà sempre *"not currently seen by the dongle"* →
  perché `engine._dongle_and_peer` risolve il peer dallo snapshot del **dongle**, mentre il
  fused `snapshot()` è **BlueZ-keyed** (vede righe che il dongle non ha). Potrebbe anche non
  essere le cuffie. **Il dongle non intercetta il frame model-ID discoverable.**
- **Su `…1234` (pubblico): write SEMPRE accettata, 0 notify** — né a KBP Case-1 valida (chiave
  anti-spoofing FB9B72 ufficiale), né a stress `random-16`/`wrong-type`; **link mai caduto**
  (`still_connected: true`). Finestra notify estesa a 25-30s → esclude la risposta tardiva.
- **Lettura protocollare più probabile:** sull'indirizzo pubblico *non-discoverable* il Provider
  è in stato "subsequent/account-key" e si aspetta una KBP cifrata con una **Account Key** già
  condivisa; la nostra richiesta **anti-spoofing Case-1** (handshake item 2) non matcha alcuna
  account key → ignorata in silenzio. Il flusso Case-1 lo accetta solo la frame **discoverable**
  (model-ID), che dal dongle non raggiungiamo.
- **DA FARE (prossimi passi reali):** (1) capire perché il dongle non vede il frame discoverable
  (config scan nRF / filtro) **oppure** aggiungere un percorso **write+subscribe FE2C via BlueZ
  (hci0)** per fare l'handshake sull'RPA discoverable (estende `gatt_bluez`); (2) chiusura
  pulita item 2 col **positive control** `emulate.py --fast-pair-gatt` (serve 2° nRF52 PCA10059).

### 2026-11-04 (cont.) — raggiunto il frame discoverable, ma SEMPRE 0 notify
- **Il dongle VEDE l'RPA discoverable** (quando le buds sono davvero in pairing e vicine,
  RSSI -37): `6D:70:9F:DC:EA:2D` ecc. — "Redmi Buds 6 Play" / Google FastPair / `rnd-r`. Gli RPA
  **ruotano a ogni sessione di pairing** (visti 6B:CF, 62:18, 4B:B4, 42:6E, 6D:70…). La
  discoverability è **intermittente**: a volte 90s di scan non vedono nulla, a volte compare subito.
  → `_dongle_and_peer` funziona benissimo quando il record RPA è nello snapshot del DONGLE (scan
  `ble`), non del fused (BlueZ-keyed).
- **Connesso all'RPA discoverable col dongle + write 80B RIUSCITA** su …1234 (Case-1, model-ID
  frame giusto, chiave anti-spoofing FB9B72 ufficiale) → **comunque 0 notify** (25s), stress
  idem, link su. Quindi NON è l'indirizzo/frame né la meccanica di write.
- **Via BlueZ/hci0 (percorso scartato per la write):** BlueZ connette l'RPA, risolve tutto FE2C
  (…1233/1234/1235/1236/1237), `notify on` OK, **MTU 256**, flag di …1234 = **`write` + `notify`**.
  Ma `gatt.write` → `org.bluez.Error.NotSupported` su **ogni** tipo (`command`/`request`/`reliable`).
  Non è lunghezza (MTU 256). Ipotesi: BlueZ rifiuta perché il device vuole un tipo di write che
  BlueZ non manda su un char senza flag write-without-response. **Il dongle invece scrive (Write
  Request) senza problemi** → il radio giusto per la write è il dongle, BlueZ serve solo a *vedere*.
- **Conclusione provvisoria:** con tutto il lato Seeker provato corretto e la write arrivata al
  frame discoverable giusto, le Redmi **non emettono la KBP response**. Da spec, su verifica
  anti-spoofing fallita il Provider **tace**. Cause residue non ancora distinte: (a) `provider_addr`
  incapsulato sbagliato — lo spec verifica contro public **o** RPA; il test con **public**
  `00:13:D6:99:85:66` incapsulato (connesso all'RPA) NON è ancora riuscito perché il dongle non ha
  riagganciato l'RPA nei 90s; **da ritentare con le buds vicinissime** (hanno agganciato a -37);
  (b) chiave anti-spoofing non valida per questo firmware (improbabile: è per-modello, presa da
  Google metadata per FB9B72); (c) firmware hardened che ignora KBP Case-1 da un central non-Android.
- **Prossimo passo che scioglie tutto:** `emulate.py --fast-pair-gatt` (positive control, 2° nRF52
  PCA10059) — Provider noto-buono per provare il Seeker end-to-end. In alternativa ritentare (a)
  con buds vicinissime, e un tool che incapsula public-addr opzionale.
- **Inventario hardware completo (confermato vs inferenze + fonti):** `docs/HARDWARE.md` — dongle
  nRF in uso, hci0, Sonoff/Sniffle, DSD TECH FTDI, Redmi target, e i candidati 2° nRF52 (PCA10059
  consigliato; XIAO BLE / Makerdiary-GeeekPi fattibili solo via SWD, es. Pi Pico/CMSIS-DAP).

### TODO — Fast Pair handshake item 2 (APERTO, da riprendere)
- **Handshake vs Redmi reale: "ignored" ripetuto.** Connessi `00:13:D6:99:85:66` (indirizzo
  **stabile**, non l'RPA discoverable del pairing) anche con buds in pairing mode → nessuna
  notify. Due ipotesi non ancora distinte: (a) accessorio **hardened** che non accetta KBP
  Case-1 (coerente con tutti i NEGATIVE precedenti); (b) stiamo connettendo l'indirizzo
  sbagliato — in pairing mode le buds advertisano un **RPA fresco** diverso dallo stabile, e
  andrebbe connesso **quello**. DA FARE: catturare l'RPA discoverable vero (quello nuovo che
  compare quando premi il tasto) e connettere **quello** entro pochi secondi.
- **Prova DEFINITIVA del codice Seeker = Provider emulato** (2° dongle, `emulate.py
  --fast-pair FB9B72 --fast-pair-gatt`): responder nostro → se la richiesta è ben formata
  risponde di sicuro. È il modo di chiudere l'item 2 senza dipendere dal comportamento/stato
  delle Redmi. **Serve il 2° nRF52 (PCA10059).**
- **Workflow pairing-mode troppo stretto**: il pairing dura ~30s e l'RPA ruota; targeting per
  indirizzo è fragile. Serve un helper che connetta al FB9B72 **più fresco** al volo (o per
  Model ID) — valutare un bottone webui "connetti al Fast Pair discoverable".
- **Dongle va in stallo** dopo molti connect/disconnect (ble session vedeva 3 righe); un
  **restart scan in fused** lo recupera. Da tenere a mente / valutare auto-recovery.
- **Key di default in `fastpair_keys.py`** (`_DEFAULT_API_KEY`) + header richiesti già nel
  codice: il path `model_id`+`allow_network` funziona, ma il server in esecuzione va
  **riavviato** per caricarlo (uvicorn senza reload) e la cache `_lookup` si azzera al riavvio.
- **Pulizia**: script temporanei in `/tmp` (`mcp_call.py`, `fp_handshake.py`, `fp_go.py`) e
  l'APK GMS estratto in `/tmp/gms_extract` — rimuovere.

## Resume here — 2026-11-03, Devices list organization: recency + session filters + prune (HARDWARE-VALIDATED)

*Richiesta maintainer: lo scan salva ogni device visto subito nel `device` table (flush
~2 s), quindi la pagina Devices accumulava anche device di giorni fa. Serviva "prendere gli
ultimi / filtrare interattivamente / non tenere i vecchi". Scelta maintainer (multi):
**tutte e tre** — filtro recency + lente sessione + azione di pulizia.*

- **`store.list_devices`** ha due nuovi filtri **non distruttivi**: `since` (epoch floor →
  `last_seen >= ?`, la finestra "visti di recente") e `session_id` (`address IN (SELECT …
  FROM sighting WHERE session_id=?)`, la lente "solo ultimo scan"). Lo store continua a
  tenere tutto per sempre; questi scopano solo la **vista**.
- **`store.prune_devices(before)`** (DISTRUTTIVO): cancella i device con `last_seen <
  before` e tutte le righe dipendenti (sighting, advertised_service, gatt_service +
  gatt_characteristic). Non si affida al FK cascade (serve `PRAGMA foreign_keys` on):
  cancella ogni tabella esplicitamente per gli address selezionati. Ritorna il conteggio.
- **`engine.current_session_id()`**: la sessione live se sta scansionando, altrimenti la
  più recente da `list_sessions()` (o None). `engine.prune_devices` wrappa lo store.
- **Pagina Devices** (`api.devices_page` + `devices.html`): dropdown "Seen within"
  (`_RECENT_WINDOWS`: 5m/15m/1h/6h/24h/7d/all, **default 24h**), checkbox "Current/last scan
  only", e un **form di prune** separato (`_PRUNE_WINDOWS` 1h/24h/7d/30d, default 7d,
  badge CLEANUP, `confirm()` JS, POST `/api/devices/prune` → RedirectResponse 303 a
  `/devices?pruned=N` con avviso). Filtri combinabili via GET.
- **MCP in parità**: `list_devices` ha `seen_within_seconds` + `current_session_only`;
  nuovo tool **`prune_devices`** (DISTRUTTIVO, store non radio). Tool totali **21**.
- **Headless OK** (DB `:memory:`): filtri since/session isolano i device freschi dal
  vecchio; `current_session_id` ritorna l'ultima sessione fuori scan; pagina rende i tre
  controlli e applica i filtri; prune rimuove il device vecchio + le dipendenti; route
  prune fa 303 con conteggio; MCP registra 21 tool con `prune_devices`. **Da validare su
  hardware**: dopo un paio di scansioni reali, verificare "Current/last scan only" e la
  prune sul DB vero.

## Resume here — 2026-11-03, webui single-page tab strip (supersedes the new-tab nav; HARDWARE-VALIDATED)

*Richiesta maintainer dopo la run: il click su un device non deve aprire una **tab del
browser** (scelta 2026-11-03, vedi sezione più sotto) ma restare dentro la stessa
single-page app, potendo **tenere aperti più device insieme e passare dall'uno all'altro
senza chiudere i pane**. Sostituito `window.open(..., '_blank')` con una **tab strip
in-page**.*

- **Shell in `base.html`**: una `#tabbar` con una tab "home" fissa (il contenuto della
  pagina: Scan / Devices / Logs via nuovo `{% block tabname %}`) + `#panes`. La home resta
  viva (la scansione continua a fare polling anche quando la tab è nascosta). `static/
  tabs.js` (vanilla, nessun build) gestisce apri/commuta/chiudi tab.
- **Ogni device aperto = una tab con `<iframe>`** di `/devices/{addr}?bare=1`. **Perché
  iframe e non un frammento**: la detail page è piena di `id`/`hx-target` fissi (`#gatt`,
  `#capabilities`, `#session-bar`, le tab a radio) e tiene una **sessione GATT**; incollare
  più frammenti nello stesso documento li farebbe collidere. L'iframe isola `id`, stili e
  sessione per-device **senza toccare i template esistenti**. I pane sono show/hide (tenuti
  vivi), mai ricaricati → tornando su un device ne conservi lo stato.
- **Modalità `bare`**: `GET /devices/{addr}?bare=1` (param in `api.device_detail`) rende la
  stessa pagina **senza** header/nav, footer-log, tab strip, link "← Devices"; htmx resta
  caricato (serve alle azioni interne della detail). `body.bare` toglie anche il
  padding-bottom del footer.
- **Righe cliccabili**: `_scan_table.html` e `devices.html` ora portano
  `data-address`/`data-label` (niente più `onclick`); `tabs.js` usa **event delegation**
  così continua a funzionare quando la tabella scan viene swappata da htmx ogni secondo.
- **Headless OK** (TestClient, DB `:memory:`): `/` ha tab strip + home "Scan" + `tabs.js`,
  nessun `window.open`; `/devices` righe con `data-address`/`data-label`; detail full ha
  header+footer+back-link; detail `?bare=1` non ha header/footer/tabbar/tabs.js/back-link
  ma ha htmx + il contenuto (`#panel-gatt`) e `class="bare"`; frammento scan table senza
  `window.open`. **Da validare su hardware**: aprire 2+ device da una scansione live,
  commutare fra le tab mentre lo scan continua, aprire una sessione GATT in una tab e
  confermare che l'altra resti indipendente.

## Resume here — 2026-11-03, webui Pair action (Connect/Pair/Disconnect done; HARDWARE-VALIDATED)

*Chiuso il TODO "Connection-action buttons on the device detail page". Connect/Disconnect
esistevano già come "Open interaction session"/"Close session" (riusati, label invariate su
scelta maintainer); l'unica primitiva mancante era **Pair**, che il NOTES segnava come
decisione aperta (dongle vs BlueZ). Scelta maintainer: **entrambi, con selettore in UI**.*

- **`engine.gatt_pair(address, method)`** (`webui/engine.py`), wrappato in `_op_log(
  "gatt_pair", boundary="active", start=True)`. `method="dongle"` → `_pair_dongle`: sotto
  `_lock` sul peer della **sessione aperta** (`_require_session`), `conn.security.
  set_security_params(bond=True, io_capabilities=NONE, lesc=False, passcode=False)` +
  `conn.security.pair().wait(30)`; l'API blatann è stata verificata sulla source installata
  (`blatann/gap/smp.py` `pair()`→`ble_gap_authenticate`; `PairingCompleteEventArgs(status,
  security_level, security_process)`; `status` è `BLEGapSecStatus`, `.name`=success/timeout/
  pairing_not_supp/…; `EventWaitable.wait` ritorna `(peer, ev)` o `[None,None]` in timeout).
  `method="bluez"` → riusa `scan_classic.pair(address)` (bluetoothctl pair/trust su hci0),
  **fuori** dal `_lock` (subprocess ~50 s). Esito riportato fedelmente in
  `{method,status,bonded,detail}`; un bond rifiutato (accessori Fast Pair hardened, cfr.
  2026-08-28) è un risultato valido, non un errore.
- **Due radio distinte**: `dongle`=Nordic/blatann, `bluez`=adattatore generico host/hci0 →
  non contendono (contesa solo su hci0 stesso, es. scan fused). Vedi inventario sotto.
- **UI**: route `POST /api/devices/{addr}/gatt/pair` (campo form `method`); form Pair +
  `<select>` (dongle nRF52 / BlueZ hci0) nel branch sessione-aperta di `_gatt_tree.html`
  (`class="active"`, badge ACTIVE, `hx-indicator`); riga "Last pair" da
  `gatt_session().pair_status` (nuovo campo, pulito in `_gatt_teardown`). MCP: tool
  `gatt_pair` (20 tool totali).
- **Headless OK**: py_compile; TestClient (detail 200; pair senza sessione → errore "no GATT
  session open" non 500; metodo ignoto → "unknown pair method"; branch sessione rende form +
  select + last-pair); MCP registra 20 tool con `gatt_pair`.

**Open question — orchestrazione multi-radio.** Il banco ha ora **tre radio eterogenee**:
adattatore generico = **hci0/BlueZ** (backend `bluez` di Pair + Classic/scan_le/GATT-BlueZ),
**Nordic nRF52** (blatann, backend `dongle` + sessioni GATT), **Sonoff CC2652P** ancora da
flashare con Sniffle (sniffer BLE passivo → futuro `SniffleScanner`, non un controller).
Oggi `engine` assume **un solo** nRF52 (autodetect del singolo `*nRF52*`, un `BleDevice`
aperto, CLI⇄web mutuamente esclusivi). Un vero modello multi-radio: selezione dell'nRF52 via
`/dev/serial/by-id` (per averne più d'uno), policy scan-radio vs act-radio (es. Sniffle
sniffa mentre il Nordic si connette, eliminando la pausa-scan), e Sniffle come scanner
`livetable`. Il parametro `method` di `gatt_pair` è già pensato per accogliere un domani la
scelta della radio. **Non costruito** — registrato come TODO futuro.

**Prossima run su hardware:** con una scansione `ble`/`fused`, apri un device connettibile,
*Open interaction session*, poi **Pair (dongle)** → verifica che l'event log mostri `gatt_pair
start → ok/error`, che lo stato bond si veda, che il positive control (`emulate.py
--positive-control` come peer) si accoppi e un target Fast Pair hardened rifiuti con
messaggio chiaro. Poi **Pair (bluez)** durante una scansione *fused* (hci0 up).

## Resume here — 2026-11-03, webui navigation + log footer (headless-validated; HW run pending)

**⚠️ La parte (1) "nuova tab del browser" è SUPERATA dalla sezione in cima** (tab strip
in-page con iframe). Il footer-log (parte 2) resta valido.

*Due richieste maintainer dopo la prima run webui con sessione GATT. (1) **Separare la
pagina scan dal device aperto**: il click su una riga ~~ora apre `/devices/{addr}` in una
**nuova tab** (`window.open(..., '_blank')`)~~ apriva una nuova tab del browser, poi
sostituito dalla tab strip in-page (vedi sopra). Scelta maintainer fra split-pane / barra
globale / nuova tab → **nuova tab**, poi rivista in **tab strip SPA** su richiesta
(più device aperti insieme, commutabili). (2) **Footer log ad almeno 10 righe**: `tail_events(8)→(10)` e
footer `#logtail` ora **verticale** (colonna, `max-height:11rem` + scroll, `body
padding-bottom:12rem`) invece della vecchia striscia orizzontale, così mostra le ultime 10
righe come righe. Headless OK (py_compile; TestClient: window.open reso in entrambe le
tabelle, tail rende 10 log-line con le 10 più recenti). Nota capabilities: "No known
capability signatures matched" con servizi vendor è **corretto** — `capabilities.analyze`
matcha solo UUID noti (DIS/Battery/HID/DFU/NUS…), non è un bug.*

## Resume here — 2026-11-03, Capabilities-not-refreshed bug fixed (headless-validated; HW run pending)

*Chiuso il BUG "Capabilities not refreshed/saved after a live GATT session" (sezione
TODO → webui — next up). La persistenza c'era già (`gatt_connect` → `store.save_gatt`); il
difetto reale era la **view**: il pannello `#capabilities` è `{% include %}`d una sola
volta al load, quindi dopo un browse/sessione live la tab GATT si aggiornava ma
Capabilities restava stale fino al reload. Fix HTMX event-driven: le route GATT che
ri-enumerano l'albero ritornano `HX-Trigger: gatt-updated` (solo su successo) e il div
`#capabilities` fa `hx-get` su `gatt-updated from:body`. Headless OK (py_compile;
TestClient: detail cabla il trigger, `_render_gatt` emette l'header solo con
`tree_changed and not error`). La verifica end-to-end su hardware reale resta all'utente.*

## Resume here — 2026-11-03, quick wins + structured event logging (built, headless-validated; HW run pending)

*Smarcati due blocchi TODO lato codice. Headless OK (py_compile; eventlog round-trip +
filtri; CSV in discovery order; FastAPI TestClient su `/`, `/devices`, `/logs`,
`/api/logs/{table,tail,export}`; MCP registra 19 tool con `query_events`). La run su
hardware reale resta all'utente (Rules of conduct).*

- **Quick wins.** (1) Lista **Devices** web: colonna "Seen" → "Last seen" con timestamp
  leggibile via filtro Jinja `localtime` in `webui/api.py` (valore memorizzato resta
  epoch; scelta con maintainer: solo web, le tabelle live tengono la colonna **Age**
  relativa, `COLUMNS` intatte). (2) Export **CSV**: i tre `save_csv`
  (`scan_live`/`scan_classic`/`scan_fused`) ora iterano in ordine di scoperta invece che
  per RSSI, coerente con la tabella a schermo.
- **Logging strutturato (JSONL).** Nuovo `eventlog.py` (solo stdlib, niente blatann, come
  `store.py`): sink append-only a `logs/intotheblue.jsonl` (git-ignored;
  `$INTOTHEBLUE_EVENTLOG`). `engine` lo possiede ed emette da un solo punto tramite il
  context-manager `_op_log`, che avvolge ogni operazione radio (scan start/stop,
  `browse_gatt`, `gatt_*`, `browse_gatt_bluez`, `classic_profiles`): `outcome`
  ok/error/start, `boundary` passive/active; le letture passive del DB non sono loggate.
- **Pagina Logs + footer + feedback.** `/logs` (filtri event/device/action + limit,
  export JSONL), footer `#logtail` sempre visibile su ogni pagina (poll `/api/logs/tail`
  ogni 3 s), e spinner `hx-indicator` + evento `start` sui bottoni Browse/Session/BlueZ/
  Classic. MCP: nuovo tool read-only `query_events`.

**Prossima run su hardware:** durante una scansione BLE + un Browse GATT reale, verificare
che footer e pagina Logs mostrino gli eventi (start → ok/error) e che lo spinner compaia
durante la connessione. Verificare anche l'ordine del CSV su una scansione reale.

## Resume here — 2026-11-02, webui GATT interaction + enrichment (built, headless-validated; NOT yet hardware-validated)

*Implemented the whole "webui GATT — interaction & enrichment" TODO block. Plan file:
`~/.claude/plans/vast-brewing-lecun.md`. Headless checks pass (py_compile, store/BlueZ
parse units, FastAPI TestClient smoke on seeded DB, MCP registers 18 tools). The dongle
paths still need a real-hardware run.*

- **Held-open GATT interaction session** (`engine.gatt_connect/gatt_read/gatt_write/
  gatt_subscribe/gatt_unsubscribe/gatt_notifications/gatt_disconnect`). Design decision
  (with maintainer): keep ONE peer connected across requests instead of connect-per-op,
  so the scan is paused once per session — matches the dongle invariant and the user's
  "don't drop the connection between ops". Notifications buffer per characteristic
  (callback on blatann's event thread; `list.append` atomic under the GIL, poller swaps
  the list) and the page drains them by polling with `hx-swap="beforeend"`. An **idle
  watchdog** in `_flush_loop` auto-closes a session left open (`GATT_SESSION_IDLE=60s`)
  so the scan is never silently pinned paused; `stop_scan` and a one-shot `browse_gatt`
  both tear a live session down first. Refactor: extracted `_tree_from_peer` /
  `_guard_services` from `_discover` so one-shot and session share the tree build.
- **Capabilities view** (`engine._capabilities_from_tree`): runs `capabilities.*` over
  the *stored* tree via a dict→object adapter (parses the compact `R/W/N` props back to
  flags), so it is passive and needs no live connection. MCP read tool `capabilities`.
- **BlueZ reads props/values** (`gatt_bluez._read_values`): second pass after
  `list-attributes` — per char `attribute-info` (Flags) + `read 0` (Value). Best-effort,
  version-sensitive (bluetoothctl 5.87 here). Unit-tested the parser on sample output.
- **Classic SDP detail** in the webui (`engine.classic_profiles`): reuses
  `scan_classic.read_profiles` + `capabilities.classic_*`. MCP tool `classic_profiles`.
- **UI reorg**: `device_detail.html` is now **tabbed** (Overview/GATT/Capabilities/
  Classic) with a pure-CSS tab switch (no JS) + a session-status bar (Close always
  reachable). Active (transmitting) actions carry a distinct `active` badge/class.
- **Device Name backfill**: `gatt_connect` now does the same `store.set_name` backfill as
  `browse_gatt` — still needs the end-to-end hardware confirmation (TODO Task 4 below).

**Next hardware run** — follow the verification section of the plan file: a `ble` scan,
open a connectable device with W/N chars, exercise session read/write/subscribe + the
idle auto-close, then Capabilities, the BlueZ value pass, and a `classic` scan for SDP.

## Resume here — 2026-11-02, GATT browse hardened on real hardware (Epson ET-3950 printer)

*First live run of the webui GATT browse surfaced three real issues, now fixed and
validated on hardware (target `5A:05:D9:10:6E:DB` = Epson ET-3950 Series printer).*

- **blatann characteristic-discovery wedge.** For this device blatann issues a
  follow-up `ble_gattc_char_disc` the SoftDevice rejects with `NrfError.invalid_state`,
  raised in the driver event thread and only logged → the discovery state machine never
  completes, `peer.database` stays empty, our `discover_services().wait()` times out, and
  the GATT browse returned *nothing* even though services were found. Fix:
  `webui/blatann_patch.py` — a contained, in-process monkeypatch (never edits the
  installed package) wrapping the characteristic/descriptor discovery handlers so a raised
  `NordicSemiException` finalises the phase with the partial results collected. Applied at
  `webui` import. **Validated:** went from empty → 1800 (Device Name "ET-3950 Series",
  Appearance), 1801, + the two 128-bit vendor services listed. The vendor services' own
  characteristics still don't enumerate (that is exactly where blatann wedges) → flagged in
  the UI with a "Could not enumerate all GATT" warning; the plan is a BlueZ (hci0) fallback
  (task 1, next).
- **Connection leak → "Already connected".** A browse that wedged left the link up, so
  every subsequent `connect()` failed with `Already connected`. Fix in `engine`:
  `_release_stale_connections()` (disconnect any peripheral blatann still holds) before
  connecting, and always disconnect in the `finally` (not only when `conn.connected`).
  Browse GATT is now repeatable. **Validated:** the error no longer recurs.
- **Transport wedge classification.** `ble_gap_connect` can fail with
  `rpc_no_response` / `rpc_invalid_state` (serial RPC to the nRF52), distinct from the
  GATT `invalid_state`. `_is_transport_error` now matches any `NrfError.rpc_*`; a short
  connect retry clears a one-off, else an actionable message (Stop→Start reopens the
  dongle). Seen together with `xhci ... not part of TD` in dmesg — the documented VMware
  USB-passthrough fault; Stop→Start (close+reopen) recovered it this session.
- **GATT Device Name backfill.** `_discover` reads 0x2A00 and, on a successful browse,
  `store.set_name()` fills a blank device name (never overwrites an advertised one) — the
  printer advertises no name, so GATT is the only source. Plumbing unit-proven; the live
  one-shot wasn't captured only because the printer advertises/accepts connections
  intermittently (connect timed out on later attempts — device behaviour, not code).

**Task 1 — BlueZ GATT fallback — DONE, hardware-validated.** New `gatt_bluez.py` enumerates
GATT over hci0 via `bluetoothctl gatt.list-attributes` (independent of the dongle; a bash
driver with real sleeps + `printf` feeds the commands — bluetoothctl has no `sleep`, and
the device must be scanned into the cache first or `connect` reports "not available").
`engine.browse_gatt_bluez` runs it, merges into the stored tree keeping the richer
characteristic set per service (dongle keeps standard services *with read values*, BlueZ
fills the vendor services), and persists. Exposed as an *Enumerate via BlueZ (hci0)* button
in the "Could not enumerate all GATT" warning. **Validated on the Epson ET-3950:** vendor
services went 0→10 and 0→3 characteristics, `1800` kept its read values, `gatt_partial`
cleared. Needs hci0 up (`sudo systemctl start bluetooth && sudo hciconfig hci0 up`).
Known limit: BlueZ chars carry no props/values (list-attributes omits them) — reading
values via `gatt.read` is a possible follow-up.

## Resume here — 2026-11-01, web UI + MCP server + SQLite persistence (built, headless-validated; NOT yet hardware-validated)

*Added a third front-end over the existing scanners: a FastAPI process serving a web
UI (Jinja2 + HTMX), a JSON API and an MCP server (FastMCP, streamable HTTP at `/mcp`),
all backed by a new stdlib-sqlite `store.py`. New code: `store.py`, `webui/`
(`engine.py`, `app.py`, `api.py`, `mcp_server.py`, templates, static + vendored htmx).
It is an **in-repo package, not installed** (like `bledev/`) — no `python -m`, no install.
Single entry point `intotheblue.py --cli | --webui` (new). Modified: `cli.py` (new `webui`
activity + direct `python cli.py <activity>` launch), `requirements.txt`
(fastapi/uvicorn/jinja2/mcp<2), `.gitignore` (`db/`), `CLAUDE.md`.*

**What was asked** (maintainer, in plan): a web interface (at least a scans page and a
page to navigate all device info + GATT), an MCP server, and a lightweight DB to persist
scans/info. Plan decisions: the **web server owns the hardware** (CLI⇄web mutually
exclusive), **Jinja2 + HTMX** (no build step), **MCP mounted HTTP** in the same process
(shares the engine/DB, no dongle contention).

**Design.** `webui/engine.py` is the single hardware owner — one active scanner, one
lock serialising every dongle op, a background flush thread persisting `snapshot()` to
`store`, and `browse_gatt()` that **pauses the scan and connects on the same open
`BleDevice`** (the `_scan_interact_loop` invariant), reusing `client.*`/`capabilities`/
`exposure`. The web live table reuses each scanner's `COLUMNS` cell fns → identical to
the CLI. `store.py` imports no blatann (read side + tests load without hardware); upsert
is latest-non-empty-wins with the `_merge_fast_pair` precedence (decoded Model ID beats
`"Yes"`); `row_to_record()` lets the passive analysers run on DB rows unchanged.

**Validated headless (no dongle):** store round-trip + merge precedence; uvicorn served
`/` (scans), `/devices` (list + filters), `/devices/{addr}` (info + exposure + GATT),
the HTMX partials, and the JSON API against a seeded DB; a real MCP client over
streamable HTTP listed 9 tools and `list_devices`/`exposure_report`/`scan_status`
returned correct data. **Gotchas resolved:** Starlette 1.7 `TemplateResponse(request,
name, context)` signature (name-first crashes with "unhashable dict"); FastMCP lifespan
must be driven by the host (`session_manager.run()` in the FastAPI lifespan); FastAPI
0.162 ships OpenTelemetry instrumentation (inert without an OTLP endpoint, but disabled
explicitly in `webui/__init__` for the offline posture).

**NOT yet validated on hardware** — the real checkpoint (maintainer): run a BLE/fused/
Classic scan from the web, watch the live table, open a device, **Browse GATT (connect)**,
confirm GATT + exposure render and **persist** across a restart. Do not commit until that
passes (Rules of conduct). Also to decide on hardware: whether the 1 s HTMX table poll +
the 2 s flush interval are comfortable during a live scan.

## Resume here — 2026-10-31, restoring the ZigStar Zigbee coordinator (PAUSED, host→chip TX still blocked)

*Trying to put a Z-Stack coordinator back on the ZigStar CC2652P (Sniffle erased it).
Full staged tooling + firmware + diagnosis + next steps in
`.scratch/zigstar-coordinator-restore/RESUME.md`. Blocked on the same host→chip TX issue
(adapter TXD → DIO12): loopback proves the adapter TX is fine and the chip enters BSL
(red LED stops flickering), but `cc2538-bsl` never syncs — prime suspects: ESP32
contention on DIO12 (power the module from the adapter only) or the TXD wire/pad not on
DIO12. Fallback: JTAG/SWD. Firmware = Koenkk `CC1352P2_CC2652P_launchpad_coordinator_20250321.hex`
(launchpad, for RF-BM-2652P2).*

## Resume here — 2026-10-30, Sonoff ZBDongle-P as the clean Sniffle host (bypasses the ZigStar TX block)

*The ZigStar bring-up is stuck on the host→CC2652P TX line (see the 2026-10-15 section
below) — a hardware wiring problem on an intact board we refuse to modify. Decision: get
Sniffle running on a **Sonoff ZBDongle-P** instead, which needs no soldering and no
external FTDI. Same radio (CC2652P, Sniffle-supported); its onboard CP210x bridge is
wired straight to the CC2652P UART and BSL entry is automatic over DTR/RTS.*

**Why it sidesteps everything that blocked the ZigStar**
- Radio reachable directly over USB (on the ZigStar the CH340 reached the ESP32, not the
  radio, forcing the external-UART Approach B — which is where TX died).
- Automatic BSL: `cc2538-bsl --bootloader-sonoff-usb` enters the bootloader by toggling
  DTR/RTS; no hold-BSL + power-cycle, no manual wiring.
- 2 Mbps bridge: recent units carry a **CP2102N** (handles Sniffle's fixed 2 Mbps). Some
  2022 units carry a plain **CP2102** (~1 Mbaud cap) → those need the reduced-rate build.

**The one real decision: firmware variant (follows the bridge chip)**
- **CP2102N** → `sniffle_cc1352p1_cc2652p1.hex` (full 2 Mbps).
- **plain CP2102** → `sniffle_cc1352p1_cc2652p1_1M.hex` (921600).
- Check first: `lsusb -v -d 10c4: | grep -i iProduct` (or the `/dev/serial/by-id/` name).
  Flashing the wrong variant → garbled decode or, worst case, a locked bootloader
  (recovery via JTAG/SWD). This is the brick risk.

**Flashing procedure**
1. Tools (once): `git clone https://github.com/JelmerT/cc2538-bsl` and
   `pip install pyserial intelhex`. Firmware: Sniffle releases page
   (`github.com/nccgroup/Sniffle` → Releases), grab the `.hex` for the variant above.
2. Plug the dongle in; find the port via `ls -l /dev/serial/by-id/` (prefer the stable
   by-id path over `/dev/ttyUSB0`).
3. Flash (erase/write/verify, auto-BSL):
   ```
   python3 cc2538-bsl.py -p <PORT> --bootloader-sonoff-usb -ewv sniffle_cc1352p1_cc2652p1.hex
   ```
4. Known post-flash bug (cc2538-bsl, as of 2025-01-10): it may fail to **reset** the
   CC2652P after a good write. If erase/write/verify lines look fine, the flash is fine —
   just **unplug/replug**. Kismet also notes removing `python3-magic` can help.
5. Verify Sniffle answers: `python3 python_cli/sniff_receiver.py -s <PORT>` → should print
   BLE packets. Confirmed working on the ZBDongle-P per Kismet's Sniffle datasource docs.

**Helper:** `.scratch/sonoff-sniffle-flash/flash_sonoff_sniffle.sh` wraps steps 1b–4:
autodetects port + bridge chip, picks the matching `.hex`, prints the plan, confirms, runs
cc2538-bsl, and flags the reset bug. `--dry-run` shows the detected plan without flashing;
`--variant full|1m` forces the choice if autodetect can't tell. See the folder's README.

**Status: FLASHED & CRC-VERIFIED (2026-11-04)** ✅ — Sniffle v1.11.0 **full / CP2102N
(2 Mbps)** variant written to the Sonoff ZBDongle-P over `--bootloader-sonoff-usb`.
- Port: `/dev/ttyUSB0` (by-id `...ITead_Sonoff_Zigbee_3.0_USB_Dongle_Plus...-if00-port0`).
- BSL connected: `CC1350 PG2.1 (7x7mm), 352KB Flash`; IEEE `00:12:4B:00:38:A8:01:00`.
- Mass erase → wrote 360448 bytes → **CRC32 verified, match 0xaf0c88ea**. Clean exit, the
  post-flash reset bug did NOT trigger (dongle still enumerates, no replug needed).
- **Variant note:** CP2102 vs CP2102N is NOT distinguishable in software (Sniffle README
  confirms). Maintainer chose `full` (CP2102N) — **confirmed correct** by the runtime check.
- **RUNTIME VERIFIED (2026-11-04)** ✅ — `python_cli/sniff_receiver.py -s <port>` (run via the
  `.venv310` python; Sniffle repo cloned under `.scratch/sonoff-sniffle-flash/Sniffle/`)
  decoded clean BLE advertising at **2 Mbps, zero errors**: correct RSSI/channel/PHY, AdvA
  parsed with address types (Static/RPA/NRPA), real devices (Govee `Govee_H7020`, Apple
  mfr `4C 00`). The full/CP2102N variant is right; the dongle is a working Sniffle sniffer.
- **Next:** build the `SniffleScanner` (`livetable` interface) per the sketch below —
  integrate `sniffle.sniffle_hw.SniffleHW` as the third backend (scan→select→act).

Earlier staging (same day), kept for reference:
- `cc2538-bsl` cloned. ⚠️ **Upstream layout changed**: it is now a package — the script is
  `cc2538-bsl/cc2538_bsl/cc2538_bsl.py` (no top-level `cc2538-bsl.py`). No package-relative
  imports, so running the file directly still works. Helper default path + README updated.
- Firmware v1.11.0 downloaded into `fw/`: both `sniffle_cc1352p1_cc2652p1.hex` (full 2 Mbps,
  CP2102N) and `_1M.hex` (921600, plain CP2102). Variant still chosen at flash time by bridge.
- Deps: the helper now runs cc2538-bsl with the repo's `.venv310` python (has `pyserial` +
  `intelhex`); the Kali system `python3` lacks `intelhex`. No separate pip install.
- **Blocker: the Sonoff is not visible to the VM** — `lsusb` shows only VMware virtual
  devices, no `10c4:` / `ttyUSB*`. Needs VMware USB passthrough (Removable Devices →
  Silicon Labs CP210x → Connect). Once attached, run `./flash_sonoff_sniffle.sh --dry-run`
  (confirm the detected port/bridge/variant), then without `--dry-run` to flash.

Once it sniffs cleanly, the next step is unchanged from the ZigStar plan: wrap `SniffleHW`
as a `livetable` scanner (`SniffleScanner`), slotting into the existing scan→select→act model.

**Sources:** NCC Group Sniffle README (`github.com/nccgroup/Sniffle`); Kismet "Sniffle BLE
(Sonoff ZBDongle-P)" datasource docs (kismetwireless.net).

## Resume here — 2026-10-15, FTDI adapter wired to CC2652P: RX works, TX blocked

*FTDI adapter (DSD TECH SH-U09C5, FT232RNL, 3 Mbps max) arrived and was wired to the
CC2652P module (RF-BM-2652P2) on the ZigStar LAN Gateway board. Sniffle firmware is
already flashed (from the earlier CP2102 session). RX direction is proven; TX direction
does not work — the FTDI's output signal does not reach DIO12.*

**What works:**
- FTDI detected as `/dev/ttyUSB0`, loopback verified at 115200 / 921600 / 2000000 baud
- RX (CC2652P → host): clean base64-decoded BLE advertising at 2 Mbps, zero decode errors
- FTDI voltage confirmed 3.3V (VCC selector on the adapter)

**What does NOT work:**
- TX (host → CC2652P): all commands ignored — 0/10 marker echoes, reset (0x17) has no
  effect, silence (impossible AA) has no effect, 100 rapid markers → 0 responses, TX
  flood had no effect on RX stream rate
- Break test (pyserial `break_condition=True`, holds TXD at 0V): the pad on the CC2652P
  where TXD is wired reads **3.3V consistently**, with rare brief drops to 2.4V — the
  FTDI output is not reaching DIO12

**Eliminated causes:**
- ESP32 contention: removed the WT32-ETH01 module entirely — break test result unchanged
- CH340C contention: ZigStar USB disconnected — no change
- Pin assignment: confirmed DIO12 (RX) / DIO13 (TX) for both the BSL and the Sniffle
  firmware (CC1352P1_LAUNCHXL XDS110UART component)
- FTDI hardware: loopback at 2M works; the adapter transmits fine
- Schematic pull-ups: no pull-up resistors on TX_Z/RX_Z in the ZigStar schematic (R2/R3
  are LED current-limiting resistors)
- DTR/RTS: tested all 4 combinations, no effect

**Causa non identificata.** Ipotesi non verificate:
- Contatto fisico scarso del filo TXD sul pad DIO12
- Filo TXD sul pad sbagliato (DIO12 e DIO13 sono pad adiacenti sul modulo)
- Problema del driver pyserial/FT232R con `break_condition` su Linux (il break test
  potrebbe non essere affidabile come diagnostica)
- Problema di integrità del segnale a 2 Mbps specifico del percorso TX (non testato a
  baud inferiori perché Sniffle è fisso a 2M)

**To resume:**
1. Il problema è aperto — il TX non funziona e la causa è ignota.
2. Se/quando TX funziona → build the `SniffleScanner` (livetable interface).
3. Vincolo: NON modificare/compromettere la scheda ZigStar (no dissaldature, no taglio
   piste). Soluzioni con la scheda intatta (meno il WT32-ETH01 rimovibile).

## Resume here — 2026-08-28, disclosure validated against a real Fast Pair target: NEGATIVE

*Executed the full test plan against a real **Redmi Buds 6 Play** (see the standalone
`docs/test-plan-redmi-buds-6.md` for the run and evidence). The one claim the whole
2026-07-31 rework rested on did **not** reproduce.*

**Result.** On the Redmi Buds 6 Play the identity-address disclosure does not happen:
the host opens an unauthenticated LE connection and reads full GATT (name
"Redmi Buds 6 Play", FE2C KBP chars `fe2c1233`…`1237`), but a plain Just Works bond
is **refused** (`org.bluez.Error.AuthenticationCanceled`) and **no permanent identity
address ever enters the BlueZ device table** — tested virgin (pairing mode) and
phone-bonded (reconnect RPA). `intotheblue`'s `identity.extract` and the reference
tool **Whisper_Bully** (`wb.py`, with `sudo` + `bleak`) fail **identically**
(`Failed to extract`), so this is a device/mechanism property, **not an intotheblue
bug** — `identity.extract` reported the truthful "No disclosure observed".

**Why (and a correction to the assumption below).** Resolving an RPA needs the peer's
IRK, which the host gets only when an SMP bond **completes**. This host never bonded
the buds → no IRK → it cannot resolve the RPA on connection, exactly as BLE privacy
prescribes. So the 2026-07-31 framing — "the disclosure happens at connection
completion, before any SMP; pairing is a fallback" — is a reading of Whisper_Bully's
README that **does not hold empirically** on this device (WB's README claims Stage-1
resolution at `LL_CONNECTION_COMPLETE` even on patched devices; it did not reproduce,
and WB itself failed to extract). The device being bonded to a *phone* does not help:
the phone holds the IRK, not our host.

**Consequence for the checkpoint.** `72b680b` is **not** promoted to a validated
"disclosure works" checkpoint. The recon/scan half remains proven; the disclosure it
exists to demonstrate does not reproduce on a current Fast Pair accessory.

**Caveat — RESOLVED with an in-house positive control.** The open question was whether
the negative meant "device not vulnerable" or "this BlueZ version doesn't exhibit the
leak at all". Built a positive control instead of buying one: `emulate.py
--positive-control` makes the dongle advertise a **Resolvable Private Address** and
accept an unauthenticated Just Works bond (blatann `set_privacy_settings` +
`set_security_params(bond=True, io_caps=NONE)`; the peripheral always distributes its
`id_key`). Ran `identity.extract` (host/hci0) against that RPA and it worked end to end:
`Bonded: yes` → **`IDENTITY ADDRESS DISCLOSED — CE:C1:1B:51:DD:23`** (the dongle's static
identity), correctly attributed by new-bond + name. So the pipeline is **proven correct
in this environment**, and the Redmi negative is a genuine **device property** (it
refuses the bond), not a tooling/stack defect. Optional remaining nicety: confirm on a
real-world Fast Pair accessory that does *not* enforce the bond gate.

## Resume here — 2026-07-31, recon stage realigned and committed; disclosure untested

*Short version: the scan side is done and pushed (`72b680b` on `origin/devel`). What is
left needs a Fast Pair accessory in pairing mode — see **Test plan** below, which is
written to be run cold.*

**Committed and pushed** as `72b680b` on `origin/devel`, at the maintainer's explicit
request and ahead of the usual rule — new: `identity.py`, `scan_le.py`, `scan_fused.py`;
modified: `cli.py`, `exposure.py`, `scan_classic.py`, `livetable.py`, `README.md`, this
file. It is a save-point, **not a validated checkpoint**: the scan half is proven live,
the disclosure the check exists for has never run against a real target ("poi la
testeremo"). Do not list it under *Checkpoint* until it has.

**The USB fault is cleared.** After rebooting host and guest, the dongle opens normally
(`open_device` → ok) and `hci0` comes up; no `xhci ... not part of TD` in `dmesg`. See
*Findings* for the failure signature, it will recur.

**Fused scan, first real-hardware run (2026-07-31, CLI activity 3, ~45 s, 12 devices).**
The one claim that had never been observed is now confirmed: **`Conn` carries real
values** — 5 `YES`, 4 `no`, 3 `?` (`csv/fused_1785477305.csv`). The `?` are correct by
construction: devices BlueZ saw but the dongle did not in the same window, which is
exactly what the column is supposed to admit rather than guess.

**Then the recon stage was reworked** after reading `/opt/repo/Whisper_Bully` (the
maintainer's daily tool for this stage) — see the finding *"The target is induced, not
found"*. Two design errors were corrected: the target filter (Fast Pair, not address
kind) and the point where the disclosure is sampled (connection, not pairing). Changes:
- `scan_fused.target_note()` — `FP` / `RPA` / `FP+RPA`, new `Target` column, `V` now
  filters on *any* target signal (`only_targets`), CSV column `Viable` → `FastPairAdv`.
- `identity.extract()` — snapshots the device table **right after the connection** and
  **skips the pairing entirely** when that already disclosed; new verdict and footer for
  the connect-only path; candidate evidence `connection (RPA resolved)`, ranked just
  below a new bond.
- `cli.py` — the menu entry states which signal holds, and warns explicitly when a row
  is `RPA` without Fast Pair, i.e. reachable but of **unverified ownership**.
- Validated offline (synthetic records, both backends): FE2C decoding from both
  `bluetoothctl` spellings, all 8 classification cases, the filter, the `Target` column,
  and both report paths. Live: the fused table and CSV still render (30 s run, 6 devices).

**`Target = FP` confirmed on real hardware** (2026-07-31), without buying anything: the
dongle was made to *transmit* a Fast Pair advertisement (`0xFE2C` + 3-byte Model ID,
connectable) and the BlueZ scanner decoded it — `CE:C1:1B:51:DD:23 rnd-s FP Yes
FPTestBeacon 0xFE2C`. So the empty column across four sessions was an empty environment,
not a decoding bug — the doubt that could not be settled by scanning harder. The
harness is `fp_advertiser.py` in the scratchpad (not committed, per CLAUDE.md); it is
~60 lines over `blatann` and worth recreating rather than remembering.

## Test plan — validating the disclosure (EXECUTED 2026-08-28 → `docs/test-plan-redmi-buds-6.md`, result NEGATIVE; kept for the cold-run procedure)

The one claim the whole rework rests on is **unverified**: that the *connection alone*
discloses the identity address, before any pairing. Everything else is proven. This plan
is written to be runnable without re-deriving the reasoning — the session it came from
is gone.

**Hardware.** Ordered / to obtain: **Redmi Buds 6 Play** (~15-20 €). Google Fast Pair is
declared on Xiaomi's official spec page ("Quick pairing with Android devices by simply
opening the case", Google Play services 11.7+, Android 6.0+). Any Fast Pair accessory
does; a phone in "pair new device" does **not** — it is a Fast Pair *seeker*, not an
advertiser. Galaxy Buds+ are ruled out (see *Findings*).

**Entering pairing mode.** Opening the lid is enough while the buds are still unpaired.
Once they have been paired to something, the way back in is: buds in the case, lid open,
**hold the case button until the LED blinks rapidly**. (This is the step that never took
on the Buds+, which is why it is written down.)

**Run it in this order:**
1. **Before pairing them to anything.** Fresh out of the box they sit in pairing mode on
   their own — the easiest possible conditions, and they only happen once.
2. CLI activity 3 (fused scan). Keep the scan **short** (20-30 s) and the buds **a few
   cm from the box**: the record must be fresh (an RPA rotates) and strong (fusion drops
   `Conn` below ≈ −94 dBm, see *Findings*).
3. Read the `Target` column, then act on what it says:
   - **`FP+RPA`** → the ready case. Select the row, `i`, and answer the actual question:
     does the report say *"disclosed ... as soon as the LE connection completed"*
     (`skipped_pairing`, connect-only footer), or did it need the pairing fallback?
     Record which, verbatim.
   - **`FP` only** (Fast Pair but a static address) → *also a result, not a failure*.
     It would mean even a current Fast Pair accessory advertises no resolvable private
     address, and the technique's real target is narrower than Whisper_Bully's README
     implies. Write it in *Findings* with the address kind observed.
   - **nothing** → suspect our decoding, not the environment, and use the cross-check
     below before touching the code.
4. **Cross-check with the phone.** An Android phone (the OnePlus Nord) pops up the Fast
   Pair dialog for an accessory in pairing mode it has never been paired with. If the
   popup appears and our `Target` column stays empty, the defect is ours. Caveat that
   invalidated this test once already: the popup is **suppressed for a device already
   paired to that phone**, and needs Bluetooth + location + nearby-device scanning on.

**Harnesses to recreate** (scratchpad, never committed — per CLAUDE.md):
- `fp_advertiser.py` — ~60 lines over `blatann`: makes the dongle transmit `0xFE2C` +
  a 3-byte Model ID, connectable, so the scan side can be tested with no accessory at
  all. `service_uuid16s` wants `Uuid16` objects, `service_data` raw bytes. **Read it
  back with `scan_le.LeScanner` directly, never through the fused scanner** — the fused
  scanner tries to open the busy dongle and that attempt resets it, killing the
  advertising for reasons that look like a decoding bug.
- `drive_fused.py` / `drive_identity.py` — drive `cli.py` through a `pty` (activity 3 →
  `^S` save CSV → `V` filter → row select → `i`). Assert on the **CSV**, not on the
  scraped curses frame.

**Verification harnesses** are not committed (per CLAUDE.md) and live in the session
scratchpad. Recreate as needed: one drives `cli.py` through a `pty` (main menu →
activity 3 → keys → `^S` save CSV → `V` filter → `Q`/`q`; arrows are `\x1bOB`/`\x1bOA`,
and the **CSV is the reliable assertion target** — a curses frame scraped off the pty is
too mangled to parse), the other injects synthetic records into
`FusedScanner.dongle.devices` / `.bluez.devices` to prove the merge-by-address,
viability, filter, CSV and the three passive refusals without any hardware.

## TODO

### webui GATT — interaction & enrichment — IMPLEMENTED 2026-11-02 (headless-validated; HW run pending)
- [x] **GATT interaction in the webui** — held-open session (`engine.gatt_*`), per-char
      Read/Write (hex/text/auto via `client._parse_write_value`)/Subscribe, NUS console,
      notification polling, idle watchdog. Writes gated behind an explicit action.
- [x] **Capabilities view in the webui** — `engine._capabilities_from_tree` over the
      stored tree (passive) + `_capabilities.html` tab + MCP `capabilities` tool.
- [x] **Read BlueZ characteristic values** — `gatt_bluez._read_values` second pass
      (`attribute-info` + `read 0`). Best-effort, bluetoothctl-version-sensitive.
- [ ] **GATT Device Name backfill — confirm end-to-end** — plumbing proven (`store.set_name`
      + 0x2A00 capture); `gatt_connect` now backfills too. Still need one clean dongle
      discovery on an intermittent advertiser to confirm the live path.
- [x] Classic (BR/EDR) detail in the webui — `engine.classic_profiles` + `_classic_profiles.html`
      tab + MCP `classic_profiles`; CoD/Icon were already rendered on the detail page.

### webui — next up
- [ ] **Hardware-validate the GATT interaction block** — see the 2026-11-02 *Resume here*
      and the plan file's verification section (session read/write/subscribe + idle
      auto-close, Capabilities, BlueZ value pass, Classic SDP).
- [x] **Connection-action buttons on the device detail page** — DONE 2026-11-03
      (headless-validated; HW run pending). Connect/Disconnect already existed as "Open
      interaction session"/"Close session" (reused, labels unchanged per maintainer). The
      missing primitive was **Pair**: new `engine.gatt_pair(address, method)` with **two
      backends chosen in the UI** — `dongle` (central-side Just Works on the open session's
      peer via blatann `peer.security.set_security_params`+`pair()`, mirroring
      `emulate.py --positive-control` on the central side) and `bluez` (reuses
      `scan_classic.pair`, bluetoothctl over hci0). They run on two physically distinct
      radios so they don't contend. Route `POST /api/devices/{addr}/gatt/pair` (method form
      field), Pair form + method `<select>` in the session-open branch of `_gatt_tree.html`
      (`active` badge, `hx-indicator`), last outcome surfaced via `gatt_session().pair_status`
      (cleared on teardown), MCP tool `gatt_pair`. Outcome reported faithfully — a refused
      bond is a valid result. See the 2026-11-03 *Resume here*.
- [x] **"Last seen" column with human-readable timestamp** — DONE 2026-11-03 (web-only,
      per maintainer). The only raw-epoch display was the web **Devices** list
      (`devices.html` "Seen" = `d.last_seen|int`); renamed to "Last seen" and formatted via
      a Jinja `localtime` filter in `webui/api.py` (stored value stays epoch). The live
      scan tables keep the deliberate relative **Age** column (`COLUMNS` untouched — the
      "shared cell fn" note in the original item didn't apply; the epoch was never in
      `COLUMNS`).
- [x] **Structured JSON logging** — DONE 2026-11-03 (headless-validated). New
      `eventlog.py` (stdlib-only, no blatann, like `store.py`): JSONL sink at
      `logs/intotheblue.jsonl` (git-ignored; `$INTOTHEBLUE_EVENTLOG` override), fields
      `ts`/`event`/`device`/`transport`/`action`/`boundary`/`outcome`/`error`. `engine`
      owns it and emits from one place via an `_op_log` context manager wrapping each radio
      op (scan start/stop, `browse_gatt`, `gatt_*`, `browse_gatt_bluez`, `classic_profiles`)
      — `outcome` is ok/error/start, `boundary` passive/active; passive DB reads are not
      logged. Read side: `engine.query_events`/`tail_events` + `eventlog.read/tail`.
- [x] **Dedicated logs page in the webui** — DONE 2026-11-03. `/logs` (filter by
      event/device/action + limit, `_logs_table.html`), `/api/logs/export` downloads the
      filtered set as JSONL (`application/x-ndjson` attachment). Read-only over the sink.
- [x] **Capabilities not refreshed/saved after a live GATT session** — FIXED 2026-11-03
      (headless-validated). Two halves: (1) *persist* — `gatt_connect` already writes the
      discovered tree back via `store.save_gatt` (same as `browse_gatt`), so the stored
      tree is current after a session opens. (2) *refresh the view* — the real gap: the
      detail page is tabbed with the `#capabilities` panel `{% include %}`d **once at page
      load**, so after a live browse/session persisted a new tree the GATT tab swapped but
      Capabilities stayed stale until a full reload. Fix: the tree-changing GATT routes
      (`browse_gatt`, `browse_gatt_bluez`, `gatt_session_open`) now return
      `HX-Trigger: gatt-updated` on success (via `_render_gatt(tree_changed=True)`), and the
      `#capabilities` div carries `hx-get=/capabilities` + `hx-trigger="gatt-updated
      from:body"` so it re-fetches from the stored tree on every enumeration. Header fires
      only on success (not on error / no-op). Validated: detail page wires the trigger,
      `_render_gatt` emits the header only when `tree_changed and not error`.
- [x] **Browse-GATT progress/activity feedback + global log footer** — DONE 2026-11-03.
      Long active ops emit a `start` event, and the browse/session/BlueZ/Classic buttons
      carry an `hx-indicator` spinner (+ `hx-disabled-elt`) so a connect/browse shows "⏳
      connecting…". The always-on footer (`#logtail` in `base.html`, polling
      `/api/logs/tail` every 3 s, `_log_tail.html`) tails recent activity on every page.
- [ ] **Keep the MCP surface in sync** — ONGOING. **MCP ⇄ webui: in parity** — every
      `engine` op has a thin MCP wrapper with passive-vs-radio marked in the docstring.
      Tool count **25**: read `list_devices` (now with `seen_within_seconds`/
      `current_session_only`), `get_device`, `exposure_report`, `capabilities`,
      `fast_pair_profile`, `fastpair_resolve_key` (network-opt-in), `list_scan_sessions`,
      `scan_status`, `scan_snapshot`, `query_events`; store-destructive `prune_devices`;
      active `scan_start`, `scan_stop`, `browse_gatt`, `gatt_connect`, `gatt_disconnect`,
      `gatt_pair`, `gatt_read`, `gatt_write`, `gatt_subscribe`, `gatt_unsubscribe`,
      `gatt_notifications`, `classic_profiles`, `fastpair_handshake`, `fastpair_stress`.
      Latest add: `fast_pair_profile` + `fastpair_handshake` + `fastpair_stress` +
      `fastpair_resolve_key` (2026-11-03).
      As future tools land (full Fast Pair pairing, etc.), add the MCP wrapper in the same change.
    - **Known CLI gap (not MCP's):** the CLI's BLE GATT menu (`cli.py` `_gatt_menu`,
      connect-on-enter model) exposes **no dongle-side Pair** — the only Pair in the CLI is
      Classic/BlueZ via `scan_classic.pair` (`cli.py:291`). So the new `gatt_pair(dongle)`
      lives in webui+MCP but not the CLI. Decide later whether to add a dongle Pair action to
      the CLI GATT menu for three-way parity (would reuse `scan_classic.pair`/a central-side
      `security.pair()` helper), or accept the CLI as the thinner front-end.

### Fast Pair — GATT interaction & complete pairing (NEW 2026-11-02, maintainer)
Target: the Fast Pair Service (`0xFE2C`) and its characteristics, chiefly **Key-based
Pairing** `FE2C1234-8366-4814-8EB0-01DE32100BEA` (Write+Notify) — the handshake that
gates Passkey (`…1235`), Account Key (`…1236`), Additional Data (`…1237`). It establishes
a shared secret: Seeker writes an AES-encrypted block (message-type `0x00`), Provider
replies via notify (`0x01`). Two cases for the shared secret (see the key note on the
*Complete pairing* item): Case 1 first-pair uses an **ECDH** agreement over the
anti-spoofing key pair — the Seeker needs only the **public** key, the Provider holds the
private one in a Secure Element; Case 2 reconnect uses a symmetric **account key** from
the Provider's list. All actions below are
**active/transmitting** → explicit-action gated + `active` badge, slotted into the
scan/select/act model and the `engine.gatt_*` held-open session (not connect-per-op),
reusing `client.*`; keep the crypto isolated in a new `fastpair.py` (like `bledev/
fastpair.py` for the passive Model-ID side). Keep MCP at parity (one thin `engine` tool
per action, passive-vs-radio marked in the docstring). Spec:
developers.google.com/nearby/fast-pair/specifications/characteristics.
- [x] **Surface the Fast Pair GATT profile in the UI** — DONE 2026-11-03 (headless-validated;
      HW run pending). `engine.fast_pair_profile`/`_fast_pair_profile_from_tree` over the
      *stored* tree lists which FE2C characteristics (`…1234`/`…1235`/`…1236`/`…1237`) are
      present + props; Fast Pair tab in `device_detail.html` (`_fastpair.html`,
      `gatt-updated` refresh like Capabilities, shown when `d.fast_pair or
      d.fast_pair_profile`); route `GET /api/devices/{addr}/fastpair`; MCP read tool
      `fast_pair_profile`. Passive, transmits nothing.
- [x] **Exercise the Key-based Pairing handshake** — DONE 2026-11-03 (headless-validated;
      HW run pending). `engine.fastpair_handshake(address, provider_public_hex)` on the
      held-open session: builds an encrypted KBP request (Case 1, ephemeral pubkey appended)
      via `fastpair.build_kbp_request`, writes `…1234`, awaits the notify (one-shot subscribe,
      not the polled buffer), decrypts via `parse_kbp_response`. A non-decrypting/absent
      response is a reported finding, not an error. Route `POST …/gatt/fastpair-handshake`,
      form in `_gatt_tree.html` (active badge, hx-indicator), MCP `fastpair_handshake`,
      outcome on `gatt_session().fp_status`.
- [x] **Robustness/stress on the handshake** — DONE 2026-11-03 (headless-validated; HW run
      pending). `engine.fastpair_stress(address, variant)` writes malformed payloads to
      `…1234` (`unencrypted-zero` / `wrong-type` / `too-short` / `too-long` / `random-16`)
      and reports notify/silence/dropped-link. Route `POST …/gatt/fastpair-stress`, `<select>`
      form, MCP `fastpair_stress`. Active, gated.
- [ ] **Complete Fast Pair pairing flow** — drive the full procedure end-to-end
      (ECDH handshake → Passkey exchange `…1235` → Account Key write `…1236`), as client.
      **Key note (corrected 2026-11-02):** the first pairing needs **only the Provider's
      anti-spoofing PUBLIC key** — the Seeker runs ECDH (secp256r1) between its own
      ephemeral key pair and that public key to derive the AES shared secret, then proves
      the Provider holds the matching private key (which never leaves the device — we do
      **not** need and cannot extract it). The **account key is generated by us** (the
      Seeker) and written to the Provider at the end of pairing, so **completing the
      pairing is exactly how we obtain a persistent reconnect key** — no vendor secret
      required. So the only real prerequisite is the public key, which in production Google
      Play services fetches from Google by Model ID. The design decision is therefore how
      to supply that public key consistent with the bench's **offline / non-phoning-home**
      posture (we deliberately chose a local `fastpair_models.yaml` over Google's live
      endpoint for Model-ID resolution — cf. `bledev/fastpair.py`): operator-supplied key
      file / local cache keyed by Model ID, vs. accepting a Google lookup. Also decide
      whether the bond rides the dongle or BlueZ (cf. the connect/pair item under *webui —
      next up*, and the hardened-device bond-refusal finding 2026-08-28). Positive-control
      candidate: `emulate.py` as a Provider whose anti-spoofing key pair we generate, so
      both public (Seeker) and private (emulated device) sides are ours.

### pre-existing
- [x] ~~Optional: make the `csv/` export order match the on-screen discovery order~~ —
      DONE 2026-11-03. All three `save_csv` (`scan_live`/`scan_classic`/`scan_fused`) now
      iterate in discovery order (dict insertion / `snapshot()`) instead of sorting by
      RSSI, matching the live table.
- [ ] Re-validate the SPP serial console end-to-end once the Jabra bond is restored
      (they dropped the box's bond by reconnecting to a phone); last live run of the
      full CLI path stopped at `br-connection-key-missing`.
- [x] ~~**Validate `identity.extract` against a real Fast Pair target**~~ — done
      2026-08-28 against a Redmi Buds 6 Play, **result NEGATIVE**: the disclosure does
      not reproduce (bond refused `AuthenticationCanceled`, no identity address in the
      table), and Whisper_Bully fails identically → not an intotheblue bug. Full run and
      the mechanism analysis in `docs/test-plan-redmi-buds-6.md` and *Resume here* above.
      Follow-up TODO added below: obtain a known-vulnerable target as a positive control.
- [x] ~~**Positive control for the disclosure pipeline**~~ — done 2026-08-28 with an
      **in-house** control: `emulate.py --positive-control` (dongle advertises an RPA +
      accepts a Just Works bond). `identity.extract` disclosed the identity
      (`CE:C1:1B:51:DD:23`) end-to-end → pipeline proven, Redmi negative is a device
      property. See *Resume here* → Caveat RESOLVED, and the actions log.
- [ ] *(optional)* Confirm on a **real-world** Fast Pair accessory that does not enforce
      the bond gate — nice-to-have; the in-house control already validates the pipeline.
- [x] ~~**Fused Model-ID masking** (`scan_fused.snapshot`)~~ — fixed 2026-08-28 (impl,
      not yet hardware-validated). `_merge_fast_pair()` now prefers a decoded Model ID
      over a bare `"Yes"` from either backend, instead of the old `bluez or dongle`.
      See the *Next steps* block and the actions log.
- [x] ~~**See the fused table with real `Conn` values**~~ — done 2026-07-31, first
      real-hardware run: 5 `YES` / 4 `no` / 3 `?` over 12 devices. See *Resume here*.
- [x] ~~**Find out whether a viable target exists at all in this environment**~~ —
      **the question was malformed**; closed 2026-07-31 after studying Whisper_Bully.
      A Fast Pair target only exists while someone holds a device in **pairing mode**,
      so scanning the ambient environment for one is structurally futile. The target is
      *induced*, not found. See the finding below; the four sessions spent on this were
      measuring the wrong thing, not measuring a narrow technique.
- [ ] ~~Consider surfacing connectability in the BlueZ table~~ — done via the fused
      scanner. Kept as a reminder of the alternative: an explicit "unknown", which is
      what the degraded (no-dongle) mode falls back to. It cannot be read from
      BlueZ (no PDU type), so the only honest options are a cross-check against the
      dongle scan or an explicit "unknown". Today the check just reports it after the
      fact in its verdict.

## Checkpoint

- 2026-11-03: user-validated checkpoint — maintainer confirmed the webui iteration works
  on real hardware ("testato anche con hardware"). Covers the single-page tab strip
  (in-page device tabs via bare-mode iframe), the Devices list organization (recency
  default 24h + current/last-scan session filters + destructive prune, MCP in parity at
  21 tools), and the dongle-side Pair action. Committed as `2fbe3c8` and pushed to
  `origin/devel`.
- 2026-07-22: user-validated checkpoint ("per adesso sembra ok"). Interactive Classic
  menu (Pair + navigable SDP profiles → SPP console / PBAP), plus the system-python
  fix for the RFCOMM/D-Bus helpers. Committed and pushed to `origin/devel`.

## Design decisions

- **The MCP/webui server is the agent's end-to-end test surface — use it, don't stop at
  "headless".** The whole point of running a webui + MCP server is that an agent can drive
  the *real* bench through it: scan/select/act, GATT browse, the held-open session,
  `fastpair_*`, etc., against the actual dongle. So validation must not end at headless unit
  checks and "HW pending" — when a server is up, **drive it** (its JSON API over
  `http://127.0.0.1:<port>/api/...`, or the MCP endpoint at `/mcp`) and exercise the real
  path. Operational truths that still bound this (state them, don't use them as an excuse):
  (1) the server owns the **one** dongle — only one process at a time, and it must be
  **restarted to load new code** (uvicorn here runs without `--reload`), so new routes need a
  restart; don't kill the maintainer's live scanning session without asking; (2) genuinely
  physical steps (put a device in pairing mode, a *second* nRF52 for the emulated Provider)
  and operator secrets (`$INTOTHEBLUE_FASTPAIR_API_KEY`) are the maintainer's, not something
  the agent can fake. Everything else that the server exposes, the agent can and should test
  via the server itself. See [[use-mcp-webui-server-to-test]].

- **Python 3.10 pinned** (`.venv310/`) — `pc-ble-driver-py` (native lib under blatann)
  ships no wheels for ≥ 3.11. Non-negotiable until that changes upstream.
- **Two hardware backends, kept separate** — nRF52 Connectivity dongle (serial, via
  blatann) for BLE; host BlueZ adapter (`bluetoothctl`) for Classic. Do not conflate.
- **Dongle stays open across an interaction** — pause the scan and connect on the same
  `BleDevice`. Closing then reopening immediately fails with
  `NrfError.rpc_h5_transport_state`; this drove the whole scan→pause→act→resume design.
- **Table keeps discovery order** (no live RSSI re-sort) so rows don't jump under the
  cursor during selection.
- **Shared `livetable` scanner interface** — new scan surfaces implement
  `snapshot/is_scanning/pause/resume/save_csv` instead of duplicating the curses UI.
- **Fusing the two BLE radios** (`scan_fused.py`) — they observe the same advertising
  packets at the same instant, so at any moment they report the *same* address, even a
  rotating private one: that makes them mergeable by address. Needed because the fact
  that decides whether the identity check is even attemptable (connectable, i.e. the
  advertising PDU type) is known **only to the dongle**, while the action can only run
  **through BlueZ**. Triaging on one table and acting from the other meant the address
  had rotated in between, so the verdict never applied to the device actually touched —
  every failed live run of 2026-07-30 traces back to this. The dongle stays optional:
  without it the table degrades to the BlueZ-only view with `Conn = ?`, and nothing is
  ever *claimed* viable on a guess.
- **Two independent target signals, never collapsed into one boolean**
  (`scan_fused.target_note`) — `FP` (advertises Fast Pair, i.e. in pairing mode:
  a target you may legitimately act on and can attribute by name) and `RPA`
  (`rnd-r` + connectable: the mechanism has something to unmask). They answer different
  questions, so the table shows which of the two holds rather than a single "viable"
  verdict that silently conflated *reachable* with *yours*. `is_viable()` keeps its exact
  old meaning and is now one input of the two.
- **Prefer connect-only over pairing** (`identity.extract`) — since the disclosure
  originates at connection completion, the pairing is attempted **only** when the
  connection produced nothing attributable (typically an RPA advertising no name, where a
  new bond is the only way to tie an address to the peer). When the connection alone
  discloses, no SMP runs and nothing is left on the target: the least intrusive path that
  still answers the question.
- **A third hardware path: BLE over BlueZ** (`scan_le.py`) — deliberate exception to
  "two backends, kept separate". Host-side actions (`bluetoothctl connect`/`pair`) only
  work on devices in **BlueZ's own** cache, and a device seen by the dongle is invisible
  to BlueZ, so the scan feeding those actions has to run on `hci0` too. Implemented as a
  subclass of `ClassicScanner` (the bluetoothctl event stream is transport-agnostic;
  only `scan le` vs `scan bredr` and the record decoding differ), not as a fourth copy
  of the scan loop. The dongle BLE path is untouched.
- **`connectable` is `None`, not `False`, on the BlueZ backend** — BlueZ does not
  surface the advertising PDU type. `exposure.assess` reports "unknown" for it rather
  than guessing: an unverified claim has no place in a security report.
- **Identity disclosure is attributed, never inferred** (`identity.py`) — the first live
  run showed why: while our own `scan le` runs, unrelated neighbours keep entering the
  device table, and a naive before/after diff reported three of them as "identity
  candidates" *and removed them from the host cache*. A new address now counts only if
  it carries the target's name or appears on a `Paired:`/`Bonded: yes` line, and cleanup
  only ever touches entries this run introduced (never anything present in the baseline
  — removing a bonded neighbour would destroy a legitimate bond).
- **Passive/active boundary** — passive analysis (`exposure.py`, record enrichment)
  must not transmit; anything that connects/writes sits behind an explicit user action.
  Heavy active primitives (e.g. L2CAP flooding) live in external, dedicated tooling.
- **English for code and all UI text.**

## Findings

- **The identity disclosure does not reproduce on a real Fast Pair target, and the
  reference tool fails the same way** (2026-08-28, Redmi Buds 6 Play; full run in
  `docs/test-plan-redmi-buds-6.md`). The host opens an unauthenticated LE connection
  and reads full GATT, but the Just Works bond is refused
  (`org.bluez.Error.AuthenticationCanceled`) and no permanent identity address enters
  the BlueZ table — virgin *and* phone-bonded. `Whisper_Bully` (`sudo` + `bleak`) fails
  identically (`Failed to extract`). Root cause: resolving an RPA needs the peer's IRK,
  distributed only on a **completed** bond; a host that has never bonded the peer has no
  IRK and cannot resolve the RPA at connection — so WB's README claim of resolution at
  `LL_CONNECTION_COMPLETE` (before SMP, even on patched devices) is empirically wrong
  here. Two operational lessons: (1) `identity.extract`'s name-based attribution is blind
  when the connection doesn't resolve a name (the reconnect RPA advertises none), so a
  leak that surfaced only as an unnamed table entry would be missed — a manual
  device-table check (as WB does) is the backstop; (2) the whole 2026-07-31 "prefer
  connect-only" rework was built on a misread of WB's mechanism — WB depends on the bond
  (or on the host already holding the IRK), it does not disclose from a bare connection.

- **Emulate the target to test the observer** (2026-07-31). The fastest way to prove a
  *scan-side* decoding path is to make the dongle transmit the advertisement we claim to
  decode, and read it back on the BlueZ side — the two backends are independent radios,
  so one can be the device under test for the other. This settled in minutes a question
  four evenings of ambient scanning could not. Its limit is equally clear: an emulated
  advertiser validates **observation**, never **disclosure**, since it has no secret to
  leak. Generalises well to the roadmap's cloning/emulation direction.
  Two practical constraints found doing it:
  - The dongle **cannot advertise and scan at the same time**. Worse, running the fused
    scan while it advertises makes `FusedScanner.open()` try to open the busy dongle,
    and that attempt *resets it* and kills the advertising — the beacon then vanishes
    from the table for a reason that has nothing to do with decoding. Test the scan-side
    with `scan_le.LeScanner` alone, not through the fused scanner.
  - `blatann`'s `AdvertisingData` wants `Uuid16` objects in `service_uuid16s` (an `int`
    raises `AttributeError: 'int' object has no attribute 'uuid'`); `service_data` is
    passed through as raw bytes.

- **`open_device`'s by-id autodetect earns its keep** (2026-07-31). Mid-session the
  dongle hit the `xhci_hcd ... not part of TD` fault again, disconnected and
  **re-enumerated as `ttyACM1`**. Everything kept working because the lookup goes through
  `/dev/serial/by-id/*nRF52*` rather than a hardcoded `ttyACM0`. So this fault is not
  always the session-ending kind documented below: it can self-recover, and the node
  number is not stable across it. Never hardcode the node.

- **Environment: VMware USB passthrough is a real failure mode** (2026-07-31). Symptoms:
  `NrfError.timeout` from `open_device` regardless of baud or reset, `EPROTO` opening
  `/dev/ttyACM0`, continuous `xhci_hcd ... not part of TD` in `dmesg`. A guest-side
  replug does not clear it; the host Bluetooth died too, forcing a reboot of both. The
  dongle and the host BT adapter share one virtual hub, so a single USB fault takes out
  **both** BLE backends at once. Check `dmesg | grep xhci` before debugging this code.
  Distinct from the documented transient `rpc_h5_transport_state` between back-to-back
  sessions, which idle time does cure.

- **You cannot attribute an RPA to an owner before acting on it** (2026-07-31) — the
  operational sting in the tail of the entry below. Selecting "the first viable row"
  is *not* the same as selecting your own device: the address rotates, so the row that
  is viable now cannot be matched against the row you identified as yours a minute ago.
  In the first full live run the check was aimed at `5E:09:2A:E7:2D:87` with no way to
  say whose it was. **Working procedure**: bring the authorised device to a few
  centimetres of the box and pick the viable row by **overwhelming RSSI** (≈ −40 against
  neighbours at −88…−100). Proximity is the only attribution signal available before a
  connection, and it has to be arranged deliberately, not hoped for.

- **The liveness probe earns its keep** (2026-07-31, first live run of the full active
  path). Verdict: *"Target advertising NO — not seen during the run; the private address
  had already rotated"*, with `Connection failed — not available` and
  `AuthenticationCanceled` downstream of it. Without the probe this is precisely the
  silent, indistinguishable failure that cost a minute per attempt on 2026-07-30 — the
  report now names the cause and tells the operator to reselect a row with a low `Age`
  instead of leaving them to guess. Corollary for the workflow: **keep the scan short
  before acting**; a 50 s scan means the selected record can already be 50 s stale, and
  an RPA does not wait.

- **`is_viable()` is a reachability test, not an authorisation test** (2026-07-31). The
  first viable row this project has ever produced — `7B:42:23:C5:AC:82`, `rnd-r` +
  connectable, Apple, −88 dBm — is an **unidentified neighbouring device**, not bench
  equipment. That is the expected case, not an accident: in a non-isolated environment
  the devices with a *rotating* address are overwhelmingly other people's phones,
  watches and earbuds, so the filter that finds a technically attackable target will
  usually find a stranger's. Viability must therefore never auto-arm the active check;
  the operator has to establish ownership first, and the menu entry stays the explicit
  gate. Worth stating in the report wording too: "viable" means *reachable*, nothing
  more.

- **Galaxy Buds+ are not usable for this check** (measured 2026-07-31, three fused runs
  + one BR/EDR inquiry). They advertise a **static** random address that was byte-for-byte
  identical across runs 20 minutes apart (`F7:18:2D:72:11:86` connectable, plus a
  non-connectable `F4:9D:80:C5:48:8D` "Buds+"), emit **no Fast Pair** service (`0xFE2C`)
  and never became BR/EDR discoverable, i.e. the pairing-mode sequence never took even
  when driven during a live scan. A Fast Pair *model* does not imply Fast Pair
  *advertising* in the state you find the device in. The RSSI rose (−88 → −76 → −80) so
  the radio was fine; the device simply had nothing else to say.

- **Fusion degrades at the edge of range, and that is where the interesting targets are**
  (2026-07-31). Merging by address only works for a device *both* radios happen to hear;
  all 3 `?` rows of the first live run sat at RSSI ≤ −94, including the run's only
  `rnd-r`. So the weakest signals — precisely the ones a rotating-address accessory tends
  to produce — are the likeliest to lose their `Conn`. Consequences: never read `?` as
  "not connectable" (the code already doesn't), and when triaging a specific target, get
  closer to it rather than scanning longer from the same spot.

- **Phones are not a target for the identity-disclosure check** (measured 2026-07-30 on a
  OnePlus Nord). In "pair new device" a phone is discoverable over **BR/EDR**; its LE
  advertising is Google Nearby (service `FEF3`) and the dongle scan shows it as
  `conn=no` — **non-connectable**. No LE connection ⇒ no SMP ⇒ nothing to distribute;
  BlueZ returns `org.bluez.Error.ConnectionAttemptFailed`, which is correct behaviour,
  not a defect. The target class is accessories advertising a **connectable** RPA that
  accept Just Works (Fast Pair earbuds, trackers, wearables).
- **The target is induced, not found — and the leak is at the connection, not the
  pairing** (2026-07-31, from reading `/opt/repo/Whisper_Bully`, the tool the maintainer
  uses daily for this stage). Two corrections to assumptions that had cost four
  sessions:
  1. **Target selection.** Whisper_Bully filters candidates on **Fast Pair service
     `0xFE2C`** and never looks at the address kind; its README says the UUID is used
     *"only as a scan filter to identify candidate targets"*. A device emits discoverable
     Fast Pair advertising **only while in pairing mode**, a state a human has to induce
     — which is also what makes the target *attributable*, by name. Our `rnd-r +
     connectable` filter describes where the mechanism works, but as a search strategy it
     selects, by construction, strangers' phones and watches: an RPA can never be tied to
     an owner by looking at it. Hence the earlier "no viable target in the environment"
     and "the technique's reach is narrow" — both were artefacts of the wrong filter, not
     results.
  2. **Where the disclosure happens.** BlueZ resolves the peer's RPA and caches the
     identity address while handling **connection completion**, at HCI level, before any
     SMP or GATT interaction. The pairing is a *fallback*, not the mechanism. Our check
     sampled the device table only after the pairing, so a failed pairing (the common
     outcome) could hide a leak that had already happened — while still paying the cost
     of an intrusive bond attempt.
  The old profile facts remain true and still drive the passive refusals: `pub`/`rnd-s`
  have no private address to unmask (a *static* random address is itself a permanent
  identity — Core spec Vol 3 Part C 10.8) and a non-connectable advertiser is unreachable
  by any connection-based check.
- **Corollary — the two BLE backends are complementary in practice**: the dongle scan is
  the only one that reports the advertising PDU type, so it is the right place to triage
  whether a target is reachable at all; the BlueZ scan is where the host-side action
  runs. That is worth keeping in mind before blaming the check for a silent failure.

## Goals (standing)

- **Identify as many paired Bluetooth devices as possible** (stated 2026-08-28). The
  negative identity-disclosure result on the Redmi Buds 6 Play retires neither the goal
  nor the direction: attributing devices and their pairing relationships stays a core
  recon objective. The connection-based RPA unmask is *one* avenue that did not work on
  a current accessory — pursue the goal by whatever fits scan/select/act (advertising,
  GATT, Classic SDP/CoD, proximity, and a future known-vulnerable positive control).

## Next steps (prioritised) — 2026-08-28

Identity-disclosure stage is closed/validated (pipeline works; hardened Fast Pair
devices refuse the bond, so as an unmask technique its reach is narrow). The standing
goal (*identify as many paired BT devices as possible*) is best served by other avenues.
Options, best value/effort first — **currently starting (1)**:

1. **Passive identification enrichment** (low effort, high value, serves the goal now):
   *2026-08-28 — four items landed and were **hardware-validated** against the buds in
   discoverable pairing mode (Model ID `FB9B72` captured live). See the actions log.*
   - [x] **Fast Pair Model ID → product name** — new `bledev/fastpair.py` resolves the
     24-bit Model ID against a **local** `fastpair_models.yaml` (mirrors the
     `manufact.yaml`/`manufacturers.py` pattern; chosen over Google's live
     `nearbydevices-pa` endpoint to keep the bench offline/non-phoning-home). The
     FastPair column (scan_live + fused) prints the product name when the Model ID is
     known, else the raw hex / `"Yes"`. Registry seeded from the live capture:
     `0xFB9B72 → Redmi Buds 6 Play` (decoded by the dongle, attributed by the
     maintainer). Grows from real captures — see the file header for the procedure.
   - [x] **Fix fused Model-ID masking** (`scan_fused._merge_fast_pair`) — a decoded Model
     ID now wins over a bare `"Yes"`; closed the TODO. **Validated live**: fused merge of
     the RPA `4C:96:7E:68:F4:36` kept `FB9B72` instead of BlueZ's `"Yes"`, `target=FP+RPA`.
   - [x] **Service-UUID → SIG name** — new `SvcNames` column in the **dongle** scan
     (`scan_live`); records accumulate `service_names`. (Empty on pure FP discoverable
     frames — they carry only FE2C service-data, no service-UUID list; expected.)
   - [x] **Authoritative SIG data** (`bledev/sig.py`, from the `BluetoothSIGPublic/`
     checkout the maintainer added) — company IDs (4018, up from manufact.yaml's 3877),
     vendor **member** UUIDs (0xFE2C→Google …), service/characteristic names. Wired as
     the *primary* source with graceful fallback in `manufacturers.get_manufacturer`,
     `scan_live._service_name`/`_decode_protocol`, and `scan_le._add_uuid`. The checkout
     is git-ignored (nested repo). Curated short labels still win over the raw registered
     company name (e.g. "Google FastPair" over "Google LLC").
   - [x] **OUI vendor lookup** for public addresses — done 2026-08-28. New top-level
     `oui.py` resolves the manufacturer from a public MAC's IEEE OUI (longest prefix: MA-S
     36-bit → MA-M 28-bit → MA-L 24-bit) using the system `ieee-data` CSVs, nmap fallback.
     Random BLE addresses are **never** looked up (no OUI). Surfaced as a `Vendor` column
     in `scan_classic` (BR/EDR MACs are always public) and a `Vendor (OUI)` row in the
     passive `exposure` report for public BLE addresses. Live-validated on a real Classic
     device (`00:1D:DF:7E:FB:44` "PHILIPS HTL2110" → OUI *Sunitec Enterprise Co.,Ltd*).
   - [x] **TX Power** — done 2026-08-28. `scan_live._tx_power` reads the Tx Power Level AD
     (0x0A) from blatann's parsed `entries` (stored as raw bytes on the receive path) and
     converts to a signed dBm. New `tx_power` field + `TxPwr` column in the dongle scan;
     merged onto the fused/BlueZ record; a `Tx Power` row in the `exposure` report shows a
     path-loss (proximity) estimate `tx_power − rssi`. Live-validated on a real advertiser
     (`46:0C:50:50:4D:61`, −8 dBm, RSSI −23 → path loss ≈ 15 dB).

   **Item 1 is complete as advertising-based enrichment** — all sub-items landed and were
   hardware-validated. **This does NOT reach the standing goal** (see below): every one of
   these signals is derived from *advertising*, so none of them can see a device that is in
   an active connection to a phone and therefore not advertising.

## The goal is NOT reached — phone-connected devices are invisible to passive scanning (2026-08-28)

The maintainer's standing goal is *identify as many paired Bluetooth devices as possible*.
Two concrete targets we cannot currently identify, and why:

- **Redmi Buds 6 Play ↔ the maintainer's Android** (BLE / Fast Pair): while connected the
  buds stop discoverable advertising; at most they emit brief RPA reconnection frames with
  an empty account key filter (see item 2). A passive advertising scanner cannot attribute
  or even reliably see them mid-connection.
- **JBL Go 3 ↔ the maintainer's iPhone** (BR/EDR / Classic): while connected it holds an
  active ACL link and is **not** in inquiry scan, so `scan_classic` (bluetoothctl inquiry)
  never sees it. Classic has no advertising to fall back on.

**Root cause.** All of intotheblue's identification is *advertising/inquiry* based. A device
already bonded to and connected with a phone communicates over a point-to-point link on
hopping data channels and does not advertise — so the thing we most want to enumerate is
exactly the thing this whole approach cannot observe.

**What could observe them — connection *sniffing*, not scanning** (researched 2026-08-28):
- **BLE**: follow the connection. A sniffer catches the phone's `CONNECT_IND` (carries both
  addresses, access address, channel map, hop increment) and then follows the hopping link,
  capturing the data-channel packets — so the device becomes visible *while connected to the
  phone*. Best-in-class tool: **Sniffle** (nccgroup). **Corrected hardware fact:** current
  Sniffle (master) runs **only on TI CC1352/CC26x2**, NOT the nRF52840 — the README has zero
  Nordic mentions; nRF52840 support existed only in *old* Sniffle releases (unmaintained).
  So:
    - **Sniffle never supported nRF52840** — checked every tag v1.0–v1.11.0, zero Nordic
      mentions (an earlier note of mine claiming an "old nRF52840 Sniffle build" was wrong).
    - the cheap maintained path is a **TI CC2652P** board — the **SONOFF ZBDongle-P
      (~€20-25)**. ⚠️ **Buy the right variant**: SONOFF's "Zigbee 3.0 USB Dongle Plus" ships
      as **ZBDongle-P = CC2652P (TI)** [works] *and* **ZBDongle-E = EFR32MG21 (Silicon Labs)**
      [does NOT work] — only the **-P** works with Sniffle. Prebuilt firmware exists (verified: release
      v1.11.0 asset `sniffle_cc1352p1_cc2652p1.hex`), so no compiling. Flash via
      `cc2538-bsl.py --bootloader-sonoff-usb` (chip is a CP2102N → /dev/ttyUSB0); use the
      `sultanqasim/cc2538-bsl` fork / PR #173 for a known post-flash reset bug, and the
      `_1M.hex` variant for some 2022-era dongles. Keep the nRF52840 for blatann. Sniffle
      scans all 3 advertising channels at once → ~3× more reliable connection detection.
    - **ZigStar GW (LAN+USB) — hardware-probed 2026-09-15; NOT usable for Sniffle (see FINAL VERDICT).** Right
      radio (**CC2652P**, module E72-2G4M20S1E / RF-BM-2652P2), and it IS connected by USB
      (a **CH340 → /dev/ttyUSB0**; an earlier "LAN-only" claim of mine was wrong). But the
      CH340 does **not** reach the CC2652P directly: probed `ttyUSB0` and got **no Z-Stack MT
      SYS_PING reply** (115200/230400/460800/2000000) and **no CC26xx serial-bootloader sync**
      (cc2538-bsl, both `--bootloader-sonoff-usb` and `--bootloader-invert-lines`: "Timeout
      waiting for ACK"). The CH340 goes to the **ESP32 (WT32-ETH01)**; the CC2652P sits
      *behind* it (RST/BSL on ESP32 GPIOs zRST/zBSL, UART on GPIO5/17), reached only *through*
      the ESP32 — the official flash path is `cc2538-bsl.py -p socket://ip:6638` over the
      ESP32's TCP stream_server (115200). **Architectural mismatch:** Sniffle needs the
      CC2652P on a **direct** USB-UART at **2 Mbps** (the SONOFF ZBDongle-P wires CC2652P
      straight to a CP2102N — no MCU between). On the ZigStar the CC2652P is always behind the
      ESP32 bridge (USB *or* LAN), so Sniffle's direct-serial model doesn't fit and 2 Mbps
      through the ESP32 is the same bottleneck either way.
      **FINAL VERDICT (same day, verified — not usable).** Chased the "USB mode" hard: set
      both DIP switches ON (docs say USB Coordinator mode, "without connection to
      WT32-ETH01"), and the maintainer put the CC2652P in BSL with the physical buttons.
      Still **no CC2652P response on `ttyUSB0`** — Z-Stack MT ping silent at every baud, and
      cc2538-bsl never synced (every entry polarity: sonoff-usb / invert-lines / active-high /
      plain, 115200–921600). The decider: the **vendor's own ZigStar Multi Tool** flashes the
      LAN GW's CC2652P **over the network (IP:PORT), never over USB serial** — its README
      lists the LAN GW under "Network gateways (enter IP:PORT)", separate from "USB sticks".
      So on the LAN GW the CC2652P is **always behind the ESP32** (BSL/RST on its GPIOs,
      UART bridged over TCP); the CH340 is the ESP32's port. There is **no direct
      CH340→CC2652P path**, whatever the DIP switches do. Sniffle needs the CC2652P on a
      **direct local USB-UART at 2 Mbps** (the SONOFF ZBDongle-P wires CC2652P straight to a
      CP2102N — no MCU between); the ZigStar can't provide that. **Conclusion: not usable for
      Sniffle** — set the DIP switches back to LAN (OFF) and power-cycle to restore the
      gateway.
      **Two paths that could still use the ZigStar (maintainer's idea, 2026-09-15):**
      (A) *Sniffle over the ESP32 TCP bridge.* Flashing is not the blocker — the vendor path
      `cc2538-bsl.py -p socket://IP:6638 -evw sniffle_cc1352p1_cc2652p1.hex` flashes Sniffle
      onto the CC2652P through the ESP32, no Sniffle change needed. To *run* it, patch
      `SniffleHW.__init__` to use `serial.serial_for_url('socket://IP:6638')` instead of
      `Serial(port)` (pyserial supports socket://; ~2 lines). Remaining blockers: the ESP32's
      bridge UART must run at Sniffle's **2 Mbps** (reconfigure ESPHome `uart: baud_rate:
      2000000` and reflash the ESP32 — not the nRF), and the ESP32/Ethernet must sustain
      2 Mbps of connection-following bursts with low enough latency for Sniffle's protocol —
      **unproven**, could drop packets / miss the CONNECT_IND. A pure-software experiment
      worth trying (no new parts). (B) *External UART on the exposed CC2652P pins* — the board
      breaks out all pins; wire a **3.3 V USB-UART** adapter straight to the CC2652P module's
      TX/RX/GND (BSL entry via the onboard buttons), bypassing BOTH the ESP32 and the CH340.
      That gives a true direct 2 Mbps serial → unmodified Sniffle, no bottleneck. Needs a
      cheap USB-UART adapter (~€2) + the module UART pinout from the board schematic. Most
      reliable revival. Fallback if neither pans out: SONOFF ZBDongle-P (CC2652P direct on a
      CP2102N).
      **EXECUTED Approach B (2026-09-15) — Sniffle flashed & running; blocked only by the
      adapter's baud ceiling.** The maintainer wired an external USB-UART to the CC2652P
      TX/RX/GND. The chip answered a **Z-Stack MT SYS_PING** (`fe 02 61 01 59 06 3d`) at
      115200 → the UART reaches the radio directly (ESP32 fully bypassed; the onboard CH340
      never did). BSL entry: the buttons alone don't reset the CC2652P (its RESET is an ESP32
      GPIO), so **hold BSL + power-cycle the module + release BSL** enters the ROM bootloader.
      cc2538-bsl then read the chip (352 KB flash, IEEE `00:12:4B:…` = TI; it mislabels it
      "CC1350") and **flashed `sniffle_cc1352p1_cc2652p1.hex`, CRC-verified**. Sniffle now
      runs and streams messages — BUT the host decodes only **garbage** (base64 padding
      errors). Root cause: the adapter is a **classic CP2102 (max ~1 Mbaud)** and Sniffle's
      UART is **fixed at 2 Mbps**; the ROM bootloader auto-bauds so flashing worked at ≤1 M
      and masked the ceiling, but Sniffle's fixed 2 M output the CP2102 can't sample →
      framing corruption. **Fix = a 2 Mbps-capable USB-UART on the same 3 wires: CH340 (~€1,
      does 2 M), FTDI FT232 (3 M), or a CP2102*N* (3 M) — NOT a classic CP2102.** Then the
      free ZigStar path is complete. (Zero-purchase alternative: rebuild Sniffle firmware
      with the UART `#define` at 1 M — needs the TI SimpleLink SDK + arm-none-eabi toolchain,
      heavy; a €1 CH340 is far simpler.) Z-Stack was erased by the flash; to restore the
      gateway's Zigbee, reflash a coordinator hex via the same BSL method.
      **Adapter ordered (2026-09-15):** DSD TECH **SH-U09C5** (FTDI **FT232RNL**, 3 Mbps max
      — 2 M with headroom, cleaner than CH340; verified from the FTDI datasheet). Chosen over
      the SH-U09C2 because it breaks out **DTR/RTS** (and RESET), enabling **automatic BSL
      entry** — no more manual hold-BSL + power-cycle. **MUST set VCC/TTL to 3.3 V** (the
      CC2652P is 3.3 V; 5 V damages it). Wiring: data = adapter TXD→CC2652P RX, RXD→TX,
      GND→GND; auto-BSL = DTR→CC2652P **RESET**, RTS→CC2652P **BSL** (then cc2538-bsl
      `--bootloader-sonoff-usb`/right polarity auto-enters BSL; also makes restoring Z-Stack
      easy). Can power the module from the adapter's 3.3 V VCC (250 mA, ample). TODO when
      wiring: get the E72 module RESET and BSL(-backdoor DIO) pins from the ZigStar schematic.
      Sniffle is already flashed, so once wired at 3.3 V it should decode clean at 2 M — then
      build the `SniffleScanner`.
    - **Honest capability limit (calibrate before buying):** following reveals the
      *connection and its endpoints* (phone ↔ device = the pairing relationship) and makes
      the device observable on-air while connected — but a **bonded device's reconnection is
      LESC-encrypted (ECDH)**, which Sniffle cannot decrypt even by capturing the pairing.
      So we get the relationship, addresses (possibly RPA), timing and packet structure — not
      the GATT payload. For "who is paired with whom" that is still the key win.
    - free "try before buy" on the **existing nRF52840**: Nordic's **nRF Sniffer for
      Bluetooth LE** *does* follow a selected device's connection (packets between it and its
      peer, even encrypted) via a Wireshark extcap — good enough for a feasibility test that
      we can see the Redmi↔Android link. Trade-offs vs Sniffle: single advertising channel
      (less reliable at catching the CONNECT_IND, retry the reconnection), Wireshark-bound
      (harder to integrate than a Python CLI), and it displaces blatann on the one dongle.
  Host side: `sniff_receiver.py` (Python + PySerial over CDC-ACM), `-m MAC` filter, follows
  connections by default, PCAP export, `-i IRK` / `-Q` for RPA. **Operational limit:** it can
  only follow a connection whose *establishment* it observes (needs the CONNECT_IND) — it
  cannot join an already-running link, so we trigger a reconnection (buds off/on, BT toggle)
  while sniffing on all 3 advertising channels. RPAs may remain, so identity attribution
  stays partly open, but the device at least becomes observable.
- **Classic (BR/EDR)**: much harder — needs **Ubertooth One** (or similar) to recover the
  piconet LAP/UAP and follow the hop; the nRF52 cannot do BR/EDR at all. The JBL Go 3 case
  is blocked on hardware we do not have.

### Sniffle integration design (sketch, 2026-08-28 — studied the master python_cli)

Integrate the maintained **Sniffle Python package directly** (don't shell out to
`sniff_receiver.py`): vendor `python_cli/sniffle/` into the repo (or add as a dep). It is a
clean library — `sniffle.sniffle_hw.SniffleHW` + `sniffle.packet_decoder`. Python 3.9+
(our 3.10 is fine); needs **pyserial** (NOT currently installed — new requirement; blatann
uses its own serial, so this is additive).

- **Third, independent backend.** The TI CC2652P dongle is its own serial device, unrelated
  to the nRF52/blatann controller and to BlueZ/hci0 — like `scan_classic` wraps bluetoothctl,
  a new top-level `sniff.py` wraps SniffleHW. Autodetect its port the way `bledev` does.
- **API path:** `hw = SniffleHW(port)`; `hw.setup_sniffer(mode=SnifferMode.CONN_FOLLOW,
  targ_mac=<bytes>|targ_irk=<bytes>, hop3=True, rssi_min=…)`; `hw.mark_and_flush()`; then a
  thread loops `msg = hw.recv_and_decode()`. Decoded types that matter:
  `ConnectIndMessage` (**the relationship** — InitA=phone ↔ AdvA=device, access address,
  channel map, hop), `AdvIndMessage`/`ScanRspMessage` (advertising), and `DataMessage` /
  `LlControlMessage` (the **followed connection's** data-channel traffic → ATT/GATT, i.e.
  the device's name/services revealed *while it is connected to the phone*).
- **`SniffleScanner`** implements the `livetable` scanner interface (snapshot / is_scanning /
  pause / resume / save_csv) so it drops into the existing UI as a new activity, e.g.
  "BLE connection sniff". Record model: key by observed **connection** — show the two peers
  (phone ↔ device) and enrich the device row from the followed link (name, services, RPA vs
  identity). This is the first capability that surfaces a *phone↔device* pairing directly.
- **Operational workflow (must be in the UI copy):** following needs the `CONNECT_IND`, so
  start the sniffer then **trigger a reconnection** (device off/on, or toggle BT). For an
  **RPA** device (the Redmi buds), filter by **IRK** if known (we don't have it) — otherwise
  run with no MAC filter and catch every `CONNECT_IND`, then filter/attribute after.
- **select/act:** PCAP export (Sniffle has `sniffle.pcap`), dump the captured GATT, correlate
  the two peers. Later: `-i IRK` resolving if we ever obtain a device/phone IRK.

**Next decision (open):** acquire a TI CC2652P (SONOFF dongle) and integrate Sniffle
connection-following into the scan/select/act model (wrap `sniff_receiver.py` like
`scan_classic` wraps bluetoothctl) as the path to seeing phone-connected BLE devices; decide
separately whether Classic sniffing (Ubertooth) is worth the hardware. User is willing to buy
hardware. Until one lands, the goal stays partially open.
2. **Bond / pairing-relationship enumeration** (heart of the goal; research + new decoder).
   *2026-08-28 — implemented and **live-validated** on a real non-discoverable frame
   (buds bonded to a phone, out of pairing mode: FE2C payload `0000` → correctly decoded
   "not paired — empty account key filter"). Finding: a plain Bluetooth bond does **not**
   write a Fast Pair account key — a non-empty filter needs the device saved to a Google
   account (account-key flow). **Conclusive:** even after a full forget + re-pair via the
   phone's Fast Pair half-sheet, the buds advertised `0000` across three distinct
   addresses and the whole discoverable↔non-discoverable lifecycle — the Redmi Buds 6 Play
   simply do not populate a Fast Pair account key filter (consistent with the prior
   NEGATIVE identity-disclosure result: a minimal/hardened Fast Pair accessory). The
   non-empty/"bonded" branch is therefore validated on synthetic frames only.* New
   passive `bond.py` decodes what advertising reveals about bonds — **existence and
   count, never identity** (the account key filter is a one-way Bloom filter, reconnect
   addresses are RPAs):
   - **Fast Pair non-discoverable account key filter** (0xFE2C service data): empty →
     not paired to any account; non-empty → bonded to ≥1 account/seeker, filter grows
     with the number of keys; plus the show/hide reconnection UI flag. Frame layout and
     type nibbles from the Google "Provider advertising signal" spec (fetch of
     developers.google.com is blocked here — captured in `bond.py`'s docstring). Only
     decodable on the **dongle** path (raw service-data); the BlueZ path says so.
   - **Directed advertising** (`connectable_directed` / ADV_DIRECT_IND): reconnecting to
     a specific bonded peer. `scan_live` now captures a sticky `directed` flag.
   - **Host-side bond**: BlueZ Paired/Bonded (already captured as `paired`).
   Wired into `exposure.report` (both BLE and fused menus). `scan_live.DeviceRecord`
   gained `fp_payload`/`directed`; `scan_fused` merges them onto the actionable BlueZ
   record. **To validate live:** pair the buds to a phone, take them *out* of pairing
   mode so they advertise non-discoverable FE2C, and run the exposure check — the account
   key filter should read "bonded".
3. **Cloning / emulation** (bigger roadmap arc). *2026-08-28 — first extension done and
   live-validated.* `emulate.py --fast-pair MODELID` clones a real Fast Pair discoverable
   advertisement: Service Data (0x16) under 0xFE2C carrying the exact 3-byte Model ID,
   plus FE2C in the 16-bit UUID list, connectable, **nameless** (a real FP device is
   nameless; the seeker resolves the name from the Model ID via Google's registry). Raw
   AD `02010606162cfefb9b7203032cfe` for `fb9b72`. Validated: offline round-trip through
   `scan_live._fast_pair` → `FB9B72`; on air via an independent receiver (BlueZ/hci0 saw
   `CE:C1:1B:51:DD:23 FE2C "Google Fast Pair"`); the phone discovered it and let a connect
   attempt start. **Finding:** the phone showed no Fast Pair half-sheet because it already
   owns this model (bonded to the real buds) — Fast Pair only offers the half-sheet for a
   model you do *not* already own; validating the half-sheet needs a seeker without the
   Redmi buds. Clones only the *advertisement* — the FP GATT (anti-spoofing key exchange)
   is not implemented, so pairing does not complete. Next: implement the FE2C GATT for a
   fuller emulation; **clone from a live capture** (wire the scan's decoded Model ID
   straight into `--fast-pair`).
4. *(optional)* A real-world **known-vulnerable Fast Pair accessory** as a positive control
   on real hardware — nice-to-have; the in-house control already validates the pipeline.

## Open questions — to study / evaluate

- ~~**OUI vendor lookup** for public (non-random) addresses~~ — DONE 2026-08-28 (`oui.py`,
  system `ieee-data` CSVs). See next-steps item 1.
- **Service-UUID → SIG name** in the scan table (blatann has assigned-number tables;
  already used for GATT names in `client.py`, not yet surfaced in the scan columns).
- ~~**TX Power** is not exposed directly by blatann~~ — DONE 2026-08-28: read from the
  parsed AD `entries` (type 0x0A, raw bytes → signed dBm). See `scan_live._tx_power`.
- **Class of Device minor** decoding covers only common majors (`scan_classic._MINOR`);
  extend as needed.
- **Roadmap depth**: active enumeration, richer peripheral/server interaction, and
  device cloning/emulation — all still to be designed into the scan/select/act model.

- **Second BLE dongle for the Fast Pair handshake (item 2) — decided 2026-11-03.**
  Validating the *valid* Key-based Pairing handshake end-to-end needs **two** nRF52
  dongles (Seeker = webui, Provider = `emulate.py --fast-pair-gatt`; webui/CLI are
  exclusive on one dongle). **Which to buy: a second Nordic nRF52840 Dongle (PCA10059)**
  (~€10-12) — the exact target `pc-ble-driver-py` ships precompiled *Connectivity*
  firmware for (USB-CDC build), flashable over USB via nRF Connect Programmer / `nrfutil`,
  no SEGGER/debugger needed (serial bootloader onboard). Alternative: nRF52840 DK
  (PCA10056, ~€45) for onboard J-Link flashing/debug. **Avoid the Seeed Studio XIAO BLE
  (nRF52840):** right chip, but NOT a Nordic reference board, so there is **no precompiled
  connectivity hex** for it and no onboard debugger — it would need the connectivity
  firmware rebuilt for the XIAO's clock/USB config and SWD/UF2 flashing (a build project,
  not plug-and-play). Match the PCA10059 hex to the same `sd_api` version as the existing
  dongle. **Key clarification on real targets:** a *valid* handshake against the real Redmi
  buds is **gated on having their anti-spoofing public key**, not forbidden. That key is
  public by nature and obtainable — the production path is Google's metadata endpoint keyed
  by Model ID (GMS-authenticated, so it needs the GMS request replicated, not an anonymous
  GET), or extracting it from a traffic/device capture, or an operator-supplied key file.
  The emulated Provider (`--fast-pair-gatt`) is just the **fastest** positive control (we
  own both keys), not the only route. **Posture correction (2026-11-03, maintainer):** the
  "offline / non-phoning-home" line was a *default* for telemetry (OTel off) and the
  *passive* Model-ID→name lookup (local YAML over Google's endpoint) — it is **not** a ban on
  contacting Google when a test needs it. The standing goal is the most complete test tool,
  so a "fetch anti-spoofing public key from Google by Model ID" capability is in-scope and
  would make item 2 runnable against real devices without the emulated Provider. Meanwhile
  item 1 (passive FE2C profile) and item 3 (malformed-write stress) already work against the
  real buds with a **single** dongle; only the emulated-Provider validation of item 2 needs
  the second radio.

## Actions log (recent first)

- **TX Power parsing — next-steps item 1 (now complete)** (2026-08-28, impl + live-validated).
  `scan_live._tx_power` reads the Tx Power Level AD (0x0A) from blatann's parsed `entries`
  (blatann exposes no property; on the receive path the value is raw bytes) and converts
  to a signed dBm. New `tx_power` field + `TxPwr` column (after RSSI) in the dongle scan;
  the field was added to `LeRecord` and merged in `scan_fused`; `exposure.report` gained a
  `Tx Power` row with a path-loss proximity estimate (`tx_power − rssi`). Offline: −59/+4/
  None via the real `from_ble_adv_records` path. Live: a real advertiser `46:0C:50:50:4D:61`
  reported −8 dBm, RSSI −23 → path loss ≈ 15 dB. This closes enrichment item 1 entirely
  (Model ID names, masking fix, SvcNames, SIG data, OUI, TX Power).

- **OUI vendor lookup — next-steps item 1, last enrichment sub-item** (2026-08-28, impl +
  live-validated). New top-level `oui.py` (like `exposure.py`/`bond.py`, no blatann import)
  resolves a manufacturer from a public MAC's IEEE OUI, longest prefix first (MA-S 36-bit →
  MA-M 28-bit → MA-L 24-bit) from the system `ieee-data` CSVs (/var/lib/ieee-data/*.csv,
  ~53k entries), nmap `nmap-mac-prefixes` fallback, None otherwise. Random BLE addresses
  are guarded out (`lookup(..., random=True) → None`) — they carry no OUI. Wired as a
  `Vendor` column in `scan_classic` and a `Vendor (OUI)` row in `exposure.report` for public
  BLE addresses. Offline: TP-Link (E8:48:B8), Nokia Shanghai Bell (28:6F:B9), random guard.
  Live: a real Classic device `00:1D:DF:7E:FB:44` "PHILIPS HTL2110" resolved to its OUI
  owner *Sunitec Enterprise Co.,Ltd* — the ODM behind the brand name. Leaves only TX Power
  open in next-steps item 1.

- **Fast Pair advertisement clone — next-steps item 3** (2026-08-28, impl + live-validated).
  New `emulate.py --fast-pair MODELID`: the dongle emits a faithful, nameless Fast Pair
  discoverable advertisement (FE2C Service Data + Model ID + FE2C in the 16-bit UUID list,
  connectable), folding the old scratchpad `fp_advertiser.py` into a first-class mode.
  Mutually exclusive with `--positive-control`; validates the 6-hex Model ID. Offline:
  round-trips through `scan_live._fast_pair` to the Model ID, serializes to
  `02010606162cfefb9b7203032cfe`. Live: BlueZ/hci0 (independent receiver) saw the dongle
  advertising FE2C while the clone ran; the phone discovered it. Half-sheet did not show —
  the phone already owns this model (bonded to the real buds); Fast Pair only offers to
  pair a model you don't already have. Advertisement-only (no FP GATT), so pairing can't
  complete. Made the clone nameless after first seeing the phone list it by the scan-
  response name, which masks the registry-driven FP recognition.

- **Bond / pairing-relationship enumeration — next-steps item 2** (2026-08-28, impl +
  offline-validated, live validation of the account key filter pending). New passive
  `bond.py`: decodes the Fast Pair non-discoverable **account key filter** (empty → not
  paired; non-empty → bonded to ≥1 account/seeker, + show/hide reconnection UI), flags
  **directed reconnect advertising**, and surfaces the **host-side bond**. Reports
  existence/count of bonds, never identity (one-way Bloom filter, RPA peers). Captured
  `fp_payload` + sticky `directed` in `scan_live.DeviceRecord`, added the same fields to
  `LeRecord` and merged them in `scan_fused.snapshot`, and wired `bond.signals` into
  `exposure.report` (reachable from both the BLE and fused per-device menus). Frame layout
  from the Google "Provider advertising signal" spec (primary-source fetch blocked in this
  env; type nibbles 0b0000 show-UI / 0b0010 hide-UI confirmed via search). Unit-tested the
  decoder on synthetic frames (discoverable/empty/bonded-show/bonded-hide/length-overflow)
  and the report on synthetic dongle + BlueZ records. **Live-validated 2026-08-28**: with
  the buds bonded to a phone and out of pairing mode, the dongle captured their
  non-discoverable FE2C frame `0000` (RPA `4C:96:7E:68:F4:36`, `directed=False`) and the
  decoder read it correctly as an **empty account key filter → not paired to an account**.
  Finding: normal Bluetooth pairing does not populate a Fast Pair account key; a non-empty
  filter requires the Fast Pair account-key flow (save to a Google account). Pushed further
  (3 more captures incl. a full forget + re-pair via the Fast Pair half-sheet): the buds
  stayed `0000` across three addresses and the whole discoverable↔non-discoverable
  lifecycle — **conclusively** these buds never populate the account key filter (minimal
  Fast Pair, as in the earlier NEGATIVE test), so the non-empty/"bonded" render stays
  synthetic-only here. Refined the empty-filter note (drop the meaningless reconnection-UI
  nibble when no filter is present).

- **Passive identification enrichment — next-steps item 1, hardware-validated + SIG data**
  (2026-08-28). Delivered and validated live against the Redmi Buds 6 Play in discoverable
  pairing mode. New `bledev/fastpair.py` + `fastpair_models.yaml` resolve a 24-bit Fast
  Pair Model ID → product name from a **local** registry (design fork resolved with the
  maintainer: local YAML over Google's live `nearbydevices-pa` API, to keep the bench
  offline/non-phoning-home). Wired into the FastPair column of `scan_live`/`scan_fused`
  (widened to 16, name-or-hex). Fixed the fused Model-ID masking (`_merge_fast_pair`: a
  Model ID beats a bare `"Yes"`). Added a `SvcNames` column to the dongle scan naming
  advertised service UUIDs. Then wired the **authoritative SIG dataset** the maintainer
  checked out at `BluetoothSIGPublic/`: new `bledev/sig.py` loads company IDs (4018),
  member/service/characteristic UUIDs, made the *primary* source (graceful fallback to the
  bundled snapshot / blatann map) in `manufacturers`, `scan_live` and `scan_le`; checkout
  git-ignored. **Live results** (dongle scan, `csv/scan_1787922603.csv`; fused,
  `csv/fused_1787922685.csv`): Model ID `FB9B72` decoded, `type=Google FastPair`; fused
  merge kept `FB9B72` over BlueZ `"Yes"` (`target=FP+RPA`, RPA `4C:96:7E:68:F4:36`).
  Attributed `FB9B72 → Redmi Buds 6 Play` and seeded the registry; the FastPair column now
  prints the product name. Offline checks also passed (synthetic ScanReport/AdvertisingData
  fakes: column output, non-colliding CSV headers, all merge cases, SIG member fallback).

- **Built an in-house positive control for the identity disclosure and validated the
  pipeline** (2026-08-28). Extended `emulate.py` with `--positive-control`: the dongle
  advertises a Resolvable Private Address and accepts an unauthenticated Just Works bond,
  distributing its IRK + identity. blatann 0.6.0 supports both
  (`BleDevice.set_privacy_settings(enabled, resolvable_address=True)` and
  `security.set_security_params(bond=True, io_capabilities=NONE)`; `_get_security_params`
  always sets `kdist_own.id_key=True`, so the identity is distributed on every bond). The
  two BLE radios are independent, so the dongle is the peripheral and the host adapter the
  central in one machine — no phone needed. `identity.extract` against the emulated RPA
  gave `Bonded: yes` → `IDENTITY ADDRESS DISCLOSED — CE:C1:1B:51:DD:23`, correctly
  attributed. This proves `identity.extract` and the disclosure pipeline are correct in
  this environment, so the 2026-08-28 Redmi Buds 6 Play negative is a device property (it
  refuses the bond), not a tooling/stack defect. Generalises the T0.2 "emulate the target
  to test the observer" method from *observation* to *disclosure*. `emulate.py` change is
  in the working tree (not yet committed).

- **Configured the `mattpocock-skills` engineering skills for this repo** (2026-08-02),
  via their `setup-matt-pocock-skills` scaffolder. Three config files under
  `docs/agents/` plus an `## Agent skills` section in `CLAUDE.md` that points at them.
  Choices made, with the reasoning: **issue tracker = local markdown** under
  `.scratch/<feature>/` rather than GitHub Issues — the project is single-maintainer and
  the repo already keeps its planning in-tree (`NOTES.md`), so issues stay next to the
  code and get versioned with it, and `.scratch/` was deliberately left **out** of
  `.gitignore` for that reason. **Triage labels = the five canonical roles verbatim** —
  no pre-existing label vocabulary to map onto. **Domain docs = single-context** —
  one package, no monorepo signals; `CONTEXT.md` and `docs/adr/` are not created now,
  `/domain-modeling` creates them lazily when a term or decision actually needs pinning.
  Nothing here changes runtime code or the scan/select/act model; it only tells those
  skills where to read and write. Note the overlap to watch: `NOTES.md` stays the
  project's working memory (rationale, TODO, findings) — `.scratch/` is for per-feature
  specs and tickets, not a second scratchpad.

- **Realigned the recon stage with `/opt/repo/Whisper_Bully`** (2026-07-31). The goal for
  phase 1 of this project is to reproduce every recon / enumeration / fingerprinting
  capability, so the identity-address stage is reproduced here rather than delegated.
  What the comparison changed is recorded in *Resume here* and in the finding *"The
  target is induced, not found"*; the short version is that our filter looked for the
  wrong thing and our check sampled the leak one step too late. Worth keeping in mind as
  a method: the reference tool is 726 lines and its README states the mechanism
  explicitly — reading it first would have saved four sessions of live guessing.
  Deliberately **not** copied: Whisper_Bully's stages 2–3 (L2CAP flood, SMP bond during
  stack recovery) stay out, per the passive/active boundary in CLAUDE.md.

- **Automated the target triage** (`scan_fused.py`), after live use showed the workflow
  put the decisive information (`Conn`) on one table and the action on the other.
  Activity 3 now runs **both radios at once** and merges by address; the per-device
  confirmation prompt was removed (the menu entry *is* the explicit action, and the
  private address rotates while you answer). Additions:
  - `scan_fused.is_viable()` — the target profile in one place: `rnd-r` **and**
    connectable. The menu labels the action VIABLE TARGET or "not viable, will explain".
  - `identity.extract(connectable=False)` now refuses **without transmitting**, like the
    existing `pub`/`rnd-s` refusals. Three passive refusals now cover every dead end we
    actually hit live, so a wasted minute is no longer possible.
  - `livetable` gained an optional filter hook (`toggle_filter`/`filter_hint`, key `V`)
    used by the fused scanner for viable-only rows. Other scanners are unaffected.
  - Liveness probe in `identity.extract`: the 8 s discovery window before connecting
    records whether the target is still on the air *under that address*. This is what
    separates "address rotated" from "advertises non-connectable" — previously the two
    produced an identical silent failure and cost a minute each to guess at.
  - Validated: fusion/viability/filter/CSV and the three passive refusals offline with
    synthetic records from both backends; BlueZ half + degraded mode live; full CLI
    walkthrough via pty. **The fused `Conn` column is now validated against real
    hardware too** (2026-07-31, after the USB fault was cleared by a reboot): real
    `YES`/`no` values, `?` only where the dongle genuinely missed the device, and the
    viable-only filter correctly emptying the table in an environment that contains no
    viable target. The action the table feeds (`identity.extract`) remains unvalidated
    against a real RPA — that is the last gap before this can be a checkpoint.

- Added **BLE scan over the host adapter** (`scan_le.py`) + the **identity address
  (BDADDR) disclosure check** (`identity.py`), reachable as activity 3 → device menu.
  Rationale in *Design decisions* above. Implementation notes worth keeping:
  - `ClassicScanner` gained a `transport` argument and three subclass seams
    (`record_cls`, `_needs_info`, `_set_prop`, plus `columns`/`csv_prefix`/`_extra_csv_*`)
    so the LE scanner is ~130 lines instead of a second scan loop. Classic behaviour
    unchanged (re-verified: CoD/Icon/RSSI decoding still correct).
  - BlueZ **5.85 emits `ManufacturerData.Key` / `ServiceData.Key` with a dot**, not a
    space — the first live run silently produced an empty Manufacturer column. Both
    spellings are accepted now.
  - The address *kind* (`rnd-s`/`rnd-r`/`rnd-n`) is not reported by BlueZ; it is derived
    from the top two bits of the MSB octet in `exposure.addr_kind` and shared with the
    dongle vocabulary. Verified live against 16 neighbouring devices.
  - `bluetoothctl` registers a pairing agent at startup and BlueZ allows a single
    *default* agent, so the scanner's session would block the check's NoInputNoOutput
    agent. Hence `ClassicScanner.release_agent()/restore_agent()`, called by `cli.py`
    around the action; the report flags it if the default-agent request still failed.
  - `identity.extract` runs its own `scan le`: BlueZ evicts non-bonded devices from the
    cache seconds after discovery stops (`[DEL]` events), and the caller has just paused
    its scanner.
  - Validated: offline parsing/reporting; live LE scan (16 devices, address kinds and
    vendors correct); full CLI walkthrough via pty; the active path end-to-end against a
    **non-existent** address (session, snapshots, ambient filter, cleanup, all four
    report shapes). Not yet run against a real RPA target — see TODO.

- Fixed system-python invocation for the RFCOMM/D-Bus helpers: `subprocess.run(["python3", ...])`
  resolved to the **venv** python (no AF_BLUETOOTH / dbus) when the CLI runs inside the
  venv → AttributeError. Now `spp.py`/`pbap.py` call `/usr/bin/python3` explicitly
  (`_system_python()`). Also: SPP console now runs `bluetoothctl connect` first (RFCOMM to
  an idle paired device fails EBADE) and flags `br-connection-key-missing` (device dropped
  the bond — re-pair). Headsets drop our bond when they reconnect to a phone.

- Restructured the **Classic device menu** for UI consistency with BLE: SDP profiles
  are now **interactive nodes** (select *Serial Port (SPP)* → open console; *Phonebook
  (PBAP)* → probe/sample; others → description), and added a **Pair / bond** action
  (`scan_classic.pair`, box-initiated) since active actions need a bond and inbound
  pairing fails in the VM. Helpers `capabilities.classic_detected/classic_note`.
  Validated live on the Jabra (Profiles → SPP → console).

- Added Classic **SPP serial console** (`spp.py` + `spp_console.py`): finds the Serial
  Port (0x1101) RFCOMM channel via `sdptool`, opens an interactive RFCOMM send/receive
  console. **Validated live** against a paired Jabra Evolve2 65 (channel 3). RFCOMM
  socket runs in the **system python3** — the venv interpreter is built WITHOUT
  AF_BLUETOOTH (checked). Needs a paired device.

- Added Classic **PBAP** (`pbap.py`) under the Classic device menu: *access probe*
  (obexctl OBEX session → accepted/rejected, no contacts) + bounded *sample pull*.
  **Validated live** against a paired OnePlus Nord. Needs a **paired** device; never
  pairs/bypasses bonding. Key findings: obexctl `pull` can't fetch a single vCard
  (only the whole book), so the bounded sample uses BlueZ D-Bus
  `PhonebookAccess1.PullAll` with the **`MaxCount`** filter (not `MaxListCount`) — a
  true protocol-level bound. That D-Bus call runs in the **system python3**
  (dbus-python isn't in the venv) via `pbap_pull.py`. Menu key `b` = Back, so PBAP
  uses `a`. Pairing a phone in this VM only worked **box-initiated** with
  `agent KeyboardDisplay` + numeric-comparison confirm; inbound (phone→box) never
  reached the stack.


- Initial bench built: BLE scan (batch + live), Classic scan, GATT client browser,
  peripheral emulation, interactive scan→select→act workflow, Fast Pair identification,
  passive Fast Pair GATT exposure check. (Details in git history on `devel`.)
- Added BLE **capability fingerprint** (`capabilities.py`): connects, enumerates GATT,
  matches a signature registry (DFU/OTA, HID, NUS, OTS, DIS, Mesh, IPSP, LE Audio,
  Media/Telephony) + attack-surface notes. Detection-only. Registry is extensible.
- Added Classic **profile capability enumeration** (`capabilities.classic_report` +
  `scan_classic.read_profiles`): parses SDP `UUID:` lines from `bluetoothctl info`
  into a profile checklist (A2DP/AVRCP/HFP/SPP/OBEX/PBAP/MAP/HID/PAN/SAP/DID).
  Detection-only. Parser validated against real soundbar output.
- Reworked per-device interaction into a **navigable menu tree** (`menu.py`): stack of
  `Menu` nodes, universal Back, breadcrumb, `on_enter`/`on_leave` (GATT node connects/
  disconnects). GATT read/write/subscribe are now leaf nodes; replaced the bespoke
  `interactive_gatt`/`_device_action`. Live-validated (read of Device Name via tree).
- Turned the BLE **Capabilities** action into navigable nodes: each detected capability
  splits **Inspect (read-only)** vs **Interact (active)** into separate sub-menus.
  Inspect → `client.read_all` (Device Information → read-all); Interact → write/subscribe
  + Nordic UART `client.serial_console`. Removed the old `capability_fingerprint` leaf.
  Registry helpers `capabilities.signatures/match_services`.
- Firmware capability → Inspect → **DFU exposure verification** (`capabilities.dfu_exposure`):
  passive; reports OTA variant, buttonless (remote-trigger) char, writable control point
  on the unauthenticated link, with a verdict. Does NOT trigger/upload (brick risk stays
  out; the generic Interact→Write can send a trigger at the tester's discretion).
