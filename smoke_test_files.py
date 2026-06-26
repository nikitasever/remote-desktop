"""
Headless smoke test for two-way file transfer and remote directory listing.

Uses an in-process socketpair (no relay/host subprocess needed).
Sets up a SecureChannel, simulates the host recv_loop handling of
file-transfer and directory-listing messages, and verifies:
  1. Client -> Host file transfer (existing flow, regression check)
  2. Host -> Client file transfer (new MSG_HOST_FILE_*)
  3. Directory listing request/response (MSG_DIR_LIST_REQ/RESP)
  4. Path safety (basename sanitization, traversal guard)
"""

import json
import os
import socket
import struct
import tempfile
import threading
import time
import sys

# Ensure the worktree's own modules are found first.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import common

PW = "file-smoke-test-pw-42"

ok = []


def check(name, cond):
    ok.append(bool(cond))
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}")


# ---------------------------------------------------------------------------
# Helpers: minimal host-side and client-side file-transfer logic running
# over a socketpair, so the test is fully in-process and headless.
# ---------------------------------------------------------------------------

def _host_side(sock, chan, downloads_dir, test_file_to_send, dir_to_list, done_ev):
    """Simulates host recv_loop: receives a file from client, serves a
    directory listing, and sends a file back to client."""
    sender = common.FrameSender(sock, chan)
    incoming = {"f": None, "name": None}

    try:
        while not done_ev.is_set():
            sock.settimeout(0.5)
            try:
                mt, body = common.recv_frame(sock, chan)
            except socket.timeout:
                continue
            except (ConnectionError, OSError):
                break

            # --- Client -> Host file ---
            if mt == common.MSG_FILE_META:
                meta = common.parse_json(body)
                os.makedirs(downloads_dir, exist_ok=True)
                safe = os.path.basename(meta["name"]) or "file.bin"
                path = os.path.join(downloads_dir, safe)
                incoming["f"] = open(path, "wb")
                incoming["name"] = path
            elif mt == common.MSG_FILE_CHUNK:
                if incoming["f"]:
                    incoming["f"].write(body)
            elif mt == common.MSG_FILE_END:
                if incoming["f"]:
                    incoming["f"].close()
                    incoming["f"] = None

            # --- Directory listing ---
            elif mt == common.MSG_DIR_LIST_REQ:
                req = common.parse_json(body)
                req_path = req.get("path", "")
                if not req_path:
                    req_path = dir_to_list
                resolved = os.path.realpath(os.path.abspath(req_path))
                if os.path.isdir(resolved):
                    entries = []
                    for name in sorted(os.listdir(resolved)):
                        full = os.path.join(resolved, name)
                        try:
                            st = os.stat(full)
                            entries.append({
                                "name": name,
                                "size": st.st_size if not os.path.isdir(full) else 0,
                                "is_dir": os.path.isdir(full),
                            })
                        except OSError:
                            continue
                    sender.send_json(common.MSG_DIR_LIST_RESP,
                                     {"path": resolved, "entries": entries})
                else:
                    sender.send_json(common.MSG_DIR_LIST_RESP,
                                     {"path": req_path, "entries": [],
                                      "error": "not found"})

            # --- File pull request (host -> client) ---
            elif mt == common.MSG_FILE_PULL_REQ:
                req = common.parse_json(body)
                pull_path = req.get("path", "")
                # Use the pre-arranged test file
                fpath = os.path.realpath(os.path.abspath(pull_path))
                if os.path.isfile(fpath):
                    size = os.path.getsize(fpath)
                    sender.send_json(common.MSG_HOST_FILE_META,
                                     {"name": os.path.basename(fpath), "size": size})
                    with open(fpath, "rb") as f:
                        while True:
                            chunk = f.read(256 * 1024)
                            if not chunk:
                                break
                            sender.send(common.MSG_HOST_FILE_CHUNK, chunk)
                    sender.send(common.MSG_HOST_FILE_END)

    except (ConnectionError, OSError):
        pass
    finally:
        if incoming["f"]:
            incoming["f"].close()


def test_client_to_host():
    """Test 1: client sends a file to host (existing flow)."""
    print("1) Client -> Host file transfer")
    tmp = tempfile.mkdtemp(prefix="rd_ft_c2h_")
    downloads = os.path.join(tmp, "downloads")

    key = common.derive_key(PW)
    a, b = socket.socketpair()
    ca, cb = common.SecureChannel(key), common.SecureChannel(key)
    done = threading.Event()

    # Create test content
    content = b"client-to-host-test-" + os.urandom(64)
    fname = "test_upload.bin"

    ht = threading.Thread(target=_host_side, args=(b, cb, downloads, None, tmp, done),
                          daemon=True)
    ht.start()

    # Client sends file
    sender = common.FrameSender(a, ca)
    sender.send_json(common.MSG_FILE_META, {"name": fname, "size": len(content)})
    sender.send(common.MSG_FILE_CHUNK, content)
    sender.send(common.MSG_FILE_END)

    time.sleep(0.5)
    done.set()
    ht.join(timeout=3)

    target = os.path.join(downloads, fname)
    check("file saved on host side", os.path.exists(target))
    if os.path.exists(target):
        check("content matches (client->host)", open(target, "rb").read() == content)
    else:
        check("content matches (client->host)", False)

    a.close()
    b.close()


def test_host_to_client():
    """Test 2: client requests a file from host, host sends it back."""
    print("2) Host -> Client file transfer")
    tmp = tempfile.mkdtemp(prefix="rd_ft_h2c_")
    downloads_client = os.path.join(tmp, "client_downloads")

    # Create test file on "host" side
    host_file_content = b"host-to-client-test-" + os.urandom(64)
    host_file_path = os.path.join(tmp, "host_file.dat")
    with open(host_file_path, "wb") as f:
        f.write(host_file_content)

    key = common.derive_key(PW)
    a, b = socket.socketpair()
    ca, cb = common.SecureChannel(key), common.SecureChannel(key)
    done = threading.Event()

    ht = threading.Thread(target=_host_side,
                          args=(b, cb, os.path.join(tmp, "host_dl"), host_file_path, tmp, done),
                          daemon=True)
    ht.start()

    sender = common.FrameSender(a, ca)
    # Client requests the file
    sender.send_json(common.MSG_FILE_PULL_REQ, {"path": host_file_path})

    # Client receives the file
    a.settimeout(5)
    incoming = {"f": None, "name": None}
    received_content = bytearray()
    got_meta = False
    got_end = False

    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            mt, body = common.recv_frame(a, ca)
        except socket.timeout:
            break
        except (ConnectionError, OSError):
            break

        if mt == common.MSG_HOST_FILE_META:
            meta = common.parse_json(body)
            got_meta = True
            os.makedirs(downloads_client, exist_ok=True)
            safe = os.path.basename(meta["name"]) or "file.bin"
            incoming["name"] = os.path.join(downloads_client, safe)
            incoming["f"] = open(incoming["name"], "wb")
        elif mt == common.MSG_HOST_FILE_CHUNK:
            if incoming["f"]:
                incoming["f"].write(body)
                received_content.extend(body)
        elif mt == common.MSG_HOST_FILE_END:
            if incoming["f"]:
                incoming["f"].close()
                incoming["f"] = None
            got_end = True
            break

    done.set()
    ht.join(timeout=3)

    check("received HOST_FILE_META", got_meta)
    check("received HOST_FILE_END", got_end)
    check("content matches (host->client)", bytes(received_content) == host_file_content)
    if incoming["name"] and os.path.exists(incoming["name"]):
        check("file saved on client side",
              open(incoming["name"], "rb").read() == host_file_content)
    else:
        check("file saved on client side", False)

    a.close()
    b.close()


def test_directory_listing():
    """Test 3: client requests directory listing from host."""
    print("3) Remote directory listing")
    tmp = tempfile.mkdtemp(prefix="rd_ft_dir_")

    # Create some test files and a subdirectory
    with open(os.path.join(tmp, "alpha.txt"), "w") as f:
        f.write("hello")
    with open(os.path.join(tmp, "beta.bin"), "wb") as f:
        f.write(b"\x00" * 100)
    os.makedirs(os.path.join(tmp, "subdir"), exist_ok=True)

    key = common.derive_key(PW)
    a, b = socket.socketpair()
    ca, cb = common.SecureChannel(key), common.SecureChannel(key)
    done = threading.Event()

    ht = threading.Thread(target=_host_side,
                          args=(b, cb, os.path.join(tmp, "dl"), None, tmp, done),
                          daemon=True)
    ht.start()

    sender = common.FrameSender(a, ca)
    sender.send_json(common.MSG_DIR_LIST_REQ, {"path": tmp})

    a.settimeout(5)
    listing = None
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            mt, body = common.recv_frame(a, ca)
        except socket.timeout:
            break
        except (ConnectionError, OSError):
            break
        if mt == common.MSG_DIR_LIST_RESP:
            listing = common.parse_json(body)
            break

    done.set()
    ht.join(timeout=3)

    check("received DIR_LIST_RESP", listing is not None)
    if listing:
        entries = listing.get("entries", [])
        names = {e["name"] for e in entries}
        check("listing contains alpha.txt", "alpha.txt" in names)
        check("listing contains beta.bin", "beta.bin" in names)
        check("listing contains subdir", "subdir" in names)
        subdir_entry = next((e for e in entries if e["name"] == "subdir"), None)
        check("subdir marked as is_dir", subdir_entry and subdir_entry.get("is_dir"))
        alpha_entry = next((e for e in entries if e["name"] == "alpha.txt"), None)
        check("alpha.txt has size > 0", alpha_entry and alpha_entry.get("size", 0) > 0)
        check("no error in response", not listing.get("error"))
    else:
        for _ in range(6):
            check("(skipped)", False)

    a.close()
    b.close()


def test_path_safety():
    """Test 4: basename sanitization and nonexistent path handling."""
    print("4) Path safety checks")
    tmp = tempfile.mkdtemp(prefix="rd_ft_safe_")

    key = common.derive_key(PW)
    a, b = socket.socketpair()
    ca, cb = common.SecureChannel(key), common.SecureChannel(key)
    done = threading.Event()

    ht = threading.Thread(target=_host_side,
                          args=(b, cb, os.path.join(tmp, "dl"), None, tmp, done),
                          daemon=True)
    ht.start()

    sender = common.FrameSender(a, ca)

    # Request nonexistent directory
    sender.send_json(common.MSG_DIR_LIST_REQ, {"path": os.path.join(tmp, "no_such_dir_xyz")})

    a.settimeout(5)
    listing = None
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            mt, body = common.recv_frame(a, ca)
        except socket.timeout:
            break
        except (ConnectionError, OSError):
            break
        if mt == common.MSG_DIR_LIST_RESP:
            listing = common.parse_json(body)
            break

    check("nonexistent dir returns error", listing is not None and listing.get("error"))

    # Request nonexistent file pull — host should silently not send anything
    sender.send_json(common.MSG_FILE_PULL_REQ, {"path": os.path.join(tmp, "no_file.txt")})
    time.sleep(0.5)

    # Verify basename sanitization: send a file with path traversal in name
    sender.send_json(common.MSG_FILE_META,
                     {"name": "../../../etc/passwd", "size": 5})
    sender.send(common.MSG_FILE_CHUNK, b"XXXXX")
    sender.send(common.MSG_FILE_END)
    time.sleep(0.5)

    dl_dir = os.path.join(tmp, "dl")
    if os.path.exists(dl_dir):
        saved = os.listdir(dl_dir)
        check("traversal name sanitized to basename",
              saved == ["passwd"] or saved == ["etc"])
        # Verify it did NOT escape the downloads dir
        check("file stayed in downloads dir",
              not os.path.exists(os.path.join(tmp, "etc", "passwd")))
    else:
        check("traversal name sanitized to basename", False)
        check("file stayed in downloads dir", True)

    done.set()
    ht.join(timeout=3)
    a.close()
    b.close()


def test_empty_dir_listing():
    """Test 5: empty directory listing (default path = home)."""
    print("5) Empty-path directory listing (defaults to listing dir)")
    tmp = tempfile.mkdtemp(prefix="rd_ft_empty_")

    key = common.derive_key(PW)
    a, b = socket.socketpair()
    ca, cb = common.SecureChannel(key), common.SecureChannel(key)
    done = threading.Event()

    ht = threading.Thread(target=_host_side,
                          args=(b, cb, os.path.join(tmp, "dl"), None, tmp, done),
                          daemon=True)
    ht.start()

    sender = common.FrameSender(a, ca)
    sender.send_json(common.MSG_DIR_LIST_REQ, {"path": ""})

    a.settimeout(5)
    listing = None
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            mt, body = common.recv_frame(a, ca)
        except socket.timeout:
            break
        except (ConnectionError, OSError):
            break
        if mt == common.MSG_DIR_LIST_RESP:
            listing = common.parse_json(body)
            break

    done.set()
    ht.join(timeout=3)

    check("empty path returns a listing", listing is not None and not listing.get("error"))
    if listing:
        check("listing has a resolved path", bool(listing.get("path")))

    a.close()
    b.close()


if __name__ == "__main__":
    test_client_to_host()
    test_host_to_client()
    test_directory_listing()
    test_path_safety()
    test_empty_dir_listing()

    total = len(ok)
    passed = sum(1 for x in ok if x)
    print(f"\nITOG: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)
