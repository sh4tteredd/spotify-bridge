# dist — binari precompilati

`bin/librespot` è compilato per **aarch64 Linux, musl, statico**.

| Proprietà | Valore |
|---|---|
| Target | `aarch64-unknown-linux-musl` |
| Linking | statico (nessuna dipendenza da glibc/OpenSSL) |
| Versione | librespot 0.8.0 |
| Feature | `rustls-tls-webpki-roots`, `with-libmdns` |
| Backend audio | `subprocess` (e `pipe`) — nessuna ALSA richiesta |
| Costruito con | `rust:1-alpine` su host aarch64 (Docker) |

## Perché queste feature

- `rustls-tls-webpki-roots` invece di `native-tls`: evita OpenSSL e produce un
  binario statico portabile.
- `with-libmdns` invece di `with-avahi`/`with-dns-sd`: annuncia il device su
  Spotify Connect senza dipendere da Avahi o da un demone DNS-SD di sistema.
- Niente `rodio-backend`: è il default ma tira dentro ALSA. A noi serve solo
  `subprocess`, che scrive il PCM su un comando (la FIFO).

## Installazione sul Pi

```bash
sudo install -m 0755 dist/bin/librespot /usr/local/bin/librespot
librespot --version
```

Se `file` dice `ELF 64-bit LSB ... ARM aarch64 ... statically linked`, sei a posto.

## Compatibilità

Funziona su Raspberry Pi OS / DietPi **aarch64** (Pi 3, 4, 5, Zero 2 W con
immagine 64-bit). **Non** funziona su immagini `armhf`/armv7: in quel caso
serve una build per `armv7-unknown-linux-musleabihf`.

## Ricostruire

```bash
./dist/build.sh
```

## Verifica integrità

```bash
sha256sum -c dist/librespot-aarch64.sha256
```
