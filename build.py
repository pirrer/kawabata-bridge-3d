# -*- coding: utf-8 -*-
"""川端橋四個年代的橋體：建模、存 .blend、匯出 .glb、渲一張預覽圖（Blender 4.5，背景模式）。

  blender -b -P build.py -- --era 1937 --blend out/kawabata_1937.blend --glb out/kawabata_1937.glb --render out/kawabata_1937.png

era：1937（原橋）／1961（拓寬到 15.4 m＋永和端延長）／1972（24.5 m，到 2019 新橋動工前）／2026（修復後的人行橋）。
貼圖要先跑 python fetch_textures.py（Poly Haven，CC0）。

.blend 保留完整 PBR 材質（物件座標 box 投影）；.glb 的材質是簡化版（每種材質取貼圖平均色），
因為 glTF 不支援物件座標投影。
"""
import argparse
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
import bmesh  # noqa: E402
import bpy  # noqa: E402
import lib  # noqa: E402
import kawabata as K  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ap = argparse.ArgumentParser()
ap.add_argument("--era", default="1937", choices=("1937", "1961", "1972", "2026"))
ap.add_argument("--blend", default="")
ap.add_argument("--glb", default="")
ap.add_argument("--render", default="")
ap.add_argument("--res", default="1920x1080")
ap.add_argument("--samples", type=int, default=128)
ap.add_argument("--pack", action="store_true", help="把貼圖打包進 .blend（檔案變大，但搬到別台電腦不必再下載貼圖）")
A = ap.parse_args(argv)

if not os.path.isdir(lib.TEX):
    sys.exit(f"找不到貼圖資料夾 {lib.TEX}：先跑 python fetch_textures.py，或用環境變數 KAWABATA_ASSETS 指定")

sc = lib.reset()
C_BR = lib.coll(f"kawabata_bridge_{A.era}")

# ---- 材質（數值與影片版相同）。PROXY 記下每個 PBR 材質的參數，匯出 glb 時換成平均色
PROXY = {}


def P(name, tex_id, **kw):
    m = lib.pbr(name, tex_id, **kw)
    PROXY[m.name] = (tex_id, kw)
    return m


M = {
    "con37": P("con37", "dirty_concrete", size_m=2.5, tint=(0.95, 0.94, 0.9), bright=0.95, sat=0.35, dirt=0.5, variation=0.3, streak=0.45),
    "con2": P("con_postwar", "dirty_concrete", size_m=3.0, tint=(0.98, 0.98, 0.96), bright=1.05, sat=0.25, dirt=0.4, streak=0.3),
    "deck": P("deck", "asphalt_02", size_m=3.0, bright=0.6, dirt=0.1),
    "wood": P("boardwalk", "brown_planks_05", size_m=2.0, tint=(0.8, 0.6, 0.45), bright=0.65, sat=0.8, dirt=0.25),
    "rail": lib.flat("rail", (0.78, 0.76, 0.70), rough=0.6),
    "rail26": lib.flat("rail26", (0.22, 0.34, 0.16), rough=0.45, metal=0.3),
    "lamp": lib.flat("lamp", (0.95, 0.85, 0.6), rough=0.4),
}
if A.era == "2026":
    # 修復後鋼梁漆回原色（綠），新漆面不做鏽蝕
    M["girder"] = lib.flat("girder26", (0.30, 0.46, 0.14), rough=0.4, metal=0.3)
else:
    M["girder"] = P("girder", "rusty_metal_02", size_m=1.5, tint=(0.62, 0.95, 0.35), bright=0.95, sat=0.55, dirt=0.3)

# ---- 建橋
K.build(M, C_BR, A.era)
objs = [o for o in C_BR.all_objects if o.type == "MESH"]
print(f"era {A.era}: {len(objs)} 個物件、{sum(len(o.data.polygons) for o in objs)} 個面（未套 bevel）")
X_MIN, X_MAX, DECK = K.extent(A.era)


def setup_view():
    """預覽用天光、水面與相機：下游側斜看全橋，台北端在左。"""
    lib.setup_render(sc, res=tuple(int(v) for v in A.res.split("x")), samples=A.samples, blur=False)
    el = lib.world_hdri(sc, strength=0.85, sun_az_deg=-20.0)
    lib.sun_toward(-20.0, max(el, 35.0), strength=3.0)
    sc.view_settings.exposure = -0.35
    bm = bmesh.new()
    lib.box_bm(bm, ((X_MIN + X_MAX) / 2, 0, -0.05), (X_MAX - X_MIN + 600, 600, 0.1))
    lib.new_obj("water_preview", bm, lib.water_mat())
    lib.camera((X_MIN - 84.0, -120.0, DECK + 6.0), (X_MIN + 116.0, 20.0, DECK - 1.0), lens=30.0)


def save_blend(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if A.pack:
        bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(path))
    if not A.pack:
        # 貼圖改相對路徑再存一次：整個 repo 搬到別處，.blend 仍找得到 assets/
        bpy.ops.file.make_paths_relative()
        bpy.ops.wm.save_mainfile()
    print("saved", path)


def srgb_to_lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def mean_color(tex_id):
    import numpy as np
    f = glob.glob(os.path.join(lib.TEX, tex_id, f"{tex_id}_Diffuse_2k.*"))[0]
    im = bpy.data.images.load(f, check_existing=True)
    px = np.empty(im.size[0] * im.size[1] * 4, dtype=np.float32)
    im.pixels.foreach_get(px)
    rgb = px.reshape(-1, 4)[:, :3].mean(axis=0)
    return [srgb_to_lin(float(c)) for c in rgb]


def proxy_material(m):
    """PBR 材質 → 只有平均色＋粗糙度的 Principled（套用影片版的飽和度、染色、亮度、髒污係數）。"""
    tex_id, kw = PROXY[m.name]
    rgb = mean_color(tex_id)
    lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    sat = kw.get("sat", 1.0)
    rgb = [lum + (c - lum) * sat for c in rgb]
    tint, bright, dirt = kw.get("tint", (1, 1, 1)), kw.get("bright", 1.0), kw.get("dirt", 0.35)
    rgb = [min(1.0, c * t * bright * (1 - dirt * 0.25)) for c, t in zip(rgb, tint)]
    p = bpy.data.materials.new(m.name + "_glb")
    p.use_nodes = True
    b = p.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = min(1.0, 0.75 + kw.get("rough_add", 0.0))
    return p


def export_glb(path):
    cache = {}
    for o in objs:
        for slot in o.material_slots:
            m = slot.material
            if m is not None and m.name in PROXY:
                if m.name not in cache:
                    cache[m.name] = proxy_material(m)
                slot.material = cache[m.name]
    for o in bpy.data.objects:
        o.select_set(o in objs)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=os.path.abspath(path), export_format="GLB", use_selection=True,
                              export_apply=True, export_yup=True)
    print("exported", path)


# 順序：先渲預覽、存 .blend（完整材質），最後才把材質換成 glb 簡化版
if A.render or A.blend:
    setup_view()
if A.render:
    sc.render.filepath = os.path.abspath(A.render)
    bpy.ops.render.render(write_still=True)
    print("rendered", A.render)
if A.blend:
    save_blend(A.blend)
if A.glb:
    export_glb(A.glb)
