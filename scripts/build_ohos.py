import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from app_build import app

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(
    description='构建鸿蒙 HAP；内置模块用 --bundle 或环境变量 BUNDLED_MODULES 指定，'
                '规则与 build_native.py 一致（相对路径以仓库根目录为准）。')
parser.add_argument('--debug', action='store_true', help='构建 debug 模式；默认 release')
parser.add_argument('--bundle', action='append', default=[], help='内置模块目录，可多次指定；也可用逗号分隔写在环境变量 BUNDLED_MODULES 里')
options = parser.parse_args()

info = json.loads((root / 'ohos/AppScope/app.json5').read_text(encoding='utf-8'))['app']
print(f'构建应用：{app.display_name}（{info["bundleName"]}）')

env = os.environ.copy()
env.setdefault('GOPROXY', 'https://goproxy.cn,direct')
env.setdefault('GOSUMDB', 'off')
flutter = shutil.which('flutter')
if not flutter:
    raise SystemExit('请先将 Flutter SDK 的 bin 目录加入 PATH。')
native_arguments = ['--platform', 'ohos']
for item in options.bundle:
    native_arguments.extend(['--bundle', item])
subprocess.run([sys.executable, str(root / 'scripts/build_native.py'), *native_arguments],
               cwd=root, env=env, check=True)
subprocess.run([flutter, 'pub', 'get'], cwd=root, env=env, check=True)
arguments = ['--debug'] if options.debug else ['--release']
arguments.extend(app.flutter_arguments)
subprocess.run([flutter, 'build', 'hap', '--no-pub', *arguments], cwd=root, env=env, check=True)
version = re.search(r'^version:\s*([\w.+-]+)\s*$',
                    (root / 'pubspec.yaml').read_text(encoding='utf-8'),
                    re.MULTILINE).group(1)
output = root / 'dist' / 'ohos'
output.mkdir(parents=True, exist_ok=True)
artifacts = (
    # hvigor 打包的未签名中间产物，用作 Release 上传包
    (root / 'ohos' / 'entry' / 'build' / 'default' / 'outputs' / 'default'
     / 'entry-default-unsigned.hap', ''),
    # flutter 工具拷出的签名包，本机调试安装用
    (root / 'build' / 'ohos' / 'hap' / 'entry-default-signed.hap', '-signed'),
)
missing = []
for source, suffix in artifacts:
    if not source.exists():
        missing.append(source.name)
        continue
    target = output / f'{app.slug}-{version}-ohos-arm64{suffix}.hap'
    shutil.copy2(source, target)
    print(str(target))
if missing:
    raise SystemExit('未找到构建产物：' + '、'.join(missing))
