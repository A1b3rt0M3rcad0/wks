"""Decoder process cannot open private broker files or Internet sockets."""

import json
import os
import subprocess
import sys


def test_landlock_and_seccomp_bound_decoder_io(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (job / "original").write_text("authorized source")
    private = tmp_path / "private-broker-file"
    private.write_text("private sentinel")
    program = """import json,socket,sys
from pathlib import Path
from wks_worker.sandbox import restrict
from wks_core.domain.models import Error
try:
    restrict(sys.argv[1])
except Error as error:
    if error.code != "processing.sandbox_unavailable": raise
    print(json.dumps({"strict_unavailable":True}));sys.exit(0)
value={"input":Path(sys.argv[1],"original").read_text()}
Path(sys.argv[1],"output").write_text("derived output")
try:
    Path(sys.argv[2]).read_bytes(); value["private_blocked"]=False
except PermissionError: value["private_blocked"]=True
try:
    socket.socket(); value["network_blocked"]=False
except PermissionError: value["network_blocked"]=True
print(json.dumps(value))"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(job), str(private)],
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "TMPDIR": str(job)},
        check=True,
    )
    value = json.loads(result.stdout)
    if value == {"strict_unavailable": True}:
        assert not (job / "output").exists()
        return
    assert value == {"input": "authorized source", "private_blocked": True, "network_blocked": True}
    assert (job / "output").read_text() == "derived output"


def test_seccomp_blocks_network_in_decoder_process(tmp_path):
    program = """import socket,sys,json
from wks_worker.sandbox import restrict
status=restrict(sys.argv[1], filesystem_required=False)
try:
    socket.socket();blocked=False
except PermissionError:blocked=True
print(json.dumps({"network_blocked":blocked,"status":status}))"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ.get("PATH", "")},
    )
    value = json.loads(result.stdout)
    assert value["network_blocked"]
    assert value["status"]["network"] == "seccomp"
