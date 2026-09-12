# MAGP_kada 開発用（手元 PC 専用）。実機 Jetson では使わない。
# 環境の中身は scripts/setup.sh が唯一の定義。
FROM osrf/ros:humble-desktop

ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=humble \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# setup.sh がリポジトリ相対で requirements.txt を見つけるための配置
WORKDIR /opt/magp-setup
COPY requirements.txt /opt/magp-setup/requirements.txt
COPY scripts/setup.sh /opt/magp-setup/scripts/setup.sh
RUN chmod +x /opt/magp-setup/scripts/setup.sh \
    && bash /opt/magp-setup/scripts/setup.sh

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ソースは実行時にマウントする（イメージに焼き込まない）
WORKDIR /ws
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
