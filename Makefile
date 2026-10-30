.PHONY: clean build cache-clean run dagger mirror new-session rollback help c cc

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

# 走る向き。make dagger c（時計回り）/ make dagger cc（反時計回り）のように、ターゲットのあとに書く
DIRECTION := $(filter c cc,$(MAKECMDGOALS))
REQUIRE_DIRECTION = $(if $(filter 1,$(words $(DIRECTION))),,$(error make $@ c か make $@ cc のように向きを 1 つ指定してください))

# Jetson の新しい bag をその向きとしてセッションに足し、その向きの bag で学習する
# Jetson の origin_<向き>.pth を新しいモデルに付け替え、current.pth をその枠にする
dagger:
	$(REQUIRE_DIRECTION)
	cd training_src && $(PYTHON) train.py --session --direction $(DIRECTION) --deploy

# その向きの bag を左右反転して学習し、Jetson の mirror_<反対の向き>.pth と current.pth を付け替える
# Jetson の新しい bag はセッションに足さない（反転したコースで録った bag を混ぜないため）
mirror:
	$(REQUIRE_DIRECTION)
	cd training_src && $(PYTHON) train.py --session --direction $(DIRECTION) --mirror --deploy

# コースが変わったら実行する（今 Jetson にある bag は学習に使わず、モデルの枠も空にする）
new-session:
	cd training_src && $(PYTHON) jetson_sync.py new-session

# Jetson の origin_<向き>.pth を 1 つ前のモデルに戻す（もう一度実行すると元に戻る）
rollback:
	$(REQUIRE_DIRECTION)
	cd training_src && $(PYTHON) jetson_sync.py rollback $(DIRECTION)

# make dagger c などの向きの指定（それ自体は何もしない）
c cc:
	@:

# ヘルプ
help:
	@echo "Available targets:"
	@echo "  make clean         - Remove install, build, and log directories"
	@echo "  make build         - Build with colcon (symlink-install)"
	@echo "  make cache-clean   - Clean CMake cache and build"
	@echo "  make run           - Source setup and launch autonomous_driving.py"
	@echo "  make dagger c|cc   - [PC] Add new bags as c/cc, train origin_<dir>, and deploy to Jetson"
	@echo "  make mirror c|cc   - [PC] Train mirror_<opposite dir> from c/cc bags and deploy"
	@echo "  make new-session   - [PC] Start a new bag session and clear the model slots on Jetson"
	@echo "  make rollback c|cc - [PC] Swap Jetson origin_<dir>.pth and origin_<dir>.prev.pth"
	@echo "  make help          - Show this help message"