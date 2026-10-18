from setuptools import setup
import os
from glob import glob

package_name = 'bno085_imu'

setup(
    name=package_name,
    version='1.0.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'),
            glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'config'),
            glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Your Name',
    maintainer_email='your_email@example.com',
    description='BNO085 IMU driver over I2C, publishes sensor_msgs/Imu',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'bno085_imu_node = bno085_imu.bno085_imu_node:main',
        ],
    },
)
