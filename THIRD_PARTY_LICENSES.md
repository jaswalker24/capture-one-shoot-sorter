# Third-party licences

Capture One Shoot Sorter's own code is MIT. This file covers the Python packages pinned in `requirements.txt` and the tools `install.sh` may download. Model weights are documented in the README, under [Models and licences](README.md#models-and-licences), and are not MIT.

Licences below were checked against the project's published licence file or package metadata at the time of writing. They can change; follow the linked source if you need a current answer.

## Tools downloaded by the installer

| Tool | Used for | Licence |
|---|---|---|
| [uv](https://github.com/astral-sh/uv) | Python package installer and venv | [Apache 2.0 / MIT](https://github.com/astral-sh/uv/blob/main/LICENSE) |
| [ExifTool](https://exiftool.org/) | Read-only image metadata | [GPL-1+ or Artistic Licence](https://exiftool.org/#license) |
| [Ollama](https://github.com/ollama/ollama) | Local model server (localhost only) | [MIT](https://github.com/ollama/ollama/blob/main/LICENSE) (covers the server, not the models it pulls) |

## Python packages (`requirements.txt`)

| Package | Licence |
|---|---|
| torch, torchvision | [BSD-style](https://github.com/pytorch/pytorch/blob/main/LICENSE) |
| open_clip_torch | [MIT](https://github.com/mlfoundations/open_clip/blob/main/LICENSE) |
| rawpy | [MIT](https://github.com/letmaik/rawpy/blob/master/LICENSE) |
| pyobjc-core, pyobjc-framework-Cocoa, pyobjc-framework-Quartz, pyobjc-framework-Vision, pyobjc-framework-CoreML | [MIT](https://github.com/ronaldoussoren/pyobjc) |
| transformers, huggingface_hub | [Apache 2.0](https://github.com/huggingface/transformers/blob/main/LICENSE) |
| pillow | [HPND / PIL licence](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| requests | [Apache 2.0](https://github.com/psf/requests/blob/main/LICENSE) |
| numpy | [BSD](https://github.com/numpy/numpy/blob/main/LICENSE.txt) |
| opencv-python-headless | [Apache 2.0](https://github.com/opencv/opencv/blob/4.x/LICENSE) |
| scikit-image | [BSD](https://github.com/scikit-image/scikit-image/blob/main/LICENSE.txt) |
| scikit-learn | [BSD](https://github.com/scikit-learn/scikit-learn/blob/main/COPYING) |
| scipy | [BSD](https://github.com/scipy/scipy/blob/main/LICENSE.txt) |
| onnxruntime | [MIT](https://github.com/microsoft/onnxruntime/blob/main/LICENSE) |
| timm | [Apache 2.0](https://github.com/huggingface/pytorch-image-models/blob/main/LICENSE) |
| ftfy | [MIT](https://github.com/rspeer/python-ftfy/blob/main/LICENSE.txt) |
| regex | [Apache 2.0](https://github.com/mrabarnett/mrab-regex) |
| safetensors | [Apache 2.0](https://github.com/huggingface/safetensors/blob/main/LICENSE) |
| tokenizers | [Apache 2.0](https://github.com/huggingface/tokenizers/blob/main/LICENSE) |
| sentencepiece | [Apache 2.0](https://github.com/google/sentencepiece/blob/master/LICENSE) |
| tiktoken | [MIT](https://github.com/openai/tiktoken/blob/main/LICENSE) |
| tqdm | [MPL-2.0 and MIT](https://github.com/tqdm/tqdm/blob/master/LICENCE) |
| PyYAML | [MIT](https://github.com/yaml/pyyaml/blob/master/LICENSE) |
| filelock | [Unlicense](https://github.com/tox-dev/filelock/blob/main/LICENSE) |
| fsspec | [BSD](https://github.com/fsspec/filesystem_spec/blob/master/LICENSE) |
| packaging | [Apache 2.0 or BSD](https://github.com/pypa/packaging/blob/main/LICENSE) |
| typing_extensions | [PSF](https://github.com/python/typing_extensions/blob/main/LICENSE) |

Some of these packages have their own dependencies, which keep their upstream licences.
