# -*- coding: utf-8 -*-
"""川端橋（中正橋）橋體：1937 原橋與 1961、1972、2026 三次變動。

座標：x 沿橋軸，0＝1937 台北端（北岸）橋台、往永和為正；y 橫橋，正＝上游；z 向上，0＝常水位；單位公尺。
數字的依據見 REFERENCES.md。有文獻數字的只有全長 300.56 m、14 孔 13 墩、寬 5.2／15.4／24.5 m、2026 全寬 6 m；
其餘（墩高、梁深、加寬斷面、帽梁抬升量、永和端延長段的柱位）都是照片判讀或示意。
"""
import math

import bmesh
from mathutils import Vector

import lib

L37 = 300.56                    # 1937 全長（REFERENCES 尺寸表）
NSPAN = 14                      # 14 孔、13 座墩
L = L37 / NSPAN                 # 模型等分跨距（實際逐孔略有差異，見 REFERENCES）
H_PIER = 7.2                    # 墩高（照片判讀）
GIRDER_D = 1.7                  # 鋼鈑梁梁深（照片判讀）
GIRDER_Y = 1.75                 # 兩道主梁的橫向位置
W37 = 5.2                       # 1937 橋寬
RAISE = 1.3                     # 2026 帽梁增築抬升量：官方未公布，示意
DECK_Z = H_PIER + GIRDER_D + 0.1
PIER_W, PIER_T, OPEN_W, SPRING = 6.4, 1.6, 2.9, 4.1   # 墩寬、墩厚、拱形開口寬、起拱高
X0, X1 = 13.4, 290.6            # 2026 保存段起訖（兩端改建，示意）

# 1962–63 永和端延長段中線（依 1967／1968 航照判讀，接到永和堤防）
EXT_PATH = [(300.47, 0.02), (329.86, -0.18), (360.89, 0.75), (390.21, 7.42), (416.46, 17.35)]

# 各年代要建的構件
PARTS = {
    "1937": ("piers", "girders", "girder_ends", "deck37"),
    "1961": ("piers", "girders", "girder_ends", "deckcore", "w61", "surf61", "ext61"),
    "1972": ("piers", "girders", "girder_ends", "deckcore", "w61", "w72", "surf72", "ext61", "ext72"),
    "2026": ("piers", "girders", "capext26", "deck26"),
}


def box(name, c, s, m, coll, bevel=0.0):
    bm = bmesh.new()
    lib.box_bm(bm, c, s)
    return lib.new_obj(name, bm, m, coll, bevel=bevel)


def cyl(name, a, b, r, m, coll, verts=12):
    a, b = Vector(a), Vector(b)
    d = b - a
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=verts, radius1=r, radius2=r, depth=d.length)
    o = lib.new_obj(name, bm, m, coll)
    o.location = (a + b) / 2
    o.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
    return o


def extrude_yz(name, pts_yz, x0, x1, m, coll):
    bm = bmesh.new()
    lo = [bm.verts.new((x0, y, z)) for y, z in pts_yz]
    hi = [bm.verts.new((x1, y, z)) for y, z in pts_yz]
    bm.faces.new(list(reversed(lo)))
    bm.faces.new(hi)
    for i in range(len(pts_yz)):
        j = (i + 1) % len(pts_yz)
        bm.faces.new((lo[i], lo[j], hi[j], hi[i]))
    return lib.new_obj(name, bm, m, coll)


def resample(pts, step):
    """折線等距重取樣 → [(x, y, s, 方向角)]。"""
    segs = list(zip(pts, pts[1:]))
    total = sum(math.dist(a, b) for a, b in segs)
    n = max(2, int(total / step) + 1)
    out, i, acc = [], 0, 0.0
    for k in range(n):
        tgt = total * k / (n - 1)
        while i < len(segs) - 1 and acc + math.dist(*segs[i]) < tgt:
            acc += math.dist(*segs[i])
            i += 1
        (ax, ay), (bx, by) = segs[i]
        ln = math.dist((ax, ay), (bx, by)) or 1e-9
        t = min(max((tgt - acc) / ln, 0.0), 1.0)
        out.append((ax + (bx - ax) * t, ay + (by - ay) * t, tgt, math.atan2(by - ay, bx - ax)))
    return out


def pier_profile(h):
    """雙柱式拱型墩的橫斷面：梯形外廓，柱間半圓拱開口（REFERENCES 照片 01、12）。"""
    hb, ht, ow = PIER_W / 2 - 0.35, PIER_W / 2, OPEN_W / 2
    pts = [(-hb, 0), (-ow, 0), (-ow, SPRING)]
    for k in range(1, 12):
        a = math.pi - math.pi * k / 12
        pts.append((ow * math.cos(a), SPRING + ow * math.sin(a)))
    return pts + [(ow, SPRING), (ow, 0), (hb, 0), (ht, h), (-ht, h)]


def build(M, coll, era):
    """M：材質表（con37、con2、girder、deck、rail、wood、rail26、lamp）；coll：放物件的 collection。"""
    want = set(PARTS[era])
    lift = RAISE if era == "2026" else 0.0

    def girder_run(s, a_, b_, tag):
        z = H_PIER + lift
        xm, ln = (a_ + b_) / 2, b_ - a_
        for gy in (-GIRDER_Y, GIRDER_Y):
            box(f"G{s}{tag}_{gy:+.0f}_web", (xm, gy, z + GIRDER_D / 2), (ln, 0.08, GIRDER_D), M["girder"], coll)
            box(f"G{s}{tag}_{gy:+.0f}_tf", (xm, gy, z + GIRDER_D - 0.04), (ln, 0.46, 0.08), M["girder"], coll)
            box(f"G{s}{tag}_{gy:+.0f}_bf", (xm, gy, z + 0.04), (ln, 0.46, 0.08), M["girder"], coll)
        n = max(1, round(ln / 4.2))
        for k in range(n + 1):
            box(f"G{s}{tag}_fb{k}", (a_ + ln * k / n, 0, z + GIRDER_D * .55), (0.18, 2 * GIRDER_Y, 0.5), M["girder"], coll)
        for k in range(n):
            p, q = (a_ + ln * k / n, -GIRDER_Y, z + 0.2), (a_ + ln * (k + 1) / n, GIRDER_Y, z + 0.2)
            if k % 2:
                p, q = (p[0], GIRDER_Y, p[2]), (q[0], -GIRDER_Y, q[2])
            cyl(f"G{s}{tag}_br{k}", p, q, 0.08, M["girder"], coll, 6)

    def deck_rails(x0, x1, w, m_deck, m_rail, z, tag):
        box(f"deck{tag}", ((x0 + x1) / 2, 0, z - 0.12), (x1 - x0, w, 0.25), m_deck, coll)
        for side in (-1, 1):
            y = side * (w / 2 - 0.12)
            box(f"rail_top_{tag}_{side}", ((x0 + x1) / 2, y, z + 1.0), (x1 - x0, 0.2, 0.12), m_rail, coll)
            box(f"rail_bot_{tag}_{side}", ((x0 + x1) / 2, y, z + 0.15), (x1 - x0, 0.2, 0.12), m_rail, coll)
            n = int((x1 - x0) / 2.4)
            for k in range(n + 1):
                px = x0 + k * (x1 - x0) / n
                box(f"post_{tag}_{side}_{k}", (px, y, z + 0.55), (0.22, 0.24, 1.0), m_rail, coll)
                if k < n:
                    box(f"panel_{tag}_{side}_{k}", (px + (x1 - x0) / n / 2, y, z + 0.55), ((x1 - x0) / n - 0.5, 0.08, 0.6), m_rail, coll)
            for k in range(1, 10):
                lx = x0 + k * (x1 - x0) / 10
                cyl(f"lamp_post_{tag}_{k}_{side}", (lx, y, z), (lx, y, z + 4.2), 0.09, m_rail, coll, 8)
                cyl(f"lamp_head_{tag}_{k}_{side}", (lx, y, z + 4.2), (lx, y, z + 4.7), 0.2, M["lamp"], coll, 10)

    def widen_strip(y0, y1, tag, girder_ys):
        """戰後加寬：橋面板＋PC 梁＋帽梁延伸（示意斷面，無工程圖）。"""
        box(f"W{tag}_slab", (L37 / 2, (y0 + y1) / 2, DECK_Z - 0.12), (L37, y1 - y0, 0.3), M["con2"], coll)
        for s in range(NSPAN):
            for gy in girder_ys:
                box(f"W{tag}_g_{s}_{gy:+.1f}", ((s + .5) * L, gy, H_PIER + GIRDER_D / 2), (L - 0.6, 0.35, GIRDER_D), M["con2"], coll)
        for i in range(1, NSPAN):
            box(f"W{tag}_cap_{i}", (i * L, (y0 + y1) / 2, H_PIER - 0.35), (1.8, y1 - y0, 0.9), M["con2"], coll, 0.05)

    def surface(y0, y1, tag):
        box(f"asph{tag}", (L37 / 2, (y0 + y1) / 2, DECK_Z + 0.05), (L37, y1 - y0 - 0.6, 0.06), M["deck"], coll)
        for side, y in ((-1, y0 + 0.15), (1, y1 - 0.15)):
            box(f"parapet{tag}_{side}", (L37 / 2, y, DECK_Z + 0.5), (L37, 0.3, 1.0), M["con2"], coll)
            for k in range(1, 13):
                lx = k * L37 / 13
                cyl(f"lamp{tag}_{k}_{side}", (lx, y, DECK_Z + 1.0), (lx, y, DECK_Z + 9.0), 0.16, M["rail"], coll, 8)
                box(f"lamp{tag}h_{k}_{side}", (lx, y - side * 1.0, DECK_Z + 9.0), (0.5, 2.2, 0.25), M["lamp"], coll)

    def ext_deck(yoff, w, tag):
        """1962–63 永和端延長段：沿航照判讀的中線鋪橋面，每 20 m 一排三柱（柱位示意）。"""
        smp = resample(EXT_PATH, 3.0)
        for (xa, ya, _, _), (xb, yb, _, _) in zip(smp, smp[1:]):
            o = box(f"ext{tag}_seg", (0, 0, 0), (math.dist((xa, ya), (xb, yb)) + 0.2, w, 0.5), M["con2"], coll)
            o.location = ((xa + xb) / 2, (ya + yb) / 2 + yoff, DECK_Z - 0.12)     # 先放原點再旋轉，轉軸才是自己的中心
            o.rotation_euler.z = math.atan2(yb - ya, xb - xa)
        for x, y, s, a in resample(EXT_PATH, 20.0)[1:-1]:
            for dy in (-w / 3, 0, w / 3):
                px, py = x - math.sin(a) * dy, y + math.cos(a) * dy + yoff
                cyl(f"ext{tag}_col", (px, py, -1.0), (px, py, DECK_Z - 0.4), 0.6, M["con2"], coll, 14)

    if "piers" in want:                     # 13 座雙柱式拱型墩（1937 原構，各年代都在）
        for i in range(1, NSPAN):
            x = i * L
            box(f"P{i}_footing", (x, 0, -0.6), (3.4, 7.8, 1.6), M["con37"], coll, 0.1)
            o = extrude_yz(f"P{i}_body", pier_profile(H_PIER - 0.7), x - PIER_T / 2, x + PIER_T / 2, M["con37"], coll)
            o.location.z = 0.2
            box(f"P{i}_cap", (x, 0, H_PIER - 0.25), (2.1, 6.9, 0.7), M["con37"], coll, 0.05)
    if "capext26" in want:                  # 2026 帽梁增築（抬升量示意）
        for i in range(1, NSPAN):
            box(f"P{i}_capext", (i * L, 0, H_PIER + RAISE / 2), (2.1, 6.9, RAISE + 0.02), M["con2"], coll, 0.04)
    if "girders" in want:                   # 14 孔上承式鉚釘鋼鈑梁（三菱神戶造船所，1935）
        for s in range(NSPAN):
            a_, b_ = s * L + 0.35, (s + 1) * L - 0.35
            if s == 0:
                if "girder_ends" in want:
                    girder_run(s, a_, X0, "e")
                a_ = X0
            if s == NSPAN - 1:
                if "girder_ends" in want:
                    girder_run(s, X1, b_, "e")
                b_ = X1
            girder_run(s, a_, b_, "")
    if "deck37" in want:                    # 1937 橋面、直立欄杆、柱燈、橋台
        deck_rails(0.0, L37, W37, M["deck"], M["rail"], DECK_Z, "37")
        for x, sg in ((0.0, -1), (L37, 1)):
            box(f"abut_{x:.0f}", (x + sg * 3, 0, DECK_Z / 2 - 1.5), (6, 8, DECK_Z + 1.0), M["con37"], coll)
    if "deckcore" in want:                  # 拓寬後中間那條舊橋面（舊欄杆、柱燈已拆）
        box("deck_mid", (L37 / 2, 0, DECK_Z - 0.12), (L37, W37, 0.25), M["deck"], coll)
    if "w61" in want:                       # 1961：寬 15.4，原鋼鈑梁兩側加 PC 梁（兩側寬度為示意）
        widen_strip(-9.2, -W37 / 2, "61w", (-4.4, -7.4))
        widen_strip(W37 / 2, 6.2, "61e", (4.4,))
    if "surf61" in want:
        surface(-9.2, 6.2, "61")
    if "ext61" in want:
        ext_deck(-1.5, 15.4, "61")
    if "w72" in want:                       # 1972：上游側加寬到 24.5，加一排單柱帽梁墩
        widen_strip(6.2, 16.0, "72", (7.8, 10.2, 12.6, 15.0))
        for i in range(1, NSPAN):
            cyl(f"W72_col_{i}", (i * L, 11.1, -1.0), (i * L, 11.1, H_PIER - 0.8), 0.9, M["con2"], coll, 20)
    if "surf72" in want:
        surface(-9.2, 16.0, "72")
    if "ext72" in want:
        ext_deck(11.1, 9.8, "72")
    if "deck26" in want:                    # 2026：全寬 6、淨寬 5.2、木質步道、欄杆依原樣重做（形狀示意）
        deck_rails(X0, X1, 6.0, M["wood"], M["rail26"], DECK_Z + RAISE, "26")
        for x, sg in ((X0, -1), (X1, 1)):
            box(f"abut26_{x:.0f}", (x + sg * 2.5, 0, (DECK_Z + RAISE) / 2 - 1.5), (5, 7.5, DECK_Z + RAISE + 1.0), M["con2"], coll)


def extent(era):
    """(x 最小, x 最大, 橋面高)：給預覽相機用。"""
    x1 = EXT_PATH[-1][0] if era in ("1961", "1972") else L37 + 6
    return -6.0, x1, DECK_Z + (RAISE if era == "2026" else 0.0)
