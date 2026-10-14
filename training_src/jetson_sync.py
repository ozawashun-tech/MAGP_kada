import os
import subprocess
from pathlib import Path

# ~/.ssh/config のエイリアス、または user@host
JETSON_HOST = os.environ.get("MAGP_JETSON_HOST", "jetson")
# bag_recorder の save_directory と合わせる
JETSON_BAG_DIR = os.environ.get("MAGP_JETSON_BAG_DIR", "/home/jetson/rosbag").rstrip("/")
LOCAL_BAG_DIR = (Path(__file__).resolve().parent / "../dataset/train").resolve()

SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]


def _list_remote_bags():
    """
    Jetson上の記録済みbag名を古い順に返す（接続できない場合はNone）

    metadata.yamlは記録終了時に書かれるため、記録中のbagは含まれない
    """
    try:
        result = subprocess.run(
            ["ssh", *SSH_OPTIONS, JETSON_HOST, f"ls -1d {JETSON_BAG_DIR}/rosbag2_*/metadata.yaml"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"Warning: Could not connect to {JETSON_HOST}: {e}")
        return None

    # 255はssh自体の失敗（lsの失敗は該当bagなしとして扱う）
    if result.returncode == 255:
        print(f"Warning: Could not connect to {JETSON_HOST}: {result.stderr.strip()}")
        return None

    # bag名は rosbag2_%Y_%m_%d-%H_%M_%S_<counter> なので名前順 = 時刻順
    return sorted(Path(line).parent.name for line in result.stdout.splitlines() if line.strip())


def _list_local_bags():
    """ローカルの記録済みbagディレクトリを古い順に返す（サブフォルダも含む）"""
    bags = [p.parent for p in LOCAL_BAG_DIR.rglob("rosbag2_*/metadata.yaml")]
    return sorted(bags, key=lambda p: p.name)


def fetch_latest_bags(count=1):
    """
    Jetsonから最新のbagを取得し、ローカルのパスを返す

    Jetsonに接続できない場合は、ローカルにある最新のbagを返す

    Args:
        count: 取得するbagの数（新しい順）

    Returns:
        list of Path: ローカルのbagディレクトリのパス（古い順）
    """
    remote_bags = _list_remote_bags()

    if not remote_bags:
        if remote_bags is not None:
            print(f"Warning: No bags found in {JETSON_HOST}:{JETSON_BAG_DIR}")
        local_bags = _list_local_bags()[-count:]
        if not local_bags:
            raise FileNotFoundError(f"No bags found in {LOCAL_BAG_DIR}")
        print("Using latest LOCAL bags (not synced with Jetson):")
        for path in local_bags:
            print(f"  {path}")
        return local_bags

    bag_paths = []
    for name in remote_bags[-count:]:
        local_path = LOCAL_BAG_DIR / name
        local_path.mkdir(parents=True, exist_ok=True)
        print(f"Syncing {JETSON_HOST}:{JETSON_BAG_DIR}/{name} -> {local_path}")
        subprocess.run(
            [
                "rsync",
                "-a",
                "--partial",
                "-e",
                "ssh " + " ".join(SSH_OPTIONS),
                f"{JETSON_HOST}:{JETSON_BAG_DIR}/{name}/",
                f"{local_path}/",
            ],
            check=True,
        )
        bag_paths.append(local_path)

    return bag_paths


if __name__ == "__main__":
    for bag_path in fetch_latest_bags():
        print(bag_path)
