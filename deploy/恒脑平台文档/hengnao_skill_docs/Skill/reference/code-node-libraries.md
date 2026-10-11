# 代码节点内置第三方库

> 已确认平台代码容器预装库清单（生成代码节点时仅使用下列库）。

## 常用库速查

- `openpyxl` 3.1.5
- `python-docx` 1.2.0
- `pandas` 2.3.3
- `numpy` 2.2.6
- `lxml` 6.1.0
- `PyMuPDF` 1.26.7
- `pypdf` 6.11.0
- `python-pptx` 1.0.2
- `pillow` 12.2.0
- `requests` 2.32.5
- `httpx` 0.28.1
- `langchain-openai` 0.3.35（平台示例亦直接 `from openai import OpenAI`，与网关兼容调用）
- `beautifulsoup4` 4.13.5
- `scikit-learn` 1.7.2
- `matplotlib` 3.9.2
- `reportlab` 4.4.7
- `cryptography` 46.0.3
- `PyYAML` 6.0.3
- `Jinja2` 3.1.6
- `regex` 2026.5.9

## 完整清单

| 名称 | 版本 |
|------|------|
| aiofiles | 25.1.0 |
| annotated-types | 0.7.0 |
| anyio | 4.13.0 |
| argcomplete | 3.6.3 |
| argon2-cffi | 25.1.0 |
| argon2-cffi-bindings | 25.1.0 |
| arrow | 1.4.0 |
| asttokens | 3.0.1 |
| async-timeout | 4.0.3 |
| attrs | 26.1.0 |
| autogen-agentchat | 0.7.5 |
| autogen-core | 0.7.5 |
| autogen-ext | 0.7.5 |
| babel | 2.18.0 |
| backoff | 2.2.1 |
| beautifulsoup4 | 4.13.5 |
| bleach | 6.3.0 |
| cairocffi | 1.7.1 |
| CairoSVG | 2.8.2 |
| certifi | 2026.4.22 |
| cffi | 2.0.0 |
| chardet | 7.4.3 |
| charset-normalizer | 3.4.7 |
| click | 8.3.3 |
| clickhouse-driver | 0.2.10 |
| colorclass | 2.2.2 |
| comm | 0.2.3 |
| compressed-rtf | 1.0.7 |
| contourpy | 1.3.2 |
| cryptography | 46.0.3 |
| cssselect2 | 0.9.0 |
| cycler | 0.12.1 |
| dataclasses-json | 0.6.7 |
| debugpy | 1.8.20 |
| decorator | 5.2.1 |
| defusedxml | 0.7.1 |
| dify-client | 0.1.10 |
| distro | 1.9.0 |
| docx2txt | 0.9 |
| docxcompose | 1.4.0 |
| easygui | 0.98.3 |
| ebcdic | 1.1.1 |
| elastic-transport | 9.4.0 |
| elasticsearch | 9.1.0 |
| emoji | 2.15.0 |
| et_xmlfile | 2.0.0 |
| exceptiongroup | 1.3.1 |
| executing | 2.2.1 |
| extract-msg | 0.55.0 |
| fastjsonschema | 2.21.2 |
| filelock | 3.29.0 |
| filetype | 1.2.0 |
| fonttools | 4.62.1 |
| fqdn | 1.5.1 |
| freetype-py | 2.5.1 |
| fsspec | 2026.4.0 |
| greenlet | 3.5.0 |
| h11 | 0.16.0 |
| hf-xet | 1.5.0 |
| html5lib | 1.1 |
| httpcore | 1.0.9 |
| httpx | 0.28.1 |
| huggingface_hub | 0.36.2 |
| idna | 3.11 |
| importlib_metadata | 8.7.1 |
| ipykernel | 6.29.3 |
| ipython | 8.22.2 |
| isoduration | 20.11.0 |
| jedi | 0.19.2 |
| Jinja2 | 3.1.6 |
| jiter | 0.14.0 |
| joblib | 1.5.3 |
| jsonpatch | 1.33 |
| jsonpointer | 3.1.1 |
| jsonref | 1.1.0 |
| jsonschema | 4.26.0 |
| jsonschema-specifications | 2025.9.1 |
| jupyter-events | 0.12.0 |
| jupyter_client | 8.8.0 |
| jupyter_core | 5.9.1 |
| jupyter_server | 2.13.0 |
| jupyter_server_terminals | 0.5.4 |
| jupyterlab_pygments | 0.3.0 |
| kiwisolver | 1.5.0 |
| langchain | 0.3.27 |
| langchain-core | 0.3.86 |
| langchain-openai | 0.3.35 |
| langchain-text-splitters | 0.3.11 |
| langdetect | 1.0.9 |
| langsmith | 0.8.3 |
| lark | 1.3.1 |
| lxml | 6.1.0 |
| markdown-it-py | 4.0.0 |
| MarkupSafe | 3.0.3 |
| marshmallow | 3.26.2 |
| matplotlib | 3.9.2 |
| matplotlib-inline | 0.2.1 |
| mdurl | 0.1.2 |
| mermaid-py | 0.8.0 |
| mistune | 3.2.0 |
| msoffcrypto-tool | 6.0.0 |
| mypy_extensions | 1.1.0 |
| nbclient | 0.10.4 |
| nbconvert | 7.17.0 |
| nbformat | 5.10.4 |
| nest-asyncio | 1.6.0 |
| nltk | 3.9.4 |
| numpy | 2.2.6 |
| olefile | 0.47 |
| oletools | 0.60.2 |
| opencv-python | 4.12.0.88 |
| openpyxl | 3.1.5 |
| opentelemetry-api | 1.41.1 |
| orjson | 3.9.15 |
| overrides | 7.7.0 |
| packaging | 25.0 |
| pandas | 2.3.3 |
| pandocfilters | 1.5.1 |
| parso | 0.8.6 |
| pcodedmp | 1.2.6 |
| pcre2 | 0.6.0 |
| pdfminer.six | 20260107 |
| pexpect | 4.9.0 |
| pillow | 12.2.0 |
| platformdirs | 4.9.4 |
| prettytable | 3.17.0 |
| prometheus_client | 0.24.1 |
| prompt_toolkit | 3.0.52 |
| protobuf | 5.29.6 |
| psutil | 7.2.2 |
| ptyprocess | 0.7.0 |
| pure_eval | 0.2.3 |
| pycairo | 1.29.0 |
| pycparser | 3.0 |
| pycryptodome | 3.23.0 |
| pycryptodomex | 3.23.0 |
| pydantic | 2.13.4 |
| pydantic_core | 2.46.4 |
| pyecharts | 2.0.9 |
| Pygments | 2.20.0 |
| PyMuPDF | 1.26.7 |
| pyparsing | 3.3.2 |
| pypdf | 6.11.0 |
| pypdfium2 | 5.8.0 |
| python-dateutil | 2.9.0.post0 |
| python-docx | 1.2.0 |
| python-iso639 | 2026.4.20 |
| python-json-logger | 4.1.0 |
| python-magic | 0.4.27 |
| python-oxmsg | 0.0.2 |
| python-pptx | 1.0.2 |
| pytz | 2026.1.post1 |
| PyYAML | 6.0.3 |
| pyzipper | 0.3.6 |
| pyzmq | 27.1.0 |
| RapidFuzz | 3.14.5 |
| rarfile | 4.2 |
| red-black-tree-mod | 1.22 |
| referencing | 0.37.0 |
| regex | 2026.5.9 |
| reportlab | 4.4.7 |
| requests | 2.32.5 |
| requests-toolbelt | 1.0.0 |
| rfc3339-validator | 0.1.4 |
| rfc3986-validator | 0.1.1 |
| rfc3987-syntax | 1.1.0 |
| rfpimp | 1.3.7 |
| rlPyCairo | 0.4.0 |
| rpds-py | 0.30.0 |
| RTFDE | 0.1.2.2 |
| safetensors | 0.7.0 |
| scapy | 2.6.1 |
| scikit-learn | 1.7.2 |
| scipy | 1.15.3 |
| Send2Trash | 2.1.0 |
| simplejson | 4.1.1 |
| six | 1.17.0 |
| sniffio | 1.3.1 |
| soupsieve | 2.8.3 |
| SpeechRecognition | 3.16.1 |
| SQLAlchemy | 2.0.49 |
| stack-data | 0.6.3 |
| svglib | 1.6.0 |
| tenacity | 9.1.4 |
| terminado | 0.18.1 |
| textract-py3 | 3.0.0 |
| threadpoolctl | 3.6.0 |
| tiktoken | 0.12.0 |
| tinycss2 | 1.4.0 |
| tokenizers | 0.22.2 |
| tornado | 6.5.5 |
| tqdm | 4.67.3 |
| traitlets | 5.14.3 |
| transformers | 4.57.3 |
| typing-inspect | 0.9.0 |
| typing-inspection | 0.4.2 |
| typing_extensions | 4.15.0 |
| tzdata | 2025.3 |
| tzlocal | 5.3.1 |
| unstructured | 0.18.21 |
| unstructured-client | 0.42.12 |
| uri-template | 1.3.0 |
| urllib3 | 2.7.0 |
| uuid_utils | 0.15.0 |
| wcwidth | 0.6.0 |
| webcolors | 25.10.0 |
| webencodings | 0.5.1 |
| websocket-client | 1.9.0 |
| wrapt | 2.1.2 |
| xarray | 2025.6.1 |
| xlrd | 1.2.0 |
| xlsxwriter | 3.2.9 |
| xxhash | 3.7.0 |
| zipp | 3.23.0 |
| zstandard | 0.25.0 |

共 215 个包。平台升级后版本可能变化，以运行环境为准。
