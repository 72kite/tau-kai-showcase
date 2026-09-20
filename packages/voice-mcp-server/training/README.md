# Training the "hey tau" wake-word model

Everything needed to reproduce `../src/voice_mcp_server/models/hey_tau.onnx`.
`hey_tau.yml` is the exact config that produced the shipped model; its comments explain each
non-default value and why it is what it is.

The measured operating curve and the honest assessment of the result live in the package
[README](../README.md#the-bundled-hey-tau-model) — read that before changing anything here.

## Environment

The upstream openWakeWord training notebook is a Colab/Linux/Python-3.10 artifact and **does not
run as written**. It also cannot run natively on Windows at all: `piper-phonemize` publishes no
Windows wheels. The shipped model was trained in WSL2 (Ubuntu) with an RTX 3070, ~1h wall clock.

```bash
# Python 3.11 exactly: piper-phonemize 1.1.0 has manylinux wheels only for cp39/310/311,
# and current Ubuntu ships 3.14.
uv venv --python 3.11 .venv && source .venv/bin/activate
uv pip install "numpy<2" torch torchaudio            # numpy<2 required by the sample generator

git clone https://github.com/dscripka/openWakeWord.git
git clone https://github.com/rhasspy/piper-sample-generator.git
git -C piper-sample-generator checkout v2.0.0        # v3 deleted the API train.py imports

uv pip install -r piper-sample-generator/requirements.txt
uv pip install torchinfo torchmetrics speechbrain "audiomentations==0.33.0" \
  torch-audiomentations acoustics pyyaml onnx onnxscript pronouncing datasets \
  "deep-phonemizer==0.0.19" mutagen webrtcvad soundfile librosa \
  "scipy==1.14.1" "setuptools<81"                    # see pins below
uv pip install -e ./openWakeWord
```

## The seven breaks, and why each pin exists

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 1 | `ImportError: cannot import name 'generate_samples'` | piper-sample-generator v3.2.0 replaced the module API `train.py` imports | pin **v2.0.0** |
| 2 | `UnpicklingError: Weights only load failed` | torch 2.6 flipped `torch.load(weights_only=)` to `True`; hits Piper's checkpoint *and* DeepPhonemizer's | `weights_only=False` shim |
| 3 | `cannot import name 'sph_harm'` | `acoustics` (module-level import in `data.py`, used for colored-noise augmentation) uses an API removed in scipy 1.17 | pin **scipy 1.14.1** |
| 4 | `No module named 'pkg_resources'` | `webrtcvad` imports it; setuptools ≥81 dropped it and uv venvs omit setuptools | pin **setuptools <81** |
| 5 | HTTP 404 on `bal_train09.tar` | AudioSet was re-laid-out as parquet shards | stream via `datasets` |
| 6 | `TorchCodec is required for load_with_torchcodec` | torchaudio 2.9 removed its built-in audio backends | shim `torchaudio.load`/`.info` onto soundfile |
| 7 | `No module named 'onnxscript'`, then a 14KB .onnx | torch 2.13's exporter needs onnxscript and writes weights as **external data** | install onnxscript; re-save to inline weights |

Breaks 2 and 6 are handled by a wrapper that patches `torch.load`, `torchaudio.load` and
`torchaudio.info` before running `train.py`. Break 7 matters most: the raw export *loads fine* and
silently carries no weights, so validate the artifact size (>100KB) — `test_wake_word.py` asserts
this.

## Data

| What | Where | Size |
|---|---|---|
| Negative features (2,000 h) | `davidscripka/openwakeword_features` → `openwakeword_features_ACAV100M_2000_hrs_16bit.npy` | 16.1 GB |
| Validation features (10.7 h) | same repo → `validation_set_features.npy` | 177 MB |
| TTS model (~900 speakers) | piper-sample-generator v2.0.0 release → `en_US-libritts_r-medium.pt` | 195 MB |
| Room impulse responses | `davidscripka/MIT_environmental_impulse_responses` | 270 files |
| Background noise | `agkphysics/AudioSet` (balanced, streamed) | 2,000 clips |

Music (FMA) was **not** included: that dataset requires `trust_remote_code`, i.e. executing
third-party code from the Hub. AudioSet's ontology already contains music classes and the 2,000h
ACAV100M set is the dominant negative signal, so the omission is minor — but it is an omission.

## Run

```bash
python run_train.py --training_config hey_tau.yml --generate_clips   # ~12 min
python run_train.py --training_config hey_tau.yml --augment_clips --overwrite   # ~32 min
python run_train.py --training_config hey_tau.yml --train_model      # ~11 min
```

`--overwrite` on the augment step is not optional. `compute_features_from_generator` preallocates
its output `.npy` via `open_memmap(mode='w+')` **before** consuming the generator, so any crash
leaves a full-size all-zero file — and the augment guard treats mere file existence as "already
done". A failed run therefore poisons every later run silently, exiting 0 with no augmentation
performed. Verify the arrays are non-zero at *both* head and tail before trusting them.

`train.py` exits non-zero even on success: it calls `convert_onnx_to_tflite` after exporting, and
we deliberately never install the TensorFlow stack (tflite-only, unbuildable on 3.11). Judge
success by whether the `.onnx` appeared, not by the exit code.
