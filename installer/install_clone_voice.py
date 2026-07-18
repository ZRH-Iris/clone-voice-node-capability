#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CAPABILITY_NAME = '克隆声音'
VERSION = '1.0.1'
BASE = Path('/opt/data')
ENV_DIR = BASE / 'voxcpm-env'
RUNTIME_DIR = BASE / 'clone_voice_runtime'
OUTPUT_DIR = RUNTIME_DIR / 'outputs'
CONFIG_PATH = RUNTIME_DIR / 'config.json'
MODEL_DIR = BASE / 'pretrained_models' / 'VoxCPM2'
SKILL_DIR = BASE / 'skills' / 'clone-voice-capability'
REQUIRED_MODEL_FILES = ['config.json', 'model.safetensors', 'audiovae.pth']


def run(cmd, check=True, cwd=None, env=None, quiet=True):
    if quiet:
        return subprocess.run(cmd, check=check, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return subprocess.run(cmd, check=check, cwd=cwd, env=env)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_dirs():
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    (BASE / 'pretrained_models').mkdir(parents=True, exist_ok=True)


def ensure_ffmpeg():
    if not command_exists('ffmpeg'):
        raise RuntimeError('缺少 ffmpeg。请先安装 ffmpeg 后重试。Ubuntu/Debian: sudo apt-get install -y ffmpeg')


def ensure_python_env():
    py = ENV_DIR / 'bin' / 'python'
    if py.exists():
        return py
    print('未发现 /opt/data/voxcpm-env，开始创建 Python 虚拟环境。')
    if command_exists('uv'):
        run(['uv', 'venv', str(ENV_DIR), '--python', '3.12'], check=False)
    if not py.exists():
        run([sys.executable, '-m', 'venv', str(ENV_DIR)])
    pip = ENV_DIR / 'bin' / 'pip'
    run([str(pip), 'install', '-U', 'pip'], check=False)
    run([str(pip), 'install', 'numpy', 'scipy', 'safetensors', 'accelerate', 'transformers', 'sentencepiece', 'soundfile'], check=False)
    run([str(pip), 'install', 'voxcpm'], check=False)
    return py


def model_ready() -> bool:
    return MODEL_DIR.exists() and all((MODEL_DIR / f).exists() for f in REQUIRED_MODEL_FILES)


def try_download_model():
    if model_ready():
        return
    print('未发现完整 VoxCPM2 模型，尝试下载。模型较大，可能需要较长时间。')
    py = ENV_DIR / 'bin' / 'python'
    pip = ENV_DIR / 'bin' / 'pip'
    run([str(pip), 'install', '-U', 'modelscope'], check=False)
    candidates = ['OpenBMB/VoxCPM2', 'iic/VoxCPM2']
    for mid in candidates:
        code = "from modelscope import snapshot_download\nsnapshot_download(%r, local_dir=r%r)\n" % (mid, str(MODEL_DIR))
        rc = subprocess.run([str(py), '-c', code]).returncode
        if rc == 0 and model_ready():
            return
    run([str(pip), 'install', '-U', 'huggingface_hub'], check=False)
    for mid in ['openbmb/VoxCPM2', 'OpenBMB/VoxCPM2']:
        code = "from huggingface_hub import snapshot_download\nsnapshot_download(repo_id=%r, local_dir=r%r, local_dir_use_symlinks=False)\n" % (mid, str(MODEL_DIR))
        rc = subprocess.run([str(py), '-c', code]).returncode
        if rc == 0 and model_ready():
            return
    raise RuntimeError('模型文件未就绪，且自动下载失败。请手动将 VoxCPM2 模型放到 %s，至少包含 %s。' % (MODEL_DIR, REQUIRED_MODEL_FILES))


def install_runtime():
    src = repo_root() / 'runtime' / 'clone_voice.py'
    if not src.exists():
        raise RuntimeError('找不到 runtime 脚本: %s' % src)
    dst = RUNTIME_DIR / 'clone_voice.py'
    shutil.copy2(src, dst)
    os.chmod(dst, 0o755)
    cfg = {
        'env_python': str(ENV_DIR / 'bin' / 'python'),
        'model_dir': str(MODEL_DIR),
        'output_dir': str(OUTPUT_DIR),
        'device': 'cuda:0',
        'installed_name': CAPABILITY_NAME,
        'version': VERSION,
    }
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')


def install_skill_note():
    py = ENV_DIR / 'bin' / 'python'
    script = RUNTIME_DIR / 'clone_voice.py'
    lines = [
        '---',
        'name: clone-voice-capability',
        'description: "克隆声音：用户提供参考声音和文字后，生成同款声音配音。"',
        'platforms: [linux]',
        'tags: [voice-cloning, tts, audio]',
        '---',
        '',
        '# 克隆声音',
        '',
        '当用户说“克隆声音”“用我的声音生成音频”“用这个参考声音读这段话”“用我的声音朗读这段文字”时，使用本能力。',
        '',
        '## 已安装环境',
        '',
        f'- Python: `{py}`',
        f'- 模型目录: `{MODEL_DIR}`',
        f'- 运行脚本: `{script}`',
        f'- 输出目录: `{OUTPUT_DIR}`',
        '',
        '## 使用步骤',
        '',
        '1. 保存用户发来的参考音频，建议 5-15 秒，环境安静，单人说话。',
        '2. 保存或读取用户要朗读的目标文字。',
        '3. 如音频不是 wav，运行脚本会自动用 ffmpeg 转成 48k 单声道 wav。',
        '4. 执行：',
        '',
        '```bash',
        f'{py} {script} --ref /path/to/reference.wav --text "用户要朗读的文字" --out {OUTPUT_DIR}/output.wav',
        '```',
        '',
        '## 关键规则',
        '',
        '参考声音只用于提取音色；朗读内容必须严格以用户提供的文字为准。不要把参考录音里的原话当成要朗读内容。',
        '',
        '## 给用户的成功说明',
        '',
        '“我已经安装好「克隆声音」能力。以后你只要给我一段参考声音和要朗读的文字，我就可以用这段声音的音色生成配音。”',
        '',
        '## 注意',
        '',
        '必须真实生成音频，不要假装成功。参考声音必须是本人或已授权声音。',
    ]
    (SKILL_DIR / 'SKILL.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def verify():
    py = ENV_DIR / 'bin' / 'python'
    script = RUNTIME_DIR / 'clone_voice.py'
    result = subprocess.run([str(py), str(script), '--check'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError('环境验证没有通过，请检查模型文件、依赖或显卡环境。')


def main():
    print('收到，正在为你安装「克隆声音」能力。')
    print('我会自动完成准备和验证，你暂时不需要做任何技术操作。')
    ensure_dirs()
    ensure_ffmpeg()
    py = ensure_python_env()
    if not model_ready():
        print('正在准备声音模型，可能需要几分钟，请稍等。')
        try_download_model()
    print('正在完成配置和可用性验证。')
    install_runtime()
    install_skill_note()
    verify()
    print('')
    print('「克隆声音」已经安装成功，可以开始使用了。')
    print('')
    print('接下来请直接发给我两样东西：')
    print('1. 一段你本人或已授权的参考声音，建议 5-15 秒，环境安静，只有一个人说话；')
    print('2. 你想让这个声音朗读的文字。')
    print('')
    print('你可以这样发：')
    print('“用我的声音朗读下面这段文字。”')
    print('然后把录音和文案一起发给我。')
    print('')
    print('以后再次使用时，不需要重新安装，直接说：“用我的声音朗读这段文字”即可。')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('', file=sys.stderr)
        print('「克隆声音」暂时没有安装成功。', file=sys.stderr)
        print('原因：' + str(e), file=sys.stderr)
        print('你可以把这段失败提示转发给我，我会继续帮你处理。', file=sys.stderr)
        sys.exit(1)
