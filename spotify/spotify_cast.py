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
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import http.server
import socketserver

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "poc"))

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
    fd = None
    if fifo:
        if not os.path.exists(fifo):
            os.mkfifo(fifo)
        fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        print(f"[+] FIFO {fifo} aperta (silenzio iniettato se inattiva)")

    phase = 0.0
    pending = b""
    try:
        while not stop.is_set():
            data = b""
            if fd is not None:
                try:
                    data = os.read(fd, CHUNK * 4)
                except BlockingIOError:
                    data = b""
            else:
                data, phase = _tone_chunk(phase)

            if not data:
                # no PCM from librespot (paused / not playing): keep the
                # live stream alive with silence
                ffmpeg_stdin.write(_silence(CHUNK))
                ffmpeg_stdin.flush()
                time.sleep(0.05)
                continue

            pending += data
            # send whole 50 ms blocks, keep the remainder
            while len(pending) >= CHUNK:
                ffmpeg_stdin.write(pending[:CHUNK])
                pending = pending[CHUNK:]
            ffmpeg_stdin.flush()
            time.sleep(0.02)
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
# Cast
# --------------------------------------------------------------------------- #
def cast_live(device: str, url: str, title: str) -> bool:
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
            "contentType": "application/x-mpegURL",
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
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True)
    ap.add_argument("--fifo", default="/tmp/librespot.pcm")
    ap.add_argument("--tone", action="store_true",
                    help="generate a test melody instead of reading the FIFO")
    ap.add_argument("--http-port", type=int, default=8090)
    ap.add_argument("--dir", default="/tmp/spotify-hls")
    ap.add_argument("--title", default="Spotify")
    ap.add_argument("--bind", default=None, help="IP announced to the device")
    ap.add_argument("--duration", type=float, default=0,
                    help="stop after N seconds (0 = run forever)")
    args = ap.parse_args()

    os.makedirs(args.dir, exist_ok=True)
    for f in os.listdir(args.dir):
        os.unlink(os.path.join(args.dir, f))

    bind_ip = args.bind
    if not bind_ip:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((args.device, 8009))
            bind_ip = s.getsockname()[0]
        finally:
            s.close()
    url = f"http://{bind_ip}:{args.http_port}/stream.m3u8"

    serve_http(args.dir, args.http_port)
    print(f"[+] HTTP HLS su {bind_ip}:{args.http_port}")

    playlist = os.path.join(args.dir, "stream.m3u8")
    ffmpeg = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "s16le", "-ar", str(RATE), "-ac", str(CHANNELS), "-i", "pipe:0",
         "-c:a", "aac", "-b:a", "160k", "-ar", str(RATE),
         "-f", "hls", "-hls_time", "2", "-hls_list_size", "10",
         "-hls_flags", "delete_segments+omit_endlist",
         "-hls_segment_filename", os.path.join(args.dir, "seg%d.ts"),
         playlist],
        stdin=subprocess.PIPE,
    )

    stop = threading.Event()
    threading.Thread(target=relay, args=(ffmpeg.stdin, None if args.tone else args.fifo, stop),
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

    print(f"[+] playlist pronta: {url}")
    ok = cast_live(args.device, url, args.title)

    if args.duration:
        time.sleep(args.duration)
        stop.set()
        ffmpeg.terminate()
        print("[*] stop")
        return 0 if ok else 1

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()
        ffmpeg.terminate()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
