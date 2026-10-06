"""A throwaway SMB server for the clean-up tests: serves one folder as "EMMC Images" (guest) on 127.0.0.1.

    .venv-smbtest/bin/python dev/smb-test-server.py <folder> <port> ["<share name>=<folder>" ...]

It runs from its own venv (python3 -m venv .venv-smbtest && .venv-smbtest/bin/pip install impacket), never the app's:
impacket installs a script named smbclient.py that would shadow the smbclient package the app uses.
"""

import sys

from impacket import smbserver


def main() -> None:
    folder, port = sys.argv[1], int(sys.argv[2])
    srv = smbserver.SimpleSMBServer(listenAddress="127.0.0.1", listenPort=port)
    srv.addShare("EMMC Images", folder, "")
    for extra in sys.argv[3:]:            # more shares, e.g. "TF Images=/tmp/x" (an ASIAIR's SD card)
        name, _, path = extra.partition("=")
        srv.addShare(name, path, "")
    srv.setSMB2Support(True)
    srv.setLogFile("/dev/null")
    print("ready", flush=True)
    srv.start()


if __name__ == "__main__":
    main()
