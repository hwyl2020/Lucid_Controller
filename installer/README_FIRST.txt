Apertix {version}  ·  by HWYL
=============================

Portable Windows (64-bit) application. No Python is needed on this PC.


1. Install the LUCID Arena SDK (once per PC)
--------------------------------------------
The app talks to the cameras through LUCID's Arena SDK, which must be installed on the PC:

  - Download "Arena SDK for Windows" (64-bit) from LUCID Vision Labs:
    https://thinklucid.com/downloads-hub/
  - Run the installer with its default options (keep the "LUCID Lightweight Filter Driver"
    selected; it is needed for full frame rates on GigE cameras).
  - The app was built and tested with Arena SDK / arena_api 2.7.1.

Without the SDK the app still opens, but tells you that no cameras can be found.


2. Copy and start the app
-------------------------
  - Copy the whole "Apertix" folder from the USB drive to the PC,
    for example to C:\Apertix (or run it straight from the drive).
  - Double-click "Apertix.exe".
    Windows SmartScreen may warn about an unknown publisher the first time:
    click "More info" > "Run anyway".
  - Optional: right-click the .exe > Send to > Desktop (create shortcut).

Keep the folder together: the .exe needs the "_internal" folder next to it.


3. Where your files go
----------------------
Settings (config.json), logs, recordings, snapshots, profiles and sessions are stored
next to the .exe when that folder is writable (portable use). If the app is in a
protected folder such as C:\Program Files, they go to
Documents\Apertix instead.


4. Network setup for GigE cameras (recommended)
-----------------------------------------------
On the network adapter the cameras are connected to
(Device Manager > Network adapters > adapter > Properties > Advanced):
  - Jumbo Packet / Jumbo Frame: 9014 bytes (the maximum)
  - Receive Buffers: the maximum value
Give the adapter a static IPv4 address (e.g. 172.16.1.52, mask 255.255.255.0).
If a camera is on a different subnet, the app's ON button moves it to the adapter's
subnet (Force IP) on the first click; the second click turns it on.
Allow the app through Windows Firewall when asked (private networks).


Troubleshooting
---------------
  - "Arena SDK not found": install the SDK (step 1) and restart the app.
  - Camera not listed: check the cable/power, the adapter's IP address, and that no
    other program (e.g. ArenaView) has the camera open.
  - Problems: File > Export diagnostics creates a zip with logs and system information.
