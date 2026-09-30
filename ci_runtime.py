"""Retain actual simulator commands, app-generated receipts and original HAR."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

OUT = Path("evidence")
OUT.mkdir(exist_ok=True)
SID = os.environ["TCGEN_DASHBOARD_SESSION"]
ORIGIN = os.environ["TCGEN_BACKEND_ORIGIN"]
RUN = os.environ["GITHUB_RUN_ID"]
BUNDLE = "org.tcgen.phase2c.IOSCoreProbe"
assert re.fullmatch(r"[a-f0-9]{32}", SID)
assert re.fullmatch(r"https://[a-z0-9-]+\.trycloudflare\.com", ORIGIN)


def command(args, filename, env=None):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    (OUT / filename).write_bytes(result.stdout)
    result.check_returncode()
    return result.stdout.decode().strip()


command(["sw_vers"], "macos.txt")
command(["xcodebuild", "-version"], "xcode.txt")
sdk = command(["xcrun", "--sdk", "iphonesimulator", "--show-sdk-version"], "sdk-version.txt")
devices = json.loads(command(["xcrun", "simctl", "list", "devices", "available", "--json"], "devices-before.json"))["devices"]
candidates = [(r, d) for r, rows in devices.items() if r.endswith(".iOS-" + sdk.replace(".", "-")) for d in rows if "iPhone" in d["name"] and d.get("isAvailable")]
runtime, device = sorted(candidates, key=lambda p: p[1]["name"])[-1]
udid = device["udid"]
if device["state"] != "Booted":
    command(["xcrun", "simctl", "boot", udid], "boot.txt")
command(["xcrun", "simctl", "bootstatus", udid, "-b"], "bootstatus.txt")
booted = json.loads(command(["xcrun", "simctl", "list", "devices", "--json"], "devices-after-boot.json"))
actual = next(d for rows in booted["devices"].values() for d in rows if d["udid"] == udid)
assert actual["state"] == "Booted"
(OUT / "boot-device.json").write_text(json.dumps({"runtime": runtime, **actual}, indent=2))
if not shutil.which("xcodegen"):
    command(["brew", "install", "xcodegen"], "xcodegen-install.txt")
command(["xcodegen", "generate", "--spec", "IOSCoreProbe/project.yml"], "xcodegen.txt")
args = ["xcodebuild", "-project", "IOSCoreProbe/IOSCoreProbe.xcodeproj", "-scheme", "IOSCoreProbe",
        "-destination", "platform=iOS Simulator,id=" + udid, "-derivedDataPath", "build",
        "-parallel-testing-enabled", "NO", "CODE_SIGNING_ALLOWED=NO",
        "TCGEN_BACKEND_ORIGIN=" + ORIGIN, "TCGEN_DASHBOARD_SESSION=" + SID, "TCGEN_CI_RUN_ID=" + RUN]
command(args + ["build-for-testing"], "build.log")
app = Path("build/Build/Products/Debug-iphonesimulator/IOSCoreProbe.app")
command(["xcrun", "simctl", "install", udid, str(app)], "install.txt")
installed = command(["xcrun", "simctl", "get_app_container", udid, BUNDLE, "app"], "installed-container.txt")
command(["plutil", "-convert", "json", "-o", "-", installed + "/Info.plist"], "installed-app.json")
launch_env = dict(os.environ, SIMCTL_CHILD_TCGEN_BACKEND_ORIGIN=ORIGIN,
                  SIMCTL_CHILD_TCGEN_DASHBOARD_SESSION=SID, SIMCTL_CHILD_TCGEN_CI_RUN_ID=RUN)
command(["xcrun", "simctl", "launch", "--terminate-running-process", udid, BUNDLE], "launch.txt", launch_env)
command(args + ["-resultBundlePath", "evidence/IOSCoreProbe.xcresult", "test-without-building"], "xcodebuild.log")
command(["xcrun", "xcresulttool", "get", "test-results", "summary", "--path", "evidence/IOSCoreProbe.xcresult"], "test-summary.json")
command(["xcrun", "xcresulttool", "export", "attachments", "--path", "evidence/IOSCoreProbe.xcresult", "--output-path", "evidence/screenshots"], "attachment-export.txt")
container = command(["xcrun", "simctl", "get_app_container", udid, BUNDLE, "data"], "data-container.txt")
shutil.copyfile(Path(container) / "Documents/runtime-receipt.json", OUT / "runtime-receipt.json")
with urllib.request.urlopen(ORIGIN + "/capture/" + SID + ".har", timeout=30) as response:
    (OUT / "original-capture.har").write_bytes(response.read())
command(["ditto", "-c", "-k", "--keepParent", str(app), "evidence/IOSCoreProbe.app.zip"], "archive-app.txt")
proof = {"schemaVersion": 1, "runId": RUN, "commit": os.environ["GITHUB_SHA"],
         "dashboardSession": SID, "bundleId": BUNDLE, "simulatorUDID": udid, "runtime": runtime,
         "artifacts": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.iterdir() if p.is_file()}}
(OUT / "provenance.json").write_text(json.dumps(proof, indent=2))
print("Native runtime and original capture evidence retained for Dashboard session " + SID)
