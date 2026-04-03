"""
Optimize a VRM 0.x model by removing unused morph targets.

Reads the GLB, identifies which morph targets to keep (VRM expression binds +
useful face expressions), removes the rest, compacts the binary buffer, and
writes a new smaller GLB file.

Usage:
    python tools/optimize_vrm.py <input.vrm> <output.vrm>
"""

import struct
import json
import sys
from pathlib import Path

# ── Morph targets to KEEP (by index in original) ────────────────────────
# VRM-bound (MUST keep — referenced by blendShapeGroups):
#   2=Blink, 4=BlinkHappy, 6=Wink, 7=WinkRight, 16=BambooShootEye,
#   27=HighlightErasure, 31=Anger, 37=Ah, 38=Ch, 39=U, 40=E, 41=Oh
#
# Useful extras (face expressions, eyebrows, mouth variants):
#   3=Blink2, 5=BlinkHappy2, 10=Stare, 11=Calm, 12=Close><, 13=Surprised,
#   28=Serious, 29=Sadness, 30=Cheerful, 33=EyebrowFront,
#   34=EyebrowUpperMove, 35=EyebrowLowerMove, 36=MouthSmall, 42=Hmm,
#   46=Grin, 48=Smile, 55=TeethErasure
KEEP_INDICES = {
    2, 3, 4, 5, 6, 7,              # blink/wink variants
    10, 11, 12, 13,                 # eye expressions
    16,                             # bamboo shoot eye (VRM)
    27, 28, 29, 30, 31,            # highlight/emotions
    33, 34, 35,                     # eyebrows
    36, 37, 38, 39, 40, 41, 42,    # mouth variants
    46, 48,                         # grin, smile
    55,                             # teeth erasure
}


def read_glb(path: str) -> tuple[dict, bytes]:
    """Parse a GLB file into JSON header and binary buffer."""
    with open(path, "rb") as f:
        magic, version, length = struct.unpack("<III", f.read(12))
        assert magic == 0x46546C67, "Not a valid GLB file"

        # JSON chunk
        json_len, json_type = struct.unpack("<II", f.read(8))
        assert json_type == 0x4E4F534A, "Expected JSON chunk"
        json_data = f.read(json_len).decode("utf-8")

        # Binary chunk
        bin_len, bin_type = struct.unpack("<II", f.read(8))
        assert bin_type == 0x004E4942, "Expected BIN chunk"
        bin_data = f.read(bin_len)

    return json.loads(json_data), bin_data


def write_glb(path: str, gltf: dict, bin_data: bytes):
    """Write a GLB file from JSON header and binary buffer."""
    json_bytes = json.dumps(gltf, ensure_ascii=False).encode("utf-8")
    # Pad JSON to 4-byte alignment with spaces
    while len(json_bytes) % 4 != 0:
        json_bytes += b" "
    # Pad binary to 4-byte alignment with zeros
    while len(bin_data) % 4 != 0:
        bin_data += b"\x00"

    total_length = 12 + 8 + len(json_bytes) + 8 + len(bin_data)

    with open(path, "wb") as f:
        # GLB header
        f.write(struct.pack("<III", 0x46546C67, 2, total_length))
        # JSON chunk
        f.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))
        f.write(json_bytes)
        # Binary chunk
        f.write(struct.pack("<II", len(bin_data), 0x004E4942))
        f.write(bin_data)


def optimize(input_path: str, output_path: str):
    print(f"Reading {input_path}...")
    gltf, bin_data = read_glb(input_path)

    mesh = gltf["meshes"][0]
    primitives = mesh["primitives"]
    num_prims = len(primitives)
    old_target_count = len(primitives[0].get("targets", []))

    print(f"  {num_prims} primitives, {old_target_count} morph targets each")
    print(f"  Keeping {len(KEEP_INDICES)} of {old_target_count} morph targets")

    # Build old→new index mapping
    sorted_keep = sorted(KEEP_INDICES)
    old_to_new = {old_idx: new_idx for new_idx, old_idx in enumerate(sorted_keep)}

    # Collect ALL accessor indices referenced by dropped morph targets
    drop_accessor_ids = set()
    keep_accessor_ids = set()

    for prim in primitives:
        targets = prim.get("targets", [])
        for t_idx, target in enumerate(targets):
            acc_ids = set(target.values())  # {"POSITION": accId, "NORMAL": accId}
            if t_idx in KEEP_INDICES:
                keep_accessor_ids.update(acc_ids)
            else:
                drop_accessor_ids.update(acc_ids)

    # Don't drop accessors that are also used by kept targets
    drop_accessor_ids -= keep_accessor_ids

    # Collect ALL referenced bufferView indices (from all accessors, images, etc.)
    all_bv_ids = set()
    drop_bv_ids = set()

    for acc_idx, acc in enumerate(gltf["accessors"]):
        bv = acc.get("bufferView")
        if bv is not None:
            if acc_idx in drop_accessor_ids:
                drop_bv_ids.add(bv)
            else:
                all_bv_ids.add(bv)

    # Also collect bufferViews referenced by images
    for img in gltf.get("images", []):
        bv = img.get("bufferView")
        if bv is not None:
            all_bv_ids.add(bv)

    # Don't drop bufferViews that are also used by other things
    drop_bv_ids -= all_bv_ids

    print(f"  Dropping {len(drop_accessor_ids)} accessors, {len(drop_bv_ids)} bufferViews")

    # ── Build new binary buffer (only keep referenced data) ──────────
    # Sort kept bufferViews by original offset for sequential copying
    kept_bv_indices = sorted(
        [i for i in range(len(gltf["bufferViews"])) if i not in drop_bv_ids],
        key=lambda i: gltf["bufferViews"][i].get("byteOffset", 0),
    )

    # Build old bufferView index → new index mapping
    bv_old_to_new = {old: new for new, old in enumerate(kept_bv_indices)}

    # Copy binary data and track new offsets
    new_bin = bytearray()
    new_buffer_views = []

    for old_bv_idx in kept_bv_indices:
        bv = gltf["bufferViews"][old_bv_idx].copy()
        offset = bv.get("byteOffset", 0)
        length = bv["byteLength"]

        # Align to 4 bytes
        while len(new_bin) % 4 != 0:
            new_bin.append(0)

        bv["byteOffset"] = len(new_bin)
        new_bin.extend(bin_data[offset : offset + length])
        new_buffer_views.append(bv)

    # Update buffer size
    gltf["buffers"][0]["byteLength"] = len(new_bin)

    # Replace bufferViews
    gltf["bufferViews"] = new_buffer_views

    # ── Remap accessor bufferView references ─────────────────────────
    # Build new accessor list (skip dropped ones)
    kept_acc_indices = [i for i in range(len(gltf["accessors"])) if i not in drop_accessor_ids]
    acc_old_to_new = {old: new for new, old in enumerate(kept_acc_indices)}

    new_accessors = []
    for old_acc_idx in kept_acc_indices:
        acc = gltf["accessors"][old_acc_idx].copy()
        old_bv = acc.get("bufferView")
        if old_bv is not None:
            acc["bufferView"] = bv_old_to_new[old_bv]
        new_accessors.append(acc)

    gltf["accessors"] = new_accessors

    # ── Remap all accessor references in primitives ──────────────────
    for prim in primitives:
        # Base attributes (POSITION, NORMAL, etc.)
        new_attrs = {}
        for attr_name, old_acc in prim.get("attributes", {}).items():
            new_attrs[attr_name] = acc_old_to_new[old_acc]
        prim["attributes"] = new_attrs

        # Indices
        if "indices" in prim:
            prim["indices"] = acc_old_to_new[prim["indices"]]

        # Morph targets — keep only selected, remap accessor indices
        old_targets = prim.get("targets", [])
        new_targets = []
        for t_idx in sorted_keep:
            if t_idx < len(old_targets):
                old_target = old_targets[t_idx]
                new_target = {}
                for attr_name, old_acc in old_target.items():
                    new_target[attr_name] = acc_old_to_new[old_acc]
                new_targets.append(new_target)
        prim["targets"] = new_targets

        # Update targetNames
        extras = prim.get("extras", {})
        old_names = extras.get("targetNames", [])
        if old_names:
            extras["targetNames"] = [old_names[i] for i in sorted_keep if i < len(old_names)]

    # ── Remap VRM blendShapeGroups morph target indices ──────────────
    vrm = gltf["extensions"]["VRM"]
    bsm = vrm.get("blendShapeMaster", {})
    for group in bsm.get("blendShapeGroups", []):
        new_binds = []
        for bind in group.get("binds", []):
            old_morph = bind["index"]
            if old_morph in old_to_new:
                bind["index"] = old_to_new[old_morph]
                new_binds.append(bind)
            else:
                print(f"  WARNING: VRM expression '{group['name']}' references dropped morph {old_morph}")
        group["binds"] = new_binds

    # ── Remap accessor references in skins, animations, etc. ─────────
    for skin in gltf.get("skins", []):
        if "inverseBindMatrices" in skin:
            skin["inverseBindMatrices"] = acc_old_to_new[skin["inverseBindMatrices"]]

    # Remap image bufferView references
    for img in gltf.get("images", []):
        old_bv = img.get("bufferView")
        if old_bv is not None:
            img["bufferView"] = bv_old_to_new[old_bv]

    # ── Write output ─────────────────────────────────────────────────
    new_bin = bytes(new_bin)
    old_size = Path(input_path).stat().st_size
    print(f"\n  Binary: {len(bin_data)/(1024*1024):.1f} MB -> {len(new_bin)/(1024*1024):.1f} MB")

    write_glb(output_path, gltf, new_bin)

    new_size = Path(output_path).stat().st_size
    saved = old_size - new_size
    print(f"  File: {old_size/(1024*1024):.1f} MB -> {new_size/(1024*1024):.1f} MB (saved {saved/(1024*1024):.1f} MB)")
    print(f"  Morph targets: {old_target_count} -> {len(sorted_keep)}")
    print(f"\nDone! Written to {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input.vrm> <output.vrm>")
        sys.exit(1)
    optimize(sys.argv[1], sys.argv[2])
