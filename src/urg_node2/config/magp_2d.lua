-- "map_builder.lua"と"trajectory_builder.lua"の設定ファイルをインクルードする
include "map_builder.lua"
include "trajectory_builder.lua"

-- オプション設定の開始
options = {
  map_builder = MAP_BUILDER,  -- 地図生成の設定オブジェクト
  trajectory_builder = TRAJECTORY_BUILDER,  -- 軌跡生成の設定オブジェクト
  map_frame = "map",  -- 地図の基準フレームの名前
  tracking_frame = "base_link",  -- ロボットの位置を追跡するためのフレーム
  published_frame = "base_link",  -- 発行するフレームの名前
  odom_frame = "odom",  -- オドメトリの基準フレームの名前
  provide_odom_frame = false,  -- 自動的にオドメトリフレームを生成するかどうか
  publish_frame_projected_to_2d = false,  -- 発行フレームを2次元に投影するか
  use_odometry = false,  -- オドメトリデータを使用するか
  use_nav_sat = false,  -- GPSデータを使用するか
  use_landmarks = false,  -- ランドマークデータを使用するか
  num_laser_scans = 1,  -- 使用するレーザースキャンの数
  num_multi_echo_laser_scans = 0,  -- マルチエコーレーザースキャンの数
  num_subdivisions_per_laser_scan = 1,  -- レーザースキャンごとに分割する数
  num_point_clouds = 0,  -- 使用するポイントクラウドの数
  lookup_transform_timeout_sec = 0.2,  -- トランスフォームのタイムアウト時間（秒）
  submap_publish_period_sec = 0.05,  -- サブマップの発行周期（秒）
  pose_publish_period_sec = 5e-3,  -- ポーズの発行周期（秒）
  trajectory_publish_period_sec = 30e-3,  -- 軌跡の発行周期（秒）
  rangefinder_sampling_ratio = 1.,  -- 距離センサーのサンプリング割合
  odometry_sampling_ratio = 1.,  -- オドメトリのサンプリング割合
  fixed_frame_pose_sampling_ratio = 1.,  -- 固定フレームのポーズのサンプリング割合
  imu_sampling_ratio = 1.,  -- IMUのサンプリング割合
  landmarks_sampling_ratio = 1.,  -- ランドマークのサンプリング割合
}

-- 2次元の軌跡生成を使用する設定
MAP_BUILDER.use_trajectory_builder_2d = true

-- 2D軌跡生成の設定
TRAJECTORY_BUILDER_2D.min_range = 0.  -- 距離測定の最小範囲（メートル）
TRAJECTORY_BUILDER_2D.max_range = 10.  -- 距離測定の最大範囲（メートル）
TRAJECTORY_BUILDER_2D.missing_data_ray_length = 5.  -- 欠損データの代わりに使用する距離（メートル）
TRAJECTORY_BUILDER_2D.use_imu_data = false  -- IMUデータを使用するか
TRAJECTORY_BUILDER_2D.use_online_correlative_scan_matching = false  -- オンラインスキャンマッチングを使用するか

-- POSE_GRAPHの制約ビルダーのスコア設定
POSE_GRAPH.constraint_builder.min_score = 0.65  -- スキャンマッチングの最小スコア
POSE_GRAPH.constraint_builder.global_localization_min_score = 0.7  -- グローバル位置推定の最小スコア

-- POSE_GRAPHの最適化問題の重み設定
POSE_GRAPH.optimization_problem.local_slam_pose_translation_weight = 1e5  -- ローカルSLAMの平行移動に対する重み
POSE_GRAPH.optimization_problem.local_slam_pose_rotation_weight = 1e5  -- ローカルSLAMの回転に対する重み
POSE_GRAPH.optimization_problem.odometry_translation_weight = 1e5  -- オドメトリの平行移動に対する重み
POSE_GRAPH.optimization_problem.odometry_rotation_weight = 1e5  -- オドメトリの回転に対する重み
POSE_GRAPH.optimization_problem.huber_scale = 1e3  -- Huber損失関数のスケール

-- スキャンマッチングの重み設定
TRAJECTORY_BUILDER_2D.ceres_scan_matcher.occupied_space_weight = 10  -- 占有空間に対する重み
TRAJECTORY_BUILDER_2D.ceres_scan_matcher.rotation_weight = 40  -- 回転に対する重み

-- サブマップとモーションフィルターの設定
TRAJECTORY_BUILDER_2D.submaps.num_range_data = 120  -- サブマップあたりの距離データの数
TRAJECTORY_BUILDER_2D.motion_filter.max_distance_meters = 0.1  -- 最大距離のフィルタ（メートル）
TRAJECTORY_BUILDER_2D.motion_filter.max_angle_radians = math.rad(0.2)  -- 最大角度のフィルタ（ラジアン）

-- 最終的なオプション設定を返す
return options

