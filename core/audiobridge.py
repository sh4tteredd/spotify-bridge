#!/usr/bin/env python3
"""
spotify_cast -- Spotify Connect -> HLS -> Google Cast (Default Media Receiver)

Why: the Nest Hub refuses to launch the Spotify receiver (LAUNCH_ERROR
NOT_FOUND), but it happily plays a normal HTTP/HLS stream through CC1AD845.

Pipeline:

    Spotify app ──Connect──> librespot ──PCM(S16LE)──> FIFO
                                                       │
                                        pcm relay ─────┤ (injects silence when idle,
                                                       │  so the live playlist never starves)
                                                       ▼
                                                    ffmpeg ──> HLS (m3u8 + .ts)
                                                       │
                                            HTTP server│
                                                       ▼
                                              CASTV2 LOAD (LIVE)

Usage:
  # test without Spotify: synthetic melody
  python3 spotify_cast.py --device 192.168.178.124 --tone

  # real use (librespot feeding the FIFO)
  python3 spotify_cast.py --device 192.168.178.124 \\
      --fifo /tmp/librespot.pcm --http-port 8090
"""
import argparse
import math
import os
import queue
import select
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import http.server
import socketserver

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                      # installato: cast_audio.py accanto
sys.path.insert(0, os.path.join(_HERE, "..", "poc"))   # repo: ../poc

from cast_audio import (
    CastClient, CONNECTION_NS, MEDIA_NS, RECEIVER_ID, RECEIVER_NS,
    DEFAULT_MEDIA_RECEIVER,
)

RATE = 44100
CHANNELS = 2
BYTES_PER_SEC = RATE * CHANNELS * 2          # S16LE
CHUNK = int(BYTES_PER_SEC * 0.05)            # 50 ms


# --------------------------------------------------------------------------- #
# PCM relay: FIFO (or generated tone) -> ffmpeg stdin, silence on idle
# --------------------------------------------------------------------------- #
def _silence(n: int) -> bytes:
    return b"\x00" * n


def _tone_chunk(phase: float) -> tuple[bytes, float]:
    """0.5 s notes of a simple ascending arpeggio, stereo S16LE."""
    notes = [440.0, 554.37, 659.25, 880.0]
    idx = int(phase / 0.5) % len(notes)
    freq = notes[idx]
    n = int(BYTES_PER_SEC * 0.05) // 4          # frames
    buf = bytearray()
    t0 = phase
    for i in range(n):
        t = t0 + i / RATE
        v = int(12000 * math.sin(2 * math.pi * freq * t))
        buf += struct.pack("<hh", v, v)
    return bytes(buf), phase + n / RATE


def relay(ffmpeg_stdin, fifo: str | None, stop: threading.Event) -> None:
    """PCM -> ffmpeg stdin.

    Non inietta silenzio solo perché la FIFO è vuota in questo istante:
    shairport-sync e librespot scrivono a burst, quindi un read vuoto non
    significa pausa. Si aspetta IDLE secondi di inattività reale prima di
    generare silenzio, altrimenti si creano buchi dentro l'audio.
    """
    fd = None
    if fifo:
        if not os.path.exists(fifo):
            os.mkfifo(fifo)
        fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        print(f"[+] FIFO {fifo} aperta (silenzio solo dopo inattività)",
              flush=True)

    IDLE = 0.25                      # secondi di silenzio per ciclo di inattività
    silence = _silence(int(BYTES_PER_SEC * IDLE))
    phase = 0.0

    try:
        while not stop.is_set():
            if fd is None:                      # modalità --tone
                data, phase = _tone_chunk(phase)
                ffmpeg_stdin.write(data)
                ffmpeg_stdin.flush()
                time.sleep(IDLE)
                continue

            try:
                ready, _, _ = select.select([fd], [], [], IDLE)
            except InterruptedError:
                continue

            if ready:
                data = os.read(fd, 1 << 16)
                if data:
                    ffmpeg_stdin.write(data)
                    ffmpeg_stdin.flush()
                    continue
                # EOF: nessuno scrittore sulla FIFO
                time.sleep(IDLE)

            ffmpeg_stdin.write(silence)
            ffmpeg_stdin.flush()
    except (BrokenPipeError, ValueError):
        pass
    finally:
        if fd is not None:
            os.close(fd)


# --------------------------------------------------------------------------- #
# HTTP server for HLS
# --------------------------------------------------------------------------- #
class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()


def serve_http(directory: str, port: int) -> socketserver.TCPServer:
    handler = lambda *a, **kw: QuietHandler(*a, directory=directory, **kw)
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("0.0.0.0", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# --------------------------------------------------------------------------- #
# MP3 progressivo: ffmpeg stdout -> HTTP chunked -> Default Media Receiver
# --------------------------------------------------------------------------- #
class Mp3Streamer:
    """Stream MP3 continuo, senza Content-Length.

    Un lettore unico fa fan-out verso i client con una coda ciascuno: un client
    lento perde i suoi chunk ma non blocca gli altri.
    """

    def __init__(self, port: int):
        self.clients = set()
        self.lock = threading.Lock()
        socketserver.TCPServer.allow_reuse_address = True
        self.httpd = socketserver.ThreadingTCPServer(("0.0.0.0", port),
                                                    self._handler())
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def _handler(self):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                hdrs = " | ".join(f"{k}: {v}" for k, v in self.headers.items())
                print(f"[*] {self.command} {self.path} da "
                      f"{self.client_address[0]} [{hdrs}]", flush=True)
                if not self.path.startswith("/stream.mp3"):
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Cache-Control", "no-cache, no-store")
                self.send_header("Pragma", "no-cache")
                # il receiver fa una fetch cross-origin da gstatic.com:
                # senza CORS il media element rifiuta lo stream
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Expose-Headers",
                                 "Content-Type, icy-metaint, icy-br, "
                                 "ice-audio-info, icy-name")
                self.send_header("Accept-Ranges", "none")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                q = queue.Queue(maxsize=400)
                with outer.lock:
                    outer.clients.add(q)
                print(f"[+] client MP3 connesso ({self.client_address[0]})",
                      flush=True)
                try:
                    while True:
                        try:
                            chunk = q.get(timeout=20)
                        except queue.Empty:
                            break
                        self.wfile.write(b"%X\r\n" % len(chunk) + chunk + b"\r\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with outer.lock:
                        outer.clients.discard(q)
                    print(f"[+] client MP3 disconnesso ({self.client_address[0]})",
                          flush=True)

        return Handler

    def attach(self, stream) -> None:
        def reader():
            while True:
                try:
                    chunk = stream.read1(8192)
                except (ValueError, OSError):
                    break
                if not chunk:
                    break
                with self.lock:
                    for q in list(self.clients):
                        try:
                            q.put_nowait(chunk)
                        except queue.Full:
                            pass

        threading.Thread(target=reader, daemon=True).start()


# --------------------------------------------------------------------------- #
# Cast
# --------------------------------------------------------------------------- #
def cast_live(device: str, url: str, title: str,
              ctype: str = "application/x-mpegURL") -> bool:
    c = CastClient(device)
    c.send(RECEIVER_ID, CONNECTION_NS, {"type": "CONNECT"})
    c.send(RECEIVER_ID, RECEIVER_NS,
           {"type": "SET_VOLUME", "volume": {"level": 0.6}, "requestId": 1})
    c.send(RECEIVER_ID, RECEIVER_NS,
           {"type": "LAUNCH", "appId": DEFAULT_MEDIA_RECEIVER, "requestId": 2})
    status = c.wait_for(
        "RECEIVER_STATUS", 25,
        predicate=lambda m: any(a.get("appId") == DEFAULT_MEDIA_RECEIVER
                                for a in m.get("status", {}).get("applications", [])),
    )
    apps = status.get("status", {}).get("applications", [])
    target = next((a for a in apps if a.get("appId") == DEFAULT_MEDIA_RECEIVER), None)
    if not target:
        print("[-] receiver non avviato")
        return False
    transport = target["transportId"]
    c.send(transport, CONNECTION_NS, {"type": "CONNECT"})
    c.send(transport, MEDIA_NS, {
        "type": "LOAD", "requestId": 3, "autoplay": True,
        "media": {
            "contentId": url,
            "streamType": "LIVE",
            "contentType": ctype,
            "metadata": {"type": 0, "metadataType": 0, "title": title},
        },
    })
    deadline = time.time() + 40
    while time.time() < deadline:
        c.send(transport, MEDIA_NS, {"type": "GET_STATUS", "requestId": 4})
        try:
            m = c.wait_for("MEDIA_STATUS", 6)
        except Exception:
            continue
        if not m.get("status"):
            continue
        st = m["status"][0]
        if st.get("playerState") == "PLAYING":
            print(f"[+] PLAYING (mediaSessionId={st.get('mediaSessionId')})")
            return True
        if st.get("idleReason") in ("ERROR", "CANCELLED"):
            print(f"[-] errore: {st.get('idleReason')} {st.get('detailedError')}")
            return False
    print("[-] nessuna riproduzione entro il timeout")
    return False


# --------------------------------------------------------------------------- #
# Watchdog: mantiene viva la sessione Cast
# --------------------------------------------------------------------------- #
def _send_load(c, transport: str, url: str, title: str, rid: int,
               ctype: str = "application/x-mpegURL") -> None:
    c.send(transport, MEDIA_NS, {
        "type": "LOAD", "requestId": rid, "autoplay": True,
        "media": {
            "contentId": url,
            "streamType": "LIVE",
            "contentType": "application/x-mpegURL",
            "metadata": {"type": 0, "metadataType": 0, "title": title},
        },
    })


def _start_session(device: str, url: str, title: str,
                   ctype: str = "application/x-mpegURL"):
    c = CastClient(device)
    c.send(RECEIVER_ID, CONNECTION_NS, {"type": "CONNECT"})
    c.send(RECEIVER_ID, RECEIVER_NS,
           {"type": "SET_VOLUME", "volume": {"level": 0.6}, "requestId": 1})
    c.send(RECEIVER_ID, RECEIVER_NS,
           {"type": "LAUNCH", "appId": DEFAULT_MEDIA_RECEIVER, "requestId": 2})
    status = c.wait_for(
        "RECEIVER_STATUS", 20,
        predicate=lambda m: any(a.get("appId") == DEFAULT_MEDIA_RECEIVER
                                for a in m.get("status", {}).get("applications", [])),
    )
    apps = status.get("status", {}).get("applications", [])
    target = next((a for a in apps if a.get("appId") == DEFAULT_MEDIA_RECEIVER), None)
    if not target:
        raise RuntimeError("receiver non avviato")
    transport = target["transportId"]
    c.send(transport, CONNECTION_NS, {"type": "CONNECT"})
    _send_load(c, transport, url, title, 3, ctype)
    return c, transport


def supervise(device: str, url: str, title: str,
              ctype: str = "application/x-mpegURL") -> None:
    """Crea la sessione e la ricrea ogni volta che il device la chiude."""
    backoff = 2
    while True:
        c, transport = None, None
        try:
            c, transport = _start_session(device, url, title, ctype)
            print("[+] sessione Cast avviata", flush=True)
            if ctype == "audio/mpeg":
                # Durante un live progressivo il media namespace del Nest Hub
                # smette di rispondere: qui interessa solo che il receiver
                # resti aperto, altrimenti si riapre la sessione.
                while True:
                    time.sleep(3)
                    c.send(RECEIVER_ID, RECEIVER_NS,
                           {"type": "GET_STATUS", "requestId": 98})
                    try:
                        m = c.wait_for("RECEIVER_STATUS", 6)
                    except TimeoutError:
                        continue
                    apps = m.get("status", {}).get("applications", [])
                    if not any(a.get("appId") == DEFAULT_MEDIA_RECEIVER
                               for a in apps):
                        print("[!] receiver chiuso, riapro", flush=True)
                        break
                continue
            backoff = 2
            noresp = 0
            frozen = 0
            started = False
            last_t = None
            while True:
                time.sleep(2)
                try:
                    c.send(transport, MEDIA_NS,
                           {"type": "GET_STATUS", "requestId": 99})
                    m = c.wait_for("MEDIA_STATUS", 6)
                except TimeoutError:
                    noresp += 1
                    if noresp >= 2:
                        print("[!] receiver non risponde, riapro la sessione",
                              flush=True)
                        break
                    continue
                noresp = 0
                sts = m.get("status") or []
                if not sts:
                    # IDLE transitorio subito dopo il LOAD: ignorare finché
                    # la sessione non è almeno partita una volta
                    if started:
                        print("[!] nessun media attivo, ricarico", flush=True)
                        _send_load(c, transport, url, title, 100, ctype)
                        started, frozen, last_t = False, 0, None
                    continue
                st = sts[0]
                state = st.get("playerState")
                cur = st.get("currentTime") or 0
                if state in ("PLAYING", "BUFFERING"):
                    started = True
                if state == "PLAYING":
                    if last_t is not None and cur - last_t < 0.2:
                        frozen += 1
                    else:
                        frozen = 0
                    if frozen >= 10:
                        print("[!] riproduzione ferma, ricarico", flush=True)
                        _send_load(c, transport, url, title, 101, ctype)
                        started, frozen, last_t = False, 0, None
                        time.sleep(3)
                        continue
                elif state == "IDLE":
                    if started:
                        print(f"[!] idle ({st.get('idleReason')}), ricarico",
                              flush=True)
                        _send_load(c, transport, url, title, 102, ctype)
                        started, last_t = False, None
                last_t = cur
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"[!] {type(exc).__name__}: {exc} — riconnetto fra {backoff}s",
                  flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
        finally:
            if c is not None:
                try:
                    c.sock.close()
                except OSError:
                    pass


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True)
    ap.add_argument("--fifo", default="/run/audiobridge/pcm")
    ap.add_argument("--tone", action="store_true",
                    help="generate a test melody instead of reading the FIFO")
    ap.add_argument("--http-port", type=int, default=8090)
    ap.add_argument("--dir", default="/tmp/spotify-hls")
    ap.add_argument("--title", default="Cast")
    ap.add_argument("--transport", choices=("hls", "mp3"), default="hls",
                    help="hls = segmenti (stabile, ~10s); mp3 = progressivo (~5s)")
    ap.add_argument("--bitrate", default="192k", help="bitrate MP3")
    ap.add_argument("--bind", default=None, help="IP announced to the device")
    ap.add_argument("--duration", type=float, default=0,
                    help="stop after N seconds (0 = run forever)")
    args = ap.parse_args()

    bind_ip = args.bind
    if not bind_ip:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((args.device, 8009))
            bind_ip = s.getsockname()[0]
        finally:
            s.close()

    stop = threading.Event()

    if args.transport == "mp3":
        url = f"http://{bind_ip}:{args.http_port}/stream.mp3"
        ctype = "audio/mpeg"
        streamer = Mp3Streamer(args.http_port)
        ffmpeg = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-f", "s16le", "-ar", str(RATE), "-ac", str(CHANNELS),
             "-i", "pipe:0",
             "-c:a", "libmp3lame", "-b:a", args.bitrate,
             "-f", "mp3", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        )
        streamer.attach(ffmpeg.stdout)
        threading.Thread(target=relay,
                         args=(ffmpeg.stdin,
                               None if args.tone else args.fifo, stop),
                         daemon=True).start()
        time.sleep(1.5)
        print(f"[+] stream MP3 {args.bitrate} su {url}", flush=True)
    else:
        os.makedirs(args.dir, exist_ok=True)
        for f in os.listdir(args.dir):
            os.unlink(os.path.join(args.dir, f))

        url = f"http://{bind_ip}:{args.http_port}/stream.m3u8"
        ctype = "application/x-mpegURL"
        serve_http(args.dir, args.http_port)
        print(f"[+] HTTP HLS su {bind_ip}:{args.http_port}", flush=True)

        playlist = os.path.join(args.dir, "stream.m3u8")
        ffmpeg = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-f", "s16le", "-ar", str(RATE), "-ac", str(CHANNELS),
             "-i", "pipe:0",
             "-c:a", "aac", "-b:a", "160k", "-ar", str(RATE),
             "-f", "hls", "-hls_time", "2", "-hls_list_size", "12",
             "-hls_flags", "delete_segments+omit_endlist",
             "-hls_segment_filename", os.path.join(args.dir, "seg%d.ts"),
             playlist],
            stdin=subprocess.PIPE,
        )

        threading.Thread(target=relay,
                         args=(ffmpeg.stdin,
                               None if args.tone else args.fifo, stop),
                         daemon=True).start()

        # wait for a playlist with at least two segments
        deadline = time.time() + 30
        while time.time() < deadline:
            if os.path.exists(playlist):
                try:
                    txt = open(playlist).read()
                except OSError:
                    txt = ""
                if txt.count("#EXTINF") >= 2:
                    break
            time.sleep(0.5)
        else:
            print("[-] playlist non pronta")
            stop.set()
            ffmpeg.kill()
            return 1

        print(f"[+] playlist pronta: {url}", flush=True)

    if args.duration:
        ok = cast_live(args.device, url, args.title, ctype)
        time.sleep(args.duration)
        stop.set()
        ffmpeg.terminate()
        print("[*] stop")
        return 0 if ok else 1

    try:
        supervise(args.device, url, args.title, ctype)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        ffmpeg.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
