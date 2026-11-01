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

# 本番のコース（待機場所から見て左 / 右。左右対称で、走る向きも鏡写し）。make dagger left / right で指定し、
# session.txt の bag の行にも書く。片方のコースの走りを左右反転すると、もう片方のコースの走りになる
COURSES = ("left", "right")
# Jetson のモデルの枠。JETSON_MODEL_DIR/<枠>.pth が実体へのリンクで、current.pth は最後に
# deploy・rollback した枠のリンクを指す。推論ノードは Manual 中に R1 でこの順に切り替える
SLOTS = ["origin_left", "origin_right", "mirror_left", "mirror_right"]
# origin の枠の 1 つ前のモデル（make rollback left / right で戻す）
PREV_SUFFIX = ".prev"

# 学習に使う bag とコースの一覧。行頭が # の bag は使わない
SESSION_FILE = LOCAL_BAG_DIR / "session.txt"

SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]


def opposite(course):
    """反対のコース（left ↔ right）"""
    return "right" if course == "left" else "left"


def slot_name(course, mirror=False):
    """
    学習に使う bag のコースから、モデルの枠の名前を返す

    反転したモデルは反対のコースを走るので、left の bag から作ると mirror_right になる
    """
    if mirror:
        return f"mirror_{opposite(course)}"
    return f"origin_{course}"


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
    """
    session.txt から (使う bag の (名前, コース, mirror で走ったか) の組, 書かれている全 bag) を返す

    3 つ目に mirror と書いた bag は、そのコースで mirror の枠を走らせて録った bag
    """
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
        course = words[1] if len(words) > 1 else None
        if course not in COURSES:
            print(f"Warning: {name} のコース（left / right）が書かれていないので使いません。{SESSION_FILE} に書き足してください")
            continue
        used.append((name, course, words[2:3] == ["mirror"]))
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
    sync_session() で自動的に足される。前の bag を使うときは、行頭の # を消してコースを書く
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
        "# bag 名のあとに走ったコース（待機場所から見て left: 左 / right: 右）を書く。make dagger left / right が書き足す",
        "# 3 つ目の mirror は、そのコースで mirror の枠を走らせて録った bag（make dagger-mirror left / right が書き足す）。",
        "# 反対のコースの bag と一緒に、反対のコースの origin と、そのコースの mirror の学習に使う",
        "# 以下はセッション開始前の bag（使うときは # を消し、コースを書く）",
    ]
    lines += [f"# {name}" for name in remote_bags]
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text("\n".join(lines) + "\n")
    print(f"New session: {SESSION_FILE}（既存の bag {len(remote_bags)} 本は使わない）")


def sync_session(course, add_new=True, mirror_run=False):
    """
    Jetson の新しい bag を session.txt に書き足し、origin_<course> と対の mirror の学習に使う bag を
    取ってきて返す

    使うのは、コースが course の bag と、反対のコースで mirror の枠（mirror_<反対のコース>）を
    走らせて録った bag（session.txt で「<反対のコース> mirror」）。後者は origin の学習では反転して使う

    Args:
        course: "left"（左）か "right"（右）。origin の枠のコース
        add_new: False なら新しい bag を書き足さず、session.txt にある bag だけを使う
            （反転モデルだけを作るとき。どのコースで録ったかわからない bag を混ぜないため）
        mirror_run: True なら新しい bag を「<反対のコース> mirror」として書き足す。False なら「<course>」

    Returns:
        list of (Path, bool): ローカルの bag ディレクトリのパスと、mirror で走って録った bag か
        （session.txt の順）
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
        # mirror で走ったコースは、origin の枠の反対
        driven = opposite(course) if mirror_run else course
        tag = f"{driven} mirror" if mirror_run else driven
        with SESSION_FILE.open("a") as f:
            f.write(f"# {datetime.now():%Y-%m-%d %H:%M} に追加\n")
            f.writelines(f"{name} {tag}\n" for name in new_bags)
        print(f"Added to session as '{tag}': {', '.join(new_bags)}")
        print(f"  前回のあとに録った bag をすべて '{tag}' として足しました。違う bag があれば {SESSION_FILE} を直してください")
        used += [(name, driven, mirror_run) for name in new_bags]

    used = [(name, m) for name, c, m in used if c == (opposite(course) if m else course)]
    if not used:
        raise FileNotFoundError(f"{SESSION_FILE} に {slot_name(course)} の学習に使う bag がありません")

    bags = []
    for name, m in used:
        if name in remote_bags:
            bags.append((_rsync_bag(name), m))
        elif (LOCAL_BAG_DIR / name / "metadata.yaml").exists():
            bags.append((LOCAL_BAG_DIR / name, m))
        else:
            raise FileNotFoundError(f"{name} が Jetson にもローカルにもありません")
    return bags


def _deploy_script(name, slot, model_dir=JETSON_MODEL_DIR, switch_current=True):
    """送った .{name}.part に名前を付け、枠 <slot>.pth と current.pth（switch_current のとき）を付け替えるスクリプト"""
    q = shlex.quote
    part = f".{name}.part"
    link = f"{slot}.pth"
    prev = f"{slot}{PREV_SUFFIX}.pth"
    # origin の枠だけ、1 つ前のモデルを残す（make rollback left / right で戻す）
    keep_prev = (
        f'if [ -L {link} ]; then ln -sfn -- "$(readlink {link})" .{prev}.tmp && mv -Tf .{prev}.tmp {prev}; fi'
        if slot.startswith("origin_")
        else ""
    )
    if switch_current:
        switch = f"ln -sfn -- {link} .{CURRENT_LINK}.tmp && mv -Tf .{CURRENT_LINK}.tmp {CURRENT_LINK}"
        done = f'echo "{link} -> {name}（{CURRENT_LINK} -> {link}）"'
    else:
        switch = ""
        done = f'echo "{link} -> {name}（{CURRENT_LINK} はそのまま）"'
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
{switch}
{done}
"""


def deploy_model(local_path, slot, switch_current=True):
    """
    学習したモデルを Jetson の JETSON_MODEL_DIR に送り、枠 <slot>.pth と current.pth を付け替える

    既存のファイルは上書きしない。origin の枠は、前の行き先を <slot>.prev.pth に残す。
    推論ノードは Manual 中に、current.pth が指す枠（送ったモデル）に切り替える。
    switch_current=False なら current.pth は付け替えず、車は今の枠のまま
    （R1 でこの枠を選んでいれば、Manual 中に新しいモデルを読み込む）
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
    _run_remote(_deploy_script(name, slot, switch_current=switch_current))
    if switch_current:
        print("Manual のまま 1 秒ほど待つと、推論ノードが新しいモデルを読み込みます")
    else:
        print(f"車は今の枠のまま。Manual 中に R1 で {slot} を選ぶと、このモデルで走ります")


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


def rollback_model(course):
    """origin の枠を 1 つ前のモデルに戻し、その枠に切り替える（もう一度実行すると元に戻る）"""
    _run_remote(_rollback_script(slot_name(course)))


if __name__ == "__main__":
    import sys

    command = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    if command == "new-session":
        new_session()
    elif command == "rollback" and len(sys.argv) == 3 and sys.argv[2] in COURSES:
        rollback_model(sys.argv[2])
    elif command == "fetch":
        for bag_path in fetch_latest_bags():
            print(bag_path)
    else:
        sys.exit(f"usage: {sys.argv[0]} [fetch | new-session | rollback left|right]")
