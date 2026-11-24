#!/usr/bin/env python3
"""Customize official Batocera images offline; never write to physical drives."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


TOOLS = ("losetup", "sfdisk", "sgdisk", "blkid", "mount", "umount",
         "e2fsck", "resize2fs", "unsquashfs", "mksquashfs", "rsync")


def run(*args, input=None, accepted=(0,)):
    result = subprocess.run([str(a) for a in args], input=input, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode not in accepted:
        raise RuntimeError(f"Commande échouée : {' '.join(map(str, args))}\n"
                           f"{result.stdout}{result.stderr}")
    return result.stdout.strip()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def set_conf(path, values):
    """Replace active/commented keys, remove duplicates, preserve other settings."""
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(values)
    output = []
    for line in lines:
        match = re.match(r"^\s*#?\s*([^#=\s]+)\s*=", line)
        key = match.group(1) if match else None
        if key in values:
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    output.extend(f"{key}={value}" for key, value in remaining.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(output) + "\n")


def set_es(path, color):
    root = ET.parse(path).getroot() if path.exists() else ET.Element("config")
    if root.tag != "config":
        raise ValueError(f"Racine XML inattendue : {path}")
    for node in list(root):
        if node.get("name") in ("ThemeColorSet", "AlternateSplashScreen"):
            root.remove(node)
    ET.SubElement(root, "string", name="ThemeColorSet", value=color)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def partition_layout(table):
    """Only accept the official, consecutive BOOT + SHARE disk layout."""
    if table["label"] not in ("gpt", "dos") or table.get("sectorsize", 512) != 512:
        raise ValueError("Table de partitions non prise en charge")
    parts = table["partitions"]
    if len(parts) != 2 or parts[0]["start"] >= parts[1]["start"]:
        raise ValueError("Deux partitions BOOT puis SHARE attendues")
    if parts[0]["start"] + parts[0]["size"] > parts[1]["start"]:
        raise ValueError("Partitions chevauchantes")
    return parts


def squash_options(image):
    with image.open("rb") as stream:
        header = stream.read(96)
    if len(header) != 96 or header[:4] != b"hsqs":
        raise ValueError("Fichier système non SquashFS")
    block = struct.unpack_from("<I", header, 12)[0]
    compression = struct.unpack_from("<H", header, 20)[0]
    compressors = {1: "gzip", 2: "lzma", 3: "lzo", 4: "xz", 5: "lz4", 6: "zstd"}
    if compression not in compressors:
        raise ValueError("Compression SquashFS inconnue")
    return compressors[compression], block


def safe_path(root, relative):
    """Reject system-image symlinks escaping the extracted root."""
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Chemin hors image : {relative}")
    return path


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o644)


def check_profile(profile, folder, arches):
    paths = {}
    for key in ("roms", "bios", "boot_logo", "splash_video", "es_splash", "output_dir"):
        paths[key] = (folder / profile[key]).resolve()
    for key in ("roms", "bios"):
        if not paths[key].is_dir():
            raise ValueError(f"Dossier manquant : {paths[key]}")
        # Absolute/escaping links from an old SHARE are not portable.
        for path in paths[key].rglob("*"):
            if path.is_symlink() and not path.resolve().is_relative_to(paths[key]):
                raise ValueError(f"Lien non portable : {path}")
    for key in ("boot_logo", "es_splash"):
        with paths[key].open("rb") as stream:
            if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                raise ValueError(f"Image PNG attendue : {paths[key]}")
    with paths["splash_video"].open("rb") as stream:
        if stream.read(12)[4:8] != b"ftyp":
            raise ValueError("Vidéo MP4 attendue")
    for key in ("language", "theme_colorset", "version"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", profile[key]):
            raise ValueError(f"Valeur invalide : {key}")
    size = profile["image_size_bytes"]
    if not isinstance(size, int) or not 1_000_000_000 <= size < 15_000_000_000:
        raise ValueError("Taille image attendue : entre 1 Go et moins de 15 Go décimaux")
    for arch in arches:
        entry = profile["images"][arch]
        source = (folder / entry["path"]).resolve()
        if not source.is_file() or not (source.name.endswith(".img") or source.name.endswith(".img.gz")):
            raise ValueError(f"Image .img ou .img.gz manquante : {source}")
        expected = entry["sha256"].lower()
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError(f"Renseigner SHA-256 de l'image {arch} dans profile.json")
        print(f"Vérification SHA-256 : {source}", flush=True)
        if digest(source) != expected:
            raise ValueError(f"SHA-256 incorrect : {source}")
        paths[arch] = source
    return paths


def customize(root, share, profile, paths):
    version_file = safe_path(root, "usr/share/batocera/batocera.version")
    version = version_file.read_text().strip()
    version_match = re.match(r"\d+", version)
    if not version_match or version_match.group() != profile["version"]:
        raise ValueError(f"Version image inattendue : {version}")
    theme = safe_path(root, "usr/share/emulationstation/themes")
    if not any(f'name="{profile["theme_colorset"]}"' in p.read_text()
               for p in theme.glob("*/theme.xml")):
        raise ValueError("Variante de couleur absente des thèmes intégrés")
    seed = safe_path(root, "usr/share/batocera/datainit")
    if not seed.is_dir():
        raise ValueError("Squelette userdata v44 absent")
    copy_file(paths["boot_logo"], safe_path(root, "usr/share/batocera/splash/boot-logo.png"))
    # Customize seed too: S12populateshare can copy newer default files at boot.
    for destination in (seed, share):
        conf = safe_path(destination, "system/batocera.conf")
        if destination == share and not conf.exists():
            copy_file(seed / "system/batocera.conf", conf)
        set_conf(conf, {
            "system.language": profile["language"],
            "splash.screen.enabled": "1",
            "es.customsargs": "--splash-image /userdata/system/splash/es-loading.png",
        })
        set_es(safe_path(destination, "system/configs/emulationstation/es_settings.cfg"),
               profile["theme_colorset"])
        copy_file(paths["es_splash"], safe_path(destination, "system/splash/es-loading.png"))
        splash = safe_path(destination, "splash")
        splash.mkdir(parents=True, exist_ok=True)
        for media in splash.iterdir():
            if media.is_file() and media.suffix.lower() in (".mp4", ".png", ".jpg", ".jpeg"):
                media.unlink()
        copy_file(paths["splash_video"], splash / "intro.mp4")
    for key in ("roms", "bios"):
        destination = safe_path(share, key)
        destination.mkdir(parents=True, exist_ok=True)
        run("rsync", "-aL", "--", str(paths[key]) + "/", str(destination) + "/")
    return version


def build(arch, profile, paths):
    output = paths["output_dir"]
    output.mkdir(parents=True, exist_ok=True)
    name = f"batocera-{arch}-{profile['version']}-custom.img.gz"
    final = output / name
    if final.exists() or (output / (name + ".manifest.json")).exists():
        raise ValueError(f"Sortie existante : {final}. Déplacer avant régénération.")
    work = Path(tempfile.mkdtemp(prefix=f".build-{arch}-", dir=output))
    image = work / "custom.img"
    loop = None
    mounted = []
    try:
        print(f"[{arch}] Décompression, agrandissement et montage", flush=True)
        opener = gzip.open if paths[arch].name.endswith(".gz") else open
        with opener(paths[arch], "rb") as source, image.open("wb") as target:
            shutil.copyfileobj(source, target, 8 * 1024 * 1024)
        if image.stat().st_size > profile["image_size_bytes"]:
            raise ValueError("Image officielle plus grande que taille cible ; réduction non automatique")
        with image.open("r+b") as target:
            target.truncate(profile["image_size_bytes"] // 512 * 512)
        loop = run("losetup", "--find", "--show", "--partscan", image)
        table = json.loads(run("sfdisk", "--json", loop))["partitiontable"]
        parts = partition_layout(table)
        boot_device, share_device = (part["node"] for part in parts)
        if run("blkid", "-s", "TYPE", "-o", "value", boot_device) != "vfat":
            raise ValueError("Partition BOOT FAT attendue")
        if run("blkid", "-s", "TYPE", "-o", "value", share_device) != "ext4":
            raise ValueError("Partition SHARE ext4 attendue")
        if table["label"] == "gpt":
            run("sgdisk", "-e", loop)
        # Empty size means all available space; preserve type, UUID, start and boot code.
        run("sfdisk", "--no-reread", "--no-tell-kernel", "--wipe", "never",
            "--wipe-partitions", "never", "-N", "2", loop, input=", +\n")
        run("losetup", "-d", loop)
        loop = None
        loop = run("losetup", "--find", "--show", "--partscan", image)
        parts = partition_layout(json.loads(run("sfdisk", "--json", loop))["partitiontable"])
        boot_device, share_device = (part["node"] for part in parts)
        if parts[1]["size"] * 512 < 1024 ** 3:
            raise ValueError("SHARE trop petite : au moins 1 Gio requis")
        run("e2fsck", "-f", "-p", share_device, accepted=(0, 1))
        run("resize2fs", share_device)
        boot, share = work / "boot", work / "share"
        for device, folder in ((boot_device, boot), (share_device, share)):
            folder.mkdir()
            run("mount", "-o", "nosuid,nodev,noexec", device, folder)
            mounted.append(folder)
        squash = boot / "boot/batocera"
        compressor, block = squash_options(squash)
        root = work / "root"
        print(f"[{arch}] Extraction et personnalisation système ({compressor})", flush=True)
        run("unsquashfs", "-no-progress", "-d", root, squash)
        version = customize(root, share, profile, paths)
        # v44 S02resize grows the partition but does not call resize2fs when
        # skipping formatting. Keep preloaded SHARE fixed instead of relying on it.
        set_conf(boot / "batocera-boot.conf", {
            "autoresize": "false", "sharedevice": "INTERNAL", "splash.screen.enabled": "1",
        })
        bootconf = boot / "batocera-boot.conf"
        if re.search(r"^\s*format-internal\s*=", bootconf.read_text(), re.M):
            raise ValueError("Déclencheur format-internal inattendu dans image officielle")
        rebuilt = work / "batocera.squashfs"
        run("mksquashfs", root, rebuilt, "-noappend", "-comp", compressor,
            "-b", block, "-no-progress")
        run("unsquashfs", "-s", rebuilt)
        # Delete old squash before copying: FAT needs space for only one copy.
        squash.unlink()
        shutil.copyfile(rebuilt, squash)
        os.sync()
        for folder in reversed(mounted.copy()):
            run("umount", folder)
            mounted.remove(folder)
        run("e2fsck", "-f", "-p", share_device, accepted=(0, 1))
        run("losetup", "-d", loop)
        loop = None
        print(f"[{arch}] Compression finale", flush=True)
        compressed = work / name
        with image.open("rb") as source, compressed.open("wb") as target:
            with gzip.GzipFile(filename="", mode="wb", compresslevel=6, fileobj=target, mtime=0) as gz:
                shutil.copyfileobj(source, gz, 8 * 1024 * 1024)
        manifest = {
            "architecture": arch, "batocera_version": version, "profile": profile,
            "base_sha256": profile["images"][arch]["sha256"],
            "image_size_bytes": image.stat().st_size,
            "output_sha256": digest(compressed),
            "assets_sha256": {key: digest(paths[key]) for key in
                              ("boot_logo", "splash_video", "es_splash")},
        }
        compressed.rename(final)
        (output / (name + ".manifest.json")).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        print(f"Image prête : {final}", flush=True)
    finally:
        for folder in reversed(mounted.copy()):
            try:
                run("umount", folder)
                mounted.remove(folder)
            except RuntimeError as error:
                print(error, file=sys.stderr)
        if loop and not mounted:
            try:
                run("losetup", "-d", loop)
                loop = None
            except RuntimeError as error:
                print(error, file=sys.stderr)
        if not mounted and not loop:
            shutil.rmtree(work)
        else:
            print(f"Nettoyage incomplet : conserver {work}, montage {mounted}, loop {loop}",
                  file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=Path(__file__).with_name("profile.json"))
    parser.add_argument("--arch", choices=("x86_64", "rpi3", "all"), default="all")
    parser.add_argument("--check", action="store_true", help="Valider entrées sans montage ni root")
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text())
    arches = ["x86_64", "rpi3"] if args.arch == "all" else [args.arch]
    paths = check_profile(profile, args.profile.resolve().parent, arches)
    missing = [tool for tool in TOOLS if not shutil.which(tool)]
    if missing:
        raise ValueError(f"Outils manquants : {', '.join(missing)}")
    if args.check:
        print("Entrées et outils validés. Structure interne vérifiée pendant génération.")
        return
    if os.geteuid() != 0:
        raise ValueError("Génération : lancer avec sudo python3 build_image.py")
    for arch in arches:
        build(arch, profile, paths)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, ET.ParseError, KeyError) as error:
        print(f"Erreur : {error}", file=sys.stderr)
        sys.exit(1)
