.PHONY: clean build cache-clean run dagger new-session rollback help

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

# Jetson の新しい bag をセッションに足して学習し、Jetson の current.pth を新しいモデルに付け替える
dagger:
	cd training_src && $(PYTHON) train.py --session --deploy

# コースや走る向きが変わったら実行する（今 Jetson にある bag は学習に使わない）
new-session:
	cd training_src && $(PYTHON) jetson_sync.py new-session

# Jetson の current.pth を 1 つ前のモデルに戻す（もう一度実行すると元に戻る）
rollback:
	cd training_src && $(PYTHON) jetson_sync.py rollback

# ヘルプ
help:
	@echo "Available targets:"
	@echo "  make clean         - Remove install, build, and log directories"
	@echo "  make build         - Build with colcon (symlink-install)"
	@echo "  make cache-clean   - Clean CMake cache and build"
	@echo "  make run           - Source setup and launch autonomous_driving.py"
	@echo "  make dagger        - [PC] Sync session bags, train, and deploy to Jetson"
	@echo "  make new-session   - [PC] Start a new bag session (course or direction changed)"
	@echo "  make rollback      - [PC] Swap Jetson current.pth and previous.pth"
	@echo "  make help          - Show this help message"