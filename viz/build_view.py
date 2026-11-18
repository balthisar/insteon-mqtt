#!/usr/bin/env python3
"""Build the split-view link visualizer.

Reads the all-link databases (DATA, default ./data), device names from
config.yaml (CONFIG) and scene definitions from scenes.yaml (SCENES), then
writes a self-contained viewer.html next to this script.

Everything is derived from the files, so re-running this after changing
config.yaml, scenes.yaml or the data directory refreshes the viewer:

    python viz/build_view.py
    open viz/viewer.html
"""
import datetime
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import link_graph as lg  # noqa: E402  (needs matplotlib installed)

VIEW = (0, 0, 940, 900)
VIEW_W, VIEW_H = 940, 900
CX, CY, RING_R = 470, 395, 290
TRAY_Y = 805


#-----------------------------------------------------------------------
def slug(text):
    """URL-safe id from a scene label."""
    value = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return value or "scene"


#-----------------------------------------------------------------------
def display_name(addr, devices):
    """Human name for an address, falling back to the model or address."""
    if addr == lg.MODEM:
        return "modem"
    data = devices.get(addr)
    if not data:
        return addr
    return lg.NAMES.get(addr) or lg.desc_of(data) or lg.model_of(data) or addr


#-----------------------------------------------------------------------
def short(addr, devices):
    """Short label used in scene names and warnings."""
    return lg.short_name(display_name(addr, devices))


#-----------------------------------------------------------------------
def issue_label(addr, devices):
    """Name plus address for issues, because names can be reused."""
    if addr == lg.MODEM:
        return "modem [%s]" % addr
    name = lg.NAMES.get(addr)
    if name:
        return "%s [%s]" % (lg.short_name(name), addr)
    data = devices.get(addr)
    desc = lg.desc_of(data) if data else None
    return "%s [%s]" % (desc, addr) if desc else addr


#-----------------------------------------------------------------------
def edge_kind(ctrl, resp, group):
    """Classify a controller -> responder link."""
    if ctrl == lg.MODEM:
        return "pair" if group == 1 else "scene"
    if resp == lg.MODEM:
        return "report" if group == 1 else "button"
    return "device"


#-----------------------------------------------------------------------
def layout(devices, states, phantoms):
    """Place modem, connected devices on a ring and orphans in a tray."""
    ring = [a for a in devices if not (states[a][2] or states[a][3])]
    ring.sort(key=lambda a: (lg.NAMES.get(a) or a).lower())
    pos = {lg.MODEM: (CX, CY)}
    for i, addr in enumerate(ring):
        angle = -math.pi / 2 + 2 * math.pi * i / len(ring)
        pos[addr] = (CX + RING_R * math.cos(angle),
                     CY + RING_R * math.sin(angle))
    tray = [a for a in devices if states[a][2] or states[a][3]] + phantoms
    tray.sort(key=lambda a: (lg.NAMES.get(a) or a).lower())
    for i, addr in enumerate(tray):
        step = (VIEW_W - 200) / max(1, len(tray) - 1)
        pos[addr] = (100 + i * step, TRAY_Y)
    return pos, ring, tray


#-----------------------------------------------------------------------
def scene_label(entry, devices):
    """Display label for a scenes.yaml entry."""
    if entry["name"]:
        return entry["name"]
    ctrls = entry["controllers"]
    if len(ctrls) == 1:
        addr, group = ctrls[0]
        if addr == lg.MODEM:
            return "M%d" % group
        return "%s button %d" % (short(addr, devices), group)
    for addr, group in ctrls:
        if lg.TYPES.get(addr) in ("keypad_linc", "keypad_linc_sw") and group > 1:
            return "%s button %d" % (short(addr, devices), group)
    bases = {short(addr, devices).split(" (")[0] for addr, _ in ctrls}
    if len(bases) == 1:
        return bases.pop() + " 3-way"
    return "Multi-controller scene"


#-----------------------------------------------------------------------
CLI = "insteon-mqtt config.yaml"
SEVERITY = {"error": 0, "warn": 1, "info": 2}

CONFIG_TYPE = {
    "dimmer": "dimmer", "switch": "switch", "relay": "switch",
    "keypad": "keypad_linc", "remote": "mini_remote8",
    "fan": "fan_linc", "sensor": "battery_sensor",
    "thermostat": "thermostat",
}


def cmd(label, command, risk="read"):
    """One copyable command (or YAML snippet) in a fix step."""
    return {"label": label, "command": command, "risk": risk}


def issue(kind, severity, message, why, checks=None, fixes=None, guard=None):
    """Structured issue shown in the details panel."""
    return {"kind": kind, "severity": severity, "message": message,
            "why": why, "checks": checks or [], "fixes": fixes or [],
            "guard": guard}


def is_configured(addr):
    """True if the address is in config.yaml (or is the modem)."""
    return addr == lg.MODEM or addr in lg.TYPES


def config_snippet(addr, devices):
    """Suggested config.yaml entry for an unconfigured device."""
    data = devices.get(addr)
    model = lg.model_of(data) if data else "?"
    dev_type = CONFIG_TYPE.get(lg.family_of(addr, model), "switch")
    name = (lg.NAMES.get(addr) or lg.desc_of(data) or addr).replace("'", "")
    return "%s:\n  - %s: '%s'" % (dev_type, addr, name)


def has_ctrl(ctrl, resp, group, devices, modem):
    """True if the controller side of a link exists."""
    if ctrl == lg.MODEM:
        return any(e["addr"] == resp and e["group"] == group and
                   e["is_controller"] for e in modem["entries"])
    data = devices.get(ctrl)
    if not data:
        return False
    return any(e["addr"] == resp and e["group"] == group and
               e["db_flags"]["is_controller"] for e in data["used"])


def not_configured_issue(addr, devices):
    """Issue for a device that has a DB but no config entry."""
    return issue(
        "not_configured", "warn",
        "%s is not in config.yaml" % issue_label(addr, devices),
        "The server resolves devices from config.yaml. This address has a "
        "database but no config entry, so CLI and MQTT commands cannot "
        "target it.",
        fixes=[cmd("Add it to config.yaml", config_snippet(addr, devices),
                   "write")],
        guard="Use the right device type, restart the server after editing, "
              "then refresh and re-check.")


def inspect_cmd(addr, devices):
    """Read-only command to inspect a device or raw DB file."""
    if is_configured(addr):
        return cmd("Inspect %s" % issue_label(addr, devices),
                   "%s print-db %s" % (CLI, addr))
    return cmd("Inspect the raw DB", "cat data/%s.json" % addr)


def unexpected_responder_issue(ctrl, resp, group, devices):
    """Issue for a controller-only device that is a responder."""
    checks = [inspect_cmd(resp, devices)]
    if ctrl != lg.MODEM:
        checks.append(inspect_cmd(ctrl, devices))
    fixes = [cmd("Remove the responder record",
                 "%s db-delete -o %s resp %s %d" %
                 (CLI, resp, ctrl, group), "destructive")]
    if ctrl != lg.MODEM:
        fixes.append(cmd("Or remove both sides",
                         "%s db-delete %s resp %s %d" %
                         (CLI, resp, ctrl, group), "destructive"))
    return issue(
        "unexpected_responder", "warn",
        "%s is a responder to %s group %d" %
        (issue_label(resp, devices), issue_label(ctrl, devices), group),
        "This is a mini-remote: it has no load to switch, so a responder "
        "record is illogical. It is usually created by linking two remotes "
        "directly, and it does not make one remote replay the other's "
        "scenes.",
        checks=checks,
        fixes=fixes,
        guard="Wake battery devices before writing. If the controller is "
              "the modem, the responder link may be an I2CS requirement; "
              "verify before deleting.")


#-----------------------------------------------------------------------
def validate(entry, edges, devices, modem):
    """Compare a scenes.yaml entry with the actual link databases."""
    issues = []
    seen = set()

    def add(item):
        if item["message"] not in seen:
            seen.add(item["message"])
            issues.append(item)

    controllers = entry["controllers"]
    responders = set(entry["responders"])
    extras = set()

    for addr, group in controllers:
        db = {r for (c, r, g) in edges if c == addr and g == group
              and r not in (lg.MODEM, addr)}
        expected = responders - {addr}
        extras |= db - expected

        for missing in sorted(expected - db):
            add(issue(
                "missing_link", "error",
                "%s has no link from %s" % (issue_label(missing, devices),
                                            issue_label(addr, devices)),
                "scenes.yaml declares %s as a responder for %s group %d, "
                "but the controller's database has no entry for it." %
                (issue_label(missing, devices), issue_label(addr, devices),
                 group),
                checks=[inspect_cmd(addr, devices)],
                fixes=[cmd("Dry-run the controller sync",
                           "%s sync %s" % (CLI, addr)),
                       cmd("Apply the sync",
                           "%s sync %s --run" % (CLI, addr), "destructive")],
                guard="Read the dry run first: sync removes links that "
                      "scenes.yaml doesn't declare."))

        for extra in sorted(db - expected):
            ctrl_ok = has_ctrl(addr, extra, group, devices, modem)
            if ctrl_ok:
                message = "%s is linked from %s but not declared" % (
                    issue_label(extra, devices), issue_label(addr, devices))
                why = ("The link exists on both sides, but scenes.yaml "
                       "doesn't include it.")
            else:
                message = ("%s has a device-side link for %s but is not "
                           "declared" % (issue_label(extra, devices),
                                         issue_label(addr, devices)))
                why = ("The device has a responder record for this group, "
                       "but the controller has no controller record and "
                       "scenes.yaml doesn't declare it.")
            fixes = []
            if is_configured(extra):
                fixes += [cmd("Inspect the device",
                              "%s print-db %s" % (CLI, extra)),
                          cmd("Remove the device-side link",
                              "%s db-delete -o %s resp %s %d" %
                              (CLI, extra, addr, group), "destructive"),
                          cmd("Or import the controller's links",
                              "%s import-scenes %s" % (CLI, addr), "write")]
                if ctrl_ok:
                    fixes.insert(2, cmd(
                        "Or remove both sides",
                        "%s db-delete %s resp %s %d" %
                        (CLI, extra, addr, group), "destructive"))
            else:
                fixes += [cmd("Add it to config.yaml first",
                              config_snippet(extra, devices), "write"),
                          cmd("Inspect the raw DB",
                              "cat data/%s.json" % extra)]
            add(issue("extra_link", "warn", message, why,
                      checks=[inspect_cmd(addr, devices)],
                      fixes=fixes,
                      guard="If the device is gone, remove its side; if it "
                            "is real, declare it and add the missing "
                            "controller half. Dry-run any sync first."))

        for resp in sorted(expected):
            if (addr, resp, group) in edges and \
                    edges[(addr, resp, group)] is None:
                fixes = []
                if is_configured(resp):
                    fixes += [cmd("Add the responder link",
                                  "%s db-add -o %s 1 resp %s %d" %
                                  (CLI, resp, addr, group), "write"),
                              cmd("Or dry-run a device sync",
                                  "%s sync %s" % (CLI, resp))]
                else:
                    fixes.append(cmd("Add it to config.yaml first",
                                     config_snippet(resp, devices), "write"))
                add(issue(
                    "no_responder_record", "error",
                    "%s has no responder record for %s" %
                    (issue_label(resp, devices), issue_label(addr, devices)),
                    "The controller has a link for this group, but the "
                    "responder's database has no matching record, so it "
                    "will not act on the scene.",
                    checks=[inspect_cmd(resp, devices)],
                    fixes=fixes,
                    guard="Targeted db-add avoids deleting this device's "
                          "other links; sync --run may remove undeclared "
                          "ones. db-add uses the device default level; add "
                          "--level/--ramp to match a scene-specific level."))

        for resp in sorted(expected & db):
            if not has_ctrl(addr, resp, group, devices, modem):
                add(issue(
                    "missing_link", "error",
                    "%s has no controller record for %s" %
                    (issue_label(addr, devices), issue_label(resp, devices)),
                    "The device has a responder record, but the controller "
                    "has no controller record; the link is one-sided.",
                    checks=[inspect_cmd(addr, devices)],
                    fixes=[cmd("Add the controller record",
                               "%s db-add -o %s %d ctrl %s 1" %
                               (CLI, addr, group, resp), "write")],
                    guard="Add only the missing side; the responder already "
                          "has its record."))

    for addr in sorted(({a for a, _ in controllers} | responders | extras)):
        if addr in devices and not is_configured(addr):
            add(not_configured_issue(addr, devices))

    for resp in sorted(responders):
        if resp not in devices and resp != lg.MODEM:
            for addr, group in controllers:
                if (addr, resp, group) in edges:
                    add(issue(
                        "no_database", "warn",
                        "%s has no database file (referenced by %s)" %
                        (resp, issue_label(addr, devices)),
                        "This link points at an address with no database "
                        "file. It may be a removed device or an old PLM.",
                        checks=[inspect_cmd(addr, devices)],
                        fixes=[cmd("Remove the stale link",
                                   "%s db-delete -o %s ctrl %s %d" %
                                   (CLI, addr, resp, group), "destructive")],
                        guard="The missing address isn't commandable; fix "
                              "the device that points at it."))
                    break
    return issues


#-----------------------------------------------------------------------
def build():
    """Return the JSON data used by the viewer."""
    devices, modem = lg.load()
    lg.NAMES.update(lg.load_names(lg.CONFIG))
    raw_scenes = lg.load_scenes(lg.SCENES_PATH, lg.NAMES)
    records, edges = lg.build(devices, modem)
    states = {addr: lg.pairing_state(addr, devices, modem,
                                     lg.is_controller_only(addr))
              for addr in devices}

    # Controller-only devices must not have responder records.
    controller_only = {a for a in devices if lg.is_controller_only(a)}
    device_issues = {}
    for (ctrl, resp, group), rec in edges.items():
        if rec is not None and resp in controller_only:
            device_issues.setdefault(resp, []).append(
                unexpected_responder_issue(ctrl, resp, group, devices))

    # Addresses referenced by links but with no database file.
    seen = {lg.MODEM} | set(devices)
    phantoms = set()
    for (ctrl, resp, _group) in edges:
        for addr in (ctrl, resp):
            if addr not in seen:
                phantoms.add(addr)

    pos, ring, tray = layout(devices, states, sorted(phantoms))

    # ---- scenes ------------------------------------------------------
    scenes = []
    used_ids = set()
    for entry in raw_scenes:
        label = scene_label(entry, devices)
        sid = slug(label)
        while sid in used_ids:
            sid += "-2"
        used_ids.add(sid)
        kind = "modem" if any(a == lg.MODEM for a, _ in entry["controllers"]) \
            else "device"
        warnings = validate(entry, edges, devices, modem)
        for ref in entry.get("unresolved", []):
            warnings.append(issue(
                "unknown_ref", "error",
                "unknown device '%s' in scenes.yaml" % ref,
                "scenes.yaml references a name or address that isn't in "
                "config.yaml; the member was skipped.",
                guard="Fix the typo or add the device to config.yaml."))
        scenes.append({
            "id": sid,
            "label": label,
            "name": entry["name"],
            "kind": kind,
            "declared": True,
            "controllers": [list(c) for c in entry["controllers"]],
            "responders": entry["responders"],
            "warnings": sorted(warnings, key=lambda w: SEVERITY.get(
                w["severity"], 3)),
        })

    # Scenes present in the databases but not declared in scenes.yaml.
    declared = {(a, g) for s in scenes for a, g in s["controllers"]}
    unmanaged = {}
    for (ctrl, resp, group) in edges:
        if ctrl == lg.MODEM and group == 1:
            continue
        if ctrl != lg.MODEM and group == 1 and resp == lg.MODEM:
            continue
        if (ctrl, group) in declared:
            continue
        if resp != lg.MODEM:
            unmanaged.setdefault((ctrl, group), set()).add(resp)
    for (ctrl, group), responders in sorted(unmanaged.items()):
        if ctrl == lg.MODEM:
            label = "M%d [modem %s]" % (group, ctrl)
        else:
            label = "%s [%s] button %d" % (short(ctrl, devices), ctrl, group)
        label += " (unmanaged)"
        sid = slug(label)
        warnings = [issue(
            "unmanaged", "warn",
            "controller %s not in scenes.yaml" % issue_label(ctrl, devices),
            "The database has links for this controller/group, but "
            "scenes.yaml doesn't declare them. A sync would delete them.",
            checks=[inspect_cmd(ctrl, devices)],
            fixes=([cmd("Import into scenes.yaml",
                        "%s import-scenes %s" % (CLI, ctrl), "write"),
                    cmd("Or remove them (dry run first)",
                        "%s sync %s" % (CLI, ctrl), "destructive")]
                   if is_configured(ctrl) else
                   [cmd("Add it to config.yaml first",
                        config_snippet(ctrl, devices), "write")]),
            guard="Import first if these are real buttons; sync --run "
                  "erases undeclared links.")]
        if not is_configured(ctrl):
            warnings.append(not_configured_issue(ctrl, devices))
        scenes.append({
            "id": sid, "label": label, "name": None,
            "kind": "modem" if ctrl == lg.MODEM else "device",
            "declared": False,
            "controllers": [[ctrl, group]],
            "responders": sorted(responders),
            "warnings": sorted(warnings, key=lambda w: SEVERITY.get(
                w["severity"], 3)),
        })

    # ---- nodes -------------------------------------------------------
    nodes = []
    for addr in sorted(set(devices) | set(phantoms) | {lg.MODEM}):
        info = lg.node_info(addr, devices, edges)
        if addr == lg.MODEM:
            pairing = "modem"
        elif addr in phantoms:
            pairing = "phantom"
        else:
            state = states[addr]
            pairing = ("complete" if state[0] else "partial" if state[1]
                       else "orphan" if state[2] else "disconnected")
        scene_count = sum(1 for s in scenes
                          if addr in [a for a, _ in s["controllers"]] or
                          addr in s["responders"])
        buttons = sum(1 for (c, r, g) in edges
                      if c == addr and r == lg.MODEM and g > 1)
        x, y = pos.get(addr, (CX, CY))
        nodes.append({
            "id": addr,
            "name": (display_name(addr, devices) if addr != lg.MODEM
                     else "modem %s" % addr),
            "model": info["model"],
            "family": info["family"],
            "engine": info.get("engine"),
            "phantom": addr in phantoms,
            "configured": addr == lg.MODEM or is_configured(addr),
            "one_way": lg.is_controller_only(addr),
            "pairing": pairing,
            "scenes": scene_count,
            "buttons": buttons,
            "x": round(x, 1), "y": round(y, 1),
        })

    # ---- edges -------------------------------------------------------
    edge_list = []
    for (ctrl, resp, group), rec in edges.items():
        edge_list.append({
            "c": ctrl, "r": resp, "g": group,
            "kind": edge_kind(ctrl, resp, group),
            "complete": rec is not None,
            "data": list(rec["data"]) if rec else None,
        })

    # One canvas pairing edge per device (pair and report overlap).
    pair_edges = []
    for addr in devices:
        state = states[addr]
        if state[2] or state[3]:
            continue
        pair_edges.append({"c": lg.MODEM, "r": addr, "g": 1,
                           "kind": "pair", "complete": state[0]})
    edge_list = [e for e in edge_list if e["kind"] not in ("pair", "report")]
    edge_list.extend(pair_edges)

    # Flag unexpected responders in every scene that touches them.
    for scene in scenes:
        ctrls = {(a, g) for a, g in scene["controllers"]}
        known = {w["message"] for w in scene["warnings"]}
        for (ctrl, resp, group), rec in edges.items():
            if rec is None or resp not in controller_only:
                continue
            if (ctrl, group) not in ctrls:
                continue
            item = unexpected_responder_issue(ctrl, resp, group, devices)
            if item["message"] not in known:
                scene["warnings"].append(item)
                known.add(item["message"])
        scene["warnings"].sort(key=lambda w: SEVERITY.get(w["severity"], 3))

    return {
        "meta": {
            "modem": lg.MODEM,
            "generated": datetime.datetime.now().isoformat(timespec="seconds"),
            "devices": len(devices),
            "links": len(edges),
            "scenes": len(scenes),
            "colors": lg.COLOR,
        },
        "layout": {
            "view": list(VIEW),
            "modem": [CX, CY],
            "tray": tray,
            "trayY": TRAY_Y,
        },
        "nodes": nodes,
        "edges": edge_list,
        "scenes": scenes,
        "device_issues": device_issues,
    }


#-----------------------------------------------------------------------
def main():
    data = build()
    template_path = os.path.join(HERE, "viewer_template.html")
    with open(template_path) as f:
        template = f.read()
    payload = json.dumps(data, separators=(",", ":"))
    output = template.replace("__DATA_JSON__", payload)
    out_path = os.environ.get("VIEW", os.path.join(HERE, "viewer.html"))
    with open(out_path, "w") as f:
        f.write(output)
    print("wrote %s (%d nodes, %d edges, %d scenes)"
          % (out_path, len(data["nodes"]), len(data["edges"]),
             len(data["scenes"])))


if __name__ == "__main__":
    main()
