from setuptools import setup
import os
from glob import glob

package_name = 'm5stack_visualizer'

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
    ],
    install_requires=['setuptools', 'pyserial'],
    zip_safe=True,
    maintainer='Your Name',
    maintainer_email='your_email@example.com',
    description='M5Stack Core2 visualizer for ROS2 bag recorder and mux input status',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'm5stack_bridge_node = m5stack_visualizer.m5stack_bridge_node:main',
        ],
    },
)