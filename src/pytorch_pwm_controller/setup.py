from setuptools import setup
import os
from glob import glob

package_name = 'pytorch_pwm_controller'

setup(
    name=package_name,
    version='0.0.1',
    packages=[package_name, f'{package_name}.model'],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        # モデルファイルをインストール
        (os.path.join('share', package_name, 'model'), 
            glob('pytorch_pwm_controller/model/*.pth')),
        # launchファイルをインストール（後で作成）
        (os.path.join('share', package_name, 'launch'), 
            glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Your Name',
    maintainer_email='your_email@example.com',
    description='PyTorch-based pwm controller using laser scan data',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'nn_pwm_controller_node = pytorch_pwm_controller.nn_pwm_controller_node:main',
        ],
    },
)