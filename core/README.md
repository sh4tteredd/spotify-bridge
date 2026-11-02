# core — audio bridge

Motore unico, indipendente dalla sorgente: legge PCM S16LE 44.1 kHz stereo da una
FIFO, lo ricodifica in AAC/HLS e lo manda al Google Cast con il **Default Media
Receiver**, mantenendo viva la sessione con un watchdog.

## Perché esiste

Il Nest Hub:

- non annuncia `_googlecast._tcp` → vedi `bridge/`;
- non ha il receiver Spotify (`LAUNCH_ERROR NOT_FOUND`);
- ma riproduce senza problemi uno stream HTTP/HLS.

Il core sfrutta solo l'ultima capacità: qualunque sorgente che produca PCM può
usarlo.

## Configurazione

`/etc/default/audiobridge` (sovrascrive i default della unit):

```sh
DEVICE=192.168.178.124
FIFO=/run/audiobridge/pcm
PORT=8090
TITLE=AirPlay
```

| Variabile | Significato |
|---|---|
| `DEVICE` | IP del Nest Hub |
| `FIFO` | named pipe dove la sorgente scrive il PCM |
| `PORT` | porta HTTP che serve la playlist |
| `TITLE` | titolo mostrato sul display |

## Watchdog

Ogni 2 s interroga il receiver e ricrea la sessione quando:

- `MEDIA_STATUS` è vuoto (solo se la sessione era già partita);
- lo stato è `IDLE`;
- `currentTime` è fermo da 20 s;
- il receiver non risponde per 4 s (in quel caso riconnette con backoff).

## Relay PCM

Il relay usa `select()` e inietta silenzio solo dopo 250 ms di **vera**
inattività. Non inserisce silenzio nei buchi tra i burst della sorgente, che era
la causa degli scatti con shairport-sync.
