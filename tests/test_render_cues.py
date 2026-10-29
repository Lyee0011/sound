"""Generated fixtures exercise timing and preservation, not sound-design quality."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
import numpy as np

spec = importlib.util.spec_from_file_location('render_cues', Path(__file__).parents[1]/'scripts/render_cues.py')
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)


class CueTests(unittest.TestCase):
    def test_rational_clock_has_no_cumulative_rounding_drift(self):
        self.assertEqual(E.frame_sample(30000, '30000/1001'), 48048000)
        self.assertEqual(E.frame_sample(24000, '24000/1001'), 48048000)
        self.assertEqual(E.frame_sample(2400, '60/1'), 1920000)

    def test_ducking_tracks_energy_and_releases_without_boost(self):
        voice = np.zeros((E.SR, 2))
        voice[12000:24000] = .1
        env = E.dialogue_envelope(voice, {})
        self.assertTrue(np.all((env >= 0) & (env <= 1)))
        self.assertGreater(env[18000], .99)
        self.assertGreater(env[24500], .9)
        self.assertLess(env[-1], .1)
        self.assertEqual(env[0], 0)
        with self.assertRaises(ValueError):
            E.dialogue_envelope(voice, {'attack_ms': 0})

    def fixture(self, root):
        t = np.arange(14400)/E.SR
        transient = .2*np.sin(2*np.pi*510*t)*np.exp(-((t-.09)/.025)**2)
        E.write_wav(root/'tap.wav', np.stack([transient, transient], axis=1))
        voice = np.ones((E.SR, 2))*.01
        E.write_wav(root/'voice.wav', voice)
        cue = {'id': 'first', 'asset': 'tap', 'target_frame': 18, 'trim_seconds': [0, .3],
               'anchor': 'peak', 'rms_dbfs': -26, 'peak_dbfs': -17, 'duck_db': 4}
        return {'schema': 'sound-cues/1', 'fps': '60/1', 'duration_frames': 60,
                'voice': {'path': 'voice.wav', 'sha256': E.digest(root/'voice.wav'), 'gain_db': -3},
                'assets': {'tap': {'path': 'tap.wav', 'sha256': E.digest(root/'tap.wav'), 'rights_status': 'reviewed'}},
                'cues': [cue, {**cue, 'id': 'second', 'target_frame': 22}]}

    def test_transient_alignment_stem_sum_and_overlap_lanes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cfg = self.fixture(root)
            (root/'cues.json').write_text(json.dumps(cfg))
            report = E.render(root/'cues.json', root/'out')
            self.assertEqual(report['status'], 'render_verified')
            self.assertEqual(report['lanes']['foley'], 2)
            rebuilt = E.decode(root/'out/dialogue.wav')
            original_voice = E.decode(root/'voice.wav')*10**(-3/20)
            self.assertLess(abs(rebuilt-original_voice).max(), 2e-7)
            for c in report['cues']:
                self.assertEqual(c['start_sample']+c['feature_sample'], c['target_sample'])
                clip = E.decode(root/'out'/c['rendered_path'])
                rebuilt[c['start_sample']:c['start_sample']+len(clip)] += clip
            self.assertLess(abs(rebuilt-E.decode(root/'out/mix.wav')).max(), 5e-7)
            with self.assertRaises(ValueError):
                E.render(root/'cues.json', root/'out')

    def test_changed_source_and_unsafe_ids_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cfg = self.fixture(root)
            cfg['assets']['tap']['sha256'] = '0'*64
            (root/'cues.json').write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ValueError, 'hash changed'):
                E.render(root/'cues.json', root/'out')
            self.assertFalse((root/'out').exists())
            cfg['cues'][0]['id'] = '../escape'
            (root/'cues.json').write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ValueError, 'safe filenames'):
                E.render(root/'cues.json', root/'out')


if __name__ == '__main__':
    unittest.main()
