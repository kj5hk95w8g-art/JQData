from setuptools import setup, find_packages

setup(
    name="jqdata-sdk",
    version="2.2.0",
    description="JQData Platform Python SDK —— 内部金融数据查询工具",
    author="Yuntu Tech",
    packages=find_packages(),
    install_requires=[
        "requests>=2.28.0",
        "pandas>=1.5.0",
    ],
    python_requires=">=3.8",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
