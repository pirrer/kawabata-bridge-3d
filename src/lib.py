# -*- coding: utf-8 -*-
"""寫實版共用函式庫（Blender 4.5，背景模式）。

材質、天光、地形、水、樹林散佈、算圖設定都放這裡；各年代的橋體與分鏡另寫。
素材：Poly Haven（CC0），用 repo 根目錄的 fetch_textures.py 下載到 assets/polyhaven。

🔴 防閃爍（2026-09-26 首版全片實測）：
  · 任兩個面不可共平面（地坪／草地、橋面／加寬段曾 z-fighting 一格草一格灰）→ 一律錯開 ≥3 cm。
  · 快速移動的碎塊、構件一律開動態模糊。
  · 薄構件（桁架、欄杆）至少 0.08 m 厚，避免次像素跳格。
"""
import bmesh
import bpy
import glob
import math
import os
import random
from mathutils import Vector

# Poly Haven 素材資料夾：預設 repo 根目錄的 assets/polyhaven（python fetch_textures.py 下載）；可用環境變數 KAWABATA_ASSETS 改
ASSETS = os.environ.get("KAWABATA_ASSETS") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "polyhaven")
TEX = ASSETS + "/textures"
MOD = ASSETS + "/models"
HDRI = ASSETS + "/hdri"


# ---------------------------------------------------------------- 場景與算圖
def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.unit_settings.system = "METRIC"
    return sc


def setup_render(sc, engine="CYCLES", res=(1080, 1920), samples=96, fps=30, blur=True, mist=(120.0, 2500.0)):
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.fps = fps
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Base Contrast"
    if engine == "CYCLES":
        sc.render.engine = "CYCLES"
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for dev_type in ("OPTIX", "CUDA"):
            try:
                prefs.compute_device_type = dev_type
                prefs.get_devices()
                if any(d.type == dev_type for d in prefs.devices):
                    break
            except TypeError:
                continue
        for d in prefs.devices:
            d.use = d.type != "CPU"
        sc.cycles.device = "GPU"
        sc.cycles.samples = samples
        sc.cycles.use_adaptive_sampling = True
        sc.cycles.adaptive_threshold = 0.02
        sc.cycles.use_denoising = True
        sc.cycles.denoiser = "OPTIX"
        sc.cycles.seed = 7                 # 🔴 固定種子：噪點圖樣不隨格變，降噪後才不會「沸騰」閃爍
        sc.cycles.use_animated_seed = False
        sc.cycles.max_bounces = 8
        sc.cycles.transparent_max_bounces = 16   # 樹葉透明貼圖
        sc.cycles.filter_width = 1.5
    else:
        sc.render.engine = "BLENDER_EEVEE_NEXT"
        sc.eevee.taa_render_samples = samples
        sc.eevee.use_raytracing = True
        sc.eevee.use_shadows = True
    sc.render.use_motion_blur = blur
    sc.render.use_persistent_data = True   # 靜態幾何（樹林實例、地形）跨格保留，不每格重建
    sc.render.motion_blur_shutter = 0.5
    # 大氣透視：mist pass 在合成裡加霧，無噪點
    vl = sc.view_layers[0]
    vl.use_pass_mist = True
    if sc.world is None:
        sc.world = bpy.data.worlds.new("world")
    sc.world.mist_settings.start, sc.world.mist_settings.depth = mist
    sc.world.mist_settings.falloff = "QUADRATIC"


def compositor_haze(sc, color=(0.72, 0.76, 0.80, 1.0), amount=0.55):
    """mist 係數 × amount 混入霧色（遠山變淡、變藍灰）。"""
    sc.use_nodes = True
    nt = sc.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    rl = nt.nodes.new("CompositorNodeRLayers")
    mul = nt.nodes.new("CompositorNodeMath"); mul.operation = "MULTIPLY"; mul.inputs[1].default_value = amount
    mix = nt.nodes.new("CompositorNodeMixRGB"); mix.blend_type = "MIX"
    mix.inputs[2].default_value = color
    out = nt.nodes.new("CompositorNodeComposite")
    nt.links.new(rl.outputs["Mist"], mul.inputs[0])
    nt.links.new(mul.outputs[0], mix.inputs[0])
    nt.links.new(rl.outputs["Image"], mix.inputs[1])
    nt.links.new(mix.outputs[0], out.inputs[0])


def hdri_sun_azimuth(img):
    """找 HDRI 最亮點，回傳 (方位角, 仰角)（度）。方位角＝atan2(y, x)；Blender 等距柱狀 u=0.5 為 +X。"""
    import numpy as np
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)
    lum = px[..., 0] * 0.2126 + px[..., 1] * 0.7152 + px[..., 2] * 0.0722
    j, i = np.unravel_index(np.argmax(lum), lum.shape)
    u, v = (i + 0.5) / w, (j + 0.5) / h
    return math.degrees((0.5 - u) * 2 * math.pi), math.degrees((v - 0.5) * math.pi)


def world_hdri(sc, name="kloofendal_48d_partly_cloudy_puresky_4k.hdr", strength=1.0, sun_az_deg=None):
    """sun_az_deg：把 HDRI 裡的太陽轉到這個方位（朝向太陽的方向 atan2(y, x)）。回傳 HDRI 太陽仰角。"""
    w = sc.world
    w.use_nodes = True
    nt = w.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    img = bpy.data.images.load(os.path.join(HDRI, name), check_existing=True)
    az, el = hdri_sun_azimuth(img)
    rot = 0.0 if sun_az_deg is None else math.radians(az - sun_az_deg)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping"); mp.inputs["Rotation"].default_value[2] = rot
    env = nt.nodes.new("ShaderNodeTexEnvironment"); env.image = img
    bg = nt.nodes.new("ShaderNodeBackground"); bg.inputs["Strength"].default_value = strength
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], env.inputs["Vector"])
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    print(f"HDRI sun az={az:.1f} el={el:.1f} -> rotate {math.degrees(rot):.1f}")
    return el


def sun_toward(az_deg, el_deg, strength=3.0, angle_deg=1.0, color=(1.0, 0.96, 0.9)):
    """太陽燈：az＝朝向太陽的方位角 atan2(y, x)，el＝仰角。與 HDRI 太陽對齊，陰影更銳利。
    Blender 太陽燈 -Z 經 (rx, 0, rz) 旋轉後＝(-sin rz·sin rx, cos rz·sin rx, -cos rx)＝光行進方向。"""
    rx = math.radians(90 - el_deg)
    tx, ty = -math.cos(math.radians(az_deg)), -math.sin(math.radians(az_deg))
    rz = math.atan2(-tx, ty)
    d = bpy.data.lights.new("sun", "SUN")
    d.energy = strength; d.angle = math.radians(angle_deg); d.color = color
    o = bpy.data.objects.new("sun", d)
    o.rotation_euler = (rx, 0.0, rz)
    bpy.context.scene.collection.objects.link(o)
    return o


def coll(name, parent=None, hidden=False):
    c = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(c)
    if hidden:
        lc = find_layer_coll(bpy.context.view_layer.layer_collection, name)
        if lc:
            lc.exclude = True
    return c


def find_layer_coll(lc, name):
    if lc.collection.name == name:
        return lc
    for ch in lc.children:
        r = find_layer_coll(ch, name)
        if r:
            return r
    return None


# ---------------------------------------------------------------- 材質
_MATS = {}
USE_AO = False   # 材質內 AO 髒污節點：每個取樣點多打 8 條光線，動畫一格慢一倍以上；路徑追蹤本身已有遮蔽，預設關


def _img(path, noncolor=False):
    im = bpy.data.images.load(path, check_existing=True)
    if noncolor:
        im.colorspace_settings.name = "Non-Color"
    return im


def pbr_nodes(nt, tex_id, size_m=2.0, tint=(1, 1, 1), bright=1.0, rough_add=0.0, bump=0.35,
              dirt=0.35, variation=0.18, blend=0.25, sat=1.0, streak=0.0):
    """在 node tree 裡建一組 Poly Haven 貼圖節點，回傳 (color, roughness, normal) 三個輸出 socket。
    物件座標 box 投影（mesh 以世界公尺建，尺度一致）＋大尺度明暗斑駁（打破重複）＋AO 髒污（縫隙變暗）。"""
    L = nt.links.new
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1 / size_m,) * 3
    L(tc.outputs["Object"], mp.inputs["Vector"])
    base = f"{TEX}/{tex_id}/{tex_id}"

    def teximg(suffix, noncolor):
        cands = glob.glob(f"{base}_{suffix}_2k.*")
        if not cands:
            return None
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image = _img(cands[0], noncolor)
        t.projection = "BOX"; t.projection_blend = blend
        L(mp.outputs["Vector"], t.inputs["Vector"])
        return t

    diff = teximg("Diffuse", False)
    rgh = teximg("Rough", True)
    nor = teximg("nor_gl", True)
    hsv = nt.nodes.new("ShaderNodeHueSaturation"); hsv.inputs["Saturation"].default_value = sat
    L(diff.outputs["Color"], hsv.inputs["Color"])
    tintn = nt.nodes.new("ShaderNodeMixRGB"); tintn.blend_type = "MULTIPLY"; tintn.inputs[0].default_value = 1.0
    tintn.inputs[2].default_value = (tint[0] * bright, tint[1] * bright, tint[2] * bright, 1)
    L(hsv.outputs["Color"], tintn.inputs[1])
    noise = nt.nodes.new("ShaderNodeTexNoise"); noise.inputs["Scale"].default_value = 0.08
    noise.inputs["Detail"].default_value = 6
    L(tc.outputs["Object"], noise.inputs["Vector"])
    ramp = nt.nodes.new("ShaderNodeMapRange")
    ramp.inputs["To Min"].default_value = 1 - variation; ramp.inputs["To Max"].default_value = 1 + variation * 0.4
    L(noise.outputs["Fac"], ramp.inputs["Value"])
    var = nt.nodes.new("ShaderNodeMixRGB"); var.blend_type = "MULTIPLY"; var.inputs[0].default_value = 1.0
    L(tintn.outputs["Color"], var.inputs[1]); L(ramp.outputs["Result"], var.inputs[2])
    dirtn = nt.nodes.new("ShaderNodeMixRGB"); dirtn.blend_type = "MULTIPLY"; dirtn.inputs[0].default_value = 1.0
    L(var.outputs["Color"], dirtn.inputs[1])
    if USE_AO:
        ao = nt.nodes.new("ShaderNodeAmbientOcclusion"); ao.inputs["Distance"].default_value = 0.8; ao.samples = 8
        aor = nt.nodes.new("ShaderNodeMapRange")
        aor.inputs["To Min"].default_value = 1 - dirt; aor.inputs["To Max"].default_value = 1.0
        L(ao.outputs["AO"], aor.inputs["Value"]); L(aor.outputs["Result"], dirtn.inputs[2])
    else:
        dirtn.inputs[2].default_value = (1 - dirt * 0.25,) * 3 + (1,)   # 無 AO 時整體略壓暗，維持舊感
    if streak > 0:
        # 垂直水漬：物件座標 z 方向拉長 25 倍的噪聲 → 往下流的深色條紋（refs/10 橋體黑斑）
        smp = nt.nodes.new("ShaderNodeMapping"); smp.inputs["Scale"].default_value = (1.6, 1.6, 0.06)
        L(tc.outputs["Object"], smp.inputs["Vector"])
        sn = nt.nodes.new("ShaderNodeTexNoise"); sn.inputs["Scale"].default_value = 1.0; sn.inputs["Detail"].default_value = 4
        sn.inputs["Roughness"].default_value = 0.6
        L(smp.outputs["Vector"], sn.inputs["Vector"])
        sr = nt.nodes.new("ShaderNodeMapRange")
        sr.inputs["From Min"].default_value = 0.45; sr.inputs["From Max"].default_value = 0.7
        sr.inputs["To Min"].default_value = 1.0; sr.inputs["To Max"].default_value = 1 - streak
        L(sn.outputs["Fac"], sr.inputs["Value"])
        stn = nt.nodes.new("ShaderNodeMixRGB"); stn.blend_type = "MULTIPLY"; stn.inputs[0].default_value = 1.0
        L(dirtn.outputs["Color"], stn.inputs[1]); L(sr.outputs["Result"], stn.inputs[2])
        dirtn = stn
    rough = None
    if rgh:
        ra = nt.nodes.new("ShaderNodeMath"); ra.operation = "ADD"; ra.use_clamp = True
        ra.inputs[1].default_value = rough_add
        L(rgh.outputs["Color"], ra.inputs[0]); rough = ra.outputs[0]
    normal = None
    if nor:
        nm = nt.nodes.new("ShaderNodeNormalMap"); nm.inputs["Strength"].default_value = bump * 2
        L(nor.outputs["Color"], nm.inputs["Color"]); normal = nm.outputs["Normal"]
    return dirtn.outputs["Color"], rough, normal


def pbr(name, tex_id, **kw):
    key = (name, tex_id, tuple(sorted(kw.items())))
    if key in _MATS:
        return _MATS[key]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    col, rough, normal = pbr_nodes(nt, tex_id, **kw)
    nt.links.new(col, bsdf.inputs["Base Color"])
    if rough:
        nt.links.new(rough, bsdf.inputs["Roughness"])
    if normal:
        nt.links.new(normal, bsdf.inputs["Normal"])
    _MATS[key] = m
    return m


def height_mix_mat(name, low, high, z0, z1, noise_amp=0.8):
    """依高度（物件座標 z）在兩組貼圖間漸變：low=(tex_id, kw) 用在 z<z0，high 用在 z>z1。河岸泥灘→草地。"""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    L = nt.links.new
    bsdf = nt.nodes["Principled BSDF"]
    a = pbr_nodes(nt, low[0], **low[1])
    b = pbr_nodes(nt, high[0], **high[1])
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ"); L(tc.outputs["Object"], sep.inputs[0])
    nz = nt.nodes.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 0.15; nz.inputs["Detail"].default_value = 8
    L(tc.outputs["Object"], nz.inputs["Vector"])
    nzm = nt.nodes.new("ShaderNodeMath"); nzm.operation = "MULTIPLY_ADD"
    nzm.inputs[1].default_value = noise_amp * 2; nzm.inputs[2].default_value = -noise_amp
    L(nz.outputs["Fac"], nzm.inputs[0])
    zz = nt.nodes.new("ShaderNodeMath"); zz.operation = "ADD"
    L(sep.outputs["Z"], zz.inputs[0]); L(nzm.outputs[0], zz.inputs[1])
    fac = nt.nodes.new("ShaderNodeMapRange")
    fac.inputs["From Min"].default_value = z0; fac.inputs["From Max"].default_value = z1
    L(zz.outputs[0], fac.inputs["Value"])
    mc = nt.nodes.new("ShaderNodeMix"); mc.data_type = "RGBA"
    L(fac.outputs["Result"], mc.inputs["Factor"]); L(a[0], mc.inputs["A"]); L(b[0], mc.inputs["B"])
    L(mc.outputs["Result"], bsdf.inputs["Base Color"])
    if a[1] and b[1]:
        mr = nt.nodes.new("ShaderNodeMix"); mr.data_type = "FLOAT"
        L(fac.outputs["Result"], mr.inputs["Factor"]); L(a[1], mr.inputs["A"]); L(b[1], mr.inputs["B"])
        L(mr.outputs["Result"], bsdf.inputs["Roughness"])
    if a[2] and b[2]:
        mn = nt.nodes.new("ShaderNodeMix"); mn.data_type = "VECTOR"
        L(fac.outputs["Result"], mn.inputs["Factor"]); L(a[2], mn.inputs["A"]); L(b[2], mn.inputs["B"])
        L(mn.outputs["Result"], bsdf.inputs["Normal"])
    return m


def aging_mat(name, a, b):
    """兩組貼圖（a＝新、b＝舊）以一個可 keyframe 的 Value 節點混合；回傳 (材質, value 節點)。m04 1933→1968 變舊。"""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree; L = nt.links.new
    bsdf = nt.nodes["Principled BSDF"]
    ca = pbr_nodes(nt, a[0], **a[1]); cb = pbr_nodes(nt, b[0], **b[1])
    val = nt.nodes.new("ShaderNodeValue"); val.name = "age"; val.outputs[0].default_value = 0.0
    for idx, key, dt in ((0, "Base Color", "RGBA"), (1, "Roughness", "FLOAT"), (2, "Normal", "VECTOR")):
        if ca[idx] is None or cb[idx] is None:
            continue
        mx = nt.nodes.new("ShaderNodeMix"); mx.data_type = dt
        L(val.outputs[0], mx.inputs["Factor"]); L(ca[idx], mx.inputs["A"]); L(cb[idx], mx.inputs["B"])
        L(mx.outputs["Result"], bsdf.inputs[key])
    return m, val


def cutout_mat(name, png, sx, sz, z0=0.0, color=(0.05, 0.05, 0.048), rough=0.45, metal=0.5):
    """鏤空貼圖材質（鑄鐵欄杆）：物件座標 x／sx、(z−z0)／sz 當 UV，貼圖 alpha 決定鏤空。"""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    m.blend_method = "HASHED" if hasattr(m, "blend_method") else None
    nt = m.node_tree; L = nt.links.new
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ"); L(tc.outputs["Object"], sep.inputs[0])
    u = nt.nodes.new("ShaderNodeMath"); u.operation = "DIVIDE"; u.inputs[1].default_value = sx
    L(sep.outputs["X"], u.inputs[0])
    vz = nt.nodes.new("ShaderNodeMath"); vz.operation = "SUBTRACT"; vz.inputs[1].default_value = z0
    L(sep.outputs["Z"], vz.inputs[0])
    v = nt.nodes.new("ShaderNodeMath"); v.operation = "DIVIDE"; v.inputs[1].default_value = sz
    L(vz.outputs[0], v.inputs[0])
    cmb = nt.nodes.new("ShaderNodeCombineXYZ"); L(u.outputs[0], cmb.inputs["X"]); L(v.outputs[0], cmb.inputs["Y"])
    img = nt.nodes.new("ShaderNodeTexImage"); img.image = _img(png); img.extension = "REPEAT"
    img.interpolation = "Cubic"
    L(cmb.outputs[0], img.inputs["Vector"])
    L(img.outputs["Alpha"], bsdf.inputs["Alpha"])
    return m


def flat(name, color, rough=0.5, metal=0.0, emit=None, emit_strength=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if emit:
        b.inputs["Emission Color"].default_value = (*emit, 1)
        b.inputs["Emission Strength"].default_value = emit_strength
    return m


def water_mat(color=(0.045, 0.06, 0.05), rough=0.045, wave_scale=0.9, wave_strength=0.22):
    """基隆河：混濁、不透明、會倒影；法線用兩層噪聲做細波與長湧。"""
    m = bpy.data.materials.new("water")
    m.use_nodes = True
    nt = m.node_tree; L = nt.links.new
    b = nt.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["IOR"].default_value = 1.333
    b.inputs["Specular IOR Level"].default_value = 0.5
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sc_ = nt.nodes.new("ShaderNodeMapping"); sc_.inputs["Scale"].default_value = (1 / wave_scale, 1 / (wave_scale * 3.0), 1)
    L(tc.outputs["Object"], sc_.inputs["Vector"])
    n1 = nt.nodes.new("ShaderNodeTexNoise"); n1.inputs["Scale"].default_value = 1.0; n1.inputs["Detail"].default_value = 8
    L(sc_.outputs["Vector"], n1.inputs["Vector"])
    n2 = nt.nodes.new("ShaderNodeTexNoise"); n2.inputs["Scale"].default_value = 0.05; n2.inputs["Detail"].default_value = 3
    L(tc.outputs["Object"], n2.inputs["Vector"])
    add = nt.nodes.new("ShaderNodeMath"); add.operation = "ADD"
    L(n1.outputs["Fac"], add.inputs[0]); L(n2.outputs["Fac"], add.inputs[1])
    bump = nt.nodes.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = wave_strength
    bump.inputs["Distance"].default_value = 0.08
    L(add.outputs[0], bump.inputs["Height"]); L(bump.outputs["Normal"], b.inputs["Normal"])
    return m


# ---------------------------------------------------------------- 幾何
def new_obj(name, bm, mat=None, coll_=None, bevel=0.0, smooth_angle=None):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(name, me)
    (coll_ or bpy.context.scene.collection).objects.link(o)
    if mat:
        me.materials.append(mat)
    if bevel > 0:
        md = o.modifiers.new("bevel", "BEVEL")
        md.width = bevel; md.segments = 2; md.limit_method = "ANGLE"
        md.angle_limit = math.radians(40); md.harden_normals = True
    if smooth_angle is not None:
        for p in me.polygons:
            p.use_smooth = True
        md = o.modifiers.new("smooth", "SMOOTH_BY_ANGLE") if hasattr(bpy.types, "NodesModifier") else None
    return o


def box_bm(bm, c, s):
    """把一個軸對齊方塊加進 bm（中心 c、尺寸 s）。"""
    r = bmesh.ops.create_cube(bm, size=1.0)
    for v in r["verts"]:
        v.co = Vector((c[0] + v.co.x * s[0], c[1] + v.co.y * s[1], c[2] + v.co.z * s[2]))
    return r


def box(name, c, s, mat, coll_=None, bevel=0.03):
    bm = bmesh.new(); box_bm(bm, c, s)
    return new_obj(name, bm, mat, coll_, bevel)


def prism_xz_bm(bm, pts, y0, y1):
    """xz 平面多邊形（逆時針）沿 y 擠出。凹多邊形可（ngon 由 Blender 三角化）。"""
    a = [bm.verts.new((x, y0, z)) for x, z in pts]
    b = [bm.verts.new((x, y1, z)) for x, z in pts]
    n = len(pts)
    bm.faces.new(a[::-1]); bm.faces.new(b)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((a[i], a[j], b[j], b[i]))


def prism_xy_bm(bm, pts, z0, z1):
    a = [bm.verts.new((x, y, z0)) for x, y in pts]
    b = [bm.verts.new((x, y, z1)) for x, y in pts]
    n = len(pts)
    bm.faces.new(a[::-1]); bm.faces.new(b)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((a[i], a[j], b[j], b[i]))


def recalc(bm):
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)


# ---------------------------------------------------------------- 地形
def terrain(name, size_x, size_y, res, height_fn, mat, center=(0, 0), coll_=None, attrs=None):
    """格點地形；height_fn(x, y) → z。attrs = {名稱: fn(x, y, z) → float}，存成點屬性（給散佈用）。"""
    bm = bmesh.new()
    nx, ny = res
    verts = []
    for j in range(ny + 1):
        row = []
        for i in range(nx + 1):
            x = center[0] - size_x / 2 + size_x * i / nx
            y = center[1] - size_y / 2 + size_y * j / ny
            row.append(bm.verts.new((x, y, height_fn(x, y))))
        verts.append(row)
    for j in range(ny):
        for i in range(nx):
            bm.faces.new((verts[j][i], verts[j][i + 1], verts[j + 1][i + 1], verts[j + 1][i]))
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    for p in me.polygons:
        p.use_smooth = True
    if attrs:
        for an, fn in attrs.items():
            at = me.attributes.new(an, "FLOAT", "POINT")
            for k, v in enumerate(me.vertices):
                at.data[k].value = fn(v.co.x, v.co.y, v.co.z)
    o = bpy.data.objects.new(name, me)
    (coll_ or bpy.context.scene.collection).objects.link(o)
    me.materials.append(mat)
    return o


# ---------------------------------------------------------------- 樹木
_PROTOS = {}


def _fix_image_paths():
    for im in bpy.data.images:
        p = bpy.path.abspath(im.filepath)
        if im.source == "FILE" and not os.path.exists(p):
            hit = glob.glob(f"{MOD}/*/textures/{os.path.basename(p)}")
            if hit:
                im.filepath = hit[0]
                im.reload()


def load_tree(asset, lod="LOD1", proto_coll=None):
    """把 Poly Haven 模型的樹幹＋樹葉同一 LOD 物件收成一個原型 collection（供實例化）。"""
    key = (asset, lod)
    if key in _PROTOS:
        return _PROTOS[key]
    path = glob.glob(f"{MOD}/{asset}/*.blend")[0]
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        dst.objects = [n for n in src.objects if n.endswith(lod) or (lod == "LOD1" and n.endswith("trunk"))]
    c = bpy.data.collections.new(f"proto_{asset}_{lod}")
    (proto_coll or bpy.context.scene.collection).children.link(c)
    for o in dst.objects:
        if o is not None:
            c.objects.link(o)
    _fix_image_paths()
    _PROTOS[key] = c
    return c


def scatter(name, surface, protos, density, attr, seed=1, scale=(0.8, 1.4), dist_min=2.0, coll_=None,
            align_up=True):
    """在 surface（有點屬性 attr，0–1）上散佈 protos（collection 清單）實例。"""
    ng = bpy.data.node_groups.new(name + "_gn", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    N = ng.nodes; L = ng.links.new
    gi = N.new("NodeGroupInput"); go = N.new("NodeGroupOutput")
    dp = N.new("GeometryNodeDistributePointsOnFaces"); dp.distribute_method = "POISSON"
    dp.inputs["Distance Min"].default_value = dist_min
    dp.inputs["Density Max"].default_value = density
    dp.inputs["Seed"].default_value = seed
    na = N.new("GeometryNodeInputNamedAttribute"); na.data_type = "FLOAT"; na.inputs["Name"].default_value = attr
    L(gi.outputs[0], dp.inputs["Mesh"]); L(na.outputs["Attribute"], dp.inputs["Density Factor"])
    # 原型：把多個 collection 包成一個 parent collection，Collection Info 分開子物件
    parent = bpy.data.collections.new(name + "_set")
    bpy.context.scene.collection.children.link(parent)
    for p in protos:
        parent.children.link(p)
    lc = find_layer_coll(bpy.context.view_layer.layer_collection, parent.name)
    lc.exclude = True
    ci = N.new("GeometryNodeCollectionInfo"); ci.inputs["Collection"].default_value = parent
    ci.inputs["Separate Children"].default_value = True; ci.inputs["Reset Children"].default_value = True
    ip = N.new("GeometryNodeInstanceOnPoints")
    ip.inputs["Pick Instance"].default_value = True
    rid = N.new("FunctionNodeRandomValue"); rid.data_type = "INT"
    rid.inputs["Min"].default_value = 0; rid.inputs["Max"].default_value = len(protos) - 1
    rid.inputs["Seed"].default_value = seed + 3
    rrot = N.new("FunctionNodeRandomValue"); rrot.data_type = "FLOAT_VECTOR"
    rrot.inputs["Min"].default_value = (0, 0, 0); rrot.inputs["Max"].default_value = (0.06, 0.06, 6.283)
    rrot.inputs["Seed"].default_value = seed + 5
    rsc = N.new("FunctionNodeRandomValue"); rsc.data_type = "FLOAT"
    rsc.inputs["Min"].default_value = scale[0]; rsc.inputs["Max"].default_value = scale[1]
    rsc.inputs["Seed"].default_value = seed + 7
    L(dp.outputs["Points"], ip.inputs["Points"]); L(ci.outputs["Instances"], ip.inputs["Instance"])
    L(rid.outputs[2], ip.inputs["Instance Index"])
    L(rrot.outputs[0], ip.inputs["Rotation"]); L(rsc.outputs[1], ip.inputs["Scale"])
    L(ip.outputs["Instances"], go.inputs[0])
    o = bpy.data.objects.new(name, surface.data)
    (coll_ or bpy.context.scene.collection).objects.link(o)
    md = o.modifiers.new("scatter", "NODES"); md.node_group = ng
    try:                                   # 畫面外的樹不進加速結構（動畫每格省同步與 BVH）
        o.cycles.use_camera_cull = True
        bpy.context.scene.cycles.use_camera_cull = True
        bpy.context.scene.cycles.camera_cull_margin = 0.15
    except AttributeError:
        pass
    return o


# ---------------------------------------------------------------- 相機
def camera(loc, target, lens=35.0, name="cam", sensor=36.0):
    cd = bpy.data.cameras.new(name); cd.lens = lens; cd.sensor_width = sensor; cd.clip_end = 20000; cd.clip_start = 0.3
    cam = bpy.data.objects.new(name, cd)
    bpy.context.scene.collection.objects.link(cam)
    tgt = bpy.data.objects.new(name + "_tgt", None)
    bpy.context.scene.collection.objects.link(tgt)
    tgt.location = target; cam.location = loc
    tc = cam.constraints.new("TRACK_TO"); tc.target = tgt
    tc.track_axis = "TRACK_NEGATIVE_Z"; tc.up_axis = "UP_Y"
    bpy.context.scene.camera = cam
    return cam, tgt


def seeded(seed):
    random.seed(seed)
    return random
