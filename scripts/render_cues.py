"""Render reviewed local sound cues and an optional dialogue stem. No network calls.

Experimental extension: Python 3.10+, NumPy, FFmpeg. Source assets stay local.
All times inside this renderer use 48 kHz samples; frame anchors use rational fps.
"""
import argparse
import hashlib
import json
import math
import re
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np

SR = 48000


def run(args, data=None):
    return subprocess.run(args, input=data, capture_output=True, check=True)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def decode(path, filters=None):
    args = ['ffmpeg', '-v', 'error', '-i', str(path)]
    if filters:
        args += ['-af', ','.join(filters)]
    args += ['-ac', '2', '-ar', str(SR), '-f', 'f32le', 'pipe:1']
    data = run(args).stdout
    return np.frombuffer(data, dtype='<f4').reshape(-1, 2).astype(np.float64)


def write_wav(path, audio):
    if not np.isfinite(audio).all() or np.max(np.abs(audio), initial=0) >= 1:
        raise ValueError('Non-finite or clipped PCM: ' + str(path))
    run(['ffmpeg', '-v', 'error', '-n', '-f', 'f32le', '-ar', str(SR),
         '-ac', '2', '-i', 'pipe:0', '-c:a', 'pcm_s24le', str(path)],
        audio.astype('<f4').tobytes())


def loudness(path):
    p = run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(path), '-af',
             'loudnorm=I=-24:TP=-2:LRA=11:print_format=json', '-f', 'null', '-'])
    log = p.stderr.decode('utf-8', errors='replace')
    return json.loads(log[log.rfind('{'):log.rfind('}') + 1])


def frame_sample(frame, fps):
    return round(Fraction(frame) * SR / Fraction(fps))


def dialogue_envelope(voice, options):
    """Energy-controlled attenuation, not speech recognition; never boosts effects."""
    for key in ('attack_ms', 'release_ms'):
        if key in options and (not math.isfinite(options[key]) or options[key] <= 0):
            raise ValueError(key + ' must be finite and positive')
    for key in ('lookahead_ms', 'hold_ms'):
        if key in options and (not math.isfinite(options[key]) or options[key] < 0):
            raise ValueError(key + ' must be finite and nonnegative')
    block = 240  # 5 ms control rate.
    power = np.mean(voice * voice, axis=1)
    power = np.pad(power, (0, (-len(power)) % block))
    rms = np.sqrt(power.reshape(-1, block).mean(axis=1) + 1e-16)
    db = 20 * np.log10(rms)
    threshold = options.get('threshold_dbfs', -36)
    gate = np.clip((db - threshold) / 12, 0, 1)
    lookahead = round(options.get('lookahead_ms', 15) / 5)
    hold = round(options.get('hold_ms', 80) / 5)
    held = np.array([max(gate[max(0, i-hold):min(len(gate), i+lookahead+1)])
                     for i in range(len(gate))])
    attack = math.exp(-5 / options.get('attack_ms', 15))
    release = math.exp(-5 / options.get('release_ms', 160))
    smoothed = np.zeros_like(held)
    current = 0.0
    for i, value in enumerate(held):
        coeff = attack if value > current else release
        current = coeff * current + (1 - coeff) * value
        smoothed[i] = current
    return np.interp(np.arange(len(voice)), np.arange(len(smoothed))*block, smoothed)


def render_clip(path, cue):
    start, end = cue['trim_seconds']
    length = cue.get('duration_seconds', end-start)
    if not all(math.isfinite(x) for x in (start, end, length)) or not (0 <= start < end and length > 0):
        raise ValueError('Invalid source trim or duration')
    filters = [f'atrim=start={start}:end={end}', 'asetpts=PTS-STARTPTS',
               f"highpass=f={cue.get('highpass_hz', 80)}",
               f"lowpass=f={cue.get('lowpass_hz', 10000)}"]
    if cue.get('speech_notch_db', 0):
        filters += [f"equalizer=f=2600:t=q:w=0.9:g={cue['speech_notch_db']}"]
    speed = (end-start) / length
    while speed > 2:
        filters.append('atempo=2')
        speed /= 2
    while speed < .5:
        filters.append('atempo=0.5')
        speed /= .5
    filters += [f'atempo={speed}']
    audio = decode(path, filters)
    if len(audio) < 48 or np.max(abs(audio), initial=0) < 1e-8:
        raise ValueError('Empty/silent source trim')
    count = round(length*SR)
    audio = np.pad(audio[:count], ((0, max(0, count-len(audio))), (0, 0)))
    audio -= audio.mean(axis=0)
    fade_in = min(count, round(cue.get('fade_in_ms', 3)*SR/1000))
    fade_out = min(count, round(cue.get('fade_out_ms', 35)*SR/1000))
    if fade_in:
        audio[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    if fade_out:
        audio[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    mid = audio.mean(axis=1)
    side = (audio[:, 0]-audio[:, 1])*.5*cue.get('width', .55)
    audio = np.stack([mid+side, mid-side], axis=1)
    pan = np.linspace(cue.get('pan', 0), cue.get('pan_end', cue.get('pan', 0)), count)
    if np.max(abs(pan)) > 1:
        raise ValueError('pan must lie in [-1, 1]')
    audio[:, 0] *= np.cos((pan+1)*np.pi/4)*math.sqrt(2)
    audio[:, 1] *= np.sin((pan+1)*np.pi/4)*math.sqrt(2)
    power = np.mean(audio*audio, axis=1)
    blocks = power[:count//480*480].reshape(-1, 480).mean(axis=1)
    if not len(blocks):
        blocks = np.array([power.mean()])
    active = blocks[blocks > blocks.max()*.01]
    rms = math.sqrt(float(active.mean()))
    gain = min(10**(cue['rms_dbfs']/20)/max(rms, 1e-12),
               10**(cue['peak_dbfs']/20)/max(float(abs(audio).max()), 1e-12))
    audio *= gain
    anchor = cue.get('anchor', 'start')
    if anchor == 'peak':
        # Short RMS rather than a single phase-dependent waveform spike.
        width = min(240, count)
        energy = np.convolve(np.mean(audio*audio, axis=1), np.ones(width)/width, mode='same')
        feature = int(energy.argmax())
    elif anchor == 'start':
        feature = 0
    else:
        feature = int(cue['anchor_sample'])
        if not 0 <= feature < count:
            raise ValueError('Invalid anchor_sample')
    return audio, feature, {'filters': filters, 'gain_db': 20*math.log10(gain)}


def render(config_path, output):
    config_path = Path(config_path).resolve()
    cfg = json.loads(config_path.read_text(encoding='utf-8'))
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output folder must be empty; preserve earlier renders')
    if cfg.get('schema') != 'sound-cues/1':
        raise ValueError('Unsupported schema')
    fps = cfg['fps']
    if Fraction(fps) <= 0 or not isinstance(cfg['duration_frames'], int) or cfg['duration_frames'] <= 0:
        raise ValueError('fps and integer duration_frames must be positive')
    count = frame_sample(cfg['duration_frames'], fps)
    assets = cfg['assets']
    ids = [c['id'] for c in cfg['cues']]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r'[A-Za-z0-9_-]+', x) for x in ids):
        raise ValueError('Cue IDs must be unique and safe filenames')
    for cue in cfg['cues']:
        if not isinstance(cue['target_frame'], int):
            raise ValueError('target_frame must be an integer; use target_offset_samples for subframes')
        if not isinstance(cue.get('target_offset_samples', 0), int):
            raise ValueError('target_offset_samples must be an integer')
        if not 0 <= cue.get('duck_db', 5) <= 24:
            raise ValueError('duck_db must attenuate, in [0, 24]')
        if not all(math.isfinite(cue[x]) for x in ('rms_dbfs', 'peak_dbfs')):
            raise ValueError('Cue levels must be finite')
    paths = {}
    for key in {cue['asset'] for cue in cfg['cues']}:
        asset = assets[key]
        path = (config_path.parent/asset['path']).resolve()
        if asset.get('rights_status') != 'reviewed' or not asset.get('sha256'):
            raise ValueError('Asset needs local rights review and digest: ' + key)
        if digest(path) != asset['sha256']:
            raise ValueError('Source hash changed: ' + key)
        paths[key] = path
    voice = np.zeros((count, 2))
    voice_info = None
    if cfg.get('voice'):
        spec = cfg['voice']
        path = (config_path.parent/spec['path']).resolve()
        if digest(path) != spec['sha256']:
            raise ValueError('Dialogue source hash changed')
        decoded = decode(path)
        # Allow only <1 ms decoder end rounding; never silently accept missing speech.
        if len(decoded) < count-48:
            raise ValueError('Dialogue is shorter than the requested movie')
        voice[:min(count, len(decoded))] = decoded[:count]
        voice *= 10**(spec.get('gain_db', 0)/20)
        voice_info = {**spec, 'source_samples': len(decoded)}
    envelope = dialogue_envelope(voice, cfg.get('ducking', {}))
    output.mkdir(parents=True, exist_ok=True)
    (output/'clips').mkdir()
    sfx = np.zeros_like(voice)
    cues = []
    lanes = {}
    for cue in cfg['cues']:
        audio, feature, info = render_clip(paths[cue['asset']], cue)
        target = frame_sample(cue['target_frame'], fps) + cue.get('target_offset_samples', 0)
        start = target-feature
        if start < 0 or start+len(audio) > count:
            raise ValueError('Cue extends beyond movie: ' + cue['id'])
        attenuation_db = -cue.get('duck_db', 5)*envelope[start:start+len(audio)]
        audio *= 10**(attenuation_db[:, None]/20)
        role = cue.get('role', 'foley')
        ends = lanes.setdefault(role, [])
        lane = next((i for i, end in enumerate(ends) if end <= start), len(ends))
        if lane == len(ends):
            ends.append(start+len(audio))
        else:
            ends[lane] = start+len(audio)
        write_wav(output/'clips'/f"{cue['id']}.wav", audio)
        sfx[start:start+len(audio)] += audio
        cues.append({**cue, **info, 'start_sample': start, 'sample_count': len(audio),
                     'feature_sample': feature, 'target_sample': target,
                     'duck_min_db': float(attenuation_db.min()), 'lane': lane+1,
                     'rendered_path': f"clips/{cue['id']}.wav",
                     'rendered_sha256': digest(output/'clips'/f"{cue['id']}.wav")})
    mix = voice+sfx
    for name, audio in [('dialogue', voice), ('sfx', sfx), ('mix', mix)]:
        write_wav(output/f'{name}.wav', audio)
    report = {'schema': 'sound-render/1', 'status': 'needs_qc', 'fps': fps, 'duration_frames': cfg['duration_frames'],
              'sample_rate': SR, 'sample_count': count, 'voice': voice_info,
              'config_sha256': digest(config_path), 'cues': cues, 'lanes': {k: len(v) for k,v in lanes.items()},
              'loudness': loudness(output/'mix.wav'),
              'sample_peak_dbfs': 20*math.log10(max(float(abs(mix).max()), 1e-12)),
              'clipped_samples': int((abs(mix) >= 1).sum()),
              'voice_policy': 'constant gain only; no compression, denoise or retiming',
              'ducking_policy': 'energy controlled gain reduction on SFX only',
              'subjective_listening': False, 'resolve_import_verified': False}
    peak_ok = float(report['loudness']['input_tp']) <= cfg.get('max_true_peak_dbfs', -1)
    report['status'] = 'render_verified' if peak_ok else 'failed_true_peak'
    (output/'render-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    if not peak_ok:
        raise ValueError('True peak exceeds budget; reduce cue levels or reviewed dialogue gain')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = render(args.config, args.output)
    print(json.dumps({k: result[k] for k in ['sample_count', 'loudness', 'clipped_samples', 'lanes']}, indent=2))
