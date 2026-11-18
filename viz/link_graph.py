#!/usr/bin/env python3
"""Prototype link-database visualizer.

Reads the Insteon all-link database JSON files from data/ and renders:

  network.png     device-to-device controller -> responder graph
  scene_matrix.png  modem virtual-scene membership heat map

This is a scratch prototype to test visual encodings, not part of the
insteon-mqtt package.  Run with the project's Python environment:

    python viz/link_graph.py

Set the DATA and OUT environment variables to override the paths.
"""
import glob
import json
import os
import re
import sys
import collections
import math
import textwrap

# Make the repo root importable when this script is run from anywhere.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Patch
import networkx as nx
import numpy as np
import yaml

DATA_DIR = os.environ.get("DATA", "data")
OUT_DIR = os.environ.get("OUT", "viz")
CONFIG = os.environ.get("CONFIG", os.path.join(ROOT, "config.yaml"))
SCENES_PATH = os.environ.get("SCENES", os.path.join(ROOT, "scenes.yaml"))
MODEM = None

# address -> nice name and device type, loaded from config.yaml.
NAMES = {}
TYPES = {}
# scenes.yaml entries: {name, controllers: [(addr, group)], responders: [addr]}
SCENES = []

# Model -> visual family.  Anything unknown gets the "other" style.
FAMILY = {
    "2477D": "dimmer",
    "2442-222": "dimmer",
    "2477S": "switch",
    "2476S": "switch",
    "2334-232": "keypad",
    "2342-222": "remote",
    "2342-242": "remote",
    "2845-222": "sensor",
    "2475F": "fan",
    "2413U": "modem",
}
COLOR = {
    "dimmer": "#4C78A8",
    "switch": "#59A14F",
    "relay": "#59A14F",
    "keypad": "#B07AA1",
    "remote": "#F28E2B",
    "sensor": "#76B7B2",
    "fan": "#EDC948",
    "thermostat": "#E15759",
    "modem": "#666666",
    "other": "#D3D3D3",
}
MARKER = {
    "dimmer": "o", "switch": "o", "relay": "o", "keypad": "s",
    "remote": "D", "sensor": "^", "fan": "h", "thermostat": "P",
    "modem": "X", "other": "o",
}

# Role colors (kept in sync with the viewer): cool/warm contrast.
CTRL_COLOR = "#1D4ED8"
RESP_COLOR = "#EA580C"

# config device type -> visual family
# Controller-only devices (battery remotes) have no load: they report to the
# modem and never need the modem->device command direction.
CONTROLLER_ONLY_TYPES = {
    "mini_remote1", "mini_remote4", "mini_remote6", "mini_remote8", "remote",
}


def is_controller_only(addr):
    """True for a battery/controller-only device type."""
    return TYPES.get(addr) in CONTROLLER_ONLY_TYPES


TYPE_FAMILY = {
    "switch": "switch", "outlet": "switch",
    "dimmer": "dimmer",
    "keypad_linc": "keypad", "keypad_linc_sw": "keypad",
    "fan_linc": "fan",
    "mini_remote1": "remote", "mini_remote4": "remote",
    "mini_remote6": "remote", "mini_remote8": "remote",
    "battery_sensor": "sensor", "hidden_door": "sensor",
    "motion": "sensor", "leak": "sensor", "smoke_bridge": "sensor",
    "thermostat": "thermostat",
    "io_linc": "relay", "ezio4o": "relay",
}


#-----------------------------------------------------------------------
def load():
    """Return (devices, modem) parsed from the JSON databases.

    The modem address is derived from the file that has an "entries" list,
    so nothing is tied to a specific installation.
    """
    global MODEM
    devices = {}
    modem = None
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*.json"))):
        with open(path) as f:
            data = json.load(f)
        if "entries" in data:
            modem = data
            name = os.path.splitext(os.path.basename(path))[0]
            MODEM = normalize_addr(name) or name
        else:
            devices[data["address"]] = data
    return devices, modem


#-----------------------------------------------------------------------
def short_name(name):
    """Drop the repetitive 'Light Switch' suffix for graph labels."""
    text = re.sub(r"\s+(Light|Lights)\s+Switch", "", name, flags=re.I)
    text = re.sub(r"\s+Switch\b", "", text, flags=re.I)
    return text.strip()


#-----------------------------------------------------------------------
def normalize_addr(value):
    """Return a dotted lower case address for a config key, or None."""
    text = re.sub(r"[^0-9a-f]", "", str(value).lower())
    if len(text) != 6:
        return None
    return ".".join(text[i:i + 2] for i in range(0, 6, 2))


#-----------------------------------------------------------------------
def load_names(path):
    """Read device address -> name mappings from a config.yaml file."""
    if not path or not os.path.exists(path):
        return {}
    with open(path) as f:
        config = yaml.safe_load(f) or {}
    names = {}
    devices = (config.get("insteon") or {}).get("devices") or {}
    for dev_type, entries in devices.items():
        for item in entries or []:
            if isinstance(item, str):
                addr = normalize_addr(item)
                if addr:
                    TYPES[addr] = dev_type
                continue
            if not isinstance(item, dict):
                continue
            for key, value in item.items():
                addr = normalize_addr(key)
                if addr and isinstance(value, str):
                    names[addr] = value
                    TYPES[addr] = dev_type
    return names


#-----------------------------------------------------------------------
def _scene_label(info):
    """Name + address label for a scene member."""
    name = info["name"] or info["desc"] or info["model"]
    return textwrap.fill(short_name(name), 16) + "\n" + info["addr"]


#-----------------------------------------------------------------------
def load_scenes(path, names):
    """Read scenes.yaml and resolve device names to addresses.

    Scene entries keep their controller groups and responder addresses.
    A device can be both a controller and a responder (3-way scenes).
    """
    if not path or not os.path.exists(path):
        return []
    name2addr = {name.lower(): addr for addr, name in names.items()}

    def resolve(ref):
        if isinstance(ref, dict):
            ref = next(iter(ref))
        if str(ref).lower() == "modem":
            return MODEM
        if isinstance(ref, str) and ref.lower() in name2addr:
            return name2addr[ref.lower()]
        return normalize_addr(ref)

    with open(path) as f:
        raw = yaml.safe_load(f) or []
    scenes = []
    for entry in raw:
        unresolved = []
        controllers = []
        for ctrl in entry.get("controllers") or []:
            if isinstance(ctrl, dict):
                ref, group = next(iter(ctrl.items()))
            else:
                ref, group = ctrl, 1
            addr = resolve(ref)
            if addr:
                controllers.append((addr, int(group)))
            else:
                unresolved.append(str(ref))
        responders = []
        for resp in entry.get("responders") or []:
            ref = next(iter(resp)) if isinstance(resp, dict) else resp
            addr = resolve(ref)
            if addr:
                responders.append(addr)
            else:
                unresolved.append(str(ref))
        scenes.append({"name": entry.get("name"),
                       "controllers": controllers,
                       "responders": responders,
                       "unresolved": unresolved})
    return scenes


#-----------------------------------------------------------------------
def model_of(data):
    """Return the catalog model string for a device db dict."""
    try:
        import insteon_mqtt.catalog as catalog
        entry = catalog.find(data.get("dev_cat"), data.get("sub_cat"))
        return entry.model if entry else "?"
    except Exception:                    # catalog not importable
        return "?"


#-----------------------------------------------------------------------
def desc_of(data):
    """Return the catalog description for a device db dict."""
    if not data:
        return None
    try:
        import insteon_mqtt.catalog as catalog
        entry = catalog.find(data.get("dev_cat"), data.get("sub_cat"))
        return entry.description if entry else None
    except Exception:
        return None


#-----------------------------------------------------------------------
def build(devices, modem):
    """Build records, directed edges, and per-edge responder data."""
    records = []
    for addr, data in devices.items():
        for e in data["used"]:
            records.append({
                "owner": addr, "remote": e["addr"], "group": e["group"],
                "is_controller": e["db_flags"]["is_controller"],
                "data": tuple(e["data"]), "mem_loc": e["mem_loc"],
            })
    for e in modem["entries"]:
        records.append({
            "owner": MODEM, "remote": e["addr"], "group": e["group"],
            "is_controller": e["is_controller"], "data": tuple(e["data"]),
            "mem_loc": None,
        })

    # Directed controller -> responder edges, de-duplicated.
    edges = {}
    for rec in records:
        ctrl, resp = ((rec["owner"], rec["remote"])
                      if rec["is_controller"]
                      else (rec["remote"], rec["owner"]))
        edges.setdefault((ctrl, resp, rec["group"]), None)

    # Attach the responder's link data (on level, ramp, local group).
    for key in edges:
        ctrl, resp, group = key
        edges[key] = find_resp_record(records, ctrl, resp, group)
    return records, edges


#-----------------------------------------------------------------------
def find_resp_record(records, ctrl, resp, group):
    """Return the responder-side record for a link, or None."""
    for rec in records:
        if (rec["owner"] == resp and rec["remote"] == ctrl and
                rec["group"] == group and not rec["is_controller"]):
            return rec
    return None


#-----------------------------------------------------------------------
def family_of(addr, model):
    """Visual family from the config device type, then the model code."""
    dev_type = TYPES.get(addr)
    if dev_type in TYPE_FAMILY:
        return TYPE_FAMILY[dev_type]
    return FAMILY.get(model, "other")


#-----------------------------------------------------------------------
def node_info(addr, devices, edges):
    """Collect display attributes for a device address."""
    if addr == MODEM:
        data = None
        model = "2413U"
    else:
        data = devices.get(addr)
        model = model_of(data) if data else "?"
    family = family_of(addr, model)
    return {
        "addr": addr,
        "model": model,
        "desc": desc_of(data),
        "name": NAMES.get(addr),
        "family": family,
        "ghost": data is None and addr != MODEM,
        "engine": data.get("engine") if data else None,
        "out": sum(1 for (c, r, g) in edges if c == addr),
        "in": sum(1 for (c, r, g) in edges if r == addr),
    }


#-----------------------------------------------------------------------
def packed_layout(graph, ncols=3):
    """Lay out each connected component separately and pack them in a grid.

    A force layout of the whole graph pushes unrelated clusters far apart.
    Packing the components keeps each star/pair readable.
    """
    comps = sorted(nx.connected_components(graph.to_undirected()),
                   key=len, reverse=True)
    pos = {}
    for index, comp in enumerate(comps):
        sub = graph.subgraph(comp)
        local = component_layout(sub)
        xs = [p[0] for p in local.values()]
        ys = [p[1] for p in local.values()]
        width = (max(xs) - min(xs)) or 1.0
        height = (max(ys) - min(ys)) or 1.0
        row, col = divmod(index, ncols)
        for node, (x, y) in local.items():
            pos[node] = (col + 0.2 + (x - min(xs)) / width * 0.6,
                         -row + 0.15 + (y - min(ys)) / height * 0.7)
    return pos


#-----------------------------------------------------------------------
def component_layout(graph):
    """Small deterministic layout: pairs side by side, stars with a hub."""
    nodes = list(graph.nodes)
    count = len(nodes)
    if count == 1:
        return {nodes[0]: (0.0, 0.0)}
    if count == 2:
        return {nodes[0]: (-0.32, 0.0), nodes[1]: (0.32, 0.0)}
    if count == 3:
        return {nodes[0]: (0.0, 0.38), nodes[1]: (-0.34, -0.22),
                nodes[2]: (0.34, -0.22)}
    hub = max(nodes, key=lambda n: graph.degree(n))
    leaves = [n for n in nodes if n != hub]
    pos = {hub: (0.0, 0.0)}
    for i, node in enumerate(leaves):
        angle = 2.0 * math.pi * i / len(leaves) + math.pi / 2.0
        pos[node] = (0.55 * math.cos(angle), 0.55 * math.sin(angle))
    return pos


#-----------------------------------------------------------------------
def draw_network(devices, edges, path):
    """Draw device-to-device links (modem and its pair links removed)."""
    pairs = [(c, r, g) for (c, r, g) in edges if c != MODEM and r != MODEM]
    graph = nx.DiGraph()
    for c, r, g in pairs:
        graph.add_edge(c, r, group=g)

    info = {n: node_info(n, devices, edges) for n in graph.nodes}
    pos = packed_layout(graph, ncols=3)
    n_modem_only = sum(1 for a in devices if
                       not any((c == a or r == a) and c != MODEM and
                               r != MODEM for (c, r, g) in edges))
    n_real = sum(1 for n in graph.nodes if not info[n]["ghost"])

    fig, ax = plt.subplots(figsize=(13, 9))
    ax.set_title("Device-to-device links: controller \u2192 responder "
                 "(%d devices, %d links; %d devices only link to the modem)"
                 % (n_real, graph.number_of_edges(), n_modem_only),
                 fontsize=12)
    ax.axis("off")

    for u, v in graph.edges:
        if graph.has_edge(v, u):
            rad = 0.22 if u < v else -0.22
        else:
            rad = 0.08
        arrow = FancyArrowPatch(
            pos[u], pos[v], arrowstyle="-|>", mutation_scale=11,
            linewidth=1.0, color="#555555", alpha=0.75,
            connectionstyle="arc3,rad=%s" % rad, shrinkA=10, shrinkB=10)
        ax.add_patch(arrow)

    for family in ["dimmer", "switch", "keypad", "remote", "sensor",
                   "fan", "other"]:
        nodes = [n for n in graph.nodes if info[n]["family"] == family]
        if not nodes:
            continue
        sizes = [140 + 45 * (info[n]["out"] + info[n]["in"]) for n in nodes]
        ax.scatter([pos[n][0] for n in nodes], [pos[n][1] for n in nodes],
                   s=sizes, c=COLOR[family], marker=MARKER[family],
                   edgecolors="black", linewidths=0.8, zorder=3,
                   label=family)

    ghosts = [n for n in graph.nodes if info[n]["ghost"]]
    if ghosts:
        ax.scatter([pos[n][0] for n in ghosts], [pos[n][1] for n in ghosts],
                   s=180, facecolors="none", edgecolors="red",
                   linestyles="dashed", linewidths=1.4, zorder=4,
                   label="no database")

    # Push labels away from each component's centre so star clusters don't
    # stack their text on top of each other.
    for comp in nx.connected_components(graph.to_undirected()):
        comp = list(comp)
        cx = sum(pos[n][0] for n in comp) / len(comp)
        cy = sum(pos[n][1] for n in comp) / len(comp)
        for n in comp:
            dx = pos[n][0] - cx
            dy = pos[n][1] - cy
            dist = math.hypot(dx, dy)
            if dist < 0.05:
                ox, oy, ha, va = 0, -10, "center", "top"
            else:
                ox, oy = 11.0 * dx / dist, 11.0 * dy / dist
                ha = "left" if dx > 0.15 else "right" if dx < -0.15 \
                    else "center"
                va = "center"
            name = info[n]["name"] or info[n]["desc"] or info[n]["model"]
            label = textwrap.fill(short_name(name), 16) + "\n" + n[3:]
            ax.annotate(label, pos[n], fontsize=5.6, ha=ha, va=va,
                        xytext=(ox, oy), textcoords="offset points",
                        zorder=5,
                        bbox=dict(boxstyle="round,pad=0.15",
                                  facecolor="white", edgecolor="none",
                                  alpha=0.75))

    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=7,
               fontsize=8, frameon=False)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path, dpi=200)
    plt.close(fig)


#-----------------------------------------------------------------------
def draw_scene_matrix(devices, edges, modem, path):
    """Draw devices x modem virtual scenes, colored by responder on level."""
    scenes = collections.defaultdict(dict)
    for (ctrl, resp, group), rec in edges.items():
        if ctrl == MODEM and group > 1 and rec:
            scenes[group][resp] = rec["data"][0]

    groups = sorted(scenes)
    # Only show devices that are members of at least one virtual scene.
    addrs = [a for a in sorted(devices)
             if any(a in scenes[g] for g in groups)]
    matrix = np.full((len(groups), len(addrs)), np.nan)
    for row, group in enumerate(groups):
        for col, addr in enumerate(addrs):
            if addr in scenes[group]:
                matrix[row, col] = scenes[group][addr]

    fig, ax = plt.subplots(figsize=(15, 5.5))
    cmap = plt.cm.viridis.copy()
    cmap.set_bad("#f2f2f2")
    im = ax.imshow(np.ma.masked_invalid(matrix), aspect="auto", cmap=cmap,
                   vmin=0, vmax=255)

    ax.set_yticks(range(len(groups)))
    ax.set_yticklabels(["M%d (%d)" % (g, len(scenes[g]))
                        for g in groups], fontsize=8)
    ax.set_xticks(range(len(addrs)))
    ax.set_xticklabels([a[3:] for a in addrs], rotation=90, fontsize=7)
    for label, addr in zip(ax.get_xticklabels(), addrs):
        info = node_info(addr, devices, edges)
        label.set_color(COLOR[info["family"]])

    # Annotate anything that isn't full-on so partial scene levels pop out.
    for row, group in enumerate(groups):
        for col, addr in enumerate(addrs):
            level = matrix[row, col]
            if np.isnan(level) or level == 255:
                continue
            text = "off" if level == 0 else "%d%%" % round(level / 2.55)
            ax.text(col, row, text, ha="center", va="center", fontsize=6,
                    color="white" if level < 140 else "black")

    ax.set_title("Modem virtual scenes: responder on-level "
                 "(255 = 100%%, blank = not a member; %d devices "
                 "not shown)" % (len(devices) - len(addrs)),
                 fontsize=12)
    ax.set_xlabel("device (label colored by model family)", fontsize=8)
    ax.set_ylabel("modem group", fontsize=8)
    fig.colorbar(im, ax=ax, pad=0.01, label="on level (0-255)")
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


#-----------------------------------------------------------------------
def main():
    devices, modem = load()
    records, edges = build(devices, modem)
    NAMES.update(load_names(CONFIG))
    SCENES.extend(load_scenes(SCENES_PATH, NAMES))
    os.makedirs(OUT_DIR, exist_ok=True)
    draw_network(devices, edges, os.path.join(OUT_DIR, "network.png"))
    draw_scene_matrix(devices, edges, modem,
                      os.path.join(OUT_DIR, "scene_matrix.png"))
    draw_modem_hub(devices, edges, modem,
                   os.path.join(OUT_DIR, "modem_hub.png"))
    draw_scene_focus(devices, edges, modem,
                     os.path.join(OUT_DIR, "scene_focus.png"))
    print("%d devices, %d records, %d edges"
          % (len(devices), len(records), len(edges)))



#-----------------------------------------------------------------------
def pairing_state(addr, devices, modem, one_way=False):
    """Classify how a device is attached to the modem.

    Returns (complete, partial, orphan, disconnected, device_side):
      complete      the required group 1 directions exist.  Actuators need
                    both directions; controller-only devices need only
                    device-controller -> modem-responder.
      partial       the modem has some record but the pairing is incomplete
      orphan        no modem records, but the device has records to the modem
      disconnected  no modem records and no device records to the modem
    """
    def dev(group, is_ctrl):
        return any(e["addr"] == MODEM and e["group"] == group and
                   e["db_flags"]["is_controller"] == is_ctrl
                   for e in devices[addr]["used"])
    m_ctrl = any(e["addr"] == addr and e["group"] == 1 and e["is_controller"]
                 for e in modem["entries"])
    m_resp = any(e["addr"] == addr and e["group"] == 1 and
                 not e["is_controller"] for e in modem["entries"])
    m_any = any(e["addr"] == addr for e in modem["entries"])
    d_ctrl = dev(1, True)
    d_resp = dev(1, False)
    if one_way:
        complete = d_ctrl and m_resp
    else:
        complete = m_ctrl and d_resp and d_ctrl and m_resp
    device_side = d_ctrl or d_resp
    partial = m_any and not complete
    orphan = not m_any and device_side
    disconnected = not m_any and not device_side
    return complete, partial, orphan, disconnected, device_side


#-----------------------------------------------------------------------
def draw_modem_hub(devices, edges, modem, path):
    """Hub view as an arc list: one readable row per device."""
    scenes = collections.Counter()
    buttons = collections.Counter()
    for (ctrl, resp, group) in edges:
        if ctrl == MODEM and group > 1:
            scenes[resp] += 1
        elif resp == MODEM and group > 1:
            buttons[ctrl] += 1

    states = {a: pairing_state(a, devices, modem, is_controller_only(a))
              for a in devices}
    ring = sorted((a for a in devices if not (states[a][2] or states[a][3])),
                  key=lambda a: ((NAMES.get(a) or a).lower(), a))
    outside = sorted((a for a in devices if states[a][2] or states[a][3]),
                     key=lambda a: ((NAMES.get(a) or a).lower(), a))

    row_h = 0.40
    y = {addr: -i * row_h for i, addr in enumerate(ring)}
    n_ring = len(ring)
    modem_y = -(n_ring - 1) * row_h / 2.0
    sep_y = -n_ring * row_h - 0.15
    for i, addr in enumerate(outside):
        y[addr] = sep_y - 0.55 - i * row_h
    last_y = y[outside[-1]] if outside else sep_y
    plm_y = last_y - 0.55

    fig_h = max(8, (n_ring + len(outside) + 6) * 0.24)
    fig, ax = plt.subplots(figsize=(13, fig_h))
    ax.set_title("Modem relationship map "
                 "(names from config.yaml; S=scenes, B=buttons)", fontsize=12)
    ax.axis("off")
    ax.set_xlim(-1.3, 11.0)
    ax.set_ylim(plm_y - 0.5, 0.8)

    for addr in ring:
        complete, partial, _, _, _ = states[addr]
        direction = 1.0 if y[addr] >= modem_y else -1.0
        rad = 0.05 * direction
        style = dict(color="#BBBBBB", lw=0.9, zorder=1)
        if partial:
            style = dict(color="#D62728", lw=1.1, ls=(0, (4, 3)), zorder=2)
        ax.add_patch(FancyArrowPatch(
            (0.0, modem_y), (3.2, y[addr]), arrowstyle="-",
            connectionstyle="arc3,rad=%.3f" % rad, **style))
        if scenes[addr]:
            ax.add_patch(FancyArrowPatch(
                (0.0, modem_y), (3.2, y[addr]), arrowstyle="-",
                connectionstyle="arc3,rad=%.3f" % (rad * 1.8),
                color=RESP_COLOR, lw=0.6 + 0.18 * scenes[addr],
                alpha=0.55, zorder=1))
        if buttons[addr]:
            ax.add_patch(FancyArrowPatch(
                (0.0, modem_y), (3.2, y[addr]), arrowstyle="-",
                connectionstyle="arc3,rad=%.3f" % (rad * 2.6),
                color="#9467BD", lw=0.8, ls=":", alpha=0.7, zorder=1))

        info = node_info(addr, devices, edges)
        bad = partial
        ax.scatter([3.2], [y[addr]], s=70, c=COLOR[info["family"]],
                   marker=MARKER[info["family"]],
                   edgecolors="#D62728" if bad else "black",
                   linewidths=1.4 if bad else 0.7, zorder=4)
        name = info["name"] or info["desc"] or info["model"]
        ax.text(3.45, y[addr], textwrap.shorten(name, 42), fontsize=7.5,
                va="center", zorder=5)
        ax.text(7.45, y[addr], addr, fontsize=6.5, color="#666666",
                va="center", zorder=5)
        badges = []
        if scenes[addr]:
            badges.append("S%d" % scenes[addr])
        if buttons[addr]:
            badges.append("B%d" % buttons[addr])
        ax.text(8.45, y[addr], " ".join(badges), fontsize=6.5,
                color="#333333", va="center", zorder=5)
        if partial:
            ax.text(9.55, y[addr], "partial", fontsize=6.5,
                    color="#D62728", va="center", zorder=5)

    ax.scatter([0.0], [modem_y], s=260, c=COLOR["modem"], marker="X",
               edgecolors="black", zorder=5)
    ax.text(-0.1, modem_y, "modem 70.1a.89", fontsize=8, ha="right",
            va="center")

    if outside:
        ax.plot([-1.0, 10.6], [sep_y, sep_y], color="#999999", lw=0.8,
                ls="--")
        ax.text(-1.0, sep_y + 0.12, "not connected to the modem",
                fontsize=8, color="#D62728")
        for addr in outside:
            complete, partial, orphan, disconnected, _ = states[addr]
            info = node_info(addr, devices, edges)
            ax.scatter([3.2], [y[addr]], s=70, c=COLOR[info["family"]],
                       marker=MARKER[info["family"]], edgecolors="#D62728",
                       linewidths=1.4, zorder=4)
            name = info["name"] or info["desc"] or info["model"]
            ax.text(3.45, y[addr], textwrap.shorten(name, 42), fontsize=7.5,
                    va="center")
            ax.text(7.45, y[addr], addr, fontsize=6.5, color="#666666",
                    va="center")
            label = "device side only" if orphan else "no records"
            ax.text(9.55, y[addr], label, fontsize=6.5, color="#D62728",
                    va="center")

    if any(b == "3d.98.c5" for (c, r, g) in edges for b in (c, r)):
        ax.scatter([3.2], [plm_y], s=90, facecolors="none",
                   edgecolors="red", linewidths=1.4)
        ax.text(3.45, plm_y, "Old PLM 3d.98.c5", fontsize=7.5, va="center",
                color="#D62728")
        ax.text(7.45, plm_y, "no database", fontsize=6.5, color="#D62728",
                va="center")
        ax.text(8.45, plm_y, "FanLinc still links here", fontsize=6.5,
                color="#D62728", va="center")

    header_y = 0.5
    for x, text in [(3.45, "device"), (7.45, "address"),
                    (8.45, "scenes / buttons"), (9.55, "pairing")]:
        ax.text(x, header_y, text, fontsize=8, weight="bold")

    handles = [
        Patch(facecolor="#BBBBBB", label="complete pairing"),
        Patch(facecolor="#D62728", label="partial pairing"),
        Patch(facecolor=RESP_COLOR, label="virtual scene member (S)"),
        Patch(facecolor="#9467BD", label="button link to modem (B)"),
    ]
    ax.legend(handles=handles, loc="upper left", fontsize=8,
              frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


#-----------------------------------------------------------------------
def draw_scene_focus(devices, edges, modem, path):
    """Mock up the selected-scene state for a named and a 3-way scene."""
    named = next((s for s in SCENES if s["name"] == "First Floor from Mudroom"),
                 None)
    multi = max(SCENES, key=lambda s: len(s["controllers"]), default=None)

    fig, axes = plt.subplots(1, 2, figsize=(15, 7.5))

    # ---- panel 1: a named modem scene --------------------------------
    ax = axes[0]
    if named:
        ctrl, group = named["controllers"][0]
        actuators = [a for a in named["responders"] if a != MODEM]
        ax.set_title("%s (M%d)" % (named["name"], group), fontsize=11,
                     pad=12)
        ax.text(0.5, 0.99, "modem scene \u00b7 %d responders"
                % len(actuators), transform=ax.transAxes, ha="center",
                va="top", fontsize=8, color="#555555")
        ax.scatter([0.0], [0.0], s=460, c=COLOR["modem"], marker="X",
                   edgecolors=CTRL_COLOR, linewidths=3.0, zorder=5)
        ax.annotate("controller\nmodem 70.1a.89", (0.0, 0.0), fontsize=7,
                    ha="center", va="top", xytext=(0, -16),
                    textcoords="offset points", zorder=6,
                    bbox=dict(boxstyle="round,pad=0.2", facecolor="white",
                              edgecolor="none", alpha=0.8))
        count = len(actuators)
        for i, addr in enumerate(actuators):
            angle = math.radians(90 - i * 360.0 / count)
            x, y = 1.55 * math.cos(angle), 1.55 * math.sin(angle)
            ax.add_patch(FancyArrowPatch(
                (0.0, 0.0), (x, y), arrowstyle="-|>", mutation_scale=14,
                color=RESP_COLOR, lw=1.7, shrinkA=14, shrinkB=13, zorder=2))
            info = node_info(addr, devices, edges)
            ax.scatter([x], [y], s=230, c=COLOR[info["family"]],
                       marker=MARKER[info["family"]], edgecolors=RESP_COLOR,
                       linewidths=2.4, zorder=4)
            label = _scene_label(info)
            ha = "left" if x > 0.2 else "right" if x < -0.2 else "center"
            ox = 14.0 * math.cos(angle)
            oy = 14.0 * math.sin(angle)
            va = "bottom" if oy > 3 else "top" if oy < -3 else "center"
            ax.annotate(label, (x, y), fontsize=7, ha=ha, va=va,
                        xytext=(ox, oy), textcoords="offset points",
                        zorder=6,
                        bbox=dict(boxstyle="round,pad=0.2",
                                  facecolor="white", edgecolor="none",
                                  alpha=0.8))
        ax.set_xlim(-2.7, 2.7)
        ax.set_ylim(-2.4, 2.4)
        ax.set_aspect("equal")
        ax.axis("off")

    # ---- panel 2: a multi-controller (3-way) scene --------------------
    ax = axes[1]
    if multi and len(multi["controllers"]) > 1:
        ctrl_set = {addr for addr, _ in multi["controllers"]}
        resp_set = set(multi["responders"])
        nodes = sorted(ctrl_set | resp_set)
        if len(nodes) == 3:
            pos = {nodes[0]: (0.0, 1.0), nodes[1]: (-0.95, -0.65),
                   nodes[2]: (0.95, -0.65)}
        else:
            pos = {}
            for i, addr in enumerate(nodes):
                angle = 2 * math.pi * i / len(nodes) + math.pi / 2
                pos[addr] = (1.15 * math.cos(angle),
                             1.15 * math.sin(angle))

        for ctrl_addr in ctrl_set:
            for resp_addr in resp_set:
                if ctrl_addr == resp_addr:
                    continue
                rad = 0.09 if ctrl_addr < resp_addr else -0.09
                ax.add_patch(FancyArrowPatch(
                    pos[ctrl_addr], pos[resp_addr], arrowstyle="-|>",
                    mutation_scale=12, color=RESP_COLOR, lw=1.4,
                    shrinkA=13, shrinkB=13, zorder=2,
                    connectionstyle="arc3,rad=%.3f" % rad))

        names = [NAMES.get(a) or a for a, _ in multi["controllers"]]
        bases = {n.split(" (")[0].replace(" Light Switch", "").strip()
                 for n in names}
        scene_title = ("%s 3-way scene" % bases.pop()) if len(bases) == 1 \
            else "Multi-controller scene"
        ax.set_title(scene_title + " (unnamed)", fontsize=11, pad=12)
        ax.text(0.5, 0.99, "%d controllers \u00b7 %d responders"
                % (len(ctrl_set), len(resp_set)), transform=ax.transAxes,
                ha="center", va="top", fontsize=8, color="#555555")

        cx = sum(p[0] for p in pos.values()) / len(pos)
        cy = sum(p[1] for p in pos.values()) / len(pos)
        for addr in nodes:
            info = node_info(addr, devices, edges)
            x, y = pos[addr]
            ax.scatter([x], [y], s=210, c=COLOR[info["family"]],
                       marker=MARKER[info["family"]],
                       edgecolors=RESP_COLOR if addr in resp_set else "black",
                       linewidths=2.2, zorder=4)
            if addr in ctrl_set:
                ax.scatter([x], [y], s=380, facecolors="none",
                           edgecolors=CTRL_COLOR, linewidths=2.6, zorder=5)
            dx, dy = x - cx, y - cy
            dist = math.hypot(dx, dy) or 1.0
            ha = "left" if dx > 0.15 else "right" if dx < -0.15 else "center"
            va = "bottom" if dy > 0.15 else "top" if dy < -0.15 else "center"
            ax.annotate(_scene_label(info), (x, y), fontsize=7, ha=ha,
                        va=va, xytext=(13 * dx / dist, 13 * dy / dist),
                        textcoords="offset points", zorder=6,
                        bbox=dict(boxstyle="round,pad=0.2",
                                  facecolor="white", edgecolor="none",
                                  alpha=0.8))
        ax.set_xlim(-2.7, 2.7)
        ax.set_ylim(-2.4, 2.4)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.text(0.5, 0.02, "every device is both controller and responder",
                transform=ax.transAxes, ha="center", fontsize=7,
                color="#777777")

    handles = [
        Patch(facecolor=CTRL_COLOR, label="controller"),
        Patch(facecolor=RESP_COLOR, label="responder (actuator)"),
        Patch(facecolor="#888888", label="responder (modem listener)"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=9,
               frameon=False)
    fig.suptitle("Clicking a scene shows every device that participates",
                 fontsize=13)
    fig.tight_layout(rect=(0, 0.05, 1, 0.95))
    fig.savefig(path, dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    main()
