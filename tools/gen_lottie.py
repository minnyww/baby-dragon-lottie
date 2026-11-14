#!/usr/bin/env python3
"""Convert the Figma-exported dragon SVG into rigged Lottie JSONs.

The SVG comes from `download_assets` on the whole "Generated illustration"
frame: absolute coordinates, no transforms, only M/L/C/Z path commands and
linear gradients. Each logical part (wings, tail, head, eyes, sparkles, ...)
becomes one Lottie layer so it can be animated around a pivot.
"""
import json
import math
import re
import sys
import xml.etree.ElementTree as ET

SVG_NS = {"svg": "http://www.w3.org/2000/svg"}
NAMED_COLORS = {"white": "#FFFFFF", "black": "#000000"}

FPS = 60
DUR = 180  # frames (3s loop)
W, H = 1024, 1024


def hex_to_rgb01(c):
    """Colours for lottie files declaring version >= 5: 0-1 floats (lottie-web scales by 255)."""
    c = c.strip()
    if c.startswith("#"):
        c = c[1:]
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        return [int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    if c.lower() in NAMED_COLORS:
        return hex_to_rgb01(NAMED_COLORS[c.lower()])
    raise ValueError(f"unsupported colour {c!r}")


NUM = re.compile(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?")


def parse_path(d):
    """Parse an SVG path (M/L/C/Z absolute) into subpaths of bezier segments.

    Returns [(closed, [(p0, c1, c2, p3), ...]), ...] with tuples of (x, y).
    """
    tokens = re.findall(r"([MLCZ])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)", d)
    vals = []
    for cmd, num in tokens:
        if cmd:
            vals.append((cmd, []))
        else:
            vals[-1][1].append(float(num))

    subs, segs = [], []
    cur = sub_start = None
    closed = False
    for cmd, v in vals:
        if cmd == "M":
            if segs:
                subs.append((closed, segs))
                segs, closed = [], False
            cur = sub_start = (v[0], v[1])
        elif cmd == "L":
            segs.append((cur, cur, (v[0], v[1]), (v[0], v[1])))
            cur = (v[0], v[1])
        elif cmd == "C":
            segs.append((cur, (v[0], v[1]), (v[2], v[3]), (v[4], v[5])))
            cur = (v[4], v[5])
        elif cmd == "Z":
            if segs:
                segs.append((cur, cur, sub_start, sub_start))
                cur = sub_start
            closed = True
            subs.append((closed, segs))
            segs, closed = [], False
    if segs:
        subs.append((closed, segs))
    return [(c, s) for c, s in subs if s]


def segments_to_shape(segs, closed):
    """Turn a subpath's segments into one Lottie 'sh' shape (bezier vertices)."""
    verts = [segs[0][0]] + [s[3] for s in segs]
    n = len(verts)
    out_t = [[0.0, 0.0]] * n
    in_t = [[0.0, 0.0]] * n
    for j, seg in enumerate(segs):
        p0, c1, c2, p3 = seg
        out_t[j] = [c1[0] - p0[0], c1[1] - p0[1]]
        in_t[j + 1] = [c2[0] - p3[0], c2[1] - p3[1]]
    return {
        "ty": "sh",
        "ind": 0,
        "ks": {
            "a": 0,
            "k": {
                "c": closed,
                "v": [[x, y] for x, y in verts],
                "i": in_t,
                "o": out_t,
            },
        },
    }


def bbox_of(subs):
    xs, ys = [], []
    for _, segs in subs:
        for p0, c1, c2, p3 in segs:
            for p in (p0, c1, c2, p3):
                xs.append(p[0])
                ys.append(p[1])
    return min(xs), min(ys), max(xs), max(ys)


def val(node, name, default=None):
    if node.get(name) is not None:
        return node.get(name)
    return default


class SvgImporter:
    def __init__(self, path):
        self.tree = ET.parse(path)
        self.root = self.tree.getroot()
        self.gradients = {}
        for g in self.root.iter():
            tag = g.tag.split("}")[-1]
            if tag in ("linearGradient", "radialGradient"):
                self.gradients[g.get("id")] = g

    def group_by_id(self, gid):
        """Find a <g> whose id == gid (search anywhere)."""
        for el in self.root.iter():
            tag = el.tag.split("}")[-1]
            if tag == "g" and el.get("id") == gid:
                return el
        return None

    def paint(self, node, bbox):
        """Return Lottie paint items (fill/stroke) for an SVG path node."""
        items = []
        fill = val(node, "fill", "none")
        fo = float(val(node, "fill-opacity", "1"))
        if fill and fill != "none":
            o = round(fo * 100)
            if fill.startswith("url("):
                gid = re.search(r"url\(#([^)]+)\)", fill).group(1)
                items.append(self.gradient_fill(gid, bbox, o))
            else:
                items.append({
                    "ty": "fl", "r": 2, "bm": 0,
                    "c": {"a": 0, "k": hex_to_rgb01(fill) + [1]},
                    "o": {"a": 0, "k": o},
                })
        stroke = val(node, "stroke", "none")
        so = float(val(node, "stroke-opacity", "1"))
        if stroke and stroke != "none":
            if stroke.startswith("url("):
                gid = re.search(r"url\(#([^)]+)\)", stroke).group(1)
                rgb = self.gradient_mid(gid)
            else:
                rgb = hex_to_rgb01(stroke)
            width = float(val(node, "stroke-width", "1"))
            cap = {"butt": 1, "round": 2, "square": 3}.get(
                val(node, "stroke-linecap", "butt"), 1)
            join = {"miter": 0, "round": 2, "bevel": 3}.get(
                val(node, "stroke-linejoin", "miter"), 0)
            items.append({
                "ty": "st", "bm": 0, "lc": cap, "lj": join, "ml": 4,
                "c": {"a": 0, "k": rgb + [1]},
                "o": {"a": 0, "k": round(so * 100)},
                "w": {"a": 0, "k": width},
            })
        return items

    def gradient_mid(self, gid):
        g = self.gradients[gid]
        stops = [s for s in g if s.tag.split("}")[-1] == "stop"]
        mid = stops[len(stops) // 2]
        return hex_to_rgb01(val(mid, "stop-color", "#000000"))

    def gradient_fill(self, gid, bbox, opacity):
        g = self.gradients[gid]
        stops = [s for s in g if s.tag.split("}")[-1] == "stop"]
        x0, y0, x1, y1 = bbox
        w, h = x1 - x0, y1 - y0
        user_space = (g.get("gradientUnits") or "objectBoundingBox") == "userSpaceOnUse"

        def pt(fx, fy):
            if user_space:
                return [fx, fy]
            return [x0 + fx * w, y0 + fy * h]

        if g.tag.split("}")[-1] == "linearGradient":
            s_pt = pt(float(g.get("x1", 0)), float(g.get("y1", 0)))
            e_pt = pt(float(g.get("x2", 1)), float(g.get("y2", 0)))
            gtype = 1
        else:
            cx, cy = pt(float(g.get("cx", 0.5)), float(g.get("cy", 0.5)))
            r = float(g.get("r", 0.5))
            s_pt = [cx, cy]
            e_pt = [cx + (r if user_space else r * w), cy]
            gtype = 2

        colors, alphas = [], []
        for s in stops:
            pos = float(val(s, "offset", "0"))
            rgb = hex_to_rgb01(val(s, "stop-color", "#000000"))
            a = float(val(s, "stop-opacity", "1"))
            colors.extend([pos] + rgb)
            alphas.extend([pos, a])
        n = len(stops)
        return {
            "ty": "gf", "bm": 0, "t": gtype, "r": 2,
            "s": {"a": 0, "k": s_pt},
            "e": {"a": 0, "k": e_pt},
            "g": {"p": n, "k": {"a": 0, "k": colors + alphas}},
            "o": {"a": 0, "k": opacity},
        }

    def collect_paths(self, node, skip_groups=()):
        """Recursively collect (svg path element) under node, skipping groups."""
        out = []
        for child in node:
            tag = child.tag.split("}")[-1]
            if tag == "g":
                if child.get("id") in skip_groups:
                    continue
                out.extend(self.collect_paths(child, skip_groups))
            elif tag == "path":
                out.append(child)
        return out

    def shapes_for_paths(self, paths):
        """All paths of a part -> Lottie shape items.

        SVG paints in document order (last = on top) but Lottie shape items
        render first = on top, so the group list must be reversed.
        """
        items = []
        for idx, p in enumerate(paths):
            d = p.get("d")
            if not d:
                continue
            subs = parse_path(d)
            if not subs:
                continue
            bbox = bbox_of(subs)
            paints = self.paint(p, bbox)
            if not paints:
                continue
            shapes = []
            for k_i, (closed, segs) in enumerate(subs):
                sh = segments_to_shape(segs, closed)
                sh["ind"] = k_i
                shapes.append(sh)
            for p_i, paint_item in enumerate(paints):
                paint_item = dict(paint_item)
                if paint_item["ty"] in ("fl", "gf", "st"):
                    paint_item["nm"] = f"{p.get('id', 'p')}_{p_i}"
                group = {
                    "ty": "gr", "nm": f"p{idx}",
                    "it": shapes + [paint_item] + [{"ty": "tr", "p": {"a": 0, "k": [0, 0]},
                                                    "a": {"a": 0, "k": [0, 0]},
                                                    "s": {"a": 0, "k": [100, 100]},
                                                    "r": {"a": 0, "k": 0},
                                                    "o": {"a": 0, "k": 100}}],
                }
                items.append(group)
        return list(reversed(items))


def kf(frames, dim=1):
    """frames: [(t, value, ease?)] -> animated Lottie property.

    Easing arrays are sized to the value dimension (bodymovin requirement).
    """
    keys = []
    for i, (t, v, *rest) in enumerate(frames):
        if dim > 1 and not isinstance(v, (list, tuple)):
            v = [v] * dim
        key = {"t": t, "s": v if dim > 1 else [v]}
        if i < len(frames) - 1:
            ease = rest[0] if rest else "io"
            ox, oy = {"io": (0.42, 0.0), "lin": (0.5, 0.5)}.get(ease, (0.42, 0.0))
            key["o"] = {"x": [ox] * dim, "y": [oy] * dim}
            key["i"] = {"x": [0.58] * dim, "y": [1.0] * dim}
        keys.append(key)
    return {"a": 1, "k": keys}


def static(v):
    return {"a": 0, "k": v}


def make_layer(ind, name, shapes, anchor=None, pivot=None, parent=None):
    """Shape layer in absolute coords; if pivot given, layer rotates about it."""
    if pivot is None:
        ks_p, ks_a = static([0, 0, 0]), static([0, 0, 0])
    else:
        ks_p, ks_a = static(list(pivot) + [0]), static(list(anchor or pivot) + [0])
    layer = {
        "ddd": 0, "ind": ind, "ty": 4, "nm": name, "sr": 1,
        "ks": {
            "o": static(100),
            "r": static(0),
            "p": ks_p,
            "a": ks_a,
            "s": static([100, 100, 100]),
        },
        "ao": 0,
        "shapes": shapes,
        "ip": 0, "op": DUR, "st": 0, "bm": 0,
    }
    if parent is not None:
        layer["parent"] = parent
    return layer


def ease_frames(peaks, base, peak, span, lo=0, hi=DUR):
    """Build opacity keyframes: base everywhere, peak at each peak t (±span)."""
    frames = [(lo, base)]
    for pk in peaks:
        a, b = max(lo, pk - span), min(hi, pk + span)
        if a > lo:
            frames.append((a, base))
        frames.append((pk, peak))
        if b < hi:
            frames.append((b, base))
    frames.append((hi, base))
    seen, out = set(), []
    for t, v in frames:
        if t in seen:
            out[-1] = (t, v)
        else:
            seen.add(t)
            out.append((t, v))
    return out


def build_dragon(svg_path, variant="idle"):
    imp = SvgImporter(svg_path)
    root_g = imp.group_by_id("Generated illustration")
    head_g = imp.group_by_id("Head")
    spark_g = imp.group_by_id("Sparkles")

    paths = {
        "shadow": imp.collect_paths(imp.group_by_id("Background"))[1:2],
        "wing_left": imp.collect_paths(imp.group_by_id("Wing Left")),
        "wing_right": imp.collect_paths(imp.group_by_id("Wing Right")),
        "tail": imp.collect_paths(imp.group_by_id("Tail")),
        "body": imp.collect_paths(imp.group_by_id("Body")),
        "head": imp.collect_paths(head_g, skip_groups=("Eye-Left", "Eye-Right")),
        "eye_left": imp.collect_paths(imp.group_by_id("Eye-Left")),
        "eye_right": imp.collect_paths(imp.group_by_id("Eye-Right")),
    }
    sparkle_paths = imp.collect_paths(spark_g)

    layers = []
    ind = 1

    def add(name, part, pivot=None, anchor=None, extra_ks=None):
        nonlocal ind
        shapes = imp.shapes_for_paths(paths[part])
        layer = make_layer(ind, name, shapes, pivot=pivot, anchor=anchor)
        if extra_ks:
            for k_, v_ in extra_ks.items():
                layer["ks"][k_] = v_
        layers.append(layer)
        ind += 1
        return ind - 1

    def add_sparkle(p_el):
        nonlocal ind
        shapes = imp.shapes_for_paths([p_el])
        bb = bbox_of(parse_path(p_el.get("d")))
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        layer = make_layer(ind, "Sparkle", shapes, pivot=(cx, cy))
        layers.append(layer)
        ind += 1
        return ind - 1

    # --- bob root (null) ---
    bob_ind = ind
    bob = {
        "ddd": 0, "ind": ind, "ty": 3, "nm": "Bob", "sr": 1,
        "ks": {
            "o": static(100), "r": static(0),
            "p": static([0, 0, 0]), "a": static([0, 0, 0]),
            "s": static([100, 100, 100]),
        },
        "ao": 0, "ip": 0, "op": DUR, "st": 0, "bm": 0,
    }
    layers.append(bob)
    ind += 1

    pivots = {
        "wing_left": (398, 560),
        "wing_right": (626, 560),
        "tail": (645, 690),
        "head": (512, 600),
        "eye_left": (406, 436),
        "eye_right": (619, 435),
        "body": (512, 720),
        "shadow": (512, 867),
    }

    ids = {}
    # top-to-bottom render order (Lottie: first layer = top)
    for i, p in enumerate(sparkle_paths):
        ids[f"sp{i}"] = add_sparkle(p)
    ids["eye_l"] = add("Eye Left", "eye_left", pivot=pivots["eye_left"])
    ids["eye_r"] = add("Eye Right", "eye_right", pivot=pivots["eye_right"])
    ids["head"] = add("Head", "head", pivot=pivots["head"])
    ids["body"] = add("Body", "body", pivot=pivots["body"])
    ids["tail"] = add("Tail", "tail", pivot=pivots["tail"])
    ids["wing_r"] = add("Wing Right", "wing_right", pivot=pivots["wing_right"])
    ids["wing_l"] = add("Wing Left", "wing_left", pivot=pivots["wing_left"])
    ids["shadow"] = add("Shadow", "shadow", pivot=pivots["shadow"])

    for layer in layers:
        if layer["ind"] != bob_ind:
            layer["parent"] = bob_ind

    # ================= animation =================
    def L(key):
        return next(l for l in layers if l["ind"] == ids[key])

    blink_frames = [(80, 100), (86, 5), (92, 100), (150, 100), (156, 5), (162, 100)]
    for key in ("eye_l", "eye_r"):
        L(key)["ks"]["s"] = kf([(t, [100, v, 100]) for t, v in blink_frames], dim=3)

    if variant == "idle":
        bob["ks"]["p"] = kf([(0, [0, 0, 0]), (90, [0, -12, 0]), (180, [0, 0, 0])], dim=3)
        L("body")["ks"]["s"] = kf(
            [(0, [100, 100, 100]), (90, [99.2, 101.6, 100]), (180, [100, 100, 100])], dim=3)
        L("head")["ks"]["r"] = kf(
            [(0, 0), (60, 1.6), (110, 0), (150, -1.2), (180, 0)])
        L("head")["ks"]["p"] = kf(
            [(0, list(pivots["head"]) + [0]), (90, [512, 595, 0]), (180, list(pivots["head"]) + [0])], dim=3)
        L("wing_l")["ks"]["r"] = kf(
            [(0, 0), (45, -7), (90, 0), (135, -5), (180, 0)])
        L("wing_r")["ks"]["r"] = kf(
            [(0, 0), (45, 7), (90, 0), (135, 5), (180, 0)])
        L("tail")["ks"]["r"] = kf(
            [(0, 0), (55, -4), (100, 0), (145, 4), (180, 0)])
        L("shadow")["ks"]["s"] = kf(
            [(0, [100, 100, 100]), (90, [104, 100, 100]), (180, [100, 100, 100])], dim=3)
        peaks = [0, 30, 60, 90, 120, 150]
        for i in range(6):
            L(f"sp{i}")["ks"]["o"] = kf(ease_frames([peaks[i]], 25, 100, 28))
            L(f"sp{i}")["ks"]["s"] = kf(
                ease_frames([peaks[i]], 85, 115, 22), dim=3)

    elif variant == "happy":
        bob["ks"]["p"] = kf([(0, [0, 0, 0]), (14, [0, -66, 0]), (30, [0, 0, 0]),
                             (44, [0, 0, 0]), (58, [0, -66, 0]), (74, [0, 0, 0]),
                             (180, [0, 0, 0])], dim=3)
        bob["ks"]["s"] = kf([(0, [100, 94, 100]), (10, [102, 108, 100]), (26, [104, 92, 100]),
                             (34, [100, 100, 100]), (44, [100, 94, 100]), (54, [102, 108, 100]),
                             (70, [104, 92, 100]), (78, [100, 100, 100]), (180, [100, 100, 100])], dim=3)
        L("wing_l")["ks"]["r"] = kf(
            [(0, 0), (10, -16), (22, 6), (34, 0), (44, 0), (54, -16), (66, 6), (78, 0), (180, 0)])
        L("wing_r")["ks"]["r"] = kf(
            [(0, 0), (10, 16), (22, -6), (34, 0), (44, 0), (54, 16), (66, -6), (78, 0), (180, 0)])
        L("tail")["ks"]["r"] = kf(
            [(0, 0), (15, -8), (30, 0), (45, 8), (60, 0), (75, -8), (90, 0), (180, 0)])
        peaks = [0, 36, 72, 108, 144, 172]
        for i in range(6):
            L(f"sp{i}")["ks"]["o"] = kf(ease_frames([peaks[i]], 40, 100, 22))
            L(f"sp{i}")["ks"]["s"] = kf(
                ease_frames([peaks[i]], 90, 125, 18), dim=3)

    elif variant == "celebrate":
        bob["ks"]["p"] = kf([(0, [0, 0, 0]), (5, [0, 8, 0]), (20, [0, -150, 0]),
                             (38, [0, 0, 0]), (43, [0, 8, 0]), (58, [0, -150, 0]),
                             (76, [0, 0, 0]), (180, [0, 0, 0])], dim=3)
        bob["ks"]["s"] = kf([(0, [100, 96, 100]), (5, [104, 88, 100]), (20, [96, 110, 100]),
                             (38, [106, 90, 100]), (43, [104, 88, 100]), (58, [96, 110, 100]),
                             (76, [106, 90, 100]), (84, [100, 100, 100]), (180, [100, 100, 100])], dim=3)
        flaps = [(t, (-20 if k % 2 == 0 else 14)) for k, t in enumerate(range(0, 90, 9))]
        flaps += [(90, flaps[-1][1] * -1), (180, 0)]
        flaps.sort(key=lambda f: f[0])
        L("wing_l")["ks"]["r"] = kf(flaps)
        flaps_r = [(t, (20 if k % 2 == 0 else -14)) for k, t in enumerate(range(0, 90, 9))]
        flaps_r += [(90, flaps_r[-1][1] * -1), (180, 0)]
        flaps_r.sort(key=lambda f: f[0])
        L("wing_r")["ks"]["r"] = kf(flaps_r)
        L("tail")["ks"]["r"] = kf(
            [(0, 0), (12, -12), (24, 0), (36, 12), (48, 0), (60, -12), (72, 0), (90, 0), (180, 0)])
        for i in range(6):
            off = i * 12
            peaks = [(45 + off) % 180, (135 + off) % 180]
            L(f"sp{i}")["ks"]["o"] = kf(ease_frames(sorted(set(peaks)), 35, 100, 20))
            L(f"sp{i}")["ks"]["s"] = kf(
                ease_frames(sorted(set(peaks)), 90, 130, 16), dim=3)

    return {
        "v": "5.9.0", "fr": FPS, "ip": 0, "op": DUR, "w": W, "h": H,
        "nm": f"BabyDragon_{variant}", "ddd": 0, "assets": [], "layers": layers,
    }


if __name__ == "__main__":
    svg = sys.argv[1] if len(sys.argv) > 1 else "svg/dragon_full.svg"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "lottie"
    import os
    os.makedirs(outdir, exist_ok=True)
    for variant in ("idle", "happy", "celebrate"):
        data = build_dragon(svg, variant)
        path = os.path.join(outdir, f"dragon_{variant}.json")
        with open(path, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        print(f"{path}: {os.path.getsize(path)} bytes, {len(data['layers'])} layers")
