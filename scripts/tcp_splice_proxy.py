#!/usr/bin/env python3
# scripts/tcp_splice_proxy.py — Proxy TCP transparente (sin TLS termination).
"""Reenvía bytes en crudo entre un puerto local y un destino remoto.

Sirve para dar conectividad temporal a la Arduino UNO Q usando ADB:

    # En el PC
    python scripts/tcp_splice_proxy.py --listen 8443 --dest api.devnet.solana.com:443
    adb reverse tcp:443 tcp:8443

El cliente (la placa) abre TLS contra 127.0.0.1:443 con SNI
`api.devnet.solana.com`, así que el certificado valida normalmente: aquí solo
se copian bytes, no se descifra nada. NO usar en producción; es una ayuda de
laboratorio.

Uso:
    python scripts/tcp_splice_proxy.py --listen 8443 --dest host:443 [--verbose]
"""
from __future__ import annotations

import argparse
import socket
import threading
import time


def _splice(src: socket.socket, dst: socket.socket, label: str, verbose: bool) -> None:
    total = 0
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
            total += len(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if verbose:
            print(f"[{label}] {total} bytes", flush=True)


def handle(client: socket.socket, addr, dest_host: str, dest_port: int, verbose: bool) -> None:
    try:
        upstream = socket.create_connection((dest_host, dest_port), timeout=15)
    except OSError as exc:
        print(f"  ! no se pudo conectar a {dest_host}:{dest_port}: {exc}", flush=True)
        client.close()
        return
    if verbose:
        print(f"  + {addr[0]}:{addr[1]} -> {dest_host}:{dest_port}", flush=True)
    t = threading.Thread(target=_splice, args=(upstream, client, "down", verbose), daemon=True)
    t.start()
    _splice(client, upstream, "up", verbose)
    t.join(timeout=5)
    client.close()
    upstream.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Proxy TCP splice para túneles ADB")
    ap.add_argument("--listen", type=int, default=8443)
    ap.add_argument("--dest", default="api.devnet.solana.com:443")
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    host, _, port = args.dest.partition(":")
    dest_port = int(port or 443)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.bind, args.listen))
    srv.listen(64)
    print(f"Proxy {args.bind}:{args.listen} -> {host}:{dest_port} (Ctrl-C para salir)", flush=True)
    try:
        while True:
            client, addr = srv.accept()
            client.settimeout(None)
            threading.Thread(
                target=handle, args=(client, addr, host, dest_port, args.verbose), daemon=True
            ).start()
    except KeyboardInterrupt:
        print("\nCerrando proxy…", flush=True)
        time.sleep(0.2)
    finally:
        srv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
