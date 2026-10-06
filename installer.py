"""Install the original Musi package with structured progress events."""
import argparse
import asyncio
import json
import plistlib
import sys
import time
import uuid
import zipfile
from pathlib import Path

BUNDLE_ID = "com.feelthemusi.musi"


def verify_ipa(path):
    with zipfile.ZipFile(path) as archive:
        infos = [name for name in archive.namelist()
                 if name.startswith("Payload/") and name.endswith(".app/Info.plist")
                 and name.count("/") == 2]
        if len(infos) != 1:
            raise ValueError("The package does not contain a single valid app.")
        info = plistlib.loads(archive.read(infos[0]))
        if info.get("CFBundleIdentifier") != BUNDLE_ID:
            raise ValueError("This package is not Musi. Select your original Musi IPA.")
        return info.get("CFBundleShortVersionString", "Unknown version")


async def connect_device(notify):
    from pymobiledevice3.usbmux import list_devices
    from pymobiledevice3.lockdown import create_using_usbmux
    notify({"type": "stage", "stage": "device", "state": "active",
            "message": "Looking for your iPhone over USB"})
    devices = [d for d in await list_devices() if d.connection_type == "USB"]
    if not devices:
        raise RuntimeError("No iPhone found. Connect it by USB, unlock it and trust this computer.")
    if len(devices) != 1:
        raise RuntimeError("More than one device is connected. Connect only the iPhone you want to use.")
    lockdown = await create_using_usbmux(serial=devices[0].serial, connection_type="USB")
    notify({"type": "stage", "stage": "device", "state": "done",
            "message": "Your iPhone is connected", "device": lockdown.all_values.get("DeviceName", "iPhone")})
    return lockdown


async def check_device(notify):
    lockdown = await connect_device(notify)
    await lockdown.close()


async def install_package(path, notify):
    from pymobiledevice3.services.afc import AfcService
    from pymobiledevice3.services.installation_proxy import InstallationProxyService
    path = Path(path)
    verify_ipa(path)
    # Reconnect after authentication/download; the phone may have been unplugged.
    lockdown = await connect_device(notify)
    remote = f"/PublicStaging/pymobiledevice3/{uuid.uuid4()}.ipa"
    try:
        async with AfcService(lockdown) as afc:
            await afc.makedirs("/PublicStaging/pymobiledevice3")
            try:
                notify({"type": "stage", "stage": "transfer", "state": "active",
                        "message": "Copying Musi to your iPhone"})
                handle = await afc.fopen(remote, "w")
                try:
                    total, completed = path.stat().st_size, 0
                    started = time.monotonic()
                    with path.open("rb") as file:
                        while chunk := file.read(512 * 1024):
                            await afc.fwrite(handle, chunk)
                            completed += len(chunk)
                            elapsed = max(time.monotonic() - started, 0.001)
                            speed = completed / elapsed
                            notify({"type": "progress", "stage": "transfer",
                                    "progress": completed / total, "bytes": completed, "total": total,
                                    "speed": speed, "eta": (total - completed) / speed})
                finally:
                    await afc.fclose(handle)
                notify({"type": "stage", "stage": "transfer", "state": "done", "message": "App copied to your iPhone"})
                notify({"type": "stage", "stage": "install", "state": "active", "message": "iOS is installing Musi"})
                def progress(percent, *args):
                    notify({"type": "progress", "stage": "install", "progress": float(percent) / 100})
                async with InstallationProxyService(lockdown) as service:
                    await service.send_package("Install", {}, progress, remote)
                notify({"type": "stage", "stage": "install", "state": "done", "message": "Musi is installed"})
            finally:
                await afc.rm_single(remote, force=True)
    finally:
        await lockdown.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ipa", type=Path)
    parser.add_argument("--verify", action="store_true", help="Validate the package without connecting to an iPhone")
    args = parser.parse_args()
    try:
        print(f"Musi {verify_ipa(args.ipa)}")
        if not args.verify:
            asyncio.run(install_package(args.ipa, lambda event: print(json.dumps(event), flush=True)))
        return 0
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
