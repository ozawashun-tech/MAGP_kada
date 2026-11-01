.PHONY: clean build cache-clean run dagger dagger-mirror mirror new-session rollback help left right

# デフォルトターゲット
.DEFAULT_GOAL := help

# ディレクトリのクリーンアップ
clean:
	@echo "Cleaning install, build, and log directories..."
	rm -rf install build log
	@echo "Clean completed."

# 通常のビルド
build:
	@echo "Building with colcon..."
	colcon build --symlink-install

# キャッシュクリーンしてビルド
cache-clean:
	@echo "Cleaning cache and building..."
	colcon build --cmake-clean-cache

# セットアップとlaunchファイルの実行
run:
	@echo "Sourcing setup.bash and launching autonomous_driving..."
	bash -c "source install/setup.bash && ros2 launch launch/autonomous_driving.py"

# --- 以下は PC で実行する（SSH で Jetson（MAGP_JETSON_HOST、既定 jetson）に接続する） ---
PYTHON ?= python3

# 本番のコース（待機場所から見て左 / 右）。make dagger left / make dagger right のように、ターゲットのあとに書く
COURSE := $(filter left right,$(MAKECMDGOALS))
REQUIRE_COURSE = $(if $(filter 1,$(words $(COURSE))),,$(error make $@ left か make $@ right のようにコースを 1 つ指定してください))

# Jetson の新しい bag をそのコースとしてセッションに足し、そのコースの bag で学習する
# Jetson の origin_<コース>.pth を新しいモデルに付け替え、current.pth をその枠にする
# 続けて同じ bag を反転して学習し、mirror_<反対のコース>.pth も付け替える（current.pth は origin のまま）
dagger:
	$(REQUIRE_COURSE)
	cd training_src && $(PYTHON) train.py --session --course $(COURSE) --deploy --with-mirror

# そのコースで mirror_<コース> の枠を走らせて録った bag を「<コース> mirror」としてセッションに足し、
# mirror_<コース> と、対の origin_<反対のコース> を学習する（L2 補正が両方に入る）
# mirror の枠を先に送って current.pth もそちらにし、origin はあとから送る（current.pth は mirror のまま）
dagger-mirror:
	$(REQUIRE_COURSE)
	cd training_src && $(PYTHON) train.py --session --course $(COURSE) --deploy --mirror-run

# そのコースの bag を左右反転して学習し、Jetson の mirror_<反対のコース>.pth と current.pth を付け替える
# Jetson の新しい bag はセッションに足さない（反転したコースで録った bag を混ぜないため）
mirror:
	$(REQUIRE_COURSE)
	cd training_src && $(PYTHON) train.py --session --course $(COURSE) --mirror --deploy

# コースが変わったら実行する（今 Jetson にある bag は学習に使わず、モデルの枠も空にする）
new-session:
	cd training_src && $(PYTHON) jetson_sync.py new-session

# Jetson の origin_<コース>.pth を 1 つ前のモデルに戻す（もう一度実行すると元に戻る）
rollback:
	$(REQUIRE_COURSE)
	cd training_src && $(PYTHON) jetson_sync.py rollback $(COURSE)

# make dagger left などのコースの指定（それ自体は何もしない）
left right:
	@:

# ヘルプ
help:
	@echo "Available targets:"
	@echo "  make clean         - Remove install, build, and log directories"
	@echo "  make build         - Build with colcon (symlink-install)"
	@echo "  make cache-clean   - Clean CMake cache and build"
	@echo "  make run           - Source setup and launch autonomous_driving.py"
	@echo "  make dagger left|right        - [PC] Add new bags as left/right, train origin_<course> and mirror_<opposite>, and deploy to Jetson"
	@echo "  make dagger-mirror left|right - [PC] Add new bags recorded with mirror_<course>, train mirror_<course> and origin_<opposite>, and deploy"
	@echo "  make mirror left|right        - [PC] Train mirror_<opposite> from left/right bags and deploy"
	@echo "  make new-session   - [PC] Start a new bag session and clear the model slots on Jetson"
	@echo "  make rollback left|right      - [PC] Swap Jetson origin_<course>.pth and origin_<course>.prev.pth"
	@echo "  make help          - Show this help message"