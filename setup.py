#!/usr/bin/env python
# coding: utf-8

import re

from setuptools import setup

# 版本號只寫在 redshift_connector_bade/__init__.py
with open("redshift_connector_bade/__init__.py", encoding="utf-8") as f:
    version = re.search(r'__version__ = "(.+?)"', f.read()).group(1)

setup(
    name="redshift-connector-bade",  # 套件名稱
    version=version,                 # 版本號
    author="Hans, Roy",
    author_email="royhsu1012@gmail.com",
    description="A simple connector for Amazon Redshift using JDBC, with the driver bundled",
    long_description=open("README.md", encoding="utf-8").read(),  # 你的 README 文件
    long_description_content_type="text/markdown",
    url="https://github.com/royhsu1012/redshift-connector",  # 你的 GitHub 地址
    packages=["redshift_connector_bade"],
    package_data={"redshift_connector_bade": ["jars/*.jar", "jars/*.txt"]},  # 內建 JDBC 驅動
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",  # 這個根據你的授權許可來修改
        "Operating System :: OS Independent",
    ],
    install_requires=[         # 必要的依賴包
        "jpype1",
        "jaydebeapi",
        "pandas",
    ],
    python_requires=">=3.8",   # 支援的 Python 版本
)
