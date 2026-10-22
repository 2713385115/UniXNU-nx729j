#!/usr/bin/env python3
"""Extract the panel description of a Qualcomm MDSS node out of a DTS source.

The panel bring-up needs three groups of numbers, and all three are already in
the device tree the stock bootloader hands to the payload:

  * geometry     - width, height, bits per pixel, DSC slice layout
  * video timing - one entry per refresh rate: porches, pulse widths, pixel clock
  * init sequence - the DCS command stream the panel needs after reset

The init sequence is why this script parses the .dts text instead of the .dtb:
Qualcomm writes the stream with cells of *variable width* (0x39010000 is four
bytes, 0x2fe is two, 0x98 is one) and the width is encoded in the number of hex
digits in the source. A compiled DTB turns every cell into a u32, so that
information is gone. Reading the source keeps it.

    python3 scripts/extract-panel-timings.py --dts nx729j.dts --node bf068
    python3 scripts/extract-panel-timings.py --dts nx729j.dts --list
    python3 scripts/extract-panel-timings.py --dts nx729j.dts --node bf068 \
        --output docs/reference/panel/nx729j-panel.json

Nothing is guessed: a value that is not in the node is reported as missing in
`warnings`, not invented.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

NAME_RE = re.compile(r"[A-Za-z0-9_,\.\-\+#@]+")
HEX_RE = re.compile(r"0[xX][0-9a-fA-F]+")
DEC_RE = re.compile(r"-?\d+")


# --------------------------------------------------------------------------
# tiny DTS reader
# --------------------------------------------------------------------------
def skip(text: str, pos: int) -> int:
    """Advance past whitespace and comments."""
    while pos < len(text):
        if text[pos] in " \t\r\n":
            pos += 1
        elif text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            pos = len(text) if end < 0 else end + 2
        elif text.startswith("//", pos):
            end = text.find("\n", pos)
            pos = len(text) if end < 0 else end + 1
        else:
            return pos
    return pos


def cells_to_bytes(inner: str) -> tuple[bytes, list[dict]]:
    """Decode a <...> cell list the way Qualcomm's DSI parser does.

    Each token's byte count comes from how it was written: 0x98 is one byte,
    0x2fe is two, 0x39010000 is four. That is how the init stream stays a flat
    byte array even though DTS stores cells.
    """
    out = bytearray()
    tokens = []
    for match in re.finditer(r"0[xX][0-9a-fA-F]+|-?\d+", inner):
        raw = match.group(0)
        if raw.lower().startswith("0x"):
            digits = len(raw) - 2
            width = max(1, (digits + 1) // 2)
            value = int(raw, 16)
        else:
            value = int(raw, 10)
            width = max(1, (value.bit_length() + 7) // 8) if value >= 0 else 4
        out += (value & ((1 << (8 * width)) - 1)).to_bytes(width, "big")
        tokens.append({"token": raw, "bytes": width})
    return bytes(out), tokens


def parse_value(text: str, pos: int) -> tuple[dict, int]:
    """Parse a property value. Returns ({'kind','raw','bytes','tokens'}, pos)."""
    pos = skip(text, pos)
    if text.startswith("/bits/", pos):
        pos = text.find("<", pos)
    if pos < len(text) and text[pos] == "<":
        depth = 0
        start = pos
        while pos < len(text):
            if text[pos] == "<":
                depth += 1
            elif text[pos] == ">":
                depth -= 1
                if depth == 0:
                    pos += 1
                    break
            pos += 1
        inner = text[start + 1: pos - 1]
        data, tokens = cells_to_bytes(inner)
        return {"kind": "cells", "raw": inner.strip(), "bytes": len(data),
                "hex": data.hex(), "tokens": tokens}, pos
    if pos < len(text) and text[pos] == '"':
        end = pos + 1
        chars = []
        while end < len(text):
            if text[end] == "\\":
                chars.append(text[end: end + 2])
                end += 2
                continue
            if text[end] == '"':
                break
            chars.append(text[end])
            end += 1
        return {"kind": "string", "value": "".join(chars)}, end + 1
    if pos < len(text) and text[pos] == "[":
        end = text.find("]", pos)
        inner = text[pos + 1:end]
        data = bytes(int(t, 16) for t in inner.split())
        return {"kind": "bytes", "bytes": len(data), "hex": data.hex()}, end + 1
    match = NAME_RE.match(text, pos)
    if match:
        return {"kind": "bare", "value": match.group(0)}, match.end()
    return {"kind": "empty"}, pos + 1


def parse_block(text: str, pos: int) -> tuple[dict, list, int]:
    props: dict = {}
    children: list = []
    while True:
        pos = skip(text, pos)
        if pos >= len(text):
            return props, children, pos
        if text[pos] == "}":
            return props, children, pos + 1
        if text[pos] == "{":            # anonymous block, keep going
            pos += 1
            continue
        match = NAME_RE.match(text, pos)
        if not match:
            pos += 1
            continue
        name = match.group(0)
        pos = skip(text, match.end())
        if pos < len(text) and text[pos] == "{":
            child_props, child_children, pos = parse_block(text, pos + 1)
            children.append({"name": name, "props": child_props,
                             "children": child_children})
        elif pos < len(text) and text[pos] == "=":
            value, pos = parse_value(text, pos + 1)
            pos = skip(text, pos)
            if pos < len(text) and text[pos] == ";":
                pos += 1
            props[name] = value
        elif pos < len(text) and text[pos] == ";":
            props[name] = {"kind": "bool", "value": True}
            pos += 1
        else:
            pos += 1


def find_node(text: str, needle: str) -> tuple[str, dict, list, int] | None:
    """Find the first node whose name contains `needle`; return its contents."""
    for match in re.finditer(r"(?m)^[ \t]*([A-Za-z0-9_,\.\-\+#@]+)[ \t]*\{", text):
        name = match.group(1)
        if needle.lower() not in name.lower():
            continue
        props, children, _ = parse_block(text, match.end())
        line = text.count("\n", 0, match.start()) + 1
        return name, props, children, line
    return None


# --------------------------------------------------------------------------
# panel interpretation
# --------------------------------------------------------------------------
def number(props: dict, name: str, default=None):
    value = props.get(name)
    if not value:
        return default
    if value["kind"] == "cells":
        data = bytes.fromhex(value["hex"])
        return int.from_bytes(data, "big") if data else default
    if value["kind"] in ("bare", "string"):
        try:
            return int(value["value"], 0)
        except ValueError:
            return default
    return default


def word(props: dict, name: str, default=None):
    value = props.get(name)
    if not value:
        return default
    return value.get("value", default)


def describe_stream(prop: dict) -> dict:
    """Keep the DSI command stream as the exact byte array the panel expects.

    The stream is *not* split into packets on purpose. Qualcomm's command
    framing uses a different header length per DSI data type (0x05 and 0x15
    carry a different trailer than 0x39 and 0x29), and a wrong split reads as
    authoritative while being nonsense. The byte array is what the panel
    driver in the tree consumes, so that is what gets stored, plus the two
    landmarks that prove the array was assembled correctly:
      * the page-select write at the start (FE 98 here)
      * the sleep-out / display-on tail with its delays
    """
    data = bytes.fromhex(prop["hex"])
    findings = []

    if data[:7] == bytes.fromhex("3901000002fe98"):
        findings.append("starts with DCS long write FE 98 (page select)")
    else:
        findings.append(f"starts with {data[:7].hex(' ')}, not the usual FE 98 page select")

    sleep_out = data.find(bytes.fromhex("05010000640001 11".replace(" ", "")))
    display_on = data.find(bytes.fromhex("05010000 1400 0129".replace(" ", "")))
    if sleep_out >= 0:
        findings.append(f"sleep out (0x11, 100 ms delay) at byte {sleep_out}")
    if display_on >= 0:
        findings.append(f"display on (0x29, 20 ms delay) at byte {display_on}")
    if sleep_out >= 0 and display_on > sleep_out:
        findings.append("sleep out precedes display on, stream order is sane")
    else:
        findings.append("no sleep-out/display-on tail found: byte widths may be wrong")

    return {
        "bytes": len(data),
        "hex": prop["hex"],
        "head": data[:16].hex(" "),
        "tail": data[-16:].hex(" "),
        "findings": findings,
        "note": "byte array only; packet framing depends on the DSI data type "
                "and must be decoded by the panel driver in the build tree",
    }


def descendants(nodes: list):
    """Yield every node below `nodes`, depth first."""
    for node in nodes:
        yield node
        yield from descendants(node["children"])


def find_active_panel(text: str) -> tuple[str, dict, list, int, str] | None:
    """Follow `panel = <0xNNN>` to the node that owns that phandle.

    The bootloader hands the kernel a panel library plus one selection; the
    selection is the only way to know which panel this SKU actually uses.
    """
    selection = re.search(r"(?m)^[ \t]*panel[ \t]*=[ \t]*<(0x[0-9a-fA-F]+)>", text)
    if not selection:
        return None
    phandle = int(selection.group(1), 16)
    owner = re.search(
        rf"(?m)^[ \t]*phandle[ \t]*=[ \t]*<0x{phandle:0x}>",
        text, re.IGNORECASE)
    if not owner:
        return None
    header = None
    for candidate in re.finditer(r"(?m)^[ \t]*([A-Za-z0-9_,\.\-\+#@]+)[ \t]*\{",
                                 text[:owner.start()]):
        header = candidate
    if header is None:
        return None
    props, children, _ = parse_block(text, header.end())
    line = text.count("\n", 0, header.start()) + 1
    return header.group(1), props, children, line, f"0x{phandle:x}"


def summarise(name: str, props: dict, children: list, line: int, source: Path) -> dict:
    warnings = []
    width = number(props, "qcom,mdss-dsi-panel-width")
    height = number(props, "qcom,mdss-dsi-panel-height")
    if not width or not height:
        # Panels usually keep geometry inside each timing, under
        # qcom,mdss-dsi-display-timings; that is fine and handled below.
        pass

    # The timings live under qcom,mdss-dsi-display-timings, so look at any depth.
    timing_nodes = [n for n in descendants(children) if "timing@" in n["name"]]
    timing_nodes.sort(key=lambda n: int(re.search(r"timing@(\d+)", n["name"]).group(1)))
    timings = []
    for index, child in enumerate(timing_nodes):
        p = child["props"]
        h_pw = number(p, "qcom,mdss-dsi-h-pulse-width")
        h_bp = number(p, "qcom,mdss-dsi-h-back-porch")
        h_fp = number(p, "qcom,mdss-dsi-h-front-porch")
        v_pw = number(p, "qcom,mdss-dsi-v-pulse-width")
        v_bp = number(p, "qcom,mdss-dsi-v-back-porch")
        v_fp = number(p, "qcom,mdss-dsi-v-front-porch")
        fps = number(p, "qcom,mdss-dsi-panel-framerate")
        w = number(p, "qcom,mdss-dsi-panel-width", width)
        h = number(p, "qcom,mdss-dsi-panel-height", height)
        entry = {
            "index": index,
            "fps": fps,
            "width": w,
            "height": h,
            "h": {"pulse": h_pw, "back": h_bp, "front": h_fp},
            "v": {"pulse": v_pw, "back": v_bp, "front": v_fp},
        }
        if None not in (h_pw, h_bp, h_fp, v_pw, v_bp, v_fp, w, h, fps):
            h_total = w + h_pw + h_bp + h_fp
            v_total = h + v_pw + v_bp + v_fp
            entry["h_total"] = h_total
            entry["v_total"] = v_total
            entry["pixel_clock_hz"] = h_total * v_total * fps
            entry["pixel_clock_mhz"] = round(entry["pixel_clock_hz"] / 1e6, 3)
        else:
            warnings.append(f"timing@{index}: incomplete porches")

        clock = number(p, "qcom,mdss-dsi-panel-clockrate")
        if clock:
            entry["panel_clockrate_hz"] = clock
        for key in ("qcom,mdss-dsi-t-clk-post", "qcom,mdss-dsi-t-clk-pre",
                    "qcom,mdss-dsi-clk-post", "qcom,mdss-dsi-clk-pre"):
            value = number(p, key)
            if value is not None:
                entry[key.split("-")[-1].replace("qcom,mdss-dsi-", "")] = value

        for key, target in (("qcom,mdss-dsi-on-command", "on_command"),
                            ("qcom,mdss-dsi-off-command", "off_command")):
            prop = p.get(key) or props.get(key)
            if prop and prop.get("kind") == "cells":
                entry[target] = describe_stream(prop)
        if "on_command" not in entry:
            warnings.append(f"timing@{index}: no qcom,mdss-dsi-on-command")
        timings.append(entry)

    if not timings:
        warnings.append("no timing@N child nodes; single-timing panels keep the "
                        "porches in the node itself")
        prop = props.get("qcom,mdss-dsi-on-command")
        if prop and prop.get("kind") == "cells":
            warnings.append(f"node level on-command has {prop['bytes']} bytes")

    # DSC and compression are declared per timing as well, so fall back to the
    # first timing before giving up.
    first_props = timing_nodes[0]["props"] if timing_nodes else {}

    def panel_number(key, default=None):
        value = number(props, key)
        return value if value is not None else number(first_props, key, default)

    def panel_word(key, default=None):
        value = word(props, key)
        return value if value is not None else word(first_props, key, default)

    dsc = {}
    for key, label in (("qcom,mdss-dsc-bit-per-pixel", "bits_per_pixel"),
                       ("qcom,mdss-dsc-bit-per-component", "bits_per_component"),
                       ("qcom,mdss-dsc-slice-width", "slice_width"),
                       ("qcom,mdss-dsc-slice-height", "slice_height"),
                       ("qcom,mdss-dsc-slice-per-pkt", "slice_per_packet")):
        value = panel_number(key)
        if value is not None:
            dsc[label] = value
    if props.get("qcom,mdss-dsc-block-prediction-enable") \
            or first_props.get("qcom,mdss-dsc-block-prediction-enable"):
        dsc["block_prediction"] = True
    if panel_number("qcom,mdss-dsc-version"):
        dsc["version"] = panel_number("qcom,mdss-dsc-version")
    if panel_number("qcom,mdss-dsc-scr-version"):
        dsc["scr_version"] = panel_number("qcom,mdss-dsc-scr-version")

    if width is None and timings:
        width = timings[0].get("width")
    if height is None and timings:
        height = timings[0].get("height")
    if not width or not height:
        warnings.append("panel width/height missing from the node and its timings")

    panel = {
        "width": width,
        "height": height,
        "type": panel_word("qcom,mdss-dsi-panel-type"),
        "traffic_mode": panel_word("qcom,mdss-dsi-traffic-mode"),
        "panel_name": panel_word("qcom,mdss-dsi-panel-name"),
        "compression": panel_word("qcom,compression-mode"),
        "lanes": panel_number("qcom,mdss-dsi-panel-lanes")
                 or panel_number("qcom,mdss-dsi-lane-map"),
        "refresh_rates": sorted({t["fps"] for t in timings if t.get("fps")}),
        "dsc": dsc or None,
    }
    if panel["compression"] != "dsc" and dsc:
        warnings.append("DSC properties present but compression-mode is not dsc")

    return {
        "source": str(source),
        "node": name,
        "line": line,
        "panel": panel,
        "timings": timings,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dts", type=Path, required=True)
    parser.add_argument("--node", default="dsc_cmd",
                        help="substring of the panel node name (e.g. bf068)")
    parser.add_argument("--active", action="store_true",
                        help="follow `panel = <phandle>` and use that node")
    parser.add_argument("--list", action="store_true",
                        help="list candidate panel nodes and exit")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    text = args.dts.read_text(encoding="utf-8", errors="replace")

    if args.list:
        seen = []
        for match in re.finditer(r"(?m)^[ \t]*([A-Za-z0-9_,\.\-\+#@]+_dsc_[a-z]+)\s*\{", text):
            line = text.count("\n", 0, match.start()) + 1
            seen.append((line, match.group(1)))
        if not seen:
            print("no *_dsc_cmd / *_dsc_vid nodes found")
            return 1
        for line, name in seen:
            print(f"{line:>7}  {name}")
        return 0

    if args.active:
        active = find_active_panel(text)
        if not active:
            print("no `panel = <phandle>` selection found; pass --node instead")
            return 1
        name, props, children, line, phandle = active
        print(f"active panel selection: panel = <{phandle}>\n")
        result = summarise(name, props, children, line, args.dts)
        result["selected_by"] = f"panel = <{phandle}>"
    else:
        found = find_node(text, args.node)
        if not found:
            print(f"no node matching {args.node!r} in {args.dts}")
            return 1
        name, props, children, line = found
        result = summarise(name, props, children, line, args.dts)

    panel = result["panel"]
    print(f"node      {result['node']}  (line {result['line']})")
    print(f"panel     {panel['panel_name'] or '(unnamed)'}")
    print(f"geometry  {panel['width']}x{panel['height']}  {panel['type'] or '?'} "
          f"/ {panel['traffic_mode'] or '?'}")
    if panel["dsc"]:
        d = panel["dsc"]
        print(f"dsc       {d.get('bits_per_pixel')}bpp "
              f"slice {d.get('slice_width')}x{d.get('slice_height')} "
              f"per packet {d.get('slice_per_packet')}")
    print(f"refresh   {panel['refresh_rates']} Hz")
    print()
    print(f"{'idx':>3} {'fps':>4} {'h pw/bp/fp':>18} {'v pw/bp/fp':>14} "
          f"{'h_total':>8} {'v_total':>8} {'pclk MHz':>9} {'init':>6}")
    for t in result["timings"]:
        init = t.get("on_command", {}).get("bytes", 0)
        print(f"{t['index']:>3} {t.get('fps') or 0:>4} "
              f"{t['h']['pulse']}/{t['h']['back']}/{t['h']['front']:>4}".rjust(18)
              + f" {t['v']['pulse']}/{t['v']['back']}/{t['v']['front']}".rjust(14)
              + f" {t.get('h_total') or 0:>8} {t.get('v_total') or 0:>8}"
              + f" {t.get('pixel_clock_mhz') or 0:>9} {init:>6}")

    first = next((t for t in result["timings"] if t.get("on_command")), None)
    if first:
        stream = first["on_command"]
        print()
        print(f"init stream  {stream['bytes']} bytes")
        print(f"  head       {stream['head']}")
        print(f"  tail       {stream['tail']}")
        for finding in stream["findings"]:
            print(f"  - {finding}")

    if result["warnings"]:
        print()
        for warning in result["warnings"]:
            print(f"[warn] {warning}")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nwritten {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
