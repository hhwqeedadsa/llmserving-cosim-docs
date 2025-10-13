# -*- coding: utf-8 -*-
import sys
import subprocess
from importlib import util
import os

def check_and_install(package_name, max_retries=3, mirrors=None):
    """
    检查模块是否安装，若未安装则尝试通过pip安装
    :param package_name: 模块名称
    :param max_retries: 最大重试次数（默认3次）
    :param mirrors: 备选镜像源列表（如清华源、阿里云等）
    """

    if util.find_spec(package_name) is None:
        print(f" 模块 {package_name} 未安装，尝试自动安装...")
        count = 0
        while count < max_retries:
            try:
                # 优先使用默认镜像源，失败后切换备选镜像
                if mirrors and count > 0:
                    mirror = mirrors[count % len(mirrors)]
                    subprocess.check_call([sys.executable, "-m", "pip", "install", package_name, "-i", mirror])
                else:
                    subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
                print(f"? {package_name} 安装成功")
                return
            except subprocess.CalledProcessError:
                count += 1
                print(f"? 第 {count} 次安装失败，尝试重试...")
        raise RuntimeError(f"无法安装 {package_name}，请手动检查网络或依赖")

# 需要检测的模块列表
required_packages = ["glob", "pandas", "re", "csv"]

# 国内镜像源备选（按需配置）
mirror_sources = []

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("python3 parse_script.py traffic.csv")
        sys.exit(1)
    for pkg in required_packages:
        check_and_install(pkg, mirrors=mirror_sources)
    print("所有依赖已满足，开始执行脚本...")
    # 获取当前脚本所在目录的绝对路径
    script_dir = os.path.dirname(os.path.abspath(__file__))
    task_stats_script = os.path.join(script_dir, "task_statistics.py")
    cal_throughput_script = os.path.join(script_dir, "cal_throughput.py")
    
    if len(sys.argv[2]) > 0 and sys.argv[2].lower() == 'true' :
        cmd1 = f"python3 {task_stats_script} " + sys.argv[1] + " True"
        cmd2 = f"python3 {cal_throughput_script} " + sys.argv[1]  + " True"
    else :
        cmd1 = f"python3 {task_stats_script} " + sys.argv[1] + " False"
        cmd2 = f"python3 {cal_throughput_script} " + sys.argv[1] + " False"
    os.system(cmd1)
    os.system(cmd2)
