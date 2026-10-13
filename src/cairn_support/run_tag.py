"""The tag in the name of the keys the Search and Context Retriever columns write, so each machine finds and deletes its own.

RUN_TAG is the first eight hex characters of the SHA-1 of user@host, the same on every run on one
machine. KEY_PREFIX, "cairn:<RUN_TAG>:", starts every key the Search and Context Retriever columns
write, and Clean up deletes those keys by name.
"""

import getpass
import hashlib
import socket

RUN_TAG = hashlib.sha1(f"{getpass.getuser()}@{socket.gethostname()}".encode()).hexdigest()[:8]
KEY_PREFIX = f"cairn:{RUN_TAG}:"
