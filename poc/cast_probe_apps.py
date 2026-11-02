#!/usr/bin/env python3
"""
Probe which receiver apps the device is willing to launch, and whether it can
fetch a media URL from the public internet.

Usage: python3 cast_probe_apps.py <ip>
"""
import sys
import time

from cast_audio import (
    CastClient, CONNECTION_NS, MEDIA_NS, RECEIVER_ID, RECEIVER_NS,
    DEFAULT_MEDIA_RECEIVER,
)

APPS = [
    ("Default Media Receiver", DEFAULT_MEDIA_RECEIVER),
    ("YouTube", "233637DE"),
    ("Spotify", "CC32E753"),
]

PUBLIC_MEDIA = "https://download.samplelib.com/mp3/sample-3s.mp3"


def launch(c, app_id: str, rid: int) -> dict:
    c.send(RECEIVER_ID, RECEIVER_NS, {
        "type": "LAUNCH", "appId": app_id, "requestId": rid,
        "supportedAppTypes": ["WEB", "ANDROID_TV"],
    })
    deadline = time.time() + 12
    while time.time() < deadline:
        msg = c.recv()
        if msg.get("type") == "PING":
            c.send(RECEIVER_ID, "urn:x-cast:com.google.cast.tp.heartbeat",
                   {"type": "PONG"})
            continue
        if msg.get("type") in ("RECEIVER_STATUS", "LAUNCH_ERROR") \
                and msg.get("requestId") == rid:
            return msg
    return {"type": "TIMEOUT"}


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.178.124"
    c = CastClient(host)
    c.send(RECEIVER_ID, CONNECTION_NS, {"type": "CONNECT"})

    rid = 1
    for name, app_id in APPS:
        rid += 1
        resp = launch(c, app_id, rid)
        if resp.get("type") == "LAUNCH_ERROR":
            print(f"  {name:<24} {app_id:<10} -> LAUNCH_ERROR "
                  f"{resp.get('reason')}")
            continue
        if resp.get("type") == "TIMEOUT":
            print(f"  {name:<24} {app_id:<10} -> nessuna risposta")
            continue
        apps = resp.get("status", {}).get("applications", [])
        print(f"  {name:<24} {app_id:<10} -> OK "
              f"({len(apps)} app attive)")
        for a in apps:
            if a.get("appId") != "EurekaIdleScreen":
                c.send(RECEIVER_ID, RECEIVER_NS,
                       {"type": "STOP", "sessionId": a.get("sessionId"),
                        "requestId": rid})
                time.sleep(1)

    print("\n[*] test fetch media da internet (Default Media Receiver)")
    rid += 1
    resp = launch(c, DEFAULT_MEDIA_RECEIVER, rid)
    apps = resp.get("status", {}).get("applications", [])
    target = next((a for a in apps if a.get("appId") == DEFAULT_MEDIA_RECEIVER), None)
    if not target:
        print("  impossibile avviare il receiver")
        return 1
    transport = target["transportId"]
    c.send(transport, CONNECTION_NS, {"type": "CONNECT"})
    c.send(transport, MEDIA_NS, {
        "type": "LOAD", "requestId": rid + 1,
        "media": {"contentId": PUBLIC_MEDIA, "streamType": "BUFFERED",
                  "contentType": "audio/mp3",
                  "metadata": {"type": 0, "metadataType": 0,
                               "title": "internet test"}},
        "autoplay": True,
    })

    deadline = time.time() + 25
    while time.time() < deadline:
        c.send(transport, MEDIA_NS, {"type": "GET_STATUS", "requestId": rid + 2})
        try:
            m = c.wait_for("MEDIA_STATUS", 5)
        except Exception:
            continue
        if not m.get("status"):
            continue
        st = m["status"][0]
        if st.get("playerState") == "PLAYING":
            print("  internet OK: il device scarica dalla rete pubblica")
            c.send(transport, MEDIA_NS, {"type": "STOP", "requestId": rid + 3})
            return 0
        if st.get("idleReason") in ("ERROR", "CANCELLED"):
            print(f"  fetch fallito: {st.get('idleReason')} "
                  f"{st.get('detailedError')}")
            return 1
        time.sleep(1.5)

    print("  timeout: nessuna riproduzione dalla rete pubblica")
    return 1


if __name__ == "__main__":
    sys.exit(main())
