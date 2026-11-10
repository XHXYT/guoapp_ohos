import argparse
import os
import platform
import shutil
import subprocess
from pathlib import Path

from app_build import BuildVariant, add_variant_argument

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--platform', choices=['android', 'windows', 'darwin', 'ohos'], required=True)
parser.add_argument('--abi', action='append', choices=['arm64-v8a', 'armeabi-v7a', 'x86_64'])
add_variant_argument(parser)
options = parser.parse_args()
variant = BuildVariant(options.all_sources)

environment = os.environ.copy()
environment.setdefault('GOPROXY', 'https://goproxy.cn,direct')
environment.setdefault('GOSUMDB', 'off')
environment['CGO_ENABLED'] = '1'
go = shutil.which('go')
if not go:
    raise SystemExit('请先安装 Go 1.24.1 或更新版本。')
bootstrap_env = environment.copy()
bootstrap_env['GOSUMDB'] = os.environ.get('GOSUMDB', 'sum.golang.org')
if bootstrap_env['GOSUMDB'] == 'off':
    bootstrap_env['GOSUMDB'] = 'sum.golang.org'
toolchain_root = subprocess.check_output([go, 'env', 'GOROOT'], cwd=root / 'native',
    env=bootstrap_env, text=True).strip()
go = str(Path(toolchain_root) / 'bin' / ('go.exe' if platform.system() == 'Windows' else 'go'))

def build(goos, architecture, compiler, output, extra=None, build_args=None):
    output.parent.mkdir(parents=True, exist_ok=True)
    build_env = environment.copy()
    build_env.update(GOOS=goos, GOARCH=architecture, CC=str(compiler))
    if extra:
        build_env.update(extra)
    print('Building ' + str(output.relative_to(root)), flush=True)
    command = [go, 'build', '-trimpath', '-buildmode=c-shared']
    if build_args:
        command.extend(build_args)
    command.extend(['-ldflags=' + variant.linker_flags, '-o', str(output), './bridge'])
    subprocess.run(command, cwd=root / 'native', env=build_env, check=True)

if options.platform == 'android':
    sdk = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
    if not sdk:
        raise SystemExit('请设置 ANDROID_HOME 为 Android SDK 目录。')
    ndk = Path(os.environ.get('ANDROID_NDK_HOME', Path(sdk) / 'ndk' / '28.2.13676358'))
    host = {'Darwin': 'darwin-x86_64', 'Linux': 'linux-x86_64', 'Windows': 'windows-x86_64'}[platform.system()]
    compilers = ndk / 'toolchains' / 'llvm' / 'prebuilt' / host / 'bin'
    mappings = {
        'arm64-v8a': ('arm64', 'aarch64-linux-android24-clang'),
        'armeabi-v7a': ('arm', 'armv7a-linux-androideabi24-clang'),
        'x86_64': ('amd64', 'x86_64-linux-android24-clang'),
    }
    for abi in options.abi or list(mappings):
        architecture, name = mappings[abi]
        compiler = compilers / (name + ('.cmd' if platform.system() == 'Windows' else ''))
        if not compiler.exists():
            raise SystemExit('缺少 Android NDK 编译器：' + str(compiler))
        output = root / 'android' / 'app' / 'src' / 'main' / 'jniLibs' / abi / 'libduanju_core.so'
        extra = {'CGO_LDFLAGS': '-Wl,-z,max-page-size=16384'}
        if architecture == 'arm':
            extra['GOARM'] = '7'
        build('android', architecture, compiler, output, extra)
elif options.platform == 'ohos':
    sdk = os.environ.get('HOS_SDK_HOME') or os.environ.get('DEVECO_SDK_HOME')
    if not sdk:
        properties = root / 'ohos' / 'local.properties'
        if properties.exists():
            for line in properties.read_text(encoding='utf-8').splitlines():
                if line.startswith('hwsdk.dir='):
                    raw = line.split('=', 1)[1].strip()
                    sdk = raw.replace('\\\\', '\x00').replace('\\:', ':').replace('\x00', '\\')
    if not sdk:
        raise SystemExit('请设置 HOS_SDK_HOME 为 DevEco SDK 目录（含 default/openharmony/native）。')
    native_sdk = Path(sdk) / 'default' / 'openharmony' / 'native'
    llvm_bin = native_sdk / 'llvm' / 'bin'
    sysroot = native_sdk / 'sysroot'
    if not (llvm_bin / 'clang.exe').exists() and not (llvm_bin / 'clang').exists():
        raise SystemExit('缺少 OHOS NDK 工具链：' + str(llvm_bin))
    mappings = {
        'arm64-v8a': ('arm64', 'aarch64-linux-ohos', '-target aarch64-linux-ohos'),
        'x86_64': ('amd64', 'x86_64-linux-ohos', '-target x86_64-linux-ohos'),
    }
    # OHOS musl 拒绝加载带 initial-exec TLS 重定位的 dlopen 库（Go linux c-shared 的
    # runtime.tls_g 即是），改走 GOOS=android 的 TLSG_IS_VARIABLE 路径 + overlay 替换
    # gcc_android.c（去掉 bionic dlopen/TLS_SLOT 假设，仅保留可移植的 pthread_key 扫描）。
    ohos_dir = root / 'native' / 'build' / 'ohos'
    ohos_dir.mkdir(parents=True, exist_ok=True)
    gcc_android_replacement = ohos_dir / 'gcc_android_ohos.c'
    gcc_android_replacement.write_text(
        '// OpenHarmony (musl) adaptation of runtime/cgo/gcc_android.c:\n'
        '// no bionic TLS_SLOT_APP or android_get_device_api_level, and no\n'
        '// dlopen("libc.so") (not guaranteed resolvable in OHOS app linker\n'
        '// namespaces). Always use the portable pthread_key scan; on musl\n'
        '// TPIDR_EL0 points at struct pthread whose tsd[] lies in low memory.\n'
        '#include <pthread.h>\n'
        '#include "libcgo.h"\n'
        '\n'
        '#define magic1 (0x23581321345589ULL)\n'
        '\n'
        'static void\n'
        'inittls(void **tlsg, void **tlsbase)\n'
        '{\n'
        '\tpthread_key_t k;\n'
        '\tint i, err;\n'
        '\n'
        '\terr = pthread_key_create(&k, NULL);\n'
        '\tif(err != 0) {\n'
        '\t\tfatalf("pthread_key_create failed: %d", err);\n'
        '\t}\n'
        '\tpthread_setspecific(k, (void*)magic1);\n'
        '\tfor (i=0; i<384; i++) {\n'
        '\t\tif (*(tlsbase+i) == (void*)magic1) {\n'
        '\t\t\t*tlsg = (void*)(i*sizeof(void *));\n'
        '\t\t\tpthread_setspecific(k, 0);\n'
        '\t\t\treturn;\n'
        '\t\t}\n'
        '\t}\n'
        '\tfatalf("inittls: could not find pthread key");\n'
        '}\n'
        '\n'
        'void (*x_cgo_inittls)(void **tlsg, void **tlsbase) = inittls;\n',
        encoding='utf-8')
    overlay = {'Replace': {
        str(Path(toolchain_root) / 'src' / 'runtime' / 'cgo' / 'gcc_android.c'): str(gcc_android_replacement),
    }}
    import json as _json
    overlay_file = ohos_dir / 'overlay.json'
    overlay_file.write_text(_json.dumps(overlay), encoding='utf-8')
    # cmd/link 对 GOOS=android 默认追加 -llog；OHOS musl 无 liblog，
    # 且 -target *-linux-ohos 不定义 __ANDROID__（gcc_fatalf.c 不会引用其符号），
    # 空桩静态库即可满足链接。
    stub_dir = ohos_dir / 'stub'
    stub_dir.mkdir(parents=True, exist_ok=True)
    (stub_dir / 'empty.c').write_text('static int ohos_liblog_stub;\n', encoding='utf-8')
    if not (stub_dir / 'liblog.a').exists():
        ar_tool = llvm_bin / ('llvm-ar.exe' if platform.system() == 'Windows' else 'llvm-ar')
        subprocess.run([str(llvm_bin / ('clang.exe' if platform.system() == 'Windows' else 'clang')),
                        '-target', mappings['arm64-v8a'][2], '--sysroot=' + str(sysroot),
                        '-c', 'empty.c', '-o', 'empty.o'], cwd=stub_dir, check=True)
        subprocess.run([str(ar_tool), 'rcs', 'liblog.a', 'empty.o'], cwd=stub_dir, check=True)
    for abi in options.abi or ['arm64-v8a']:
        architecture, target, flag = mappings[abi]
        if platform.system() == 'Windows':
            # NDK 自带的 triple wrapper 是 sh 脚本，Windows 下生成无空格路径的 .cmd 包装器供 cgo 调用。
            compiler = ohos_dir / (target.replace('-', '_') + '_clang.cmd')
            compiler.write_text('@echo off\r\n"{}" {} --sysroot="{}" -D__MUSL__ %*\r\n'
                                .format(llvm_bin / 'clang.exe', flag, sysroot), encoding='ascii')
        else:
            compiler = llvm_bin / (target.replace('x86_64-linux', 'x86_64-unknown-linux').replace('aarch64-linux', 'aarch64-unknown-linux') + '-clang')
            if not compiler.exists():
                raise SystemExit('缺少 OHOS NDK 编译器：' + str(compiler))
        output = root / 'ohos' / 'entry' / 'libs' / abi / 'libduanju_core.so'
        # netgo：net 的 android cgo 解析器（cgo_resold.go）按 bionic 的
        # size_t 原型写，与 musl 的 socklen_t 不匹配；OHOS 有 /etc/resolv.conf，
        # 纯 Go 解析器即可工作。
        build('android', architecture, compiler, output,
              {'CGO_LDFLAGS': '-Wl,-z,max-page-size=16384 -L' + str(stub_dir),
               # cmd/go 的 go:cgo_ldflag 安全校验只放行 relro/now/execstack，
               # 16KB 页对齐参数需要显式白名单（见 go.dev/s/invalidflag）。
               'CGO_LDFLAGS_ALLOW': '-Wl,-z,max-page-size=.*'},
              build_args=['-tags', 'netgo', '-overlay=' + str(overlay_file)])
elif options.platform == 'windows':
    compiler = shutil.which('x86_64-w64-mingw32-gcc') or (shutil.which('gcc') if platform.system() == 'Windows' else None)
    if not compiler:
        raise SystemExit('请安装 MinGW-w64，并将其 bin 目录加入 PATH。')
    build('windows', 'amd64', compiler, root / 'windows' / 'runner' / 'duanju_core.dll',
          {'CGO_LDFLAGS': '-static-libgcc'})
else:
    if platform.system() != 'Darwin':
        raise SystemExit('macOS 核心需要在 macOS 上构建。')
    compiler = shutil.which('clang')
    if not compiler:
        raise SystemExit('需要安装 Xcode Command Line Tools。')
    output = root / 'macos' / 'DuanjuCore' / 'libDuanjuCore.a'
    slices = []
    for architecture, triple in [('arm64', 'arm64-apple-macos12.0'), ('amd64', 'x86_64-apple-macos12.0')]:
        library = root / 'native' / 'build' / 'macos' / architecture / 'libDuanjuCore.a'
        library.parent.mkdir(parents=True, exist_ok=True)
        print('Building ' + str(library.relative_to(root)), flush=True)
        build_env = environment | {'GOOS': 'darwin', 'GOARCH': architecture, 'CC': str(compiler),
                                   'CGO_CFLAGS': '-target ' + triple, 'CGO_LDFLAGS': '-target ' + triple}
        subprocess.run([go, 'build', '-trimpath', '-buildmode=c-archive',
                        '-ldflags=' + variant.linker_flags, '-o', str(library), './bridge'],
                       cwd=root / 'native', env=build_env, check=True)
        slices.append(str(library))
    subprocess.run(['lipo', '-create', *slices, '-output', str(output)], check=True)
