from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

DeviceKind = Literal["input", "output"]
DeviceSetting = int | str | None


def _channel_key(kind: DeviceKind) -> str:
    return "max_input_channels" if kind == "input" else "max_output_channels"


def _device_has_channels(device: Mapping[str, Any], kind: DeviceKind) -> bool:
    return int(device.get(_channel_key(kind), 0) or 0) > 0


def _default_for_kind(default_device: Any, kind: DeviceKind) -> int | None:
    if default_device is None:
        return None
    if isinstance(default_device, int):
        return default_device
    try:
        index = 0 if kind == "input" else 1
        value = default_device[index]
    except (IndexError, TypeError):
        return None
    return int(value) if value is not None and int(value) >= 0 else None


def resolve_audio_device(
    devices: Sequence[Mapping[str, Any]],
    requested: DeviceSetting,
    kind: DeviceKind,
    default_device: Any = None,
) -> int | None:
    """Resolve an audio device by index, name substring, or auto/default."""
    if requested is None:
        requested = "auto"
    if isinstance(requested, str):
        requested = requested.strip()
        if not requested or requested.lower() in {"auto", "default"}:
            default_id = _default_for_kind(default_device, kind)
            if default_id is not None and default_id < len(devices) and _device_has_channels(devices[default_id], kind):
                return default_id
            for index, device in enumerate(devices):
                if _device_has_channels(device, kind):
                    return index
            return None
        if requested.isdigit():
            requested = int(requested)

    if isinstance(requested, int):
        if requested < 0 or requested >= len(devices):
            raise ValueError(f"{kind} audio device index {requested} is out of range")
        if not _device_has_channels(devices[requested], kind):
            raise ValueError(f"audio device {requested} cannot be used as an {kind} device")
        return requested

    needle = str(requested).casefold()
    matches = [
        index
        for index, device in enumerate(devices)
        if needle in str(device.get("name", "")).casefold() and _device_has_channels(device, kind)
    ]
    if not matches:
        raise ValueError(f"No {kind} audio device matches {requested!r}")
    if len(matches) > 1:
        names = ", ".join(f"{index}: {devices[index].get('name', 'Unknown')}" for index in matches)
        raise ValueError(f"Multiple {kind} audio devices match {requested!r}: {names}")
    return matches[0]


def format_audio_devices(devices: Sequence[Mapping[str, Any]], default_device: Any = None) -> str:
    default_input = _default_for_kind(default_device, "input")
    default_output = _default_for_kind(default_device, "output")
    lines = ["index  in  out  default  name"]
    for index, device in enumerate(devices):
        input_channels = int(device.get("max_input_channels", 0) or 0)
        output_channels = int(device.get("max_output_channels", 0) or 0)
        markers = []
        if index == default_input:
            markers.append("input")
        if index == default_output:
            markers.append("output")
        default_marker = ",".join(markers) if markers else "-"
        lines.append(
            f"{index:>5}  {input_channels:>2}  {output_channels:>3}  {default_marker:<7}  "
            f"{device.get('name', 'Unknown')}"
        )
    return "\n".join(lines)
