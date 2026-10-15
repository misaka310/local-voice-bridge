from __future__ import annotations

import os
import socket
import sys
import threading


def _set_binary_stdio() -> None:
    if os.name != "nt":
        return
    import msvcrt

    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: remote_tcp_stdio_bridge.py HOST PORT", file=sys.stderr)
        return 2

    host = sys.argv[1]
    port = int(sys.argv[2])
    _set_binary_stdio()

    sock = socket.create_connection((host, port), timeout=10)
    sock.settimeout(None)

    def stdin_to_socket() -> None:
        try:
            while True:
                chunk = os.read(sys.stdin.fileno(), 65536)
                if not chunk:
                    break
                sock.sendall(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    writer = threading.Thread(target=stdin_to_socket, name="stdin-to-socket", daemon=True)
    writer.start()

    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            os.write(sys.stdout.fileno(), chunk)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
