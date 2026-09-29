#!/usr/bin/env python3
"""從 Poly Haven（CC0）下載建模需要的貼圖與 HDRI 到 assets/polyhaven/。用法：python fetch_textures.py
已存在的檔案會跳過。 / Downloads the CC0 textures and HDRI this project needs from Poly Haven."""
import json
import os
import urllib.request

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "polyhaven")
HDRIS = [("kloofendal_48d_partly_cloudy_puresky", "4k")]          # 只有 build.py --render 會用到
TEXTURES = ["dirty_concrete", "asphalt_02", "rusty_metal_02", "brown_planks_05"]
MAPS = ["Diffuse", "nor_gl", "Rough"]
UA = {"User-Agent": "kawabata-bridge-3d-fetch/1.0"}


def get(url, dst):
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA)) as r, open(dst + ".part", "wb") as f:
        f.write(r.read())
    os.replace(dst + ".part", dst)
    print("  ok", os.path.relpath(dst, HERE), os.path.getsize(dst) // 1024, "KB")


def files(aid):
    with urllib.request.urlopen(urllib.request.Request(f"https://api.polyhaven.com/files/{aid}", headers=UA)) as r:
        return json.load(r)


for aid, res in HDRIS:
    print("hdri", aid)
    get(files(aid)["hdri"][res]["hdr"]["url"], os.path.join(HERE, "hdri", f"{aid}_{res}.hdr"))
for aid in TEXTURES:
    print("tex", aid)
    d = files(aid)
    for m in MAPS:
        if m in d and "2k" in d[m]:
            fmt = "jpg" if "jpg" in d[m]["2k"] else next(iter(d[m]["2k"]))
            get(d[m]["2k"][fmt]["url"], os.path.join(HERE, "textures", aid, f"{aid}_{m}_2k.{fmt}"))
