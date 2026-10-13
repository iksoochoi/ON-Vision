"""List the connected OAK devices and print an ON_CAMERAS line for .env.

    .venv/bin/python -m backend.cli.list_cameras

Assign roles by order first, start the app, and look at the live page: wave a hand in front of one
camera to see which tile reacts, then swap the ids in .env until every role shows the right camera."""

import sys

ROLES = ("top", "front", "back", "left", "right")


def main():
    import depthai as dai

    try:
        devices = dai.Device.getAllAvailableDevices()
    except Exception as e:  # noqa: BLE001 - usually a USB / driver problem
        raise SystemExit(f"Could not query OAK devices: {e}")
    if not devices:
        print("No OAK devices found. Check the USB cables / hub power. (Cameras opened by another program are not listed.)")
        sys.exit(1)

    print(f"{len(devices)} OAK device(s) found:")
    ids = []
    for number, info in enumerate(devices, 1):
        try:
            device_id = str(info.getDeviceId())
        except AttributeError:
            device_id = str(info.deviceId)
        ids.append(device_id)
        print(f"  {number}. id={device_id}  name={info.name}  state={info.state.name}")

    print("\nPaste into .env (roles assigned in this order):")
    print("ON_CAMERAS=" + ",".join(f"{role}:{device_id}" for role, device_id in zip(ROLES, ids)))
    if len(ids) not in (1, len(ROLES)):
        print(f"note: {len(ids)} device(s) found, the plan is {len(ROLES)} (1 top + 4 side)")


if __name__ == "__main__":
    main()
