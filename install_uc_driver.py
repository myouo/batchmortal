import re
import os
import urllib.request
import json
import zipfile
import shutil
import platform
import subprocess

def get_chrome_version():
    """自动检测 Chrome 的主版本号及完整版本号"""
    try:
        # Windows
        output = subprocess.check_output(
            r'reg query "HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon" /v version',
            shell=True
        ).decode('utf-8')
        full_version = re.search(r'([\d\.]+)', output).group(1)
        major_version = int(full_version.split('.')[0])
        return major_version, full_version
    except Exception:
        try:
            # Linux / Mac (回退方案)
            output = subprocess.check_output(["google-chrome", "--version"]).decode("utf-8")
            full_version = re.search(r'([\d\.]+)', output).group(1)
            major_version = int(full_version.split('.')[0])
            return major_version, full_version
        except Exception:
            return None, None

def get_npm_mirror_download_url(major_version, full_version):
    """根据 Chrome 版本从国内 npmmirror 获取 ChromeDriver 下载链接"""
    # Chrome 115 及以上更改了分发路径
    if major_version >= 115:
        base_url = "https://registry.npmmirror.com/-/binary/chrome-for-testing/"
        is_64bit = platform.architecture()[0] == '64bit'
        archive_name = "chromedriver-win64.zip" if is_64bit else "chromedriver-win32.zip"
        arch_dir = "win64" if is_64bit else "win32"
    else:
        # Chrome 114 及以下
        base_url = "https://registry.npmmirror.com/-/binary/chromedriver/"
        archive_name = "chromedriver_win32.zip"
        arch_dir = None

    try:
        print(f"正在向镜像站请求可用版本列表: {base_url}")
        req = urllib.request.Request(base_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
        
        # 提取所有可用的版本号文件夹
        available_versions = [x['name'].strip('/') for x in data if x['type'] == 'dir']
        
        # 匹配策略
        best_version = None
        if full_version in available_versions:
            best_version = full_version  # 精确匹配（最佳）
        else:
            # 找不到精确匹配时，找同一个主版本下最新的补丁版本
            prefix = f"{major_version}."
            matches = [v for v in available_versions if v.startswith(prefix)]
            if not matches:
                return None
            # 将版本号按照数字排序，取最大值
            best_version = sorted(matches, key=lambda x: [int(p) for p in x.split('.')])[-1]
            
        print(f"匹配到最合适的驱动版本: {best_version}")

        # 拼接最终的下载 URL
        if major_version >= 115:
            return f"https://registry.npmmirror.com/-/binary/chrome-for-testing/{best_version}/{arch_dir}/{archive_name}"
        else:
            return f"https://registry.npmmirror.com/-/binary/chromedriver/{best_version}/{archive_name}"
    except Exception as e:
        print(f"获取版本列表失败: {e}")
        return None

def install_uc_driver():
    print("=== 开始自动配置 uc_driver (国内镜像加速) ===")
    
    try:
        import seleniumbase
    except ImportError:
        print("❌ 错误：此环境尚未安装 seleniumbase。请先执行 pip install seleniumbase")
        return

    major, full = get_chrome_version()
    if not major:
        print("❌ 错误：无法在系统中检测到 Google Chrome，请确认是否已安装。")
        return
        
    print(f"✅ 检测到本地 Chrome: 完整版本 {full} (主版本 {major})")
    
    download_url = get_npm_mirror_download_url(major, full)
    if not download_url:
        print(f"❌ 错误：未能在镜像站找到 Chrome {major} 对应的驱动程序。")
        return
    
    print(f"🔗 驱动下载链接: {download_url}")
    
    zip_path = "chromedriver_temp.zip"
    try:
        print("⬇️ 正在下载驱动，请稍候...")
        req = urllib.request.Request(download_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=60) as response, open(zip_path, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        print("✅ 下载完成。")
    except Exception as e:
        print(f"❌ 下载失败: {e}")
        return

    # 获取当前 Python 环境中 seleniumbase 的 driver 存放目录
    sb_drivers_dir = os.path.join(seleniumbase.__path__[0], 'drivers')
    os.makedirs(sb_drivers_dir, exist_ok=True)
    # 对于 Windows，最终的文件名是 uc_driver.exe
    uc_driver_path = os.path.join(sb_drivers_dir, 'uc_driver.exe')

    try:
        print("📦 正在解压并配置...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            exe_name = None
            # 在压缩包中寻找 chromedriver.exe (应对不同版本的目录嵌套)
            for name in zip_ref.namelist():
                if name.lower().endswith('chromedriver.exe'):
                    exe_name = name
                    break
            
            if not exe_name:
                print("❌ 错误：未在压缩包中找到 chromedriver.exe")
                return
            
            # 直接提取内容并写入到期望的 uc_driver.exe 文件位置
            with zip_ref.open(exe_name) as source, open(uc_driver_path, "wb") as target:
                shutil.copyfileobj(source, target)
                
        print(f"🎉 成功！uc_driver.exe 已自动安装至:")
        print(f"   -> {uc_driver_path}")
        print("现在程序可以正常运行了。")
    except Exception as e:
        print(f"❌ 解压或配置失败: {e}")
    finally:
        # 清理临时下载的压缩包
        if os.path.exists(zip_path):
            os.remove(zip_path)

if __name__ == '__main__':
    install_uc_driver()
