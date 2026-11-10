import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from app_build import BuildVariant

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(
    description='构建鸿蒙 HAP；按 ohos/AppScope/app.json5 的包名自动选择红果鉴或全站源版，'
                '界面与 Go 核心的编译开关保持一致，避免混装原生库。')
parser.add_argument('--debug', action='store_true', help='构建 debug 模式；默认 release')
options = parser.parse_args()

app = json.loads((root / 'ohos/AppScope/app.json5').read_text(encoding='utf-8'))['app']
bundle = app['bundleName']
display = next(item['value'] for item in json.loads(
    (root / 'ohos/AppScope/resources/base/element/string.json').read_text(encoding='utf-8'))['string']
    if item['name'] == 'app_name')
all_sources = bundle != 'com.guoapp.phoenix'
print(f'构建变体：{display}（{bundle}，all_sources={all_sources}）')
variant = BuildVariant(all_sources)

env = os.environ.copy()
env.setdefault('GOPROXY', 'https://goproxy.cn,direct')
env.setdefault('GOSUMDB', 'off')
flutter = shutil.which('flutter')
if not flutter:
    raise SystemExit('请先将 Flutter SDK 的 bin 目录加入 PATH。')
subprocess.run([sys.executable, str(root / 'scripts/build_native.py'),
                '--platform', 'ohos', *variant.arguments], cwd=root, env=env, check=True)
subprocess.run([flutter, 'pub', 'get'], cwd=root, env=env, check=True)
arguments = ['--debug'] if options.debug else ['--release']
arguments.extend(variant.flutter_arguments)
if all_sources:
    arguments.append('--dart-define=ZHENGUOJIAN_DISPLAY_NAME=' + display)
subprocess.run([flutter, 'build', 'hap', '--no-pub', *arguments], cwd=root, env=env, check=True)
version = re.search(r'^version:\s*([\w.+-]+)\s*$',
                    (root / 'pubspec.yaml').read_text(encoding='utf-8'),
                    re.MULTILINE).group(1)
output = root / 'dist' / 'ohos'
output.mkdir(parents=True, exist_ok=True)
for name in ('entry-default-signed.hap', 'entry-default-unsigned.hap'):
    source = root / 'build' / 'ohos' / 'hap' / name
    if not source.exists():
        continue
    target = output / f'{variant.slug}-{version}-ohos-arm64.hap'
    shutil.copy2(source, target)
    print(str(target))
    break
else:
    raise SystemExit('未找到构建产物 HAP。')
