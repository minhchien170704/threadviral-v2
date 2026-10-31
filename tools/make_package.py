"""
Dong goi source code de gui cho AI/nguoi khac (Claude, ChatGPT...).

Bo sung: chi lay source + tai nguyen nho, bo qua file nhung / video xuat.
Tao file .zip nho (< 30MB) chua toan bo code.

Dung:
    python tools\\make_package.py
    python tools\\make_package.py --with-media     # kem them 1 video nen nen
"""
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Thu muc nen gui
INCLUDE_DIRS = [
    "tvcore",
    "tools",
    "assets",
    ".cursor",
]

# File nen gui
INCLUDE_FILES = [
    "server.py",
    "index.html",
    "app_launcher.py",
    "ThreadViral.spec",
    "Chay_ThreadViral_App.bat",
    "requirements.txt",
]

# File dinh kem
INCLUDE_EXTRA = [
    ".gitignore",
    ".gitattributes",
    "README.md",
]

# Thu muc/file loai bo (nang, sinh ra khi chay)
EXCLUDE_DIRS = {
    ".venv", "venv", "env", "node_modules", "__pycache__", ".git",
    "dist", "build", "outputs", "temp", "bin", "bgm", "backgrounds",
    "memes", ".pytest_cache", ".idea", ".vscode",
}

EXCLUDE_FILES = {
    "background.mp4", "*.mp4", "*.mov", "*.avi", "*.mkv", "*.webm",
    "*.exe", "*.dll", "*.pyd", "*.so", "*.zip", "*.7z", "*.rar",
    "*.pyc", "*.pyo", "*.log", "*.db", "*.sqlite",
    "*.aac", "*.mp3", "*.wav", "*.ogg", "*.m4a",
    "*.ttf", "*.otf", "*.woff", "*.woff2",
    "ThreadViral.spec",
}


def _skip_file(name: str) -> bool:
    import fnmatch
    for pat in EXCLUDE_FILES:
        if fnmatch.fnmatch(name, pat):
            return True
    return False


def _add_tree(zf: zipfile.ZipFile, base: Path, arc_root: str) -> int:
    """Them cay thu muc vao zip (bo qua file bi loai)."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames
                       if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for fn in filenames:
            if _skip_file(fn):
                continue
            full = Path(dirpath) / fn
            rel = full.relative_to(base)
            arc = f"{arc_root}/{rel.as_posix()}"
            try:
                zf.write(full, arc)
                count += 1
            except OSError as e:
                print(f"  [!] bo qua {full}: {e}")
    return count


def build(with_media: bool = False, out_name: str = "threadviral_source.zip"):
    out = ROOT / out_name
    if out.exists():
        out.unlink()

    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # File goc
        for f in INCLUDE_FILES:
            p = ROOT / f
            if p.exists():
                zf.write(p, f)
                total += 1
                print(f"  + {f}")
        for f in INCLUDE_EXTRA:
            p = ROOT / f
            if p.exists():
                zf.write(p, f)
                total += 1
                print(f"  + {f}")

        # Thu muc
        for d in INCLUDE_DIRS:
            p = ROOT / d
            if not p.exists():
                continue
            n = _add_tree(zf, p, d)
            total += n
            print(f"  + {d}/ ({n} files)")

        # File requirements.txt tu dong
        req = ROOT / "requirements.txt"
        if not req.exists():
            zf.writestr("requirements.txt", (
                "fastapi\n"
                "uvicorn[standard]\n"
                "playwright\n"
                "edge-tts\n"
                "Pillow\n"
                "requests\n"
                "google-genai\n"
            ))
            print("  + requirements.txt (auto)")

        # Video nen (tuy chon) - lay 1 file trong `backgrounds/`
        if with_media:
            bg_dir = ROOT / "backgrounds"
            bgs = sorted(bg_dir.glob("*.mp4")) if bg_dir.is_dir() else []
            if bgs:
                # Lay 15 giay dau + cat nho -> nen vua du
                print(f"  + backgrounds/{bgs[0].name} -> background.mp4 (15s dau)")
                import subprocess
                from tvcore.utils import ffmpeg_bin
                tmp = ROOT / "temp" / "_pkg_bg.mp4"
                tmp.parent.mkdir(exist_ok=True)
                subprocess.run(
                    [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel",
                     "error", "-ss", "0", "-t", "15", "-i", str(bgs[0]),
                     "-vf", "scale=720:-2", "-c:v", "libx264", "-crf", "30",
                     "-preset", "veryfast", "-c:a", "aac", "-b:a", "96k", str(tmp)],
                    check=False,
                )
                if tmp.exists() and tmp.stat().st_size > 0:
                    zf.write(tmp, "background.mp4")
                    total += 1
                    tmp.unlink(missing_ok=True)
            else:
                print("  [!] Khong tim thay video nen trong backgrounds/")

    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"\n  File: {out}")
    print(f"  Files: {total}")
    print(f"  Size: {size_mb:.2f} MB")
    if size_mb > 30:
        print("  [!] Van > 30MB - can bo sung exclusion")
    else:
        print("  [OK] Du dieu kien gui cho Claude (< 30MB)")
    return out


if __name__ == "__main__":
    with_media = "--with-media" in sys.argv
    build(with_media=with_media)
