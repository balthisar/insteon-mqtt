# Link visualizer

A data-driven, split-view viewer for the insteon-mqtt all-link databases.
Nothing in it is tied to a specific installation: the modem is the database
file with an `entries` list, names/types come from `config.yaml`, and scene
names/controllers/responders come from `scenes.yaml`.

## Build

```
python viz/build_view.py
open viz/viewer.html
```

Re-run the build after refreshing device databases, editing `config.yaml`,
or editing `scenes.yaml`.  `viewer.html` is self-contained (data is
embedded), so it also works from `file://`.

Environment overrides:

| Variable | Default | Meaning |
|---|---|---|
| `DATA` | `./data` | directory of `<address>.json` link databases |
| `CONFIG` | `./config.yaml` | device names and types |
| `SCENES` | `./scenes.yaml` | scene definitions |
| `VIEW` | `viz/viewer.html` | output file |

Dependencies: `pyyaml` plus what `link_graph.py` imports
(`matplotlib`, `networkx`) — the project's requirements plus those two.

## Views

- **Left**: scene list, grouped into modem scenes, device scenes and
  unmanaged scenes (present in the databases but not in `scenes.yaml`).
- **Center**: the modem at the hub, connected devices on a ring, pairing
  edges, device-to-device chords, and an orphan/phantom tray.
- **Right**: details. A scene shows its controllers and responders with
  `C`/`R` badges (a 3-way device gets both) and any link validation issues.
  A device shows its scenes, what it controls and what controls it.

Clicking a scene highlights every participant and draws the scene links;
clicking a device focuses its neighbourhood.  The view **fits the selection**
by default: choosing the Stairway 3-way zooms to its three switches.

Navigation: wheel or pinch zooms around the cursor, drag pans (including
starting on a node), the bottom-right `+` / `-` / `Fit` buttons zoom and
reset, and `+`, `-`, `0` work as shortcuts.  `Fit` returns to the whole map
without clearing the selection; clicking the background clears it.

Device details start with **Links to** and **Linked from**, which include
modem scene and button groups as well as device-to-device links, then the
scene list.  Selecting any device fits it, even when its only links are
modem scenes.

Battery controller-only devices (the mini-remotes) pair **one-way**:
device-controller + modem-responder is complete; the modem -> device
direction is not required.  A responder record on one of these devices is
illogical (there is no load) and is flagged as an issue on the device and
in any scene that touches that group.  Search filters the scene
list; the toggles hide pairing edges, device links or orphans.  The URL
hash (`#scene=...`, `#device=...`) is shareable.

The **Key** button in the header (or the "full key" link in the legend)
opens a key for every visual encoding: family colours, pairing lines
(solid/dashed), device-to-device curves, scene arrows, controller and
responder rings, the dual-role ring, focus/dim states, tray and phantom
markers, and the scene-list dots.

## Data checks

The builder compares `scenes.yaml` with the actual databases and reports:

- responders declared in a scene that have no responder record on the
  device (the modem side may exist — a one-sided link),
- responders linked in a database but not declared in the scene,
- addresses referenced without a database file (phantoms, e.g. an old PLM).

These appear as coloured dots in the scene list (red = error, amber =
warning, grey = none) and in the details panel.  Each message names the
device and its address (`Name [aa.bb.cc]`), because names can be reused;
the address is the identity.

Each issue expands into **Why / Check / Fix**.  Commands are copyable and
carry a risk badge (`read`, `write`, `destructive`); destructive fixes
(`sync --run`, `db-delete`) should always be preceded by their dry run.
Devices that have a database but no `config.yaml` entry are flagged
"not in config.yaml" in their details and in any scene that links them.

## Prototypes

`link_graph.py` also renders exploratory PNGs (`network.png`,
`modem_hub.png`, `scene_focus.png`, `scene_matrix.png`).  Those contain
demo-specific panels; the viewer is the maintained, general path.
