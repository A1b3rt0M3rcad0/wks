"""POSIX directory cookies give filesystem GC resumable O(page) traversal state."""

import base64
import ctypes
import json
import os


def page(store, prefix="", cursor=None, page_size=100):
    class Entry(ctypes.Structure):
        _fields_ = [
            ("inode", ctypes.c_ulonglong),
            ("offset", ctypes.c_longlong),
            ("length", ctypes.c_ushort),
            ("type", ctypes.c_ubyte),
            ("name", ctypes.c_char * 256),
        ]

    libc = ctypes.CDLL(None, use_errno=True)
    libc.opendir.argtypes = [ctypes.c_char_p]
    libc.opendir.restype = ctypes.c_void_p
    libc.readdir.argtypes = [ctypes.c_void_p]
    libc.readdir.restype = ctypes.POINTER(Entry)
    libc.telldir.argtypes = [ctypes.c_void_p]
    libc.telldir.restype = ctypes.c_long
    libc.seekdir.argtypes = [ctypes.c_void_p, ctypes.c_long]
    libc.closedir.argtypes = [ctypes.c_void_p]
    state = {"root_cookie": 0, "namespace": None, "cookie": 0}
    if cursor:
        if len(cursor) > 4096:
            raise ValueError("Invalid directory cursor")
        state = json.loads(base64.urlsafe_b64decode(cursor))
        if (
            set(state) != {"root_cookie", "namespace", "cookie"}
            or not isinstance(state["root_cookie"], int)
            or not isinstance(state["cookie"], int)
        ):
            raise ValueError("Invalid directory cursor")
    root = libc.opendir(os.fsencode(store.root))
    if not root:
        raise OSError("Cannot list object root")
    values = []
    namespace_handle = None
    try:
        libc.seekdir(root, state["root_cookie"])
        while len(values) < page_size:
            namespace = state["namespace"]
            if namespace is None:
                candidate = libc.readdir(root)
                if not candidate:
                    return {"keys": values, "next_cursor": None}
                name = os.fsdecode(candidate.contents.name)
                state["root_cookie"] = libc.telldir(root)
                if name in {".", ".."} or name.startswith("."):
                    continue
                if (store.root / name).is_symlink():
                    continue
                directory = store.path(name + "/placeholder").parent
                if not directory.is_dir() or directory.is_symlink():
                    continue
                state["namespace"], state["cookie"] = name, 0
                namespace = name
            directory = store.path(namespace + "/placeholder").parent
            namespace_handle = libc.opendir(os.fsencode(directory))
            if not namespace_handle:
                state["namespace"], state["cookie"] = None, 0
                continue
            try:
                libc.seekdir(namespace_handle, state["cookie"])
                while len(values) < page_size:
                    entry = libc.readdir(namespace_handle)
                    if not entry:
                        state["namespace"], state["cookie"] = None, 0
                        break
                    name = os.fsdecode(entry.contents.name)
                    state["cookie"] = libc.telldir(namespace_handle)
                    if entry.contents.type == 10 or name in {".", ".."}:
                        continue
                    key = namespace + "/" + name
                    if key.startswith(prefix) and store.path(key).is_file():
                        values.append(key)
            finally:
                libc.closedir(namespace_handle)
                namespace_handle = None
        token = base64.urlsafe_b64encode(json.dumps(state, separators=(",", ":")).encode()).decode()
        return {"keys": values, "next_cursor": token}
    finally:
        if namespace_handle:
            libc.closedir(namespace_handle)
        libc.closedir(root)
