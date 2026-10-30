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

# コースを走る向き（make dagger c / cc）。session.txt の bag の行にも書く
DIRECTIONS = {"c": "clockwise", "cc": "counter_clockwise"}
# Jetson のモデルの枠。JETSON_MODEL_DIR/<枠>.pth が実体へのリンクで、current.pth は最後に
# deploy・rollback した枠のリンクを指す。推論ノードは Manual 中に R1 でこの順に切り替える
SLOTS = ["origin_clockwise", "origin_counter_clockwise", "mirror_clockwise", "mirror_counter_clockwise"]
# origin の枠の 1 つ前のモデル（make rollback c / cc で戻す）
PREV_SUFFIX = ".prev"

# 学習に使う bag と向きの一覧。行頭が # の bag は使わない
SESSION_FILE = LOCAL_BAG_DIR / "session.txt"

SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]


def slot_name(direction, mirror=False):
    """
    学習に使う bag の向きから、モデルの枠の名前を返す

    反転したモデルは反対の向きのコースを走るので、c の bag から作ると mirror_counter_clockwise になる
    """
    if mirror:
        return f"mirror_{DIRECTIONS['cc' if direction == 'c' else 'c']}"
    return f"origin_{DIRECTIONS[direction]}"


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
    """session.txt から (使う bag と向きの組, 書かれている全 bag) を返す"""
    used, known = [], set()
    for line in SESSION_FILE.read_text().splitlines():
        line = line.strip()
        excluded = line.startswith("#")
        words = line.lstrip("#").split()
        if not words or not words[0].startswith("rosbag2_"):
            continue
        name = words[0]
        known.add(name)
        if excluded:
            continue
        direction = words[1] if len(words) > 1 else None
        if direction not in DIRECTIONS:
            print(f"Warning: {name} の向き（c / cc）が書かれていないので使いません。{SESSION_FILE} に書き足してください")
            continue
        used.append((name, direction))
    return used, known


def _new_session_script(model_dir=JETSON_MODEL_DIR):
    """
    Jetson のモデルの枠を空にするスクリプト（ファイルは消さない）

    current.pth が枠を指していれば、その実体を直接指すように張り替える（枠を消しても同じモデルで走る）
    """
    q = shlex.quote
    links = " ".join(f"{slot}.pth {slot}{PREV_SUFFIX}.pth" for slot in SLOTS)
    slot_links = " ".join(f"{slot}.pth" for slot in SLOTS)
    return f"""
set -e
[ -d {q(model_dir)} ] || exit 0
cd {q(model_dir)}
if [ -L {CURRENT_LINK} ]; then
  target=$(readlink {CURRENT_LINK})
  for s in {slot_links}; do
    if [ "$target" = "$s" ] && [ -L "$s" ]; then
      real=$(readlink -- "$s")
      ln -sfn -- "$real" .{CURRENT_LINK}.tmp && mv -Tf .{CURRENT_LINK}.tmp {CURRENT_LINK}
      echo "{CURRENT_LINK} -> $real"
    fi
  done
fi
for f in {links}; do
  if [ -L "$f" ]; then rm -f -- "$f"; echo "removed $f"; fi
done
"""


def new_session():
    """
    session.txt を作り直し、Jetson のモデルの枠を空にする（コースが変わったときに使う）

    今 Jetson にある bag は # 付き（使わない）で書いておき、これ以降に記録した bag だけが
    sync_session() で自動的に足される。前の bag を使うときは、行頭の # を消して向きを書く
    """
    remote_bags = _list_remote_bags()
    if remote_bags is None:
        raise ConnectionError(f"{JETSON_HOST} に接続できません")

    _run_remote(_new_session_script())

    if SESSION_FILE.exists():
        backup = SESSION_FILE.with_name(f"session_{datetime.now():%Y%m%d_%H%M%S}.txt")
        SESSION_FILE.rename(backup)
        print(f"Old session saved to {backup}")

    lines = [
        f"# {datetime.now():%Y-%m-%d %H:%M} に作成",
        "# 学習に使う bag の一覧。行頭に # を付けた bag は使わない",
        "# bag 名のあとに走った向き（c: 時計回り / cc: 反時計回り）を書く。make dagger c / cc が書き足す",
        "# 以下はセッション開始前の bag（使うときは # を消し、向きを書く）",
    ]
    lines += [f"# {name}" for name in remote_bags]
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text("\n".join(lines) + "\n")
    print(f"New session: {SESSION_FILE}（既存の bag {len(remote_bags)} 本は使わない）")


def sync_session(direction, add_new=True):
    """
    Jetson の新しい bag を session.txt に書き足し、向きが direction の bag を取ってきてパスを返す

    Args:
        direction: "c"（時計回り）か "cc"（反時計回り）。新しい bag はこの向きとして書き足す
        add_new: False なら新しい bag を書き足さず、session.txt にある bag だけを使う
            （反転モデルを作るとき。反転したコースで録った bag を混ぜないため）

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
    if new_bags and not add_new:
        print(f"Not added to session (add_new=False): {', '.join(new_bags)}")
    elif new_bags:
        with SESSION_FILE.open("a") as f:
            f.write(f"# {datetime.now():%Y-%m-%d %H:%M} に追加\n")
            f.writelines(f"{name} {direction}\n" for name in new_bags)
        print(f"Added to session as {direction} ({DIRECTIONS[direction]}): {', '.join(new_bags)}")
        print(f"  前回のあとに録った bag をすべて {direction} として足しました。違う向きの bag があれば {SESSION_FILE} を直してください")
        used += [(name, direction) for name in new_bags]

    used = [name for name, d in used if d == direction]
    if not used:
        raise FileNotFoundError(f"{SESSION_FILE} に {direction}（{DIRECTIONS[direction]}）の bag がありません")

    paths = []
    for name in used:
        if name in remote_bags:
            paths.append(_rsync_bag(name))
        elif (LOCAL_BAG_DIR / name / "metadata.yaml").exists():
            paths.append(LOCAL_BAG_DIR / name)
        else:
            raise FileNotFoundError(f"{name} が Jetson にもローカルにもありません")
    return paths


def _deploy_script(name, slot, model_dir=JETSON_MODEL_DIR):
    """送った .{name}.part に名前を付け、枠 <slot>.pth と current.pth を付け替えるスクリプト"""
    q = shlex.quote
    part = f".{name}.part"
    link = f"{slot}.pth"
    prev = f"{slot}{PREV_SUFFIX}.pth"
    # origin の枠だけ、1 つ前のモデルを残す（make rollback c / cc で戻す）
    keep_prev = (
        f'if [ -L {link} ]; then ln -sfn -- "$(readlink {link})" .{prev}.tmp && mv -Tf .{prev}.tmp {prev}; fi'
        if slot.startswith("origin_")
        else ""
    )
    # 書き終わってから名前を付け、リンクも一時名で作ってから mv で置き換える
    # （推論ノードが書きかけのファイルを読まないように）
    return f"""
set -e
cd {q(model_dir)}
if [ -e {q(name)} ]; then rm -f -- {q(part)}; echo "{name} already exists" >&2; exit 1; fi
if [ -e {CURRENT_LINK} ] && [ ! -L {CURRENT_LINK} ]; then rm -f -- {q(part)}; echo "{CURRENT_LINK} is not a symlink" >&2; exit 1; fi
mv -- {q(part)} {q(name)}
{keep_prev}
ln -sfn -- {q(name)} .{link}.tmp && mv -Tf .{link}.tmp {link}
ln -sfn -- {link} .{CURRENT_LINK}.tmp && mv -Tf .{CURRENT_LINK}.tmp {CURRENT_LINK}
echo "{link} -> {name}（{CURRENT_LINK} -> {link}）"
"""


def deploy_model(local_path, slot):
    """
    学習したモデルを Jetson の JETSON_MODEL_DIR に送り、枠 <slot>.pth と current.pth を付け替える

    既存のファイルは上書きしない。origin の枠は、前の行き先を <slot>.prev.pth に残す。
    推論ノードは Manual 中に、current.pth が指す枠（送ったモデル）に切り替える
    """
    if slot not in SLOTS:
        raise ValueError(f"unknown slot: {slot}")
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
    _run_remote(_deploy_script(name, slot))
    print("Manual のまま 1 秒ほど待つと、推論ノードが新しいモデルを読み込みます")


def _rollback_script(slot, model_dir=JETSON_MODEL_DIR):
    """枠 <slot>.pth と <slot>.prev.pth の行き先を入れ替え、current.pth をその枠にするスクリプト"""
    link = f"{slot}.pth"
    prev = f"{slot}{PREV_SUFFIX}.pth"
    return f"""
set -e
cd {shlex.quote(model_dir)}
if [ ! -L {link} ] || [ ! -L {prev} ]; then echo "no {prev} to roll back to" >&2; exit 1; fi
if [ -e {CURRENT_LINK} ] && [ ! -L {CURRENT_LINK} ]; then echo "{CURRENT_LINK} is not a symlink" >&2; exit 1; fi
cur=$(readlink {link}); old=$(readlink {prev})
if [ ! -e "$old" ]; then echo "$old does not exist" >&2; exit 1; fi
ln -sfn -- "$old" .{link}.tmp && mv -Tf .{link}.tmp {link}
ln -sfn -- "$cur" .{prev}.tmp && mv -Tf .{prev}.tmp {prev}
ln -sfn -- {link} .{CURRENT_LINK}.tmp && mv -Tf .{CURRENT_LINK}.tmp {CURRENT_LINK}
echo "{link} -> $old（{prev} -> $cur、{CURRENT_LINK} -> {link}）"
"""


def rollback_model(direction):
    """origin の枠を 1 つ前のモデルに戻し、その枠に切り替える（もう一度実行すると元に戻る）"""
    _run_remote(_rollback_script(slot_name(direction)))


if __name__ == "__main__":
    import sys

    command = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    if command == "new-session":
        new_session()
    elif command == "rollback" and len(sys.argv) == 3 and sys.argv[2] in DIRECTIONS:
        rollback_model(sys.argv[2])
    elif command == "fetch":
        for bag_path in fetch_latest_bags():
            print(bag_path)
    else:
        sys.exit(f"usage: {sys.argv[0]} [fetch | new-session | rollback c|cc]")
