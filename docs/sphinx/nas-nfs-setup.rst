NAS NFS Setup (fleet runbook)
============================

How the three SciTeX NAS boxes were moved from SSHFS to native NFS,
with the benchmark numbers that justified it. Measured 2026-09-22 from
``scitex-compute-04`` (1 GiB sequential ``dd``, cold page cache —
``sync`` + ``drop_caches`` before reads).

Result table (NFS, ``vers=3``)
------------------------------

.. list-table::
   :header-rows: 1

   * - Box
     - OS
     - Disks
     - Write
     - Read
     - Role
   * - NAS-03 (``192.168.11.133``)
     - UGOS (Debian 12)
     - 4x SSD RAID5
     - 677 MB/s
     - 1.1 GB/s
     - primary shared storage
   * - NAS-02 (``192.168.11.132``)
     - QNAP QTS
     - HDD
     - 192 MB/s
     - 338 MB/s
     - secondary (customer disk)
   * - NAS-01 (``192.168.11.131``)
     - QNAP QTS
     - HDD (``8TB_HDD_NAS1``)
     - 60 MB/s
     - 96 MB/s
     - archive tier

For comparison, the same boxes over SSHFS: NAS-03 83–93/78–105,
NAS-02 182/263, NAS-01 40/40. Local disks on NAS-03 do 2.6 GB/s reads —
the old ceiling was encryption + FUSE overhead, never the hardware.

Server-side setup
-----------------

NAS-03 (UGREEN UGOS)
~~~~~~~~~~~~~~~~~~~~

1. Control Panel → File Service → NFS → check **Enable NFS service** →
   OK on the security Tips dialog → Apply. (Max protocol is NFSv3.)
2. Files → Shared Folder → right-click the share → Properties →
   NFS Permissions → Add:

   - Server address: ``192.168.11.0/24``
   - Permissions: Read & Write
   - Squash: map all users to admin
   - Security: AUTH_SYS, Async on

   Resulting export (vendor-managed, reboot-safe)::

     /volume1/shared 192.168.11.0/24(rw,async,no_wdelay,all_squash,anonuid=1001,anongid=10,sec=sys)

QNAP (NAS-01 / NAS-02)
~~~~~~~~~~~~~~~~~~~~~~

1. Control Panel → Network & File Services → Win/Mac/NFS/WebDAV →
   NFS Service → enable, check NFSv2+v3 **and** NFSv4 / v4.1.
2. Privilege → Shared Folders → the row's middle icon
   (**Edit Shared Folder Permission**, not the pencil) → permission type
   **NFS host access** → Add ``192.168.11.0/24`` read/write → Apply.
3. Async + no_wdelay lift NAS-02 writes (136 → 192 MB/s in our test).

QNAP maps all users to guest (65534) — files written over NFS land
owned by guest, unlike NAS-03's admin mapping. Live with it or
re-chown on the box.

Client-side (compute hosts)
---------------------------

Install client support, then fstab (automount style, matching the
existing SSHFS entries)::

  sudo apt-get install -y nfs-common
  192.168.11.133:/volume1/shared /mnt/nfs-nas-03 nfs vers=3,nolock,_netdev,noauto,x-systemd.automount,x-systemd.mount-timeout=20 0 0
  192.168.11.131:/share/CACHEDEV1_DATA/8TB_HDD_NAS1 /mnt/nfs-nas-01 nfs vers=3,nolock,_netdev,noauto,x-systemd.automount,x-systemd.mount-timeout=20 0 0
  192.168.11.132:/share/CACHEDEV1_DATA/shared /mnt/nfs-nas-02 nfs vers=3,nolock,_netdev,noauto,x-systemd.automount,x-systemd.mount-timeout=20 0 0

  sudo systemctl daemon-reload
  sudo systemctl start 'mnt-nfs\x2dnas\x2d03.automount'  # +01, +02

Notes: QNAP exports need the full server path
(``/share/CACHEDEV1_DATA/<share>``); UGOS takes the ``/volume1/<share>``
path directly. ``vers=3`` everywhere — UGOS serves max v3 and QNAP v4
was left for later.

Pitfalls hit (do not repeat)
----------------------------

- UGOS's ``nfs-server.service`` runs ``conf_tool`` in ExecStartPre and
  fails while UGOS itself says "nfs disable" — hand-editing
  ``/etc/exports`` then starting the unit does not work. Enable in the
  web UI first; hand lines get wiped by ``conf_tool`` anyway.
- Manually started ``rpc.mountd``/``rpc.nfsd`` squat the ports UGOS
  wants ("port occupied" on Apply). Stop them
  (``pkill rpc.mountd; rpc.nfsd 0``) before applying in the UI.
- QNAP NFS host access is NOT under Edit Properties (the pencil) — it
  is the folder-with-person icon with a permission-type dropdown.
- ``cmd | head -1 && echo OK`` masks failure: ``head`` exits 0 even
  when the command failed. Check ``${PIPESTATUS}`` or avoid the pipe
  for write probes.

Customer homes (login node + NAS-02)
------------------------------------

Customers from https://scitex.ai get SSH only on ``scitex-compute-01``,
with 32 GB homes each from NAS-02:

- ``scitex-compute-01`` mounts NAS-02 over NFS (same fstab pattern as
  above) at ``/mnt/nfs-nas-02``; homes live at
  ``/mnt/nfs-nas-02/homes/<user>``.
- New account recipe (run on ``scitex-compute-01``)::

    sudo useradd -m -d /mnt/nfs-nas-02/homes/<user> -s /bin/bash <user>
    sudo chmod 700 /mnt/nfs-nas-02/homes/<user>

  then install the customer's ``~/.ssh/authorized_keys``.
- Quota note (honest): QNAP squashes all NFS users to guest, so there
  is no per-customer hard quota without NAS-local users. Enforcement
  today is monitoring (``scitex-storage`` space scans + alerts), not a
  hard 32 GB cap — revisit with per-customer shares if abuse appears.
- SSH stays open to all hosts for staff; customer accounts exist only
  on ``scitex-compute-01``, which is the actual access boundary.
