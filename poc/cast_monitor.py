#!/usr/bin/env python3
"""
Watch the real device and print state transitions: running app, statusText,
media playerState. Used to verify that a sender discovered through the bridge
actually drives the device.

Usage: python3 cast_monitor.py <ip> [seconds]
"""
import json
import sys
import time

from cast_audio import (
    CastClient, CONNECTION_NS, MEDIA_NS, RECEIVER_ID, RECEIVER_NS,
)


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.178.124"
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 180.0
    rid = 100

    c = CastClient(host)
    c.send(RECEIVER_ID, CONNECTION_NS, {"type": "CONNECT"})
    print(f"[*] monitor {host} per {int(duration)}s", flush=True)

    last = None
    media_transport = None
    media_last = None
    end = time.time() + duration
    while time.time() < end:
        rid += 1
        c.send(RECEIVER_ID, RECEIVER_NS, {"type": "GET_STATUS", "requestId": rid})
        try:
            msg = c.wait_for("RECEIVER_STATUS", 5)
        except Exception as exc:
            print(f"[!] {exc}", flush=True)
            time.sleep(2)
            continue

        apps = msg.get("status", {}).get("applications", [])
        state = tuple((a.get("appId"), a.get("statusText")) for a in apps)
        if state != last:
            stamp = time.strftime("%H:%M:%S")
            if not apps:
                print(f"{stamp} nessuna app (idle)", flush=True)
                media_transport = None
            for a in apps:
                print(f"{stamp} app={a.get('appId')} "
                      f"name={a.get('displayName')!r} "
                      f"status={a.get('statusText')!r}", flush=True)
                if a.get("appId") != "EurekaIdleScreen":
                    media_transport = a.get("transportId")
            last = state

        if media_transport and apps and apps[0].get("appId") != "EurekaIdleScreen":
            rid += 1
            c.send(media_transport, MEDIA_NS,
                   {"type": "GET_STATUS", "requestId": rid})
            try:
                m = c.wait_for("MEDIA_STATUS", 3)
            except Exception:
                m = None
            if m and m.get("status"):
                st = m["status"][0]
                cur = (st.get("playerState"), st.get("currentTime"))
                if cur != media_last:
                    title = (st.get("media") or {}).get("metadata", {}).get("title")
                    print(f"    media state={cur[0]} t={cur[1]} title={title!r}",
                          flush=True)
                    media_last = cur
        time.sleep(1.5)

    print("[*] monitor terminato", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
