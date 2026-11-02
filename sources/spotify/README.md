# sources/spotify — librespot

Pacchetto sorgente Spotify: installa il core e `spotify-source.service`, che
esegue librespot con backend `subprocess` e scrive il PCM nella FIFO del core.

Serve perché il Nest Hub **non ha il receiver Spotify**: `LAUNCH_ERROR NOT_FOUND`
su `CC32E753`. Con questa catena Spotify non passa da Cast: suona su
"Cucina Audio" e l'audio viene ritrasmesso al device.

## Installazione

```bash
sudo ./install.sh 192.168.178.124
```

Richiede `/usr/local/bin/librespot` (il binario precompilato è in `dist/`):

```bash
sudo install -m 0755 ../../dist/bin/librespot /usr/local/bin/librespot
```

## Autenticazione

Le credenziali stanno in `/var/cache/librespot/credentials.json`.

**Dal Mac** (se hai già fatto il login):

```bash
cat ~/.cache/librespot/credentials.json | \
  ssh root@<IP-PI> 'cat > /var/cache/librespot/credentials.json'
```

**Sul Pi** (OAuth con tunnel, redirect su 127.0.0.1:8899):

```bash
ssh -L 8899:127.0.0.1:8899 root@<IP-PI>
librespot -n "Cucina Audio" -B subprocess \
  -d /opt/audiobridge/fifo_writer.sh -b 320 \
  --cache /var/cache/librespot --enable-oauth
```

## Note

- Serve **Spotify Premium**.
- Nel selettore dispositivi cerca **Cucina Audio** (non "Cucina Display", che è
  il bridge Cast e dà `NOT_FOUND`).
- Latenza tipica ~10 s, identica ad AirPlay.
