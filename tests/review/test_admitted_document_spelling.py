from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.review import ReviewError
from engine.dubflow.review.audio_composition import PinnedDocument
from engine.dubflow.review.effective_cue_execution import prepare_bound_cue
from engine.dubflow.review.synthesis_input import _digest, _encode
from engine.dubflow.tts import vieneu
from test_cue_plan import REPO, PROFILE
import test_effective_cue_execution as dispatch_helpers


def native_spelling(path):
    text = str(path)
    # The native Windows store keeps a forward-slash data-root prefix when
    # joining backslash components. POSIX Path also removes an admitted '/./'.
    return text.replace('\\', '/', 3) if os.name == 'nt' else str(path.parent) + '/./' + path.name


class AdmittedDocumentSpellingTests(unittest.TestCase):
    def bound_fixture(self, directory):
        context, bindings, content = dispatch_helpers.EffectiveCueDispatchTests().fixture(directory)
        composition = json.loads(bindings['content_composition_json'])
        metadata = Path(directory) / 'native metadata separate from audio'
        metadata.mkdir()
        for admitted, serialized in zip(bindings['documents'], composition['documents']):
            retained = metadata / (admitted['sha256'][7:] + '.json')
            retained.write_bytes(Path(admitted['path']).read_bytes())
            admitted.update(path=native_spelling(retained), metadata_root=native_spelling(metadata))
            admitted['artifact_root'] = native_spelling(Path(admitted['artifact_root']))
            self.assertNotEqual(admitted['path'], str(Path(admitted['path'])))
            serialized.clear(); serialized.update(admitted)
        composition['schema_version'] = 2
        self.bind_content(context, bindings, composition)
        return context, bindings, content

    def bind_content(self, context, bindings, composition):
        raw = _encode(composition, 65536)
        bindings['content_composition_json'] = raw.decode('utf-8')
        bindings['content_composition_sha256'] = _digest(raw)
        context['current_composition_sha256'] = _digest(raw)

    def test_native_spelling_roundtrips_exact_composition_without_inference(self):
        with TemporaryDirectory(prefix='admitted-wire-roundtrip-') as directory:
            context, bindings, content = self.bound_fixture(directory)
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in Path(directory).rglob('*') if p.is_file()}
            with patch.object(vieneu, 'load_vieneu_voice', side_effect=AssertionError('metadata cannot load models')):
                packet = prepare_bound_cue(_encode(context, 65536), _encode(bindings, 65536), app_root=REPO, profile_path=PROFILE)
            reopened = json.loads(packet['effective_composition_json'])
            self.assertEqual(reopened['documents'], bindings['documents'])
            self.assertEqual(reopened['cues'], content.to_dict()['cues'])
            self.assertEqual(reopened['source'], content.to_dict()['source'])
            self.assertFalse(packet['model_weights_verified'])
            self.assertFalse(packet['active_graph_published'])
            self.assertEqual(_digest(packet['effective_composition_json'].encode()), packet['effective_composition_sha256'])
            for p, pin in before.items():
                self.assertEqual((p.read_bytes(), p.stat().st_mtime_ns), pin)

    def test_equivalent_but_unadmitted_serialized_path_still_refuses(self):
        with TemporaryDirectory(prefix='admitted-wire-substitution-') as directory:
            context, bindings, _ = self.bound_fixture(directory)
            composition = json.loads(bindings['content_composition_json'])
            # Even a spelling that opens the SAME file cannot replace the
            # separately admitted descriptor inside serialized composition.
            composition['documents'][0]['path'] = str(Path(composition['documents'][0]['path']))
            self.bind_content(context, bindings, composition)
            with self.assertRaisesRegex(ReviewError, 'admitted document inventory'):
                prepare_bound_cue(_encode(context, 65536), _encode(bindings, 65536), app_root=REPO, profile_path=PROFILE)

    def tiny_descriptor(self, directory):
        root = Path(directory); metadata = root / 'metadata'; audio = root / 'audio'
        metadata.mkdir(); audio.mkdir(); p = metadata / 'document.json'; p.write_bytes(b'{}')
        return {'path':native_spelling(p), 'artifact_root':native_spelling(audio),
                'metadata_root':native_spelling(metadata), 'sha256':'sha256:'+hashlib.sha256(b'{}').hexdigest(), 'size_bytes':2}

    def test_admitted_descriptor_is_an_immutable_copy(self):
        with TemporaryDirectory(prefix='admitted-wire-copy-') as directory:
            value = self.tiny_descriptor(directory); expected = deepcopy(value)
            pin = PinnedDocument.from_admitted_descriptor(value)
            value['path'] = 'foreign'; value['sha256'] = 'sha256:'+'a'*64
            result = pin.to_dict(); result['metadata_root'] = 'foreign'
            self.assertEqual(pin.to_dict(), expected)
            self.assertEqual(pin.read(), b'{}')
            self.assertEqual(pin.path, Path(expected['path']).absolute())

    def test_closed_fields_and_foreign_metadata_root_stay_strict(self):
        with TemporaryDirectory(prefix='admitted-wire-strict-') as directory:
            value = self.tiny_descriptor(directory)
            for update in ({'extra':'ignored'}, {'path':b'bad'}, {'size_bytes':True},
                           {'metadata_root':str(Path(directory)/'audio')}, {'artifact_root':'relative'}):
                with self.subTest(update=update):
                    with self.assertRaises((ReviewError, ValueError)):
                        PinnedDocument.from_admitted_descriptor({**value, **update})

    def test_caller_mutation_during_path_checks_cannot_change_admitted_wire(self):
        from engine.dubflow.mix import streaming
        with TemporaryDirectory(prefix='admitted-wire-during-check-') as directory:
            value = self.tiny_descriptor(directory); expected = deepcopy(value)
            original_plain = streaming._plain
            def check_then_mutate(path):
                original_plain(path)
                value['path'] = 'foreign-after-validated-path'
            with patch.object(streaming, '_plain', side_effect=check_then_mutate):
                pin = PinnedDocument.from_admitted_descriptor(value)
            self.assertEqual(pin.to_dict(), expected)
            self.assertEqual(pin.read(), b'{}')

    def test_same_hash_conflicting_admitted_spellings_refuse_before_composition(self):
        import test_audio_composition as composition_helpers
        from engine.dubflow.review.audio_composition import compose_audio
        with TemporaryDirectory(prefix='admitted-wire-conflict-') as directory:
            current, _, _, _ = composition_helpers.AudioCompositionTests().fixture(directory)
            first = current.cues[0].document.to_dict()
            changed = {**first, 'path':native_spelling(Path(first['path']))}
            a = PinnedDocument.from_admitted_descriptor(first)
            b = PinnedDocument.from_admitted_descriptor(changed)
            self.assertEqual(a, b)  # Same validated I/O identity, unchanged API.
            self.assertNotEqual(a.to_dict(), b.to_dict())  # Distinct admitted wire pins.
            root = Path(directory) / 'before'; mix = root / 'mix_document.json'
            with self.assertRaisesRegex(ReviewError, 'conflicting paths/roots'):
                compose_audio(current.snapshot, manifest_sha256=current.manifest_sha256,
                    source=current.source, source_sha256=current.source_sha256,
                    canonical_inputs=tuple(c.canonical_input for c in current.cues),
                    assignments={current.cues[0].canonical_input.segment_id:a,
                                 current.cues[1].canonical_input.segment_id:b},
                    mix_template=PinnedDocument(mix, composition_helpers.sha(mix), mix.stat().st_size, root))

    @unittest.skipUnless(os.name == 'posix', 'POSIX literal-backslash filesystem case')
    def test_posix_literal_backslash_filename_is_not_a_separator(self):
        with TemporaryDirectory(prefix='admitted-wire-posix-') as directory:
            root = Path(directory); p = root / 'literal\\metadata.json'; p.write_bytes(b'{}')
            value = {'path':str(p), 'artifact_root':str(root), 'sha256':'sha256:'+hashlib.sha256(b'{}').hexdigest(), 'size_bytes':2}
            pin = PinnedDocument.from_admitted_descriptor(value)
            self.assertEqual(pin.to_dict(), value)
            self.assertEqual(pin.path.name, 'literal\\metadata.json')
            self.assertEqual(pin.read(), b'{}')
