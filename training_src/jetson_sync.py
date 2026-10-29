import os
import shlex
import subprocess
from datetime import datetime
from pathlib import Path

# ~/.ssh/config のエイリアス、または user@host
JETSON_HOST = os.environ.get("MAGP_JETSON_HOST", "jetson")
# bag_recorder の save_directory と合わせる
JETSON_BAG_DIR = os.environ.get("MAGP_JETSON_BAG_DIR", "/home/jetson/rosbag").rstrip("/")
LOCAL_BAG_DIR = (Path(__file__).resolve().parent / "../dataset/train").resolve()

# 推論ノード（launch/autonomous_driving.py）は JETSON_MODEL_DIR/current.pth を読む
JETSON_MODEL_DIR = os.environ.get("MAGP_JETSON_MODEL_DIR", "/home/jetson/models").rstrip("/")
CURRENT_LINK = "current.pth"
PREVIOUS_LINK = "previous.pth"
# 一度も deploy していないとき（current.pth が無いとき）に推論ノードが使うモデル
INITIAL_MODEL = "my_model.pth"

# 学習に使う bag の一覧。行頭が # の bag は使わない
SESSION_FILE = LOCAL_BAG_DIR / "session.txt"

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


def _rsync_bag(name):
    """Jetsonのbagを dataset/train/<name> に取ってきて、ローカルのパスを返す"""
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
    return local_path


def _run_remote(script):
    """Jetson でシェルスクリプトを実行する（失敗したら例外）"""
    result = subprocess.run(
        ["ssh", *SSH_OPTIONS, JETSON_HOST, "bash -s"],
        input=script,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        raise RuntimeError(f"{JETSON_HOST} での実行に失敗しました: {result.stderr.strip()}")


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

    return [_rsync_bag(name) for name in remote_bags[-count:]]


def _read_session():
    """session.txt から (使う bag, 書かれている全 bag) を返す"""
    used, known = [], set()
    for line in SESSION_FILE.read_text().splitlines():
        line = line.strip()
        excluded = line.startswith("#")
        name = line.lstrip("#").split()[0] if line.lstrip("#").strip() else ""
        if not name.startswith("rosbag2_"):
            continue
        known.add(name)
        if not excluded:
            used.append(name)
    return used, known


def new_session():
    """
    session.txt を作り直す（コースや走る向きが変わったときに使う）

    今 Jetson にある bag は # 付き（使わない）で書いておき、これ以降に記録した bag だけが
    sync_session() で自動的に足される。前の bag を使うときは、行頭の # を消す
    """
    remote_bags = _list_remote_bags()
    if remote_bags is None:
        raise ConnectionError(f"{JETSON_HOST} に接続できません")

    if SESSION_FILE.exists():
        backup = SESSION_FILE.with_name(f"session_{datetime.now():%Y%m%d_%H%M%S}.txt")
        SESSION_FILE.rename(backup)
        print(f"Old session saved to {backup}")

    lines = [
        f"# {datetime.now():%Y-%m-%d %H:%M} に作成",
        "# 学習に使う bag の一覧。行頭に # を付けた bag は使わない",
        "# 以下はセッション開始前の bag（使うときは # を消す）",
    ]
    lines += [f"# {name}" for name in remote_bags]
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text("\n".join(lines) + "\n")
    print(f"New session: {SESSION_FILE}（既存の bag {len(remote_bags)} 本は使わない）")


def sync_session():
    """
    Jetson の新しい bag を session.txt に書き足し、使う bag をすべて取ってきてパスを返す

    Returns:
        list of Path: ローカルの bag ディレクトリのパス（session.txt の順）
    """
    if not SESSION_FILE.exists():
        raise FileNotFoundError(f"{SESSION_FILE} がありません。先に make new-session を実行してください")

    remote_bags = _list_remote_bags()
    if remote_bags is None:
        raise ConnectionError(f"{JETSON_HOST} に接続できません")

    used, known = _read_session()
    new_bags = [name for name in remote_bags if name not in known]
    if new_bags:
        with SESSION_FILE.open("a") as f:
            f.write(f"# {datetime.now():%Y-%m-%d %H:%M} に追加\n")
            f.writelines(f"{name}\n" for name in new_bags)
        print(f"Added to session: {', '.join(new_bags)}")
        used += new_bags

    if not used:
        raise FileNotFoundError(f"{SESSION_FILE} に使う bag がありません")

    paths = []
    for name in used:
        if name in remote_bags:
            paths.append(_rsync_bag(name))
        elif (LOCAL_BAG_DIR / name / "metadata.yaml").exists():
            paths.append(LOCAL_BAG_DIR / name)
        else:
            raise FileNotFoundError(f"{name} が Jetson にもローカルにもありません")
    return paths


def deploy_model(local_path):
    """
    学習したモデルを Jetson の JETSON_MODEL_DIR に送り、current.pth をそれに付け替える

    既存のファイルは上書きしない。付け替える前の current.pth の行き先は previous.pth に残す
    （一度も deploy していなければ INITIAL_MODEL）。推論ノードは Manual 中に読み込み直す
    """
    local_path = Path(local_path)
    name = local_path.name
    part = f".{name}.part"
    print(f"Deploying {local_path} -> {JETSON_HOST}:{JETSON_MODEL_DIR}/{name}")
    subprocess.run(
        [
            "rsync",
            "-a",
            "-e",
            "ssh " + " ".join(SSH_OPTIONS),
            str(local_path),
            f"{JETSON_HOST}:{JETSON_MODEL_DIR}/{part}",
        ],
        check=True,
    )
    # 書き終わってから名前を付け、リンクも一時名で作ってから mv で置き換える
    # （推論ノードが書きかけのファイルを読まないように）
    q = shlex.quote
    _run_remote(f"""
set -e
cd {q(JETSON_MODEL_DIR)}
if [ -e {q(name)} ]; then rm -f -- {q(part)}; echo "{name} already exists" >&2; exit 1; fi
mv -- {q(part)} {q(name)}
if [ -L {CURRENT_LINK} ]; then prev=$(readlink {CURRENT_LINK})
elif [ -e {CURRENT_LINK} ]; then echo "{CURRENT_LINK} is not a symlink" >&2; exit 1
else prev={INITIAL_MODEL}; fi
ln -sfn -- "$prev" .{PREVIOUS_LINK}.tmp && mv -Tf .{PREVIOUS_LINK}.tmp {PREVIOUS_LINK}
ln -sfn -- {q(name)} .{CURRENT_LINK}.tmp && mv -Tf .{CURRENT_LINK}.tmp {CURRENT_LINK}
echo "{CURRENT_LINK} -> {name}（{PREVIOUS_LINK} -> $prev）"
""")
    print("Manual のまま 1 秒ほど待つと、推論ノードが新しいモデルを読み込みます")


def rollback_model():
    """current.pth と previous.pth の行き先を入れ替える（もう一度実行すると元に戻る）"""
    _run_remote(f"""
set -e
cd {shlex.quote(JETSON_MODEL_DIR)}
if [ ! -L {CURRENT_LINK} ] || [ ! -L {PREVIOUS_LINK} ]; then echo "no {PREVIOUS_LINK} to roll back to" >&2; exit 1; fi
cur=$(readlink {CURRENT_LINK}); prev=$(readlink {PREVIOUS_LINK})
if [ ! -e "$prev" ]; then echo "$prev does not exist" >&2; exit 1; fi
ln -sfn -- "$prev" .{CURRENT_LINK}.tmp && mv -Tf .{CURRENT_LINK}.tmp {CURRENT_LINK}
ln -sfn -- "$cur" .{PREVIOUS_LINK}.tmp && mv -Tf .{PREVIOUS_LINK}.tmp {PREVIOUS_LINK}
echo "{CURRENT_LINK} -> $prev（{PREVIOUS_LINK} -> $cur）"
""")


if __name__ == "__main__":
    import sys

    command = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    if command == "new-session":
        new_session()
    elif command == "rollback":
        rollback_model()
    elif command == "fetch":
        for bag_path in fetch_latest_bags():
            print(bag_path)
    else:
        sys.exit(f"usage: {sys.argv[0]} [fetch | new-session | rollback]")
