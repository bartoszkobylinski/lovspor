"""Borealis-27B on Modal - vLLM serving an OpenAI-compatible endpoint (ruling #31).

The National Library of Norway's ``NbAiLab/borealis-27b`` (final release,
2026-05-26; Gemma-3-27b-it fine-tune, nb-license-1.0, not gated) has no
hosted inference provider, so the LLHB control arm rents one: bf16, no
quantisation - the defensible configuration for a published number. Weights
are cached in a Modal Volume, so only the first cold start pays the ~54 GB
download.

Deploy:  VLLM_API_KEY=<token> modal deploy benchmarks/llhb/runner/borealis_modal.py
Stop:    modal app stop borealis-vllm      <- do this after the run; idle GPU bills
Run:     uv run python benchmarks/llhb/runner/run_arm.py --condition control \\
             --driver openai-chat --provider nbailab --model borealis-27b \\
             --base-url https://<workspace>--borealis-vllm-serve.modal.run/v1 \\
             --api-key-env BOREALIS_API_KEY \\
             --suffix <sfx> --frozen --execute
         with BOREALIS_API_KEY=<the same token> in .env.
"""

import os
import subprocess

import modal

MODEL = "NbAiLab/borealis-27b"
SERVED_NAME = "borealis-27b"
PORT = 8000

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("vllm==0.27.1", "huggingface_hub[hf_transfer]")
    # No CUDA toolchain in the slim image: keep vLLM off the FlashInfer JIT
    # sampler, which needs nvcc at first use.
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "VLLM_USE_FLASHINFER_SAMPLER": "0"})
)

app = modal.App("borealis-vllm")
cache = modal.Volume.from_name("hf-cache-borealis", create_if_missing=True)


@app.function(
    image=image,
    gpu="A100-80GB",
    timeout=3600,
    scaledown_window=600,
    volumes={"/root/.cache/huggingface": cache},
    secrets=[modal.Secret.from_dict({"VLLM_API_KEY": os.environ.get("VLLM_API_KEY", "")})],
)
@modal.concurrent(max_inputs=4)
@modal.web_server(port=PORT, startup_timeout=3000)
def serve() -> None:
    subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [  # noqa: S607 - vllm is on PATH inside the image
            "vllm",
            "serve",
            MODEL,
            "--served-model-name",
            SERVED_NAME,
            "--host",
            "0.0.0.0",  # noqa: S104 - container-internal bind, fronted by Modal's proxy
            "--port",
            str(PORT),
            "--dtype",
            "bfloat16",
            "--max-model-len",
            "8192",
            "--api-key",
            os.environ["VLLM_API_KEY"],
        ]
    )
