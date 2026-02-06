import os
import shutil
from datetime import datetime

# MODIFY THESE PATHS IF NEEDED
SRC = "/data/data/ru.iiec.pydroid3/files/home/storage/downloads/artcrawler"
DST = "/storage/emulated/0/ArtCrawler"

# File extensions to sync
SYNC_EXT = {".py", ".kv", ".log"}

def sync():
    print("=== ArtCrawler Sync Script ===")
    print(f"Source:      {SRC}")
    print(f"Destination: {DST}")
    print(f"Extensions:  {', '.join(SYNC_EXT)}")
    print("================================\n")

    copied = 0
    skipped = 0
    created_dirs = 0

    for root, dirs, files in os.walk(SRC):
        rel = os.path.relpath(root, SRC)
        dst_root = os.path.join(DST, rel)

        # Ensure destination directory exists
        if not os.path.exists(dst_root):
            print(f"[DIR] Creating directory: {dst_root}")
            os.makedirs(dst_root, exist_ok=True)
            created_dirs += 1

        print(f"\n[DIR] Entering: {root}")

        for f in files:
            ext = os.path.splitext(f)[1].lower()
            src_file = os.path.join(root, f)
            dst_file = os.path.join(dst_root, f)

            if ext not in SYNC_EXT:
                print(f"  [SKIP] {f} (extension not in sync list)")
                skipped += 1
                continue

            # Compare timestamps if destination exists
            if os.path.exists(dst_file):
                src_mtime = os.path.getmtime(src_file)
                dst_mtime = os.path.getmtime(dst_file)

                if src_mtime <= dst_mtime:
                    print(f"  [SKIP] {f} (destination is newer or same)")
                    skipped += 1
                    continue

            print(f"  [COPY] {src_file} → {dst_file}")
            shutil.copy2(src_file, dst_file)
            copied += 1

    print("\n=== Sync Complete ===")
    print(f"Directories created: {created_dirs}")
    print(f"Files copied:        {copied}")
    print(f"Files skipped:       {skipped}")
    print("======================")

if __name__ == "__main__":
    sync()
