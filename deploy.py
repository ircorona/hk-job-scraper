"""Deploy frontend/dist/ to Hostinger via FTP.

Usage:
    python deploy.py              # deploy dist/ to /hk-jobs/
    python deploy.py --dry-run    # list files without uploading
"""

import os
import sys
import argparse
from ftplib import FTP, FTP_TLS
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

FTP_HOST = os.getenv("FTP_HOST")
FTP_USER = os.getenv("FTP_USER")
FTP_PASS = os.getenv("FTP_PASS")
REMOTE_BASE = "/hk-jobs"
LOCAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")


def ensure_remote_dir(ftp: FTP, path: str):
    """Create remote directory tree if it doesn't exist."""
    parts = path.strip("/").split("/")
    current = ""
    for part in parts:
        current += f"/{part}"
        try:
            ftp.cwd(current)
        except Exception:
            ftp.mkd(current)
            ftp.cwd(current)


def deploy(dry_run: bool = False):
    if not FTP_HOST or not FTP_USER or not FTP_PASS:
        print("ERROR: FTP_HOST, FTP_USER, and FTP_PASS must be set in .env")
        sys.exit(1)

    if not os.path.isdir(LOCAL_DIR):
        print(f"ERROR: Build directory not found: {LOCAL_DIR}")
        print("Run: cd frontend && npm run build")
        sys.exit(1)

    # Collect files
    files = []
    for root, dirs, filenames in os.walk(LOCAL_DIR):
        for fname in filenames:
            local_path = os.path.join(root, fname)
            rel_path = os.path.relpath(local_path, LOCAL_DIR).replace("\\", "/")
            remote_path = f"{REMOTE_BASE}/{rel_path}"
            files.append((local_path, remote_path))

    print(f"Deploying {len(files)} files to {FTP_HOST}:{REMOTE_BASE}/")

    if dry_run:
        for local, remote in files:
            size = os.path.getsize(local)
            print(f"  {remote} ({size:,} bytes)")
        print(f"\nDry run complete. {len(files)} files would be uploaded.")
        return

    # Connect and upload
    # Hostinger requires explicit FTPS (AUTH TLS on port 21): as of 2026-10-06
    # plain FTP logs in, then the server resets the connection on the first command.
    ftp = FTP_TLS()
    ftp.connect(FTP_HOST, 21, timeout=30)
    ftp.login(FTP_USER, FTP_PASS)
    ftp.prot_p()
    print(f"Connected as {FTP_USER} (FTPS)")

    uploaded = 0
    for local, remote in files:
        remote_dir = "/".join(remote.split("/")[:-1])
        ensure_remote_dir(ftp, remote_dir)
        ftp.cwd("/")

        with open(local, "rb") as f:
            ftp.storbinary(f"STOR {remote}", f)
        uploaded += 1
        size = os.path.getsize(local)
        print(f"  [{uploaded}/{len(files)}] {remote} ({size:,} bytes)")

    ftp.quit()
    print(f"\nDeploy: expected {len(files)}, uploaded {uploaded}")
    if uploaded != len(files):
        sys.exit(1)
    print(f"Done! {uploaded} files deployed to {FTP_HOST}:{REMOTE_BASE}/")
    print(f"Live at: https://climbthesearches.com/hk-jobs/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy HK Jobs frontend to Hostinger")
    parser.add_argument("--dry-run", action="store_true", help="List files without uploading")
    args = parser.parse_args()
    deploy(dry_run=args.dry_run)
