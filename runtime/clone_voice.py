#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gc
import json
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG_PATH = Path('/opt/data/clone_voice_runtime/config.json')
DEFAULT_CONFIG = {
    'env_python': '/opt/data/voxcpm-env/bin/python',
    'model_dir': '/opt/data/pretrained_models/VoxCPM2',
    'output_dir': '/opt/data/clone_voice_runtime/outputs',
    'device': 'cuda:0',
    'installed_name': '克隆声音',
}


def cfg() -> dict:
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        merged = DEFAULT_CONFIG.copy()
        merged.update(data)
        return merged
    return DEFAULT_CONFIG.copy()


def check() -> None:
    c = cfg()
    print(json.dumps(c, ensure_ascii=False, indent=2))
    import torch
    print('torch=', torch.__version__)
    print('cuda_available=', torch.cuda.is_available())
    if torch.cuda.is_available():
        print('gpu=', torch.cuda.get_device_name(0))
    p = Path(c['model_dir'])
    print('model_dir_exists=', p.exists(), p)
    required = ['config.json', 'model.safetensors', 'audiovae.pth']
    print('model_files=', {n: (p / n).exists() for n in required})
    import voxcpm, accelerate, safetensors, scipy, transformers  # noqa: F401
    print('imports=ok')


def ensure_wav(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(f'参考声音不存在: {path}')
    if path.suffix.lower() == '.wav':
        return path
    out = Path(tempfile.gettempdir()) / (path.stem + '_clone_voice_48k_mono.wav')
    subprocess.run(['ffmpeg', '-y', '-i', str(path), '-ar', '48000', '-ac', '1', str(out)], check=True)
    return out


def load_model(model_dir: str, device: str):
    import torch
    from accelerate import init_empty_weights
    from safetensors.torch import load_file
    from transformers import LlamaTokenizerFast
    from voxcpm.model.voxcpm2 import VoxCPMConfig, VoxCPM2Model
    from voxcpm.modules.audiovae import AudioVAEV2

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    model_path = Path(model_dir)
    with open(model_path / 'config.json', encoding='utf-8') as f:
        config_obj = VoxCPMConfig.model_validate_json(f.read())

    tokenizer = LlamaTokenizerFast.from_pretrained(model_dir)
    audio_vae_config = getattr(config_obj, 'audio_vae_config', None)
    audio_vae = AudioVAEV2(config=audio_vae_config) if audio_vae_config else AudioVAEV2()
    ckpt = torch.load(str(model_path / 'audiovae.pth'), map_location='cpu', weights_only=True)
    audio_vae.load_state_dict(ckpt.get('state_dict', ckpt))
    audio_vae = audio_vae.to(device, torch.float32)
    del ckpt
    gc.collect()

    # 低 CPU RAM 机器用 empty weights + assign=True，避免常规加载 OOM。
    with init_empty_weights():
        model = VoxCPM2Model(config_obj, tokenizer, audio_vae, device=device)

    sd = load_file(str(model_path / 'model.safetensors'), device=device)
    for k, v in audio_vae.state_dict().items():
        sd[f'audio_vae.{k}'] = v.to(device)
    model.load_state_dict(sd, strict=False, assign=True)
    del sd
    gc.collect()
    return model.to(device).eval()


def synth(ref: str, text: str, out: str, cfg_value: float = 2.0, inference_timesteps: int = 10) -> Path:
    if not text or not text.strip():
        raise ValueError('目标文字为空：请提供需要朗读的文字。')

    import numpy as np
    import torch
    import scipy.io.wavfile as wavfile

    c = cfg()
    ref_wav = ensure_wav(Path(ref))
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    device = c.get('device', 'cuda:0') if torch.cuda.is_available() else 'cpu'
    model = load_model(c['model_dir'], device)

    # 关键修复：参考声音只作为音色参考，必须传 reference_wav_path。
    # 不要传 prompt_wav_path，否则可能变成续写参考音频内容，导致不按指定文字朗读。
    wav = model.generate(
        target_text=text.strip(),
        reference_wav_path=str(ref_wav),
        cfg_value=cfg_value,
        inference_timesteps=inference_timesteps,
    )

    if isinstance(wav, torch.Tensor):
        wav = wav.detach().cpu().numpy()
    wav = np.asarray(wav, dtype=np.float32).squeeze()
    wav = np.clip(wav, -1.0, 1.0)
    sr = getattr(getattr(model, 'tts_model', None), 'sample_rate', None) or getattr(model, 'sample_rate', 48000)
    wavfile.write(str(out_path), int(sr), (wav * 32767).astype(np.int16))
    print(out_path)
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description='克隆声音：用参考声音的音色朗读指定文字')
    ap.add_argument('--check', action='store_true', help='检查环境和模型')
    ap.add_argument('--ref', help='参考声音文件路径，建议 5-15 秒、安静、单人说话')
    ap.add_argument('--text', help='要朗读的目标文字')
    ap.add_argument('--text-file', help='从文本文件读取要朗读的目标文字')
    ap.add_argument('--out', default='/opt/data/clone_voice_runtime/outputs/output.wav', help='输出 wav 路径')
    ap.add_argument('--cfg-value', type=float, default=2.0)
    ap.add_argument('--inference-timesteps', type=int, default=10)
    args = ap.parse_args()

    if args.check:
        check()
        return

    text = args.text or ''
    if args.text_file:
        text = Path(args.text_file).read_text(encoding='utf-8').strip()
    if not args.ref or not text:
        print('用法：clone_voice.py --ref 参考声音.wav --text 要朗读的文字 --out 输出.wav', file=sys.stderr)
        sys.exit(2)

    synth(args.ref, text, args.out, args.cfg_value, args.inference_timesteps)


if __name__ == '__main__':
    main()
